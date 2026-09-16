from __future__ import annotations

import json
import os
import sys
import ctypes
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "Codex 使用统计"
POLL_INTERVAL_MS = 1000
FALLBACK_SCAN_MS = 60_000


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def fmt_number(value: int | float | None) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}"


def numeric_context_window(value: object, fallback: int = 0) -> int:
    """Accept numeric legacy values and ignore newer metadata-only objects."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, dict):
        for key in ("max_tokens", "size", "tokens"):
            candidate = value.get(key)
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                return int(candidate)
    return fallback


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def fmt_reset(timestamp: int | float | None) -> str:
    if not timestamp:
        return "重置时间未知"
    reset = datetime.fromtimestamp(timestamp)
    remaining = reset - datetime.now()
    seconds = max(0, int(remaining.total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours >= 24:
        days, hours = divmod(hours, 24)
        left = f"{days}天 {hours}小时"
    else:
        left = f"{hours}小时 {minutes}分钟"
    return f"{reset:%m-%d %H:%M} 重置 · 剩余 {left}"


@dataclass
class UsageSnapshot:
    session_path: Path
    session_started: datetime
    updated_at: datetime
    context_window: int
    current: dict
    total: dict
    rate_limits: dict
    rate_updated_at: datetime


@dataclass
class FileState:
    offset: int = 0
    pending: bytes = b""
    snapshot: UsageSnapshot | None = None


class DirectoryChangeWatcher(QObject):
    """Recursive, event-driven watcher backed by ReadDirectoryChangesW."""

    changed = Signal(str)

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if sys.platform != "win32" or self._thread is not None or not self.root.exists():
            return
        self._thread = threading.Thread(target=self._watch, daemon=True, name="CodexLogWatcher")
        self._thread.start()

    def _watch(self) -> None:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateFileW.restype = ctypes.c_void_p
        handle = kernel32.CreateFileW(
            str(self.root), 0x0001, 0x00000007, None, 3,
            0x02000000, None,
        )
        if handle == ctypes.c_void_p(-1).value:
            return
        buffer = ctypes.create_string_buffer(64 * 1024)
        returned = ctypes.c_ulong()
        notify_filter = 0x00000001 | 0x00000002 | 0x00000004 | 0x00000008 | 0x00000010
        while True:
            ok = kernel32.ReadDirectoryChangesW(
                handle, buffer, len(buffer), True, notify_filter,
                ctypes.byref(returned), None, None,
            )
            if not ok:
                break
            position = 0
            while position < returned.value:
                next_offset = int.from_bytes(buffer[position:position + 4], "little")
                name_length = int.from_bytes(buffer[position + 8:position + 12], "little")
                name = buffer[position + 12:position + 12 + name_length].decode("utf-16-le", "replace")
                if name.lower().endswith(".jsonl"):
                    self.changed.emit(str(self.root / name))
                if not next_offset:
                    break
                position += next_offset
        kernel32.CloseHandle(handle)


class CodexUsageReader:
    def __init__(self) -> None:
        self.sessions_dir = codex_home() / "sessions"
        self._states: dict[Path, FileState] = {}
        self._dirty: set[Path] = set()
        self._indexed = False

    def mark_changed(self, filename: str) -> None:
        self._dirty.add(Path(filename))

    @staticmethod
    def _record_time(record: dict, fallback: datetime) -> datetime:
        value = record.get("timestamp")
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
            except ValueError:
                pass
        return fallback

    def _update_file(self, path: Path, reset: bool = False) -> None:
        state = self._states.setdefault(path, FileState())
        try:
            stat = path.stat()
            if reset or stat.st_size < state.offset:
                state.offset, state.pending, state.snapshot = 0, b"", None
            if stat.st_size == state.offset:
                return
            with path.open("rb") as stream:
                stream.seek(state.offset)
                chunk = stream.read()
            state.offset += len(chunk)
        except (OSError, PermissionError):
            return

        data = state.pending + chunk
        lines = data.split(b"\n")
        state.pending = lines.pop() if lines else data
        fallback_time = datetime.fromtimestamp(stat.st_mtime)
        snapshot = state.snapshot
        started = snapshot.session_started if snapshot else datetime.fromtimestamp(stat.st_ctime)
        context_window = snapshot.context_window if snapshot else 0
        current = snapshot.current if snapshot else {}
        total = snapshot.total if snapshot else {}
        rate_limits = snapshot.rate_limits if snapshot else {}
        rate_updated = snapshot.rate_updated_at if snapshot else datetime.min
        updated = snapshot.updated_at if snapshot else datetime.min

        for raw_line in lines:
            try:
                record = json.loads(raw_line.decode("utf-8", "replace"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            record_time = self._record_time(record, fallback_time)
            if record.get("type") == "session_meta":
                started = self._record_time({"timestamp": payload.get("timestamp")}, started)
                context_window = numeric_context_window(payload.get("context_window"), context_window)
            elif record.get("type") == "token_usage_record":
                current = payload.get("usage") or payload.get("turn_token_usage") or current
                total = payload.get("thread_token_usage") or total
                updated = max(updated, record_time)
            elif record.get("type") == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info") or {}
                current = info.get("last_token_usage") or current
                total = info.get("total_token_usage") or total
                context_window = numeric_context_window(info.get("model_context_window"), context_window)
                rate_limits = payload.get("rate_limits") or rate_limits
                if payload.get("rate_limits"):
                    rate_updated = max(rate_updated, record_time)
                updated = max(updated, record_time)

        if updated != datetime.min:
            state.snapshot = UsageSnapshot(
                path, started, updated, context_window, current, total, rate_limits, rate_updated
            )

    def rescan(self) -> None:
        if not self.sessions_dir.exists():
            return
        found = set(self.sessions_dir.rglob("*.jsonl"))
        for path in found:
            self._update_file(path)
        for path in set(self._states) - found:
            del self._states[path]
        self._indexed = True

    def read(self, force: bool = False) -> UsageSnapshot | None:
        if force or not self._indexed:
            self.rescan()
        dirty, self._dirty = self._dirty, set()
        for path in dirty:
            self._update_file(path)
        snapshots = [state.snapshot for state in self._states.values() if state.snapshot]
        if not snapshots:
            return None
        with_limits = [snapshot for snapshot in snapshots if snapshot.rate_limits]
        return max(
            with_limits or snapshots,
            key=lambda snapshot: snapshot.rate_updated_at if with_limits else snapshot.updated_at,
        )


class UsageCard(QFrame):
    def __init__(self, title: str, accent: bool = False) -> None:
        super().__init__()
        self.setObjectName("usageCardAccent" if accent else "usageCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(118)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(5)
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        self.value = QLabel("—")
        self.value.setObjectName("cardValue")
        self.detail = QLabel("等待数据")
        self.detail.setObjectName("cardDetail")
        layout.addWidget(title_label)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)

    def set_data(self, value: str, detail: str) -> None:
        self.value.setText(value)
        self.detail.setText(detail)


class RateCard(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("rateCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 7, 11, 7)
        layout.setSpacing(3)
        top = QHBoxLayout()
        self.title = QLabel(title)
        self.title.setObjectName("rateTitle")
        self.percent = QLabel("—")
        self.percent.setObjectName("ratePercent")
        top.addWidget(self.title)
        top.addStretch()
        top.addWidget(self.percent)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.detail = QLabel("等待额度数据")
        self.detail.setObjectName("cardDetail")
        layout.addLayout(top)
        layout.addWidget(self.bar)
        layout.addWidget(self.detail)

    def set_data(self, data: dict | None) -> None:
        if not data:
            self.percent.setText("不可用")
            self.bar.setValue(0)
            self.detail.setText("当前日志未提供额度数据")
            return
        used = min(100.0, max(0.0, float(data.get("used_percent") or 0)))
        remaining = 100.0 - used
        self.percent.setText(f"剩余 {remaining:.0f}%")
        self.bar.setValue(round(remaining * 10))
        self.detail.setText(fmt_reset(data.get("resets_at")))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.reader = CodexUsageReader()
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(286, 156)
        self._drag_offset = None

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        page = QVBoxLayout(root)
        page.setContentsMargins(7, 7, 7, 7)
        page.setSpacing(5)

        header = QHBoxLayout()
        header_text = QVBoxLayout()
        title = QLabel("CODEX")
        title.setObjectName("title")
        header_text.addWidget(title)
        header.addLayout(header_text)
        header.addStretch()
        self.live = QLabel("●")
        self.live.setObjectName("live")
        header.addWidget(self.live)
        self.close_button = QPushButton("×")
        self.close_button.setObjectName("closeButton")
        self.close_button.setFixedSize(22, 22)
        self.close_button.clicked.connect(self.close)
        header.addWidget(self.close_button)
        page.addLayout(header)

        self.primary_rate = RateCard("5 小时剩余")
        self.secondary_rate = RateCard("每周剩余")
        page.addWidget(self.primary_rate)
        page.addWidget(self.secondary_rate)

        self.status = QLabel("正在查找 Codex 会话…")
        self.status.setObjectName("status")
        self.updated = QLabel("")
        self.updated.setObjectName("status")
        self.status.hide()
        self.updated.hide()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(POLL_INTERVAL_MS)
        self.refresh(force=True)

        self.watcher = DirectoryChangeWatcher(self.reader.sessions_dir)
        self.watcher.changed.connect(self.reader.mark_changed)
        self.watcher.start()
        self.fallback_timer = QTimer(self)
        self.fallback_timer.timeout.connect(lambda: self.refresh(force=True))
        self.fallback_timer.start(FALLBACK_SCAN_MS)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        event.accept()

    def refresh(self, force: bool = False) -> None:
        try:
            snapshot = self.reader.read(force=force)
        except (OSError, PermissionError) as error:
            self.live.setText("● 读取失败")
            self.status.setText(f"无法读取 Codex 日志：{error}")
            return
        if snapshot is None:
            self.live.setText("● 无数据")
            self.status.setText(f"未找到会话日志：{self.reader.sessions_dir}")
            return

        self.live.setText("● 实时")
        limits = snapshot.rate_limits
        self.primary_rate.set_data(limits.get("primary"))
        self.secondary_rate.set_data(limits.get("secondary"))
        self.status.setText(f"会话：{snapshot.session_path.stem[-12:]}")
        self.updated.setText(f"最后刷新 {datetime.now():%H:%M:%S}")


STYLE = """
QWidget#root { background: #0b1020; color: #e8edf7; border: 1px solid #263652; border-radius: 11px; }
QLabel { font-family: "Microsoft YaHei UI"; }
QLabel#title { font-size: 11px; font-weight: 800; color: #9aa9c2; letter-spacing: 1px; }
QLabel#subtitle, QLabel#status { font-size: 12px; color: #8490a8; }
QLabel#live { color: #45d394; font-size: 10px; font-weight: 600; padding-right: 2px; }
QPushButton { background: #1c2741; color: #cdd7ea; border: 1px solid #2e3a56; border-radius: 8px; padding: 8px 14px; font-weight: 600; }
QPushButton:hover { background: #273653; border-color: #536586; }
QPushButton#closeButton { background: transparent; border: 0; padding: 0; color: #73829d; font-size: 17px; }
QPushButton#closeButton:hover { color: #ffffff; background: #a94452; border-radius: 5px; }
QFrame#usageCard, QFrame#usageCardAccent, QFrame#rateCard, QFrame#contextBox { background: #121a2c; border: 1px solid #202c45; border-radius: 9px; }
QFrame#usageCardAccent { background: #17223a; border-color: #365994; }
QLabel#cardTitle { color: #91a0ba; font-size: 12px; font-weight: 600; }
QLabel#cardValue { color: #f4f7fd; font-size: 25px; font-weight: 700; }
QLabel#cardDetail { color: #73829d; font-size: 11px; }
QLabel#rateTitle { color: #cbd5e7; font-size: 11px; font-weight: 600; }
QLabel#ratePercent, QLabel#contextValue { color: #70a7ff; font-size: 14px; font-weight: 700; }
QLabel#tokenLine { color: #91a0ba; font-size: 10px; }
QProgressBar { min-height: 8px; max-height: 8px; border: 0; border-radius: 4px; background: #263149; }
QProgressBar::chunk { border-radius: 4px; background: #4d8dff; }
"""


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
