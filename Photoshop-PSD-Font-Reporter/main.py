from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QProgressBar, QPushButton, QSplitter, QTextEdit, QVBoxLayout,
    QWidget, QAbstractItemView, QTabWidget, QDialog, QDialogButtonBox,
)

from font_database import build_font_database
from font_manager import (
    clear_recycle_bin, delete_fonts, installed_font_files, load_recycle_bin,
    restore_fonts, run_font_helper,
)
from psd_analyzer import FileReport, analyze_file
from report_writer import load_data, preview_text, save_data, write_html, write_txt


class FileListWidget(QListWidget):
    files_dropped = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    @staticmethod
    def supported_paths(event) -> list[str]:
        if not event.mimeData().hasUrls():
            return []
        return [
            url.toLocalFile() for url in event.mimeData().urls()
            if url.isLocalFile() and Path(url.toLocalFile()).suffix.casefold() in {".psd", ".psb"}
        ]

    def dragEnterEvent(self, event):
        if self.supported_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self.supported_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = self.supported_paths(event)
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class SilentDialog(QDialog):
    def __init__(self, parent, title: str, text: str, confirm: bool = False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(390)
        layout = QVBoxLayout(self)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.Cancel
            if confirm else QDialogButtonBox.StandardButton.Ok
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @classmethod
    def show_message(cls, parent, title: str, text: str):
        cls(parent, title, text).exec()

    @classmethod
    def ask(cls, parent, title: str, text: str) -> bool:
        return cls(parent, title, text, True).exec() == QDialog.DialogCode.Accepted

class AnalyzeThread(QThread):
    progress = Signal(int, int, str)
    completed = Signal(object, str)

    def __init__(self, paths: list[str], mode: str, font_database):
        super().__init__()
        self.paths = paths
        self.mode = mode
        self.font_database = font_database

    def run(self):
        database = self.font_database or build_font_database()
        reports = []
        total = len(self.paths)
        for index, path in enumerate(self.paths, 1):
            self.progress.emit(index, total, Path(path).name)
            reports.append(analyze_file(path, database))
        self.completed.emit((reports, database), self.mode)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PSD 字体批量查询")
        self.resize(1050, 720)
        self.font_database = None
        self.cache: dict[str, FileReport] = {}
        self.worker = None
        self.pending_output = False
        self.pin_order: list[str] = []
        self.auto_timer = QTimer(self)
        self.auto_timer.setSingleShot(True)
        self.auto_timer.timeout.connect(self.auto_preview)
        self.build_ui()
        self.apply_style()

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        files_box = QGroupBox("文件选择与文件列表")
        files_layout = QVBoxLayout(files_box)
        file_buttons = QHBoxLayout()
        self.add_button = QPushButton("添加文件")
        self.remove_button = QPushButton("移除所选")
        self.clear_button = QPushButton("清空")
        file_buttons.addWidget(self.add_button)
        file_buttons.addWidget(self.remove_button)
        file_buttons.addWidget(self.clear_button)
        files_layout.addLayout(file_buttons)
        self.file_list = FileListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setAlternatingRowColors(True)
        files_layout.addWidget(self.file_list, 1)
        preview_controls = QHBoxLayout()
        self.refresh_selected_button = QPushButton("刷新")
        self.preview_button = QPushButton("全部预览")
        self.auto_preview_box = QCheckBox("自动预览")
        self.force_refresh_box = QCheckBox("强制刷新")
        preview_controls.addWidget(self.refresh_selected_button)
        preview_controls.addWidget(self.auto_preview_box)
        preview_controls.addWidget(self.force_refresh_box)
        preview_controls.addStretch()
        preview_controls.addWidget(self.preview_button)
        files_layout.addLayout(preview_controls)

        preview_box = QGroupBox("字体预览")
        preview_layout = QVBoxLayout(preview_box)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("选择文件后点击“预览”，或勾选“自动预览”。")
        self.copy_button = QPushButton("复制文本")
        self.font_manager_button = QPushButton("扩展功能")
        copy_row = QHBoxLayout()
        copy_row.addWidget(self.font_manager_button)
        copy_row.addStretch()
        copy_row.addWidget(self.copy_button)
        preview_layout.addWidget(self.preview, 1)
        preview_layout.addLayout(copy_row)
        splitter.addWidget(files_box)
        splitter.addWidget(preview_box)
        self.font_manager_box = self.build_font_manager()
        self.font_manager_box.setVisible(False)
        splitter.addWidget(self.font_manager_box)
        splitter.setSizes([430, 620])

        output_box = QGroupBox("输出")
        output_grid = QGridLayout(output_box)
        self.output_dir = QLineEdit(str(Path.home() / "Desktop"))
        self.browse_output = QPushButton("选择目录")
        self.output_name = QLineEdit("字体查询报告")
        self.output_format = QComboBox()
        self.output_format.addItems(["TXT", "HTML"])
        self.append_box = QCheckBox("追加")
        self.output_button = QPushButton("输出报告")
        self.open_output_button = QPushButton("打开输出目录")
        output_grid.addWidget(QLabel("输出目录"), 0, 0)
        output_grid.addWidget(self.output_dir, 0, 1)
        output_grid.addWidget(self.browse_output, 0, 2)
        output_grid.addWidget(QLabel("文件名称"), 1, 0)
        output_grid.addWidget(self.output_name, 1, 1)
        output_grid.addWidget(self.output_format, 1, 2)
        output_grid.addWidget(self.append_box, 2, 1)
        action_row = QHBoxLayout()
        action_row.addWidget(self.output_button)
        action_row.addWidget(self.open_output_button)
        output_grid.addLayout(action_row, 3, 1, 1, 2)
        layout.addWidget(output_box)

        status_row = QHBoxLayout()
        self.status = QLabel("就绪")
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.progress)
        layout.addLayout(status_row)

        self.add_button.clicked.connect(self.add_files)
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button.clicked.connect(self.clear_files)
        self.preview_button.clicked.connect(self.preview_all)
        self.refresh_selected_button.clicked.connect(self.refresh_selected)
        self.auto_preview_box.toggled.connect(self.on_auto_toggled)
        self.file_list.itemSelectionChanged.connect(self.on_selection_changed)
        self.file_list.files_dropped.connect(self.add_paths)
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.preview.toPlainText()))
        self.font_manager_button.clicked.connect(self.toggle_font_manager)
        self.browse_output.clicked.connect(self.choose_output_dir)
        self.output_button.clicked.connect(self.output_report)
        self.open_output_button.clicked.connect(self.open_output_dir)

    def build_font_manager(self):
        box = QGroupBox("字体管理")
        layout = QVBoxLayout(box)
        self.font_tabs = QTabWidget()
        layout.addWidget(self.font_tabs)

        installed_page = QWidget()
        installed_layout = QVBoxLayout(installed_page)
        self.installed_font_list = QListWidget()
        self.installed_font_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        installed_layout.addWidget(self.installed_font_list, 1)
        installed_buttons = QHBoxLayout()
        self.refresh_fonts_button = QPushButton("刷新")
        self.pin_fonts_button = QPushButton("置顶")
        self.delete_fonts_button = QPushButton("删除")
        installed_buttons.addWidget(self.refresh_fonts_button)
        installed_buttons.addWidget(self.pin_fonts_button)
        installed_buttons.addWidget(self.delete_fonts_button)
        installed_layout.addLayout(installed_buttons)

        recycle_page = QWidget()
        recycle_layout = QVBoxLayout(recycle_page)
        self.recycle_font_list = QListWidget()
        self.recycle_font_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        recycle_layout.addWidget(self.recycle_font_list, 1)
        recycle_buttons = QHBoxLayout()
        self.restore_fonts_button = QPushButton("还原")
        self.clear_recycle_button = QPushButton("清空回收站")
        recycle_buttons.addWidget(self.restore_fonts_button)
        recycle_buttons.addWidget(self.clear_recycle_button)
        recycle_layout.addLayout(recycle_buttons)

        self.font_tabs.addTab(installed_page, "已安装字体")
        self.font_tabs.addTab(recycle_page, "临时回收站")
        self.refresh_fonts_button.clicked.connect(self.refresh_font_manager)
        self.pin_fonts_button.clicked.connect(self.toggle_pin_fonts)
        self.delete_fonts_button.clicked.connect(self.delete_selected_fonts)
        self.restore_fonts_button.clicked.connect(self.restore_selected_fonts)
        self.clear_recycle_button.clicked.connect(self.clear_font_recycle)
        return box

    def apply_style(self):
        self.setStyleSheet("""
            QMainWindow,QWidget { background:#252525; color:#eee; font:13px 'Microsoft YaHei'; }
            QGroupBox { margin-top:10px; padding-top:13px; border:1px solid #484848; border-radius:6px; font-weight:bold; }
            QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 5px; }
            QListWidget,QTextEdit,QLineEdit,QComboBox { background:#303030; border:1px solid #555; border-radius:4px; padding:5px; }
            QListWidget::item { padding:7px 6px; color:#ddd; background:#303030; border-bottom:1px solid #252525; }
            QListWidget::item:hover { background:#393939; color:#fff; }
            QListWidget::item:selected { background:#505050; color:#fff; border-left:3px solid #a8a8a8; }
            QPushButton { min-height:28px; padding:3px 12px; background:#444; border:1px solid #606060; border-radius:4px; }
            QPushButton:hover { background:#535353; } QPushButton:disabled { color:#777; }
            QProgressBar { width:240px; border:1px solid #555; border-radius:4px; text-align:center; background:#303030; }
            QProgressBar::chunk { background:#4b9cff; }
        """)

    def paths(self) -> list[str]:
        return [self.file_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.file_list.count())]

    def selected_paths(self) -> list[str]:
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.file_list.selectedItems()]

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择 Photoshop 文件", "", "Photoshop 文件 (*.psd *.psb)")
        self.add_paths(paths)

    def add_paths(self, paths):
        existing = {os.path.normcase(path) for path in self.paths()}
        for path in paths:
            if Path(path).suffix.casefold() not in {".psd", ".psb"} or not Path(path).is_file():
                continue
            if os.path.normcase(path) in existing:
                continue
            item = QListWidgetItem(Path(path).name)
            item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.file_list.addItem(item)
            existing.add(os.path.normcase(path))
        if paths:
            self.status.setText(f"文件列表共 {self.file_list.count()} 个文件")

    def remove_selected(self):
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))
        self.status.setText(f"文件列表共 {self.file_list.count()} 个文件")

    def clear_files(self):
        self.file_list.clear()
        self.preview.clear()
        self.status.setText("文件列表已清空")

    def on_auto_toggled(self, checked):
        if checked:
            self.auto_preview()

    def on_selection_changed(self):
        if self.auto_preview_box.isChecked():
            self.auto_timer.start(250)

    def auto_preview(self):
        paths = self.selected_paths()
        if paths:
            self.start_analysis(paths, "preview")

    def preview_all(self):
        self.start_analysis(self.paths(), "preview")

    def refresh_selected(self):
        paths = self.selected_paths()
        if not paths:
            SilentDialog.show_message(self, "提示", "请先在文件列表中选择要刷新的文件。")
            return
        for path in paths:
            self.cache.pop(os.path.normcase(os.path.abspath(path)), None)
        self.start_analysis(paths, "preview")

    def start_analysis(self, paths: list[str], mode: str):
        if self.worker and self.worker.isRunning():
            self.status.setText("当前查询尚未完成")
            return
        if not paths:
            SilentDialog.show_message(self, "提示", "请先添加文件。")
            return
        force_refresh = self.force_refresh_box.isChecked()
        if force_refresh:
            self.font_database = None
            for path in paths:
                self.cache.pop(os.path.normcase(os.path.abspath(path)), None)
        cached, pending = [], []
        for path in paths:
            report = self.cache.get(os.path.normcase(os.path.abspath(path)))
            try:
                stat = os.stat(path)
                fresh = report and report.file_size == stat.st_size and report.modified_ns == stat.st_mtime_ns
            except OSError:
                fresh = False
            (cached if fresh and not force_refresh else pending).append(report if fresh and not force_refresh else path)
        if not pending:
            self.analysis_done((cached, self.font_database), mode)
            return
        self.set_busy(True)
        self.worker = AnalyzeThread(pending, mode, None if force_refresh else self.font_database)
        self.worker.progress.connect(self.analysis_progress)
        self.worker.completed.connect(lambda payload, worker_mode: self.merge_analysis(payload, worker_mode, cached))
        self.worker.start()

    def analysis_progress(self, current, total, name):
        self.progress.setRange(0, total)
        self.progress.setValue(current)
        self.status.setText(f"正在查询 {current}/{total}：{name}")

    def merge_analysis(self, payload, mode, cached):
        reports, database = payload
        self.analysis_done((cached + reports, database), mode)

    def analysis_done(self, payload, mode):
        reports, self.font_database = payload
        order = {os.path.normcase(os.path.abspath(path)): i for i, path in enumerate(self.paths())}
        for report in reports:
            self.cache[os.path.normcase(os.path.abspath(report.path))] = report
        reports.sort(key=lambda report: order.get(os.path.normcase(os.path.abspath(report.path)), 10**9))
        self.preview.setPlainText(preview_text(reports))
        self.set_busy(False)
        failures = sum(bool(report.error) for report in reports)
        self.status.setText(f"查询完成：{len(reports)} 个文件，{failures} 个失败")
        if mode == "output":
            self.write_output(reports)

    def set_busy(self, busy):
        for widget in (self.add_button, self.remove_button, self.clear_button, self.refresh_selected_button, self.preview_button, self.output_button):
            widget.setDisabled(busy)
        self.progress.setVisible(busy)

    def choose_output_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录", self.output_dir.text())
        if directory:
            self.output_dir.setText(directory)

    def output_report(self):
        self.start_analysis(self.paths(), "output")

    def write_output(self, current_reports: list[FileReport]):
        directory = Path(self.output_dir.text().strip())
        name = self.output_name.text().strip()
        if not name or any(char in name for char in '<>:"/\\|?*'):
            SilentDialog.show_message(self, "输出失败", "请输入有效的文件名称。")
            return
        try:
            directory.mkdir(parents=True, exist_ok=True)
            data_path = directory / f"{name}.data.json"
            records = load_data(data_path) if self.append_box.isChecked() else {}
            for report in current_reports:
                records[os.path.normcase(os.path.abspath(report.path))] = report
            save_data(data_path, records)
            ordered = sorted(records.values(), key=lambda item: item.file_name.casefold())
            if self.output_format.currentText() == "TXT":
                output_path = directory / f"{name}.txt"
                write_txt(output_path, ordered)
            else:
                output_path = directory / f"{name}.html"
                write_html(output_path, ordered)
            self.status.setText(f"已输出：{output_path}")
            SilentDialog.show_message(self, "输出完成", str(output_path))
        except Exception as error:
            SilentDialog.show_message(self, "输出失败", str(error))

    def open_output_dir(self):
        path = Path(self.output_dir.text().strip())
        if path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def toggle_font_manager(self):
        visible = not self.font_manager_box.isVisible()
        self.font_manager_box.setVisible(visible)
        self.font_manager_button.setText("收起扩展" if visible else "扩展功能")
        if visible:
            self.resize(max(self.width(), 1380), self.height())
            self.refresh_font_manager()

    def refresh_font_manager(self):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.font_database = build_font_database()
            entries = installed_font_files(self.font_database)
            pin_index = {path: index for index, path in enumerate(self.pin_order)}
            entries.sort(key=lambda entry: (
                0 if os.path.normcase(entry.path) in pin_index else 1,
                pin_index.get(os.path.normcase(entry.path), 10**9),
                entry.language_rank,
                entry.display_name.casefold(),
                entry.file_name.casefold(),
            ))
            self.installed_font_list.clear()
            for entry in entries:
                key = os.path.normcase(entry.path)
                prefix = "★ " if key in pin_index else ""
                item = QListWidgetItem(f"{prefix}{entry.display_name} - {entry.file_name}")
                item.setToolTip(entry.path)
                item.setData(Qt.ItemDataRole.UserRole, entry)
                self.installed_font_list.addItem(item)
            self.refresh_recycle_list()
            self.status.setText(f"已扫描 {len(entries)} 个字体文件")
        finally:
            QApplication.restoreOverrideCursor()

    def refresh_recycle_list(self):
        self.recycle_font_list.clear()
        for recycled in load_recycle_bin():
            item = QListWidgetItem(f"{recycled.display_name} - {recycled.file_name} - {recycled.deleted_at}")
            item.setToolTip(recycled.original_path)
            item.setData(Qt.ItemDataRole.UserRole, recycled)
            self.recycle_font_list.addItem(item)

    def toggle_pin_fonts(self):
        selected = [item.data(Qt.ItemDataRole.UserRole) for item in self.installed_font_list.selectedItems()]
        for entry in selected:
            key = os.path.normcase(entry.path)
            if key in self.pin_order:
                self.pin_order.remove(key)
            else:
                self.pin_order.append(key)
        self.refresh_font_manager()

    def delete_selected_fonts(self):
        entries = [item.data(Qt.ItemDataRole.UserRole) for item in self.installed_font_list.selectedItems()]
        if not entries:
            SilentDialog.show_message(self, "提示", "请先选择要删除的字体。")
            return
        names = "\n".join(f"• {entry.display_name} - {entry.file_name}" for entry in entries[:12])
        if len(entries) > 12:
            names += f"\n……共 {len(entries)} 个字体文件"
        answer = SilentDialog.ask(
            self, "确认删除字体",
            "字体将先备份到本软件的临时回收站。删除系统字体可能影响 Windows 或其他软件。\n\n" + names,
        )
        if not answer:
            return
        succeeded, errors = delete_fonts(entries)
        self.refresh_font_manager()
        message = f"已删除 {len(succeeded)} 个字体文件。"
        if errors:
            SilentDialog.show_message(self, "字体删除结果", message + "\n\n" + "\n".join(errors))
        else:
            SilentDialog.show_message(self, "字体删除结果", message)

    def restore_selected_fonts(self):
        items = [item.data(Qt.ItemDataRole.UserRole) for item in self.recycle_font_list.selectedItems()]
        if not items:
            SilentDialog.show_message(self, "提示", "请先选择要还原的字体。")
            return
        succeeded, errors = restore_fonts(items)
        self.refresh_font_manager()
        message = f"已还原 {len(succeeded)} 个字体文件。"
        if errors:
            SilentDialog.show_message(self, "字体还原结果", message + "\n\n" + "\n".join(errors))
        else:
            SilentDialog.show_message(self, "字体还原结果", message)

    def clear_font_recycle(self):
        if not load_recycle_bin():
            SilentDialog.show_message(self, "提示", "临时回收站为空。")
            return
        answer = SilentDialog.ask(self, "清空临时回收站", "清空后其中的字体备份无法恢复。确定继续吗？")
        if not answer:
            return
        errors = clear_recycle_bin()
        self.refresh_recycle_list()
        if errors:
            SilentDialog.show_message(self, "清空结果", "\n".join(errors))
        else:
            SilentDialog.show_message(self, "清空结果", "临时回收站已清空。")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PSD 字体批量查询")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--font-helper":
        raise SystemExit(run_font_helper(sys.argv[2]))
    main()
