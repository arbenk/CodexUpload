import json
import hashlib
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QSlider,
    QScrollArea,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)


@dataclass
class CharBox:
    id: int
    x: int
    y: int
    width: int
    height: int
    status: str = "auto"


def read_image(path: str) -> np.ndarray:
    data = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("无法读取图片")
    return image


def write_image(path: Path, image: np.ndarray) -> None:
    suffix = path.suffix.lower() or ".png"
    ok, encoded = cv2.imencode(suffix, image)
    if not ok:
        raise ValueError(f"无法编码图片：{path.name}")
    encoded.tofile(str(path))


OCR_COMPONENT_ID = "rapidocr-windows-x64-py314-v1"
OCR_DLL_HANDLES: list[object] = []


def app_data_directory() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root) / "AutoWord_Cut_app"
    return Path.home() / ".AutoWord_Cut_app"


def bundled_resource_path(name: str) -> Path:
    if getattr(sys, "frozen", False):
        external = Path(sys.executable).resolve().parent / name
        if external.exists():
            return external
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidate = Path(frozen_root) / name
        if candidate.exists():
            return candidate
    return Path(__file__).resolve().parent / name


def activate_ocr_component(component_dir: Path) -> None:
    """把下载的 OCR Python 包和原生 DLL 加入当前冻结程序。"""
    component_text = str(component_dir)
    if component_text not in sys.path:
        sys.path.insert(0, component_text)
    if hasattr(os, "add_dll_directory"):
        for directory in [component_dir, *[path for path in component_dir.rglob("*") if path.is_dir()]]:
            if any(directory.glob("*.dll")):
                try:
                    OCR_DLL_HANDLES.append(os.add_dll_directory(str(directory)))
                except OSError:
                    pass


def _safe_extract_zip(archive: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            target = (destination / member.filename).resolve()
            try:
                target.relative_to(destination_resolved)
            except ValueError as exc:
                raise ValueError("OCR 组件压缩包包含不安全路径") from exc
        package.extractall(destination)


def ensure_ocr_component(parent: QWidget) -> bool:
    """确保可选 OCR 组件可用；缺失时下载、校验并安装。"""
    component_dir = app_data_directory() / "ocr" / OCR_COMPONENT_ID
    marker = component_dir / "component.json"
    if marker.exists():
        activate_ocr_component(component_dir)
        return True

    manifest_path = bundled_resource_path("ocr_component_manifest.json")
    if not manifest_path.exists():
        QMessageBox.critical(parent, "缺少 OCR 清单", "程序中没有 OCR 组件下载清单。")
        return False
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    url = str(manifest.get("download_url", "")).strip()
    expected_hash = str(manifest.get("sha256", "")).strip().lower()
    size_mb = float(manifest.get("size_bytes", 0)) / (1024 * 1024)
    if not url:
        answer = QMessageBox.question(
            parent,
            "OCR 组件尚未联网发布",
            "当前安装包没有配置在线下载地址。\n\n"
            "如果已经取得 OCR 组件 ZIP，可以现在从本地安装。",
            QMessageBox.Open | QMessageBox.Cancel,
            QMessageBox.Open,
        )
        if answer != QMessageBox.Open:
            return False
        local_path, _ = QFileDialog.getOpenFileName(parent, "选择 OCR 组件", "", "OCR 组件 (*.zip)")
        if not local_path:
            return False
        archive_path = Path(local_path)
        remove_archive = False
    else:
        size_text = f"，约 {size_mb:.1f} MB" if size_mb else ""
        answer = QMessageBox.question(
            parent,
            "下载本地 OCR 组件",
            f"这是第一次使用 OCR，需要下载可选组件{size_text}。\n"
            "组件只下载一次，保存到当前用户的本地数据目录。是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return False
        download_dir = app_data_directory() / "downloads"
        download_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix="ocr-component-", suffix=".zip", dir=download_dir)
        os.close(fd)
        archive_path = Path(temporary_name)
        remove_archive = True
        progress = QProgressDialog("正在下载 OCR 组件…", "取消", 0, 1000, parent)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "AutoWord-Cut-app/1.0"})
            with urllib.request.urlopen(request, timeout=30) as response, archive_path.open("wb") as output:
                total = int(response.headers.get("Content-Length") or manifest.get("size_bytes") or 0)
                received = 0
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                    received += len(block)
                    if total:
                        progress.setValue(min(999, round(received * 1000 / total)))
                    progress.setLabelText(f"正在下载 OCR 组件… {received / 1024 / 1024:.1f} MB")
                    QApplication.processEvents()
                    if progress.wasCanceled():
                        raise InterruptedError("下载已取消")
            progress.setValue(1000)
        except InterruptedError:
            archive_path.unlink(missing_ok=True)
            return False
        except Exception as exc:
            archive_path.unlink(missing_ok=True)
            QMessageBox.critical(parent, "OCR 组件下载失败", str(exc))
            return False

    try:
        if expected_hash:
            digest = hashlib.sha256()
            with archive_path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest().lower() != expected_hash:
                raise ValueError("OCR 组件校验失败，文件可能不完整或已被替换。")
        install_root = component_dir.parent
        install_root.mkdir(parents=True, exist_ok=True)
        staging = install_root / f".{OCR_COMPONENT_ID}.installing"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        _safe_extract_zip(archive_path, staging)
        if not (staging / "rapidocr_onnxruntime").is_dir() or not (staging / "onnxruntime").is_dir():
            raise ValueError("所选压缩包不是有效的 OCR 组件。")
        (staging / "component.json").write_text(
            json.dumps({"id": OCR_COMPONENT_ID, "installed": True}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if component_dir.exists():
            shutil.rmtree(component_dir)
        staging.replace(component_dir)
        activate_ocr_component(component_dir)
        QMessageBox.information(parent, "OCR 组件安装完成", "OCR 组件已安装，接下来将继续识别。")
        return True
    except Exception as exc:
        QMessageBox.critical(parent, "OCR 组件安装失败", str(exc))
        return False
    finally:
        if remove_archive:
            archive_path.unlink(missing_ok=True)


def safe_character_filename(character: str, fallback: str) -> str:
    """生成可在 Windows 中保存的单字文件名。"""
    invalid = '<>:"/\\|?*'
    name = "".join("_" if char in invalid or ord(char) < 32 else char for char in character).rstrip(" .")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if not name or name.upper() in reserved:
        name = fallback
    return name


def character_filename_stems(labels: list[str], box_ids: list[int]) -> list[str]:
    """按出现次数生成与最终导出一致的文件名（不含扩展名）。"""
    counts: dict[str, int] = {}
    stems: list[str] = []
    for label, box_id in zip(labels, box_ids):
        base = safe_character_filename(label, f"字符{box_id:04d}")
        counts[base] = counts.get(base, 0) + 1
        occurrence = counts[base]
        stems.append(base if occurrence == 1 else f"{base}{occurrence:02d}")
    return stems


def runs(binary: np.ndarray, min_length: int) -> list[tuple[int, int]]:
    padded = np.pad(binary.astype(np.int8), (1, 1))
    edges = np.diff(padded)
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    return [(int(a), int(b)) for a, b in zip(starts, ends) if b - a >= min_length]


def close_1d(binary: np.ndarray, kernel_size: int) -> np.ndarray:
    kernel_size = max(3, kernel_size | 1)
    arr = binary.astype(np.uint8)[None, :]
    kernel = np.ones((1, kernel_size), np.uint8)
    return cv2.morphologyEx(arr, cv2.MORPH_CLOSE, kernel)[0].astype(bool)


def prepare_ink_mask(image: np.ndarray, max_side: int = 2200) -> tuple[np.ndarray, float]:
    height, width = image.shape[:2]
    scale = min(1.0, max_side / max(width, height))
    if scale < 1:
        small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        small = image.copy()

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    sigma = max(9, round(max(gray.shape) * 0.012))
    background = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma, sigmaY=sigma)
    enhanced = cv2.subtract(background, gray)
    enhanced = cv2.normalize(enhanced, None, 0, 255, cv2.NORM_MINMAX)
    _, mask = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    dot = max(2, round(max(gray.shape) * 0.0012))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((dot, dot), np.uint8))
    return mask, scale


def detect_characters(
    image: np.ndarray,
    ignore_left_percent: int = 15,
    padding_percent: int = 12,
    merge_strength: int = 50,
    whole_page: bool = False,
) -> list[CharBox]:
    mask, scale = prepare_ink_mask(image)
    h, w = mask.shape
    x_start = 0 if whole_page else round(w * ignore_left_percent / 100)
    work = mask.copy()
    work[:, :x_start] = 0

    col_projection = np.count_nonzero(work, axis=0).astype(np.float32)
    smooth_w = max(5, round(w * 0.009)) | 1
    col_projection = np.convolve(col_projection, np.ones(smooth_w) / smooth_w, mode="same")
    positive = col_projection[col_projection > 0]
    if positive.size == 0:
        return []
    col_threshold = max(h * 0.008, float(np.percentile(positive, 22)))
    col_active = col_projection > col_threshold
    col_gap = round(w * (0.010 + merge_strength / 5000))
    col_active = close_1d(col_active, col_gap)
    columns = runs(col_active, max(8, round(w * 0.025)))

    raw_boxes: list[tuple[int, int, int, int]] = []
    for x1, x2 in columns:
        column = work[:, x1:x2]
        row_projection = np.count_nonzero(column, axis=1).astype(np.float32)
        smooth_h = max(5, round(h * 0.006)) | 1
        row_projection = np.convolve(row_projection, np.ones(smooth_h) / smooth_h, mode="same")
        row_positive = row_projection[row_projection > 0]
        if row_positive.size == 0:
            continue
        row_threshold = max((x2 - x1) * 0.018, float(np.percentile(row_positive, 18)))
        row_active = row_projection > row_threshold
        row_gap = round(h * (0.012 + merge_strength / 3500))
        row_active = close_1d(row_active, row_gap)
        rows = runs(row_active, max(8, round(h * 0.025)))
        for y1, y2 in rows:
            raw_boxes.append((x1, y1, x2, y2))

    if not raw_boxes:
        return []

    # 主体模式下过滤明显小于大字中位尺寸的题跋、印章与噪点。
    widths = np.array([b[2] - b[0] for b in raw_boxes])
    heights = np.array([b[3] - b[1] for b in raw_boxes])
    if not whole_page:
        median_w = float(np.median(widths))
        median_h = float(np.median(heights))
        raw_boxes = [
            b for b in raw_boxes
            if b[2] - b[0] >= median_w * 0.55 and b[3] - b[1] >= median_h * 0.55
        ]

    # 竖排阅读顺序：列从右到左，列内从上到下。
    raw_boxes.sort(key=lambda b: (-(b[0] + b[2]) / 2, (b[1] + b[3]) / 2))
    source_h, source_w = image.shape[:2]
    result: list[CharBox] = []
    for index, (x1, y1, x2, y2) in enumerate(raw_boxes, 1):
        box_w, box_h = x2 - x1, y2 - y1
        pad_x = round(box_w * padding_percent / 100)
        pad_y = round(box_h * padding_percent / 100)
        sx1 = max(0, round((x1 - pad_x) / scale))
        sy1 = max(0, round((y1 - pad_y) / scale))
        sx2 = min(source_w, round((x2 + pad_x) / scale))
        sy2 = min(source_h, round((y2 + pad_y) / scale))
        result.append(CharBox(index, sx1, sy1, sx2 - sx1, sy2 - sy1))
    return result


def detect_dense_inscription(
    image: np.ndarray,
    ignore_left_percent: int = 15,
    padding_percent: int = 12,
    merge_strength: int = 50,
) -> list[CharBox]:
    """检测黑底白字、横向密集排列的碑刻拓片。"""
    source_h, source_w = image.shape[:2]
    scale = min(1.0, 4284 / max(source_w, source_h))
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else image.copy()
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    # 先闭合黑底中的白字孔洞，再取最大的黑色正文面板。
    dark = (gray < 110).astype(np.uint8) * 255
    close_size = max(15, round(max(gray.shape) * 0.0105)) | 1
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((close_size, close_size), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(dark, 8)
    if count <= 1:
        return []
    panel_index = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    px, py, pw, ph, _ = (int(v) for v in stats[panel_index])
    if pw < gray.shape[1] * 0.35 or ph < gray.shape[0] * 0.25:
        return []

    panel = gray[py:py + ph, px:px + pw]
    sigma = max(9, round(max(panel.shape) * 0.003))
    background = cv2.GaussianBlur(panel, (0, 0), sigmaX=sigma, sigmaY=sigma)
    light = cv2.subtract(panel, background)
    _, ink = cv2.threshold(light, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

    # 左侧常为题跋；“忽略左侧”可调节正文起点。
    mx1 = round(pw * ignore_left_percent / 100)
    mx2 = round(pw * 0.96)
    main = ink[:, mx1:mx2]
    projection = np.count_nonzero(main, axis=1).astype(np.float32)
    smooth = max(5, round(ph * 0.0048)) | 1
    projection = np.convolve(projection, np.ones(smooth) / smooth, mode="same")
    positive = projection[projection > 0]
    if not positive.size:
        return []
    active = projection > max(main.shape[1] * 0.004, float(np.percentile(positive, 30)))
    active = close_1d(active, max(5, round(ph * 0.0075)))
    rows = runs(active, max(5, round(ph * 0.0085)))
    rows = [(a, b) for a, b in rows if a >= ph * 0.04 and b <= ph * 0.96]

    raw_boxes: list[tuple[int, int, int, int]] = []
    for y1, y2 in rows:
        row = main[y1:y2]
        row_height = y2 - y1
        kx = max(3, int(row_height * (0.08 + merge_strength / 850)))
        ky = max(2, int(row_height * (0.06 + merge_strength / 1250)))
        joined = cv2.dilate(row, np.ones((ky, kx), np.uint8))
        contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        row_boxes = []
        for contour in contours:
            bx, by, bw, bh = cv2.boundingRect(contour)
            if bw >= row_height * 0.18 and bh >= row_height * 0.22:
                row_boxes.append((bx, y1 + by, bx + bw, y1 + by + bh))
        raw_boxes.extend(sorted(row_boxes, key=lambda b: (b[0] + b[2]) / 2))

    result: list[CharBox] = []
    for index, (x1, y1, x2, y2) in enumerate(raw_boxes, 1):
        box_w, box_h = x2 - x1, y2 - y1
        pad_x = round(box_w * padding_percent / 100)
        pad_y = round(box_h * padding_percent / 100)
        sx1 = max(0, round((px + mx1 + x1 - pad_x) / scale))
        sy1 = max(0, round((py + y1 - pad_y) / scale))
        sx2 = min(source_w, round((px + mx1 + x2 + pad_x) / scale))
        sy2 = min(source_h, round((py + y2 + pad_y) / scale))
        result.append(CharBox(index, sx1, sy1, sx2 - sx1, sy2 - sy1))
    return result


def detect_from_standard_box(image: np.ndarray, seed: CharBox, target_count: int) -> list[CharBox]:
    """用标准框给出单字尺度，以墨迹密度局部峰值定位不规则排列的文字。"""
    source_h, source_w = image.shape[:2]
    scale = min(1.0, 1200.0 / source_h)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if scale < 1.0:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    seed_w = max(12, round(seed.width * scale))
    seed_h = max(12, round(seed.height * scale))
    sigma = max(7, round(max(seed_w, seed_h) * 0.30))
    background = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma, sigmaY=sigma)
    ink_strength = cv2.subtract(background, gray)
    _, ink = cv2.threshold(ink_strength, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    response = cv2.boxFilter(ink.astype(np.float32), -1, (seed_w, seed_h), normalize=False)
    minimum_score = seed_w * seed_h * 0.015

    # 先取得充足的局部峰值，再按标准框尺度做非极大值抑制；位置不依赖规则网格。
    kernel_w = max(3, round(seed_w * 0.32)) | 1
    kernel_h = max(3, round(seed_h * 0.32)) | 1
    local_max = cv2.dilate(response, np.ones((kernel_h, kernel_w), np.uint8))
    peaks = ((response >= local_max - 0.01) & (response >= minimum_score)).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(peaks, 8)
    candidates: list[tuple[float, int, int]] = []
    for index in range(1, count):
        x, y, width, height, _ = (int(value) for value in stats[index])
        patch = response[y:y + height, x:x + width]
        py, px = np.unravel_index(int(np.argmax(patch)), patch.shape)
        center_x, center_y = x + int(px), y + int(py)
        left = round(center_x / scale - seed.width / 2)
        top = round(center_y / scale - seed.height / 2)
        if left >= 0 and top >= 0 and left + seed.width <= source_w and top + seed.height <= source_h:
            candidates.append((float(patch[py, px]), center_x, center_y))
    candidates.sort(reverse=True)

    selected_peaks: list[tuple[float, int, int]] = []
    for factor in (0.85, 0.80, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50, 0.45):
        separation_x = max(1.0, seed_w * factor)
        separation_y = max(1.0, seed_h * factor)
        buckets: dict[tuple[int, int], list[tuple[float, int, int]]] = {}
        current: list[tuple[float, int, int]] = []
        for candidate in candidates:
            _, center_x, center_y = candidate
            bucket_x = int(center_x / separation_x)
            bucket_y = int(center_y / separation_y)
            too_close = False
            for nearby_x in range(bucket_x - 1, bucket_x + 2):
                for nearby_y in range(bucket_y - 1, bucket_y + 2):
                    for _, chosen_x, chosen_y in buckets.get((nearby_x, nearby_y), []):
                        if abs(center_x - chosen_x) < separation_x and abs(center_y - chosen_y) < separation_y:
                            too_close = True
                            break
                    if too_close:
                        break
                if too_close:
                    break
            if too_close:
                continue
            current.append(candidate)
            buckets.setdefault((bucket_x, bucket_y), []).append(candidate)
            if len(current) >= target_count:
                break
        selected_peaks = current
        if len(selected_peaks) >= target_count:
            break

    if not selected_peaks:
        return []
    raw: list[CharBox] = []
    for _, center_x, center_y in selected_peaks[:target_count]:
        left = round(center_x / scale - seed.width / 2)
        top = round(center_y / scale - seed.height / 2)
        if left >= 0 and top >= 0 and left + seed.width <= source_w and top + seed.height <= source_h:
            raw.append(CharBox(0, left, top, seed.width, seed.height, "guided"))

    # 先按近似竖排顺序排列；进入编辑器后仍可使用“重新编号”进一步规整。
    raw.sort(key=lambda box: (-(box.x + box.width / 2), box.y + box.height / 2))
    for index, box in enumerate(raw, 1):
        box.id = index
    return raw


def _projection_peaks(values: np.ndarray, expected_spacing: float, maximum: int, minimum_ratio: float = 0.01) -> list[tuple[float, int]]:
    """从一维墨迹投影中提取允许间距变化的局部峰值。"""
    if values.size == 0 or maximum <= 0:
        return []
    values = values.astype(np.float32)
    sigma = max(1.0, expected_spacing * 0.08)
    smooth = cv2.GaussianBlur(values.reshape(-1, 1), (1, 0), sigmaX=0, sigmaY=sigma).ravel()
    kernel = max(3, round(expected_spacing * 0.42)) | 1
    local = cv2.dilate(smooth.reshape(-1, 1), np.ones((kernel, 1), np.uint8)).ravel()
    threshold = max(float(smooth.max()) * minimum_ratio, 0.5)
    peak_mask = ((smooth >= local - 1e-4) & (smooth >= threshold)).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(peak_mask.reshape(-1, 1), 8)
    peaks: list[tuple[float, int]] = []
    for index in range(1, count):
        start = int(stats[index, cv2.CC_STAT_TOP])
        length = int(stats[index, cv2.CC_STAT_HEIGHT])
        segment = smooth[start:start + length]
        offset = int(np.argmax(segment))
        peaks.append((float(segment[offset]), start + offset))
    peaks.sort(reverse=True)
    return peaks[:maximum]


def remove_box_overlaps(boxes: list[CharBox]) -> list[CharBox]:
    """沿相邻框中心中线收缩交叠边，使所有字符框最多接触但不重叠。"""
    if len(boxes) < 2:
        return boxes
    bounds = [[box.x, box.y, box.x + box.width, box.y + box.height] for box in boxes]
    centers = [((left + right) / 2, (top + bottom) / 2) for left, top, right, bottom in bounds]
    sizes = [(max(1, box.width), max(1, box.height)) for box in boxes]
    for first in range(len(boxes)):
        cx1, cy1 = centers[first]
        width1, height1 = sizes[first]
        for second in range(first + 1, len(boxes)):
            left1, top1, right1, bottom1 = bounds[first]
            left2, top2, right2, bottom2 = bounds[second]
            overlap_x = min(right1, right2) - max(left1, left2)
            overlap_y = min(bottom1, bottom2) - max(top1, top2)
            if overlap_x <= 0 or overlap_y <= 0:
                continue
            cx2, cy2 = centers[second]
            width2, height2 = sizes[second]
            horizontal_distance = abs(cx1 - cx2) / max(1.0, (width1 + width2) / 2)
            vertical_distance = abs(cy1 - cy2) / max(1.0, (height1 + height2) / 2)
            if horizontal_distance >= vertical_distance:
                boundary = round((cx1 + cx2) / 2)
                if cx1 <= cx2:
                    bounds[first][2] = max(bounds[first][0] + 1, min(bounds[first][2], boundary))
                    bounds[second][0] = min(bounds[second][2] - 1, max(bounds[second][0], boundary))
                else:
                    bounds[second][2] = max(bounds[second][0] + 1, min(bounds[second][2], boundary))
                    bounds[first][0] = min(bounds[first][2] - 1, max(bounds[first][0], boundary))
            else:
                boundary = round((cy1 + cy2) / 2)
                if cy1 <= cy2:
                    bounds[first][3] = max(bounds[first][1] + 1, min(bounds[first][3], boundary))
                    bounds[second][1] = min(bounds[second][3] - 1, max(bounds[second][1], boundary))
                else:
                    bounds[second][3] = max(bounds[second][1] + 1, min(bounds[second][3], boundary))
                    bounds[first][1] = min(bounds[first][3] - 1, max(bounds[first][1], boundary))
    result = []
    for box, (left, top, right, bottom) in zip(boxes, bounds):
        result.append(CharBox(box.id, left, top, max(1, right - left), max(1, bottom - top), box.status))
    return result


def detect_dynamic_text_region(
    image: np.ndarray,
    region: CharBox,
    target_count: int,
    padding_percent: int = 8,
) -> list[CharBox]:
    """在指定文字区域中先分竖列，再逐列动态定位和裁切字符。"""
    source_h, source_w = image.shape[:2]
    rx1 = max(0, region.x)
    ry1 = max(0, region.y)
    rx2 = min(source_w, region.x + region.width)
    ry2 = min(source_h, region.y + region.height)
    if rx2 - rx1 < 50 or ry2 - ry1 < 50 or target_count < 1:
        return []
    crop = image[ry1:ry2, rx1:rx2]
    crop_h, crop_w = crop.shape[:2]
    scale = min(1.0, 1400.0 / crop_h)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if scale < 1.0:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    work_h, work_w = gray.shape

    # 以目标字数和区域纵横比估算列、行数量，只作为局部尺度先验。
    estimated_columns = max(1, round(np.sqrt(target_count * work_w / max(work_h, 1))))
    estimated_rows = max(1.0, target_count / estimated_columns)
    column_pitch = work_w / estimated_columns
    row_pitch = work_h / estimated_rows

    sigma = max(7, round(max(column_pitch, row_pitch) * 0.32))
    background = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma, sigmaY=sigma)
    ink_strength = cv2.subtract(background, gray)
    _, ink = cv2.threshold(ink_strength, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    x_projection = np.count_nonzero(ink, axis=0).astype(np.float32)
    column_candidates = _projection_peaks(
        x_projection,
        column_pitch,
        maximum=max(estimated_columns + 8, round(estimated_columns * 1.15)),
        minimum_ratio=0.04,
    )
    if not column_candidates:
        return []
    column_candidates = sorted(column_candidates[:estimated_columns], key=lambda item: item[1], reverse=True)

    per_column_max = max(2, int(np.ceil(estimated_rows * 1.30)))
    all_candidates: list[tuple[float, int, int, int]] = []
    half_column = max(2, round(column_pitch * 0.46))
    for column_index, (column_score, center_x) in enumerate(column_candidates):
        x1 = max(0, center_x - half_column)
        x2 = min(work_w, center_x + half_column + 1)
        y_projection = np.count_nonzero(ink[:, x1:x2], axis=1).astype(np.float32)
        row_candidates = _projection_peaks(
            y_projection,
            row_pitch,
            maximum=per_column_max,
            minimum_ratio=0.025,
        )
        normalization = max(float(y_projection.max()), 1.0)
        for row_score, center_y in row_candidates:
            score = row_score / normalization + 0.08 * column_score / max(float(x_projection.max()), 1.0)
            all_candidates.append((score, column_index, center_x, center_y))

    # 每列先保留一个保底数量，再按墨迹置信度补足目标总字数。
    minimum_per_column = max(1, int(np.floor(estimated_rows * 0.65)))
    by_column: dict[int, list[tuple[float, int, int, int]]] = {}
    for candidate in all_candidates:
        by_column.setdefault(candidate[1], []).append(candidate)
    selected: list[tuple[float, int, int, int]] = []
    remaining: list[tuple[float, int, int, int]] = []
    for candidates in by_column.values():
        candidates.sort(reverse=True)
        selected.extend(candidates[:minimum_per_column])
        remaining.extend(candidates[minimum_per_column:])
    if len(selected) > target_count:
        selected.sort(reverse=True)
        selected = selected[:target_count]
    else:
        remaining.sort(reverse=True)
        selected.extend(remaining[:target_count - len(selected)])

    selected_by_column: dict[int, list[tuple[float, int, int, int]]] = {}
    for candidate in selected:
        selected_by_column.setdefault(candidate[1], []).append(candidate)

    result: list[CharBox] = []
    for column_index in sorted(selected_by_column):
        candidates = sorted(selected_by_column[column_index], key=lambda item: item[3])
        center_x = candidates[0][2]
        for _, _, _, center_y in candidates:
            # 每个字使用自己的局部窗口，避免稀疏列中相邻峰值缺失时框跨越多个字。
            top = max(0, round(center_y - row_pitch * 0.56))
            bottom = min(work_h, round(center_y + row_pitch * 0.56))
            left = max(0, round(center_x - column_pitch * 0.48))
            right = min(work_w, round(center_x + column_pitch * 0.48))
            local = ink[top:bottom, left:right]
            ys, xs = np.nonzero(local)
            if xs.size:
                ink_left = left + int(xs.min())
                ink_right = left + int(xs.max()) + 1
                ink_top = top + int(ys.min())
                ink_bottom = top + int(ys.max()) + 1
            else:
                ink_left, ink_right, ink_top, ink_bottom = left, right, top, bottom
            width = max(1, ink_right - ink_left)
            height = max(1, ink_bottom - ink_top)
            pad_x = round(width * padding_percent / 100)
            pad_y = round(height * padding_percent / 100)
            sx1 = max(rx1, rx1 + round((ink_left - pad_x) / scale))
            sy1 = max(ry1, ry1 + round((ink_top - pad_y) / scale))
            sx2 = min(rx2, rx1 + round((ink_right + pad_x) / scale))
            sy2 = min(ry2, ry1 + round((ink_bottom + pad_y) / scale))
            # 局部墨迹可能碰到相邻字；将最终框限制在约一个局部字距内，避免跨字。
            max_box_width = max(1, round(column_pitch / scale * 1.08))
            max_box_height = max(1, round(row_pitch / scale * 1.08))
            if sx2 - sx1 > max_box_width:
                center = (sx1 + sx2) / 2
                sx1 = max(rx1, round(center - max_box_width / 2))
                sx2 = min(rx2, sx1 + max_box_width)
            if sy2 - sy1 > max_box_height:
                center = (sy1 + sy2) / 2
                sy1 = max(ry1, round(center - max_box_height / 2))
                sy2 = min(ry2, sy1 + max_box_height)
            result.append(CharBox(0, sx1, sy1, max(1, sx2 - sx1), max(1, sy2 - sy1), "dynamic"))

    result.sort(key=lambda box: (-(box.x + box.width / 2), box.y + box.height / 2))
    for index, box in enumerate(result, 1):
        box.id = index
    return remove_box_overlaps(result)


def draw_preview(image: np.ndarray, boxes: list[CharBox]) -> np.ndarray:
    preview = image.copy()
    thickness = max(1, round(max(image.shape[:2]) / 3600))
    font_scale = max(0.38, max(image.shape[:2]) / 4800)
    for box in boxes:
        p1 = (box.x, box.y)
        p2 = (box.x + box.width, box.y + box.height)
        cv2.rectangle(preview, p1, p2, (20, 50, 235), thickness)
        label = f"{box.id:03d}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
        cv2.rectangle(preview, (box.x, max(0, box.y - th - 7)), (box.x + tw + 6, box.y), (20, 50, 235), -1)
        cv2.putText(preview, label, (box.x + 3, box.y - 4), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)
    return preview


def binary_background_correct(crop: np.ndarray, threshold_offset: int = 0) -> np.ndarray:
    """校正不均匀纸色，并输出黑色墨迹、白色背景的二值图。"""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    sigma = max(7, round(max(gray.shape) * 0.055))
    background = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma, sigmaY=sigma)
    ink_strength = cv2.subtract(background, gray)
    otsu_value, _ = cv2.threshold(ink_strength, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    threshold_value = int(np.clip(otsu_value + threshold_offset, 1, 254))
    ink_mask = ink_strength >= threshold_value
    result = np.full(gray.shape, 255, dtype=np.uint8)
    result[ink_mask] = 0
    return result


def binary_light_on_dark(crop: np.ndarray, threshold_offset: int = 0) -> np.ndarray:
    """把黑底白字归一化为白底黑字，便于统一训练。"""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    sigma = max(7, round(max(gray.shape) * 0.055))
    background = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma, sigmaY=sigma)
    stroke_strength = cv2.subtract(gray, background)
    otsu_value, _ = cv2.threshold(stroke_strength, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    threshold_value = int(np.clip(otsu_value + threshold_offset, 1, 254))
    result = np.full(gray.shape, 255, np.uint8)
    result[stroke_strength >= threshold_value] = 0
    return result


def tight_ink_bounds(crop: np.ndarray, noise_threshold: float = 0.0) -> tuple[int, int, int, int] | None:
    """返回裁图内有效墨迹的紧边界 (x1, y1, x2, y2)，兼容明底黑字和暗底亮字。"""
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    border = np.concatenate((gray[0], gray[-1], gray[:, 0], gray[:, -1]))
    dark_background = float(np.median(border)) < 128
    if dark_background and crop.ndim == 3:
        binary = binary_light_on_dark(crop)
    elif crop.ndim == 3:
        binary = binary_background_correct(crop)
    else:
        if dark_background:
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            binary = 255 - binary
        else:
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    ink = (binary == 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    if count <= 1:
        return None
    noise_threshold = max(0.0, float(noise_threshold))
    minimum_width = gray.shape[1] * noise_threshold / 100.0
    minimum_height = gray.shape[0] * noise_threshold / 100.0
    # 面积使用较细的比例刻度：5.00% 对应字框总面积的 0.05%。
    minimum_area = max(1, round(gray.shape[0] * gray.shape[1] * noise_threshold / 10000.0))
    cleaned = np.zeros_like(ink)
    for index in range(1, count):
        component_width = int(stats[index, cv2.CC_STAT_WIDTH])
        component_height = int(stats[index, cv2.CC_STAT_HEIGHT])
        component_area = int(stats[index, cv2.CC_STAT_AREA])
        if (
            component_width >= minimum_width
            and component_height >= minimum_height
            and component_area >= minimum_area
        ):
            cleaned[labels == index] = 1
    ys, xs = np.nonzero(cleaned)
    if not xs.size:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def tight_ink_bounds_with_expansion(
    image: np.ndarray,
    box: CharBox,
    noise_threshold: float = 0.0,
) -> tuple[int, int, int, int] | None:
    """收紧字框；与原框边缘连通的墨迹会向外追踪到完整边界。返回绝对坐标。"""
    image_h, image_w = image.shape[:2]
    original_left = max(0, box.x)
    original_top = max(0, box.y)
    original_right = min(image_w, box.x + box.width)
    original_bottom = min(image_h, box.y + box.height)
    if original_right <= original_left or original_bottom <= original_top:
        return None

    step_x = max(8, box.width // 2)
    step_y = max(8, box.height // 2)
    roi_left = max(0, original_left - step_x)
    roi_top = max(0, original_top - step_y)
    roi_right = min(image_w, original_right + step_x)
    roi_bottom = min(image_h, original_bottom + step_y)
    noise_threshold = max(0.0, float(noise_threshold))

    for _ in range(32):
        crop = image[roi_top:roi_bottom, roi_left:roi_right]
        if crop.size == 0:
            return None
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        border = np.concatenate((gray[0], gray[-1], gray[:, 0], gray[:, -1]))
        dark_background = float(np.median(border)) < 128
        if crop.ndim == 3:
            binary = binary_light_on_dark(crop) if dark_background else binary_background_correct(crop)
        else:
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            if dark_background:
                binary = 255 - binary
        # 背景校正擅长保留浅墨边缘；普通 Otsu 同时补足被框切断的粗实笔画内部，
        # 两者合并后才能沿同一连通笔画可靠地向框外追踪。
        _, global_binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        global_ink = global_binary == (255 if dark_background else 0)
        ink = ((binary == 0) | global_ink).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
        if count <= 1:
            return None

        local_left = original_left - roi_left
        local_top = original_top - roi_top
        local_right = original_right - roi_left
        local_bottom = original_bottom - roi_top
        minimum_width = box.width * noise_threshold / 100.0
        minimum_height = box.height * noise_threshold / 100.0
        minimum_area = max(1, round(box.width * box.height * noise_threshold / 10000.0))
        kept_indices: list[int] = []
        grow_left = grow_top = grow_right = grow_bottom = False
        for index in range(1, count):
            x = int(stats[index, cv2.CC_STAT_LEFT])
            y = int(stats[index, cv2.CC_STAT_TOP])
            width = int(stats[index, cv2.CC_STAT_WIDTH])
            height = int(stats[index, cv2.CC_STAT_HEIGHT])
            area = int(stats[index, cv2.CC_STAT_AREA])
            if width < minimum_width or height < minimum_height or area < minimum_area:
                continue
            component_right = x + width
            component_bottom = y + height
            # 只保留真正进入原字框的连通区域；框外独立的邻字和污点不会被吸入。
            if not np.any(labels[local_top:local_bottom, local_left:local_right] == index):
                continue
            kept_indices.append(index)
            grow_left |= x == 0 and roi_left > 0
            grow_top |= y == 0 and roi_top > 0
            grow_right |= component_right == ink.shape[1] and roi_right < image_w
            grow_bottom |= component_bottom == ink.shape[0] and roi_bottom < image_h

        if not kept_indices:
            return None
        if grow_left or grow_top or grow_right or grow_bottom:
            if grow_left:
                roi_left = max(0, roi_left - step_x)
            if grow_top:
                roi_top = max(0, roi_top - step_y)
            if grow_right:
                roi_right = min(image_w, roi_right + step_x)
            if grow_bottom:
                roi_bottom = min(image_h, roi_bottom + step_y)
            continue

        kept = np.isin(labels, kept_indices)
        ys, xs = np.nonzero(kept)
        if not xs.size:
            return None
        return (
            roi_left + int(xs.min()),
            roi_top + int(ys.min()),
            roi_left + int(xs.max()) + 1,
            roi_top + int(ys.max()) + 1,
        )
    return None


def clean_binary_image(
    image: np.ndarray,
    black_strength: float = 10.0,
    white_strength: float = 10.0,
) -> np.ndarray:
    """先以黑色填补字内白孔，再以白色清除背景黑点。"""
    if image.ndim == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = np.where(image < 128, 0, 255).astype(np.uint8)
    height, width = binary.shape
    black_strength = max(0.0, float(black_strength))
    white_strength = max(0.0, float(white_strength))
    if black_strength == 0 and white_strength == 0:
        return binary
    image_area = height * width
    saturation_strength = 100.0 / 0.0015

    def area_limit(strength: float) -> int:
        if strength <= 0:
            return 0
        if strength >= saturation_strength:
            return image_area
        return max(1, round(image_area * 0.0015 * strength / 100))

    # 第一步：干净强度黑色——用黑色填补被黑色包围的小白块。
    max_white_hole_area = area_limit(black_strength)
    if max_white_hole_area:
        white = (binary == 255).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(white, 8)
        for index in range(1, count):
            x = stats[index, cv2.CC_STAT_LEFT]
            y = stats[index, cv2.CC_STAT_TOP]
            component_width = stats[index, cv2.CC_STAT_WIDTH]
            component_height = stats[index, cv2.CC_STAT_HEIGHT]
            touches_edge = x == 0 or y == 0 or x + component_width == width or y + component_height == height
            if not touches_edge and stats[index, cv2.CC_STAT_AREA] <= max_white_hole_area:
                binary[labels == index] = 0

    # 第二步：干净强度白色——用白色清除白底中的小黑块。
    max_black_speck_area = area_limit(white_strength)
    if max_black_speck_area:
        black = (binary == 0).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(black, 8)
        for index in range(1, count):
            if stats[index, cv2.CC_STAT_AREA] <= max_black_speck_area:
                binary[labels == index] = 255
    return binary


def square_crop(crop: np.ndarray, output_size: int | None = None) -> np.ndarray:
    """居中补边为正方形；可选统一尺寸，不拉伸原始纵横比。"""
    height, width = crop.shape[:2]
    side = max(height, width)
    if crop.ndim == 2:
        canvas = np.full((side, side), 255, dtype=crop.dtype)
    else:
        border = np.concatenate((crop[0], crop[-1], crop[:, 0], crop[:, -1]), axis=0)
        color = np.median(border, axis=0).astype(crop.dtype)
        canvas = np.empty((side, side, crop.shape[2]), dtype=crop.dtype)
        canvas[:] = color
    x = (side - width) // 2
    y = (side - height) // 2
    canvas[y:y + height, x:x + width] = crop
    if output_size and output_size != side:
        interpolation = cv2.INTER_AREA if output_size < side else cv2.INTER_CUBIC
        canvas = cv2.resize(canvas, (output_size, output_size), interpolation=interpolation)
        if crop.ndim == 2:
            _, canvas = cv2.threshold(canvas, 127, 255, cv2.THRESH_BINARY)
    return canvas


class EditableBoxItem(QGraphicsRectItem):
    HANDLE = 12.0
    MIN_SIZE = 24.0

    def __init__(self, box: CharBox):
        super().__init__(0, 0, box.width, box.height)
        self.box_id = box.id
        self.setPos(box.x, box.y)
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setZValue(10)
        self._resize_edges: set[str] = set()
        self._start_scene_pos = QPointF()
        self._start_rect = QRectF()
        self._group_start_rects: list[tuple[EditableBoxItem, QRectF]] = []
        self.show_number = True
        self.show_name = False
        self.name_text: str | None = None
        self.name_edit_callback: Callable[[int], None] | None = None
        self.begin_change_callback: Callable[[], None] | None = None
        self.end_change_callback: Callable[[], None] | None = None

    def _label_rect(self) -> QRectF:
        scale = max(self.scene().views()[0].transform().m11(), 0.05) if self.scene() and self.scene().views() else 1.0
        label_parts = []
        if self.show_number:
            label_parts.append(f"{self.box_id:03d}")
        if self.show_name:
            label_parts.append(self.name_text or "?")
        text = "  ".join(label_parts)
        label_width = max(34, 13 * len(text) + 8) / scale
        return QRectF(0, 0, label_width, 16 / scale)

    def _scene_rect(self) -> QRectF:
        """返回不包含画笔外沿的精确场景坐标框。"""
        top_left = self.mapToScene(self.rect().topLeft())
        bottom_right = self.mapToScene(self.rect().bottomRight())
        return QRectF(top_left, bottom_right).normalized()

    def scene_box(self) -> CharBox:
        rect = self._scene_rect()
        return CharBox(
            self.box_id,
            round(rect.x()),
            round(rect.y()),
            round(rect.width()),
            round(rect.height()),
            "manual",
        )

    def _edges_at(self, point: QPointF) -> set[str]:
        rect = self.rect()
        margin = self.HANDLE / max(self.scene().views()[0].transform().m11(), 0.05) if self.scene() and self.scene().views() else self.HANDLE
        edges: set[str] = set()
        if abs(point.x() - rect.left()) <= margin:
            edges.add("left")
        if abs(point.x() - rect.right()) <= margin:
            edges.add("right")
        if abs(point.y() - rect.top()) <= margin:
            edges.add("top")
        if abs(point.y() - rect.bottom()) <= margin:
            edges.add("bottom")
        return edges

    def hoverMoveEvent(self, event):
        edges = self._edges_at(event.pos())
        if edges in ({"left", "top"}, {"right", "bottom"}):
            self.setCursor(Qt.SizeFDiagCursor)
        elif edges in ({"right", "top"}, {"left", "bottom"}):
            self.setCursor(Qt.SizeBDiagCursor)
        elif "left" in edges or "right" in edges:
            self.setCursor(Qt.SizeHorCursor)
        elif "top" in edges or "bottom" in edges:
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.setCursor(Qt.SizeAllCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        if (
            event.button() == Qt.LeftButton
            and self.show_name
            and self.name_edit_callback is not None
            and self._label_rect().contains(event.pos())
        ):
            self.name_edit_callback(self.box_id)
            event.accept()
            return
        if event.button() == Qt.LeftButton and self.begin_change_callback is not None:
            self.begin_change_callback()
        self._resize_edges = self._edges_at(event.pos())
        if self._resize_edges:
            self._start_scene_pos = event.scenePos()
            self._start_rect = self._scene_rect()
            self._group_start_rects = []
            if self.isSelected() and self.scene() is not None:
                for item in self.scene().selectedItems():
                    if isinstance(item, EditableBoxItem) and item is not self:
                        self._group_start_rects.append((item, item._scene_rect()))
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self._resize_edges:
            super().mouseMoveEvent(event)
            return
        delta = event.scenePos() - self._start_scene_pos
        rect = QRectF(self._start_rect)
        if "left" in self._resize_edges:
            rect.setLeft(min(rect.right() - self.MIN_SIZE, rect.left() + delta.x()))
        if "right" in self._resize_edges:
            rect.setRight(max(rect.left() + self.MIN_SIZE, rect.right() + delta.x()))
        if "top" in self._resize_edges:
            rect.setTop(min(rect.bottom() - self.MIN_SIZE, rect.top() + delta.y()))
        if "bottom" in self._resize_edges:
            rect.setBottom(max(rect.top() + self.MIN_SIZE, rect.bottom() + delta.y()))
        self.prepareGeometryChange()
        self.setPos(rect.topLeft())
        self.setRect(0, 0, rect.width(), rect.height())
        edge_deltas = {
            "left": rect.left() - self._start_rect.left(),
            "right": rect.right() - self._start_rect.right(),
            "top": rect.top() - self._start_rect.top(),
            "bottom": rect.bottom() - self._start_rect.bottom(),
        }
        for item, start_rect in self._group_start_rects:
            target = QRectF(start_rect)
            if "left" in self._resize_edges:
                target.setLeft(min(target.right() - self.MIN_SIZE, start_rect.left() + edge_deltas["left"]))
            if "right" in self._resize_edges:
                target.setRight(max(target.left() + self.MIN_SIZE, start_rect.right() + edge_deltas["right"]))
            if "top" in self._resize_edges:
                target.setTop(min(target.bottom() - self.MIN_SIZE, start_rect.top() + edge_deltas["top"]))
            if "bottom" in self._resize_edges:
                target.setBottom(max(target.top() + self.MIN_SIZE, start_rect.bottom() + edge_deltas["bottom"]))
            item.setPos(target.topLeft())
            item.setRect(0, 0, target.width(), target.height())
        event.accept()

    def mouseReleaseEvent(self, event):
        self._resize_edges.clear()
        self._group_start_rects.clear()
        super().mouseReleaseEvent(event)
        if self.end_change_callback is not None:
            self.end_change_callback()

    def paint(self, painter: QPainter, option, widget=None):
        scale = max(self.scene().views()[0].transform().m11(), 0.05) if self.scene() and self.scene().views() else 1.0
        color = QColor(255, 213, 79, 220) if self.isSelected() else QColor(239, 63, 47, 165)
        painter.setPen(QPen(color, 1.05 / scale))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.rect())
        label_parts = []
        if self.show_number:
            label_parts.append(f"{self.box_id:03d}")
        if self.show_name:
            label_parts.append(self.name_text or "?")
        if label_parts:
            font = painter.font()
            font.setPixelSize(max(8, round(11 / scale)))
            font.setBold(True)
            painter.setFont(font)
            text = "  ".join(label_parts)
            label_rect = self._label_rect()
            painter.fillRect(label_rect, color)
            painter.setPen(QColor("white"))
            painter.drawText(label_rect, Qt.AlignCenter, text)
        if self.isSelected():
            painter.setPen(QPen(color, 1.05 / scale))
            painter.setBrush(color)
            size = 5 / scale
            for point in (self.rect().topLeft(), self.rect().topRight(), self.rect().bottomLeft(), self.rect().bottomRight()):
                painter.drawRect(QRectF(point.x() - size / 2, point.y() - size / 2, size, size))


class EditorView(QGraphicsView):
    HISTORY_LIMIT = 100

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setBackgroundBrush(QColor("#222"))
        self.image_item: QGraphicsPixmapItem | None = None
        self.numbers_visible = True
        self.names_visible = False
        self.name_edit_callback: Callable[[int], None] | None = None
        self._middle_panning = False
        self._last_pan_position = None
        self.boxes_changed_callback: Callable[[], None] | None = None
        self.history_changed_callback: Callable[[bool, bool], None] | None = None
        self._undo_stack: list[tuple[list[CharBox], set[int]]] = []
        self._redo_stack: list[tuple[list[CharBox], set[int]]] = []
        self._pending_history_state: tuple[list[CharBox], set[int]] | None = None
        self._restoring_history = False
        self._drawing_mode = False
        self._drawing_start: QPointF | None = None
        self._drawing_rect = QRectF()
        self._drawing_mouse_released = False
        self._horizontal_divisions = 1
        self._vertical_divisions = 1
        self._ctrl_held = False
        self._shift_held = False
        self._draw_outline: QGraphicsRectItem | None = None
        self._draw_dividers: list[QGraphicsLineItem] = []

    def load_image(self, image: np.ndarray) -> None:
        self.cancel_drawing()
        self.clear_history()
        self.scene().clear()
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        h, w, channels = rgb.shape
        qimage = QImage(rgb.data, w, h, channels * w, QImage.Format_RGB888).copy()
        self.image_item = self.scene().addPixmap(QPixmap.fromImage(qimage))
        self.image_item.setZValue(0)
        self.scene().setSceneRect(0, 0, w, h)
        self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)

    def set_boxes(self, boxes: list[CharBox]) -> None:
        self.cancel_drawing()
        for item in list(self.scene().items()):
            if isinstance(item, EditableBoxItem):
                self.scene().removeItem(item)
        for box in boxes:
            item = EditableBoxItem(box)
            item.show_number = self.numbers_visible
            item.show_name = self.names_visible
            item.name_edit_callback = self.name_edit_callback
            item.begin_change_callback = self.begin_box_change
            item.end_change_callback = self.end_box_change
            self.scene().addItem(item)

    @staticmethod
    def _clone_boxes(boxes: list[CharBox]) -> list[CharBox]:
        return [CharBox(**asdict(box)) for box in boxes]

    def _history_state(self) -> tuple[list[CharBox], set[int]]:
        selected = {
            item.box_id for item in self.box_items()
            if item.isSelected()
        }
        return self._clone_boxes(self.boxes()), selected

    @staticmethod
    def _box_state_key(state: tuple[list[CharBox], set[int]]) -> list[tuple]:
        return [
            (box.id, box.x, box.y, box.width, box.height, box.status)
            for box in state[0]
        ]

    def _notify_history_changed(self) -> None:
        if self.history_changed_callback is not None:
            self.history_changed_callback(bool(self._undo_stack), bool(self._redo_stack))

    def clear_history(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._pending_history_state = None
        self._notify_history_changed()

    def begin_box_change(self) -> None:
        if self._restoring_history or self._pending_history_state is not None:
            return
        self._pending_history_state = self._history_state()

    def end_box_change(self) -> None:
        if self._pending_history_state is None:
            return
        before = self._pending_history_state
        self._pending_history_state = None
        after = self._history_state()
        if self._box_state_key(before) == self._box_state_key(after):
            return
        self._undo_stack.append(before)
        del self._undo_stack[:-self.HISTORY_LIMIT]
        self._redo_stack.clear()
        self._notify_history_changed()
        if self.boxes_changed_callback is not None:
            self.boxes_changed_callback()

    def record_undo_state(self) -> None:
        """在立即执行的增删操作之前记录一次可撤销状态。"""
        if self._restoring_history:
            return
        self._pending_history_state = None
        self._undo_stack.append(self._history_state())
        del self._undo_stack[:-self.HISTORY_LIMIT]
        self._redo_stack.clear()
        self._notify_history_changed()

    def _restore_history_state(self, state: tuple[list[CharBox], set[int]]) -> None:
        boxes, selected_ids = state
        self._restoring_history = True
        try:
            self.set_boxes(self._clone_boxes(boxes))
            for item in self.box_items():
                item.setSelected(item.box_id in selected_ids)
        finally:
            self._restoring_history = False
        if self.boxes_changed_callback is not None:
            self.boxes_changed_callback()

    def undo(self) -> None:
        if not self._undo_stack:
            return
        self._pending_history_state = None
        current = self._history_state()
        previous = self._undo_stack.pop()
        self._redo_stack.append(current)
        self._restore_history_state(previous)
        self._notify_history_changed()

    def redo(self) -> None:
        if not self._redo_stack:
            return
        self._pending_history_state = None
        current = self._history_state()
        following = self._redo_stack.pop()
        self._undo_stack.append(current)
        self._restore_history_state(following)
        self._notify_history_changed()

    def set_numbers_visible(self, visible: bool) -> None:
        self.numbers_visible = visible
        for item in self.box_items():
            item.show_number = visible
            item.update()

    def set_names_visible(self, visible: bool) -> None:
        self.names_visible = visible
        for item in self.box_items():
            item.show_name = visible
            item.update()

    def box_items(self) -> list[EditableBoxItem]:
        return [item for item in self.scene().items() if isinstance(item, EditableBoxItem)]

    def boxes(self) -> list[CharBox]:
        return sorted((item.scene_box() for item in self.box_items()), key=lambda box: box.id)

    def add_box(self) -> None:
        """进入鼠标拖动画框模式。"""
        if self.image_item is None:
            return
        self.cancel_drawing()
        self._drawing_mode = True
        self.viewport().setCursor(Qt.CrossCursor)
        self.setFocus(Qt.OtherFocusReason)

    def _append_box(self, box: CharBox) -> EditableBoxItem:
        item = EditableBoxItem(box)
        item.show_number = self.numbers_visible
        item.show_name = self.names_visible
        item.name_edit_callback = self.name_edit_callback
        item.begin_change_callback = self.begin_box_change
        item.end_change_callback = self.end_box_change
        self.scene().addItem(item)
        return item

    def _clear_drawing_preview(self) -> None:
        if self._draw_outline is not None and self._draw_outline.scene() is not None:
            self.scene().removeItem(self._draw_outline)
        self._draw_outline = None
        for divider in self._draw_dividers:
            if divider.scene() is not None:
                self.scene().removeItem(divider)
        self._draw_dividers.clear()

    def cancel_drawing(self) -> None:
        self._clear_drawing_preview()
        self._drawing_mode = False
        self._drawing_start = None
        self._drawing_rect = QRectF()
        self._drawing_mouse_released = False
        self._horizontal_divisions = 1
        self._vertical_divisions = 1
        self._ctrl_held = False
        self._shift_held = False
        if hasattr(self, "viewport"):
            self.viewport().unsetCursor()

    def _update_drawing_preview(self) -> None:
        self._clear_drawing_preview()
        if self._drawing_rect.width() < 1 or self._drawing_rect.height() < 1:
            return
        pen = QPen(QColor(0, 210, 255, 230), 1.4 / max(self.transform().m11(), 0.05), Qt.DashLine)
        self._draw_outline = self.scene().addRect(self._drawing_rect, pen, Qt.NoBrush)
        self._draw_outline.setZValue(1000)
        for index in range(1, self._vertical_divisions):
            y = self._drawing_rect.top() + self._drawing_rect.height() * index / self._vertical_divisions
            line = self.scene().addLine(self._drawing_rect.left(), y, self._drawing_rect.right(), y, pen)
            line.setZValue(1001)
            self._draw_dividers.append(line)
        for index in range(1, self._horizontal_divisions):
            x = self._drawing_rect.left() + self._drawing_rect.width() * index / self._horizontal_divisions
            line = self.scene().addLine(x, self._drawing_rect.top(), x, self._drawing_rect.bottom(), pen)
            line.setZValue(1001)
            self._draw_dividers.append(line)

    def _finish_drawing(self) -> None:
        rect = self._drawing_rect.normalized().intersected(self.scene().sceneRect())
        if rect.width() < EditableBoxItem.MIN_SIZE or rect.height() < EditableBoxItem.MIN_SIZE:
            self.cancel_drawing()
            return
        next_id = max((item.box_id for item in self.box_items()), default=0) + 1
        self.record_undo_state()
        created: list[EditableBoxItem] = []
        for row in range(max(1, self._vertical_divisions)):
            top = rect.top() + rect.height() * row / self._vertical_divisions
            bottom = rect.top() + rect.height() * (row + 1) / self._vertical_divisions
            for column in range(max(1, self._horizontal_divisions)):
                left = rect.left() + rect.width() * column / self._horizontal_divisions
                right = rect.left() + rect.width() * (column + 1) / self._horizontal_divisions
                cell = QRectF(left, top, right - left, bottom - top)
                if cell.width() < 1 or cell.height() < 1:
                    continue
                created.append(self._append_box(CharBox(
                    next_id + len(created), round(cell.x()), round(cell.y()),
                    max(1, round(cell.width())), max(1, round(cell.height())), "manual",
                )))
        self.cancel_drawing()
        self.scene().clearSelection()
        if created:
            created[-1].setSelected(True)
        if self.boxes_changed_callback is not None:
            self.boxes_changed_callback()

    def delete_selected(self) -> None:
        selected = [item for item in self.scene().selectedItems() if isinstance(item, EditableBoxItem)]
        if not selected:
            return
        self.record_undo_state()
        for item in selected:
            self.scene().removeItem(item)
        if self.boxes_changed_callback is not None:
            self.boxes_changed_callback()

    def renumber(self, reading_order: str = "vertical_right_to_left") -> None:
        items = self.box_items()
        if not items:
            return
        if reading_order == "horizontal_left_to_right":
            median_height = float(np.median([item.scene_box().height for item in items]))
            remaining = sorted(items, key=lambda item: item.scene_box().y + item.scene_box().height / 2)
            rows: list[list[EditableBoxItem]] = []
            while remaining:
                first = remaining.pop(0)
                center = first.scene_box().y + first.scene_box().height / 2
                row = [first]
                rest = []
                for item in remaining:
                    item_center = item.scene_box().y + item.scene_box().height / 2
                    if abs(item_center - center) <= median_height * 0.55:
                        row.append(item)
                    else:
                        rest.append(item)
                rows.append(row)
                remaining = rest
            ordered = []
            for row in rows:
                ordered.extend(sorted(row, key=lambda item: item.scene_box().x + item.scene_box().width / 2))
            for index, item in enumerate(ordered, 1):
                item.box_id = index
                item.update()
            return
        median_width = float(np.median([item.scene_box().width for item in items]))
        remaining = sorted(items, key=lambda item: -(item.scene_box().x + item.scene_box().width / 2))
        columns: list[list[EditableBoxItem]] = []
        while remaining:
            first = remaining.pop(0)
            center = first.scene_box().x + first.scene_box().width / 2
            column = [first]
            rest = []
            for item in remaining:
                item_center = item.scene_box().x + item.scene_box().width / 2
                if abs(item_center - center) <= median_width * 0.55:
                    column.append(item)
                else:
                    rest.append(item)
            columns.append(column)
            remaining = rest
        ordered = []
        for column in columns:
            ordered.extend(sorted(column, key=lambda item: item.scene_box().y + item.scene_box().height / 2))
        for index, item in enumerate(ordered, 1):
            item.box_id = index
            item.update()

    def wheelEvent(self, event):
        if self._drawing_mode and self._drawing_start is not None:
            modifiers = event.modifiers()
            ctrl = bool(modifiers & Qt.ControlModifier) or self._ctrl_held
            shift = bool(modifiers & Qt.ShiftModifier) or self._shift_held
            if ctrl or shift:
                change = 1 if event.angleDelta().y() > 0 else -1
                if ctrl:
                    self._horizontal_divisions = min(500, max(1, self._horizontal_divisions + change))
                if shift:
                    self._vertical_divisions = min(500, max(1, self._vertical_divisions + change))
                self._update_drawing_preview()
                event.accept()
                return
        factor = 1.18 if event.angleDelta().y() > 0 else 1 / 1.18
        new_scale = self.transform().m11() * factor
        if 0.03 <= new_scale <= 12:
            self.scale(factor, factor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._middle_panning = True
            self._last_pan_position = event.position().toPoint()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        if self._drawing_mode and event.button() == Qt.LeftButton and not self._drawing_mouse_released:
            self._drawing_start = self.mapToScene(event.position().toPoint())
            self._drawing_rect = QRectF(self._drawing_start, self._drawing_start)
            self._ctrl_held = bool(event.modifiers() & Qt.ControlModifier)
            self._shift_held = bool(event.modifiers() & Qt.ShiftModifier)
            self._horizontal_divisions = 1
            self._vertical_divisions = 1
            self._update_drawing_preview()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._middle_panning and self._last_pan_position is not None:
            position = event.position().toPoint()
            delta = position - self._last_pan_position
            self._last_pan_position = position
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        if self._drawing_mode and self._drawing_start is not None and not self._drawing_mouse_released:
            current = self.mapToScene(event.position().toPoint())
            self._drawing_rect = QRectF(self._drawing_start, current).normalized()
            self._update_drawing_preview()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton and self._middle_panning:
            self._middle_panning = False
            self._last_pan_position = None
            self.viewport().unsetCursor()
            event.accept()
            return
        if self._drawing_mode and event.button() == Qt.LeftButton and self._drawing_start is not None:
            current = self.mapToScene(event.position().toPoint())
            self._drawing_rect = QRectF(self._drawing_start, current).normalized()
            self._drawing_mouse_released = True
            self._ctrl_held = bool(event.modifiers() & Qt.ControlModifier)
            self._shift_held = bool(event.modifiers() & Qt.ShiftModifier)
            self._update_drawing_preview()
            if not self._ctrl_held and not self._shift_held:
                self._finish_drawing()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self._drawing_mode:
            if event.key() == Qt.Key_Control:
                self._ctrl_held = True
                event.accept()
                return
            if event.key() == Qt.Key_Shift:
                self._shift_held = True
                event.accept()
                return
        if event.key() == Qt.Key_Escape and self._drawing_mode:
            self.cancel_drawing()
            event.accept()
            return
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if self._drawing_mode:
            if event.key() == Qt.Key_Control:
                self._ctrl_held = False
            elif event.key() == Qt.Key_Shift:
                self._shift_held = False
            if self._drawing_mouse_released and not self._ctrl_held and not self._shift_held:
                self._finish_drawing()
                event.accept()
                return
        super().keyReleaseEvent(event)


@dataclass(frozen=True)
class DatasetCandidate:
    label: str
    path: Path
    score: float


def candidate_quality(path: Path) -> float:
    """给同字候选图一个轻量质量分，用于决定默认保留项。"""
    image = read_image(str(path))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    resolution = float(np.sqrt(height * width))
    border = np.concatenate((gray[0], gray[-1], gray[:, 0], gray[:, -1]))
    border_ink = float(np.mean(border < 96))
    return np.log1p(sharpness) * 12.0 + np.log1p(resolution) * 4.0 - border_ink * 80.0


def collect_dataset_candidates(root: Path) -> tuple[dict[str, list[DatasetCandidate]], int]:
    """从一个或多个最终导出结果的 boxes.json 中收集带字名的切字图。"""
    groups: dict[str, list[DatasetCandidate]] = {}
    skipped = 0
    seen: set[Path] = set()
    for manifest_path in root.rglob("boxes.json"):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            crops = manifest_path.parent / "crops"
            for record in payload.get("boxes", []):
                label = str(record.get("label") or "").strip()
                filename = record.get("filename")
                path = (crops / filename).resolve() if filename else None
                if not label or label == "□" or path is None or not path.is_file() or path in seen:
                    skipped += 1
                    continue
                seen.add(path)
                try:
                    score = candidate_quality(path)
                except Exception:
                    score = 0.0
                groups.setdefault(label, []).append(DatasetCandidate(label, path, score))
        except Exception:
            skipped += 1
    return groups, skipped


class DeduplicateDialog(QDialog):
    def __init__(self, groups: dict[str, list[DatasetCandidate]], skipped: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("字符集去重与质量审核")
        self.resize(1100, 760)
        self.groups = groups
        self.selected: dict[str, Path | None] = {}
        self.buttons: dict[str, list[tuple[QPushButton, DatasetCandidate]]] = {}

        layout = QVBoxLayout(self)
        summary = QLabel(
            f"共 {len(groups)} 个文字、{sum(len(items) for items in groups.values())} 张候选图。"
            f"已跳过 {skipped} 个无名称或缺失项目。每组默认选择质量分最高的一张。"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        for label in sorted(groups):
            candidates = groups[label]
            group = QGroupBox(f"{label}  ·  {len(candidates)} 张")
            grid = QGridLayout(group)
            best = max(candidates, key=lambda item: item.score)
            self.selected[label] = best.path
            entries: list[tuple[QPushButton, DatasetCandidate]] = []
            for index, candidate in enumerate(candidates):
                button = QPushButton()
                button.setCheckable(True)
                button.setChecked(candidate.path == best.path)
                source_pixmap = QPixmap(str(candidate.path))
                thumbnail = QPixmap(62, 62)
                thumbnail.fill(QColor("white"))
                if not source_pixmap.isNull():
                    scaled = source_pixmap.scaled(62, 62, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    painter = QPainter(thumbnail)
                    painter.drawPixmap((62 - scaled.width()) // 2, (62 - scaled.height()) // 2, scaled)
                    painter.end()
                button.setIcon(QIcon(thumbnail))
                button.setIconSize(QSize(62, 62))
                button.setFixedSize(69, 69)
                button.setText("")
                button.setStyleSheet(
                    "QPushButton { padding: 4px; border: 2px solid #9a9a9a; background: white; }"
                    "QPushButton:checked { border: 4px solid #2487d6; background: #dcefff; }"
                )
                button.setToolTip(
                    f"来源：{candidate.path.parent.parent.name}\n{candidate.path}\n"
                    f"质量分：{candidate.score:.2f}\n选中表示最终保留"
                )
                button.clicked.connect(
                    lambda checked, current_label=label, current_path=candidate.path: self.choose_candidate(
                        current_label, current_path, checked
                    )
                )
                entries.append((button, candidate))
                grid.addWidget(button, index // 14, index % 14)
            self.buttons[label] = entries
            content_layout.addWidget(group)
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        actions = QHBoxLayout()
        self.process_button = QPushButton("处理并另存选中字符")
        self.process_button.clicked.connect(self.process_selected)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        actions.addStretch()
        actions.addWidget(self.process_button)
        actions.addWidget(close_button)
        layout.addLayout(actions)

    def choose_candidate(self, label: str, path: Path, checked: bool) -> None:
        if checked:
            self.selected[label] = path
            for button, candidate in self.buttons[label]:
                if candidate.path != path:
                    button.setChecked(False)
        elif self.selected.get(label) == path:
            self.selected[label] = None

    def process_selected(self) -> None:
        parent = QFileDialog.getExistingDirectory(self, "选择去重字符集保存位置")
        if not parent:
            return
        output = Path(parent) / "去重字符集"
        output.mkdir(parents=True, exist_ok=True)
        records = []
        for label, source in self.selected.items():
            if source is None:
                continue
            stem = safe_character_filename(label, "未命名")
            destination = output / f"{stem}{source.suffix.lower()}"
            shutil.copy2(source, destination)
            records.append({"label": label, "source": str(source), "filename": destination.name})
        (output / "deduplicate_manifest.json").write_text(
            json.dumps({"format": "autocut_deduplicate_v1", "count": len(records), "items": records}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        QMessageBox.information(self, "处理完成", f"已另存 {len(records)} 个文字：\n{output}")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("自动切字工具 v0.5")
        self.resize(1280, 820)
        self.image_path: str | None = None
        self.image: np.ndarray | None = None
        self.boxes: list[CharBox] = []
        self.preview: np.ndarray | None = None
        self.reading_order = "vertical_right_to_left"
        self.ocr_engine = None

        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        stage1_group = QGroupBox("1. 原图与自动检测")
        stage1_layout = QVBoxLayout(stage1_group)
        stage1_form = QFormLayout()
        stage2_group = QGroupBox("2. 字框审核与 OCR 标记")
        stage2_layout = QVBoxLayout(stage2_group)
        stage2_form = QFormLayout()
        stage3_group = QGroupBox("3. 命名、后处理与最终导出")
        stage3_layout = QVBoxLayout(stage3_group)
        stage3_form = QFormLayout()

        self.mode = QComboBox()
        self.mode.addItems([
            "主体大字",
            "全部文字（实验）",
            "碑刻密集横排（白字黑底）",
            "标准框规则网格（保留空白）",
            "文字区域动态切字（竖排）",
        ])
        stage1_form.addRow("处理模式", self.mode)

        self.show_numbers = QCheckBox("显示字符编号  (H)")
        self.show_numbers.setChecked(True)
        stage2_form.addRow("编号显示", self.show_numbers)

        self.shrink_noise_threshold = QDoubleSpinBox()
        self.shrink_noise_threshold.setRange(0.00, 10000.00)
        self.shrink_noise_threshold.setDecimals(2)
        self.shrink_noise_threshold.setSingleStep(0.10)
        self.shrink_noise_threshold.setValue(0.00)
        self.shrink_noise_threshold.setSuffix(" %")
        self.shrink_noise_threshold.setToolTip(
            "收缩时忽略宽度或高度低于该百分比的独立细线，并忽略对应的小面积孤点；"
            "只影响边界计算，不擦除图像内容"
        )
        stage2_form.addRow("收缩去杂阈值", self.shrink_noise_threshold)

        self.ignore_left = QSpinBox()
        self.ignore_left.setRange(0, 45)
        self.ignore_left.setValue(15)
        self.ignore_left.setSuffix(" %")
        stage1_form.addRow("忽略左侧", self.ignore_left)

        self.padding = QSpinBox()
        self.padding.setRange(0, 35)
        self.padding.setValue(12)
        self.padding.setSuffix(" %")
        stage1_form.addRow("裁图留白", self.padding)

        self.merge = QSlider(Qt.Horizontal)
        self.merge.setRange(0, 100)
        self.merge.setValue(50)
        stage1_form.addRow("字符合并", self.merge)

        self.guided_target_chars = QSpinBox()
        self.guided_target_chars.setRange(1, 100000)
        self.guided_target_chars.setValue(1563)
        self.guided_target_chars.setToolTip("引导检测的目标数量，或规则网格要生成的格子总数")
        stage1_form.addRow("目标/网格总数", self.guided_target_chars)

        self.grid_chars_per_column = QSpinBox()
        self.grid_chars_per_column.setRange(1, 500)
        self.grid_chars_per_column.setValue(18)
        self.grid_chars_per_column.setToolTip("规则网格中每一列包含的格子数")
        self.grid_chars_per_column.setEnabled(False)
        stage1_form.addRow("网格每列格数", self.grid_chars_per_column)

        self.grid_x_step = QDoubleSpinBox()
        self.grid_x_step.setRange(10.00, 500.00)
        self.grid_x_step.setDecimals(2)
        self.grid_x_step.setValue(100.00)
        self.grid_x_step.setSuffix(" %")
        self.grid_x_step.setToolTip("单锚点模式的相邻列步距；使用右上、左上双锚点时由两锚点自动分布")
        self.grid_x_step.setEnabled(False)
        stage1_form.addRow("网格横向步距", self.grid_x_step)

        self.grid_y_step = QDoubleSpinBox()
        self.grid_y_step.setRange(10.00, 500.00)
        self.grid_y_step.setDecimals(2)
        self.grid_y_step.setValue(100.00)
        self.grid_y_step.setSuffix(" %")
        self.grid_y_step.setToolTip("同列相邻格中心距离相对于标准框高度的百分比")
        self.grid_y_step.setEnabled(False)
        stage1_form.addRow("网格纵向步距", self.grid_y_step)

        self.binary_export = QCheckBox("导出为纯黑白图")
        self.binary_export.setToolTip("对每个单字进行背景校正和自动阈值处理")
        stage3_form.addRow("后处理", self.binary_export)

        self.threshold_offset = QSpinBox()
        self.threshold_offset.setRange(-60, 60)
        self.threshold_offset.setValue(0)
        self.threshold_offset.setPrefix("自动 ")
        self.threshold_offset.setToolTip("正值减少浅色噪点，负值保留更多浅墨")
        self.threshold_offset.setEnabled(False)
        stage3_form.addRow("阈值微调", self.threshold_offset)

        self.clean_export = QCheckBox("干净化")
        self.clean_export.setToolTip("先用黑色填补字内白孔，再用白色清除背景黑点")
        self.clean_export.setEnabled(False)
        stage3_form.addRow("颗粒清理", self.clean_export)

        self.clean_black_strength = QDoubleSpinBox()
        self.clean_black_strength.setMinimum(0.00)
        self.clean_black_strength.setMaximum(1.7976931348623157e308)
        self.clean_black_strength.setDecimals(2)
        self.clean_black_strength.setSingleStep(0.01)
        self.clean_black_strength.setValue(10.00)
        self.clean_black_strength.setSuffix(" %")
        self.clean_black_strength.setToolTip("先执行：用黑色填补黑色字形内部的小白块")
        self.clean_black_strength.setEnabled(False)
        stage3_form.addRow("黑色净化", self.clean_black_strength)

        self.clean_white_strength = QDoubleSpinBox()
        self.clean_white_strength.setMinimum(0.00)
        self.clean_white_strength.setMaximum(1.7976931348623157e308)
        self.clean_white_strength.setDecimals(2)
        self.clean_white_strength.setSingleStep(0.01)
        self.clean_white_strength.setValue(10.00)
        self.clean_white_strength.setSuffix(" %")
        self.clean_white_strength.setToolTip("后执行：用白色清除白色背景中的小黑块")
        self.clean_white_strength.setEnabled(False)
        stage3_form.addRow("白色净化", self.clean_white_strength)

        self.square_export = QCheckBox("居中补边为正方形")
        stage3_form.addRow("方形输出", self.square_export)

        self.square_size = QComboBox()
        self.square_size.addItem("保持原像素", 0)
        self.square_size.addItem("128 × 128", 128)
        self.square_size.addItem("224 × 224", 224)
        self.square_size.addItem("256 × 256", 256)
        self.square_size.addItem("512 × 512", 512)
        self.square_size.setCurrentIndex(2)
        self.square_size.setEnabled(False)
        stage3_form.addRow("统一尺寸", self.square_size)

        self.name_export = QCheckBox("按单字名称命名")
        self.name_export.setToolTip("按编号顺序使用下方文字命名；重复字自动增加 02、03 后缀")
        stage3_form.addRow("文件命名", self.name_export)

        self.import_names_button = QPushButton("导入字名 TXT")
        self.import_names_button.setEnabled(False)
        stage3_form.addRow("字名文件", self.import_names_button)

        self.names_text = QPlainTextEdit()
        self.names_text.setPlaceholderText("按字符框编号顺序粘贴文字；可连续输入或一行一个字")
        self.names_text.setMaximumHeight(76)
        self.names_text.setEnabled(False)
        stage3_form.addRow("字名内容", self.names_text)

        self.show_name_labels = QCheckBox("显示字符名称")
        self.show_name_labels.setToolTip("在框上显示最终导出文件名；可与数字编号同时显示")
        self.show_name_labels.setEnabled(False)
        stage3_form.addRow("命名显示", self.show_name_labels)

        self.export_folder_name = QLineEdit()
        self.export_folder_name.setPlaceholderText("原图名_切字结果")
        self.export_folder_name.setToolTip("导出时创建的结果文件夹名称")
        stage3_form.addRow("导出文件夹名", self.export_folder_name)

        self.open_button = QPushButton("打开原图")
        self.open_button.setShortcut("Ctrl+O")
        self.detect_button = QPushButton("自动切字")
        self.detect_button.setShortcut("F5")
        self.grid_generate_button = QPushButton("按选中标准框引导检测")
        self.grid_generate_button.setToolTip("引导检测选一个标准框；规则网格可选右上、左上两个锚点限定文字区域")
        self.export_button = QPushButton("最终导出")
        self.add_button = QPushButton("手动画字符框  (N)")
        self.add_button.setToolTip("十字画框时：Ctrl+滚轮调整左右列数，Shift+滚轮调整上下行数，同时按下可生成二维网格")
        self.delete_button = QPushButton("删除选中框  (Delete)")
        self.undo_button = QPushButton("撤销  (Ctrl+Z)")
        self.redo_button = QPushButton("重做  (Ctrl+Y)")
        self.undo_button.setEnabled(False)
        self.redo_button.setEnabled(False)
        self.shrink_button = QPushButton("收缩选中字框到墨迹")
        self.shrink_button.setToolTip("将每个选中框的四条边独立收缩到实际字形墨迹的最外缘")
        self.renumber_button = QPushButton("按当前模式重新编号")
        self.review_square = QCheckBox("审核图补边为 512×512")
        self.export_review_button = QPushButton("导出审核图")
        self.export_review_button.setShortcut("Ctrl+R")
        self.sync_review_button = QPushButton("同步审核结果")
        self.sync_review_button.setShortcut("Ctrl+Shift+R")
        self.save_project_button = QPushButton("保存工程")
        self.save_project_button.setShortcut("Ctrl+S")
        self.load_project_button = QPushButton("载入工程")
        self.load_project_button.setShortcut("Ctrl+L")
        self.ocr_button = QPushButton("本地 OCR 初标")
        self.ocr_button.setShortcut("Ctrl+Shift+I")
        self.deduplicate_button = QPushButton("字符集去重与质量审核")
        self.export_button.setShortcut("Ctrl+E")
        self.detect_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.export_review_button.setEnabled(False)
        self.save_project_button.setEnabled(False)
        self.ocr_button.setEnabled(False)

        stage1_layout.addLayout(stage1_form)
        stage1_layout.addWidget(self.open_button)
        stage1_layout.addWidget(self.detect_button)
        stage1_layout.addWidget(self.grid_generate_button)
        stage2_layout.addLayout(stage2_form)
        stage2_layout.addWidget(self.add_button)
        stage2_layout.addWidget(self.delete_button)
        history_layout = QHBoxLayout()
        history_layout.addWidget(self.undo_button)
        history_layout.addWidget(self.redo_button)
        stage2_layout.addLayout(history_layout)
        stage2_layout.addWidget(self.shrink_button)
        stage2_layout.addWidget(self.renumber_button)
        stage2_layout.addWidget(self.review_square)
        stage2_layout.addWidget(self.export_review_button)
        stage2_layout.addWidget(self.sync_review_button)
        stage2_layout.addWidget(self.save_project_button)
        stage2_layout.addWidget(self.load_project_button)
        stage2_layout.addWidget(self.ocr_button)
        stage2_layout.addWidget(self.deduplicate_button)
        stage3_layout.addLayout(stage3_form)
        stage3_layout.addWidget(self.export_button)
        controls_layout.addWidget(stage1_group)
        controls_layout.addWidget(stage2_group)
        controls_layout.addWidget(stage3_group)

        self.info = QLabel("尚未打开图片")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color:#666; padding:12px 2px;")
        controls_layout.addWidget(self.info)
        controls_layout.addStretch()

        self.editor = EditorView()
        self.editor.name_edit_callback = self.edit_box_name
        self.editor.boxes_changed_callback = self.manual_boxes_changed
        self.editor.history_changed_callback = self.update_history_buttons
        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setWidget(controls)
        controls_scroll.setFixedWidth(330)
        outer.addWidget(controls_scroll)
        outer.addWidget(self.editor, 1)

        self.open_button.clicked.connect(self.open_image)
        self.mode.currentIndexChanged.connect(self.update_mode_controls)
        self.detect_button.clicked.connect(self.run_detection)
        self.grid_generate_button.clicked.connect(self.generate_from_standard_box)
        self.export_button.clicked.connect(self.export_results)
        self.add_button.clicked.connect(self.editor.add_box)
        self.delete_button.clicked.connect(self.editor.delete_selected)
        self.undo_button.clicked.connect(self.editor.undo)
        self.redo_button.clicked.connect(self.editor.redo)
        self.shrink_button.clicked.connect(self.shrink_selected_boxes)
        self.renumber_button.clicked.connect(self.renumber_boxes)
        self.export_review_button.clicked.connect(self.export_review)
        self.sync_review_button.clicked.connect(self.sync_review)
        self.save_project_button.clicked.connect(self.save_project)
        self.load_project_button.clicked.connect(self.load_project)
        self.ocr_button.clicked.connect(self.run_local_ocr)
        self.deduplicate_button.clicked.connect(self.open_deduplicate_review)
        self.binary_export.toggled.connect(self.threshold_offset.setEnabled)
        self.binary_export.toggled.connect(self.clean_export.setEnabled)
        self.binary_export.toggled.connect(self.update_clean_controls)
        self.clean_export.toggled.connect(self.update_clean_controls)
        self.name_export.toggled.connect(self.update_name_controls)
        self.import_names_button.clicked.connect(self.import_names)
        self.names_text.textChanged.connect(self.update_box_labels)
        self.show_name_labels.toggled.connect(self.update_box_labels)
        self.show_name_labels.toggled.connect(self.editor.set_names_visible)
        self.square_export.toggled.connect(self.square_size.setEnabled)
        self.show_numbers.toggled.connect(self.editor.set_numbers_visible)
        add_action = QAction(self)
        add_action.setShortcut("N")
        add_action.triggered.connect(self.editor.add_box)
        self.addAction(add_action)
        toggle_labels_action = QAction(self)
        toggle_labels_action.setShortcut("H")
        toggle_labels_action.triggered.connect(lambda: self.show_numbers.toggle())
        self.addAction(toggle_labels_action)
        undo_action = QAction(self.editor)
        undo_action.setShortcut("Ctrl+Z")
        undo_action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        undo_action.triggered.connect(self.editor.undo)
        self.editor.addAction(undo_action)
        redo_action = QAction(self.editor)
        redo_action.setShortcuts(["Ctrl+Y", "Ctrl+Shift+Z"])
        redo_action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        redo_action.triggered.connect(self.editor.redo)
        self.editor.addAction(redo_action)
        self.setStatusBar(QStatusBar())

    def update_history_buttons(self, can_undo: bool, can_redo: bool) -> None:
        self.undo_button.setEnabled(can_undo)
        self.redo_button.setEnabled(can_redo)

    def update_mode_controls(self) -> None:
        grid_mode = self.mode.currentIndex() == 3
        self.grid_chars_per_column.setEnabled(grid_mode)
        self.grid_x_step.setEnabled(grid_mode)
        self.grid_y_step.setEnabled(grid_mode)

    def manual_boxes_changed(self) -> None:
        """手动画框、分割或删除后同步主窗口状态。"""
        self.boxes = self.editor.boxes()
        has_boxes = bool(self.boxes)
        for button in (self.export_button, self.export_review_button, self.save_project_button, self.ocr_button):
            button.setEnabled(has_boxes)
        self.update_box_labels()
        self.info.setText(f"手动字框：{len(self.boxes)} 个\n可继续按 N 进入十字光标画框")
        self.statusBar().showMessage(f"当前共有 {len(self.boxes)} 个字符框")

    def shrink_selected_boxes(self) -> None:
        """把选中的每个字框分别收缩到框内有效墨迹的紧边界。"""
        if self.image is None:
            return
        selected = [item for item in self.editor.scene().selectedItems() if isinstance(item, EditableBoxItem)]
        if not selected:
            QMessageBox.warning(self, "尚未选择字框", "请先选择一个或多个需要收缩的字符框。")
            return
        changed = 0
        skipped = 0
        self.editor.begin_box_change()
        for item in selected:
            box = item.scene_box()
            bounds = tight_ink_bounds_with_expansion(
                self.image,
                box,
                self.shrink_noise_threshold.value(),
            )
            if bounds is None:
                skipped += 1
                continue
            ink_x1, ink_y1, ink_x2, ink_y2 = bounds
            scene_rect = QRectF(
                ink_x1,
                ink_y1,
                max(1, ink_x2 - ink_x1),
                max(1, ink_y2 - ink_y1),
            )
            item.setPos(scene_rect.topLeft())
            item.setRect(0, 0, scene_rect.width(), scene_rect.height())
            changed += 1
        self.editor.end_box_change()
        self.statusBar().showMessage(f"已收缩 {changed} 个字符框；无有效墨迹 {skipped} 个")

    def open_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开书法原图", "", "图片 (*.png *.jpg *.jpeg *.tif *.tiff *.webp)")
        if not path:
            return
        self.load_image_path(path)

    def load_image_path(self, path: str) -> None:
        try:
            self.image = read_image(path)
            self.image_path = path
            self.export_folder_name.setText(Path(path).stem + "_切字结果")
            self.names_text.clear()
            self.name_export.setChecked(False)
            self.boxes = []
            self.preview = self.image.copy()
            self.detect_button.setEnabled(True)
            self.export_button.setEnabled(False)
            self.export_review_button.setEnabled(False)
            self.save_project_button.setEnabled(False)
            self.ocr_button.setEnabled(False)
            h, w = self.image.shape[:2]
            self.info.setText(f"{Path(path).name}\n尺寸：{w} × {h}\n尚未检测")
            self.editor.load_image(self.image)
            self.statusBar().showMessage("图片已载入")
        except Exception as exc:
            QMessageBox.critical(self, "读取失败", str(exc))

    def run_detection(self) -> None:
        if self.image is None:
            return
        if self.mode.currentIndex() == 3:
            self.generate_regular_grid()
            return
        if self.mode.currentIndex() == 4:
            self.generate_dynamic_region()
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if self.mode.currentIndex() == 2:
                self.reading_order = "horizontal_left_to_right"
                self.boxes = detect_dense_inscription(
                    self.image,
                    ignore_left_percent=self.ignore_left.value(),
                    padding_percent=self.padding.value(),
                    merge_strength=self.merge.value(),
                )
                order_text = "上→下、每行左→右"
            else:
                self.reading_order = "vertical_right_to_left"
                self.boxes = detect_characters(
                    self.image,
                    ignore_left_percent=self.ignore_left.value(),
                    padding_percent=self.padding.value(),
                    merge_strength=self.merge.value(),
                    whole_page=self.mode.currentIndex() == 1,
                )
                order_text = "右→左、列内上→下"
            self.preview = draw_preview(self.image, self.boxes)
            self.editor.set_boxes(self.boxes)
            self.update_box_labels()
            self.export_button.setEnabled(bool(self.boxes))
            self.export_review_button.setEnabled(bool(self.boxes))
            self.save_project_button.setEnabled(bool(self.boxes))
            self.ocr_button.setEnabled(bool(self.boxes))
            self.info.setText(f"检测完成\n识别字符框：{len(self.boxes)} 个\n阅读顺序：{order_text}")
            self.statusBar().showMessage(f"检测完成：{len(self.boxes)} 个字符框")
        except Exception as exc:
            QMessageBox.critical(self, "检测失败", str(exc))
        finally:
            QApplication.restoreOverrideCursor()

    def generate_regular_grid(self) -> None:
        """用一个右上锚点，或右上与左上两个锚点生成规则网格。"""
        if self.image is None:
            return
        selected = [item for item in self.editor.scene().selectedItems() if isinstance(item, EditableBoxItem)]
        if len(selected) not in (1, 2):
            QMessageBox.warning(
                self,
                "请选择网格锚点",
                "请选择一个右上起始框；或同时选择右上、左上两个框来限定文字区域。",
            )
            return
        all_items = self.editor.box_items()
        if len(all_items) > len(selected):
            answer = QMessageBox.question(
                self,
                "替换现有字符框",
                f"当前已有 {len(all_items)} 个字符框。规则网格会替换它们，是否继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        anchors = sorted((item.scene_box() for item in selected), key=lambda box: box.x, reverse=True)
        right_anchor = anchors[0]
        left_anchor = anchors[1] if len(anchors) == 2 else None
        total = self.guided_target_chars.value()
        per_column = self.grid_chars_per_column.value()
        columns = (total + per_column - 1) // per_column
        image_h, image_w = self.image.shape[:2]
        boxes: list[CharBox] = []
        outside = 0
        for index in range(total):
            column = index // per_column
            row = index % per_column
            if left_anchor is not None and columns > 1:
                ratio = column / (columns - 1)
                x = round(right_anchor.x + (left_anchor.x - right_anchor.x) * ratio)
                top = round(right_anchor.y + (left_anchor.y - right_anchor.y) * ratio)
                box_width = max(1, round(right_anchor.width + (left_anchor.width - right_anchor.width) * ratio))
                box_height = max(1, round(right_anchor.height + (left_anchor.height - right_anchor.height) * ratio))
            else:
                box_width = right_anchor.width
                box_height = right_anchor.height
                x = round(right_anchor.x - column * box_width * self.grid_x_step.value() / 100.0)
                top = right_anchor.y
            y = round(top + row * box_height * self.grid_y_step.value() / 100.0)
            if x < 0 or y < 0 or x + box_width > image_w or y + box_height > image_h:
                outside += 1
                continue
            boxes.append(CharBox(len(boxes) + 1, x, y, box_width, box_height, "grid"))

        self.reading_order = "vertical_right_to_left"
        self.boxes = boxes
        self.editor.set_boxes(boxes)
        self.names_text.clear()
        self.name_export.setChecked(False)
        self.preview = self.image.copy()
        for button in (self.export_button, self.export_review_button, self.save_project_button, self.ocr_button):
            button.setEnabled(bool(boxes))
        anchor_text = "双锚点限定区域" if left_anchor is not None else "单锚点按横向步距延伸"
        self.info.setText(
            f"规则网格生成完成\n目标：{total} 格，生成：{len(boxes)} 格\n"
            f"列数：{columns}，每列：{per_column} 格\n"
            f"方式：{anchor_text}\n空白格已保留，可选中后按 Delete 删除"
        )
        self.update_box_labels()
        if outside:
            QMessageBox.warning(
                self,
                "部分网格超出图片",
                f"生成了 {len(boxes)} 格，另有 {outside} 格超出图片范围而未生成。\n"
                "请调整起始框、框大小或网格步距后重试。",
            )
        else:
            self.statusBar().showMessage(f"规则网格已生成 {len(boxes)} 格；空白格可手动删除")

    def generate_dynamic_region(self) -> None:
        """用一个选中大框限定文字区域，然后分列并逐列动态切字。"""
        if self.image is None:
            return
        selected = [item for item in self.editor.scene().selectedItems() if isinstance(item, EditableBoxItem)]
        if len(selected) != 1:
            QMessageBox.warning(
                self,
                "请选择文字区域",
                "请只选中一个大框，并将它调整为只包围需要切字的竖排文字区域。",
            )
            return
        all_items = self.editor.box_items()
        if len(all_items) > 1:
            answer = QMessageBox.question(
                self,
                "替换现有字符框",
                f"当前已有 {len(all_items)} 个字符框。动态切字会替换它们，是否继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        region = selected[0].scene_box()
        target = self.guided_target_chars.value()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            boxes = detect_dynamic_text_region(self.image, region, target, self.padding.value())
        except Exception as exc:
            QMessageBox.critical(self, "动态切字失败", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.reading_order = "vertical_right_to_left"
        self.boxes = boxes
        self.editor.set_boxes(boxes)
        self.names_text.clear()
        self.name_export.setChecked(False)
        self.preview = self.image.copy()
        for button in (self.export_button, self.export_review_button, self.save_project_button, self.ocr_button):
            button.setEnabled(bool(boxes))
        self.info.setText(
            f"文字区域动态切字完成\n目标：{target} 个，生成：{len(boxes)} 个\n"
            f"区域：{region.width} × {region.height}px\n各列与各字位置独立计算"
        )
        self.update_box_labels()
        if len(boxes) < target:
            QMessageBox.warning(
                self,
                "候选文字不足",
                f"目标为 {target} 个，目前生成 {len(boxes)} 个。\n"
                "请确认区域完整包含文字、没有包含大面积图案，并适当减少目标字数后重试。",
            )
        else:
            self.statusBar().showMessage(f"动态切字完成：{len(boxes)} 个字符框")

    def generate_from_standard_box(self) -> None:
        """用选中框提供单字尺度，按真实墨迹峰值检测不规则排列文字。"""
        if self.image is None:
            QMessageBox.warning(self, "尚未打开图片", "请先打开原图。")
            return
        selected = [item for item in self.editor.scene().selectedItems() if isinstance(item, EditableBoxItem)]
        if len(selected) != 1:
            QMessageBox.warning(
                self,
                "请选择一个标准框",
                "请只选中一个清晰、大小具有代表性的单字框。标准框不必位于第一字。",
            )
            return
        all_items = self.editor.box_items()
        if len(all_items) > 1:
            answer = QMessageBox.question(
                self,
                "替换现有字符框",
                f"当前已有 {len(all_items)} 个字符框。引导检测会替换它们，是否继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        seed = selected[0].scene_box()
        total = self.guided_target_chars.value()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            boxes = detect_from_standard_box(self.image, seed, total)
        except Exception as exc:
            QMessageBox.critical(self, "引导检测失败", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.reading_order = "vertical_right_to_left"
        self.boxes = boxes
        self.editor.set_boxes(boxes)
        self.names_text.clear()
        self.name_export.setChecked(False)
        self.preview = self.image.copy()
        for button in (self.export_button, self.export_review_button, self.save_project_button, self.ocr_button):
            button.setEnabled(bool(boxes))
        self.info.setText(
            f"标准框引导检测完成\n目标：{total} 个，生成：{len(boxes)} 个\n"
            f"标准框：{seed.width} × {seed.height}px\n位置根据每个字的实际墨迹独立确定"
        )
        self.update_box_labels()
        if len(boxes) < total:
            QMessageBox.warning(
                self,
                "候选字数不足",
                f"目标为 {total} 个，目前找到 {len(boxes)} 个墨迹中心。\n"
                "请把标准框调整得稍小一些，或换一个大小更有代表性的清晰单字后重试。",
            )
        else:
            self.statusBar().showMessage(f"标准框引导检测完成：{len(boxes)} 个字符框")

    def update_clean_controls(self) -> None:
        enabled = self.binary_export.isChecked() and self.clean_export.isChecked()
        self.clean_black_strength.setEnabled(enabled)
        self.clean_white_strength.setEnabled(enabled)

    def update_name_controls(self) -> None:
        enabled = self.name_export.isChecked()
        self.names_text.setEnabled(enabled)
        self.import_names_button.setEnabled(enabled)
        self.show_name_labels.setEnabled(enabled)
        if not enabled:
            self.show_name_labels.setChecked(False)
        self.update_box_labels()

    def update_box_labels(self) -> None:
        items = sorted(self.editor.box_items(), key=lambda item: item.box_id)
        if self.name_export.isChecked() and self.show_name_labels.isChecked():
            labels = [char for char in self.names_text.toPlainText() if not char.isspace()]
            stems = character_filename_stems(labels, [item.box_id for item in items])
            for index, item in enumerate(items):
                item.name_text = stems[index] if index < len(stems) else "?"
                item.update()
        else:
            for item in items:
                item.name_text = None
                item.update()

    def edit_box_name(self, box_id: int) -> None:
        """点击右侧框顶名称后，直接修改该框对应的字名。"""
        items = sorted(self.editor.box_items(), key=lambda item: item.box_id)
        index = next((position for position, item in enumerate(items) if item.box_id == box_id), None)
        if index is None:
            return
        labels = [char for char in self.names_text.toPlainText() if not char.isspace()]
        while len(labels) < len(items):
            labels.append("□")
        current = labels[index] if index < len(labels) else "□"
        text, accepted = QInputDialog.getText(self, f"修改第 {box_id} 个字", "文字名称：", QLineEdit.Normal, current)
        if not accepted:
            return
        characters = [char for char in text if not char.isspace()]
        if not characters:
            return
        labels[index] = characters[0]
        self.names_text.setPlainText("\n".join(labels))
        self.statusBar().showMessage(f"第 {box_id} 个字符已修改为：{labels[index]}")

    def open_deduplicate_review(self) -> None:
        root = QFileDialog.getExistingDirectory(self, "选择包含多次切字结果的总文件夹")
        if not root:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            groups, skipped = collect_dataset_candidates(Path(root))
        finally:
            QApplication.restoreOverrideCursor()
        if not groups:
            QMessageBox.warning(self, "没有找到字符集", "所选文件夹内没有可用的 boxes.json 和带名称切字图。")
            return
        dialog = DeduplicateDialog(groups, skipped, self)
        dialog.exec()

    def import_names(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入字名文本", "", "文本文件 (*.txt);;所有文件 (*)")
        if not path:
            return
        try:
            data = Path(path).read_bytes()
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = data.decode("gb18030")
            self.names_text.setPlainText(text)
        except Exception as exc:
            QMessageBox.critical(self, "导入失败", str(exc))

    def renumber_boxes(self) -> None:
        self.editor.begin_box_change()
        self.editor.renumber(self.reading_order)
        self.editor.end_box_change()
        self.update_box_labels()

    def project_payload(self) -> dict:
        labels = [char for char in self.names_text.toPlainText() if not char.isspace()]
        boxes = self.editor.boxes()
        records = []
        for index, box in enumerate(boxes):
            record = asdict(box)
            record["label"] = labels[index] if index < len(labels) else None
            records.append(record)
        return {
            "format": "autocut_project_v1",
            "source": self.image_path,
            "reading_order": self.reading_order,
            "mode_index": self.mode.currentIndex(),
            "count": len(boxes),
            "boxes": records,
        }

    def save_project(self) -> None:
        if self.image is None or not self.editor.box_items() or not self.image_path:
            return
        self.editor.renumber(self.reading_order)
        suggested = str(Path(self.image_path).with_name(Path(self.image_path).stem + "_切字工程.json"))
        path, _ = QFileDialog.getSaveFileName(self, "保存切字工程", suggested, "切字工程 (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        Path(path).write_text(json.dumps(self.project_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
        self.statusBar().showMessage(f"工程已保存：{path}")

    def load_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "载入切字工程", "", "切字工程 (*.json)")
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            source = payload.get("source")
            if not source or not Path(source).exists():
                QMessageBox.warning(self, "找不到原图", f"工程记录的原图不存在：\n{source}")
                return
            image = read_image(source)
            records = payload.get("boxes", [])
            boxes = [CharBox(
                int(record["id"]), int(record["x"]), int(record["y"]),
                int(record["width"]), int(record["height"]), record.get("status", "manual")
            ) for record in records]
            self.image = image
            self.image_path = source
            self.boxes = boxes
            self.preview = image.copy()
            self.reading_order = payload.get("reading_order", "vertical_right_to_left")
            self.mode.setCurrentIndex(int(payload.get("mode_index", 2 if self.reading_order == "horizontal_left_to_right" else 0)))
            self.export_folder_name.setText(Path(source).stem + "_切字结果")
            self.editor.load_image(image)
            self.editor.set_boxes(boxes)
            labels = [record.get("label") for record in records]
            if any(label for label in labels):
                self.name_export.setChecked(True)
                self.names_text.setPlainText("\n".join(label or "□" for label in labels))
            self.detect_button.setEnabled(True)
            for button in (self.export_button, self.export_review_button, self.save_project_button, self.ocr_button):
                button.setEnabled(bool(boxes))
            self.update_box_labels()
            h, w = image.shape[:2]
            self.info.setText(f"工程已载入\n{Path(source).name}\n尺寸：{w} × {h}\n字符框：{len(boxes)} 个")
            self.statusBar().showMessage(f"工程已载入：{path}")
        except Exception as exc:
            QMessageBox.critical(self, "载入失败", str(exc))

    def export_review(self) -> None:
        if self.image is None or not self.editor.box_items() or not self.image_path:
            return
        self.editor.renumber(self.reading_order)
        self.boxes = self.editor.boxes()
        parent = QFileDialog.getExistingDirectory(self, "选择审核包保存位置", str(Path(self.image_path).parent))
        if not parent:
            return
        output = Path(parent) / (Path(self.image_path).stem + "_字框审核")
        review_dir = output / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        progress = QProgressDialog("正在导出审核图…", "取消", 0, len(self.boxes), self)
        progress.setWindowModality(Qt.WindowModal)
        exported = []
        for index, box in enumerate(self.boxes):
            progress.setValue(index)
            QApplication.processEvents()
            if progress.wasCanceled():
                break
            crop = self.image[box.y:box.y + box.height, box.x:box.x + box.width]
            if self.review_square.isChecked():
                crop = square_crop(crop, 512)
            filename = f"{box.id:04d}.png"
            write_image(review_dir / filename, crop)
            exported.append(asdict(box))
        progress.setValue(len(exported))
        payload = {
            "format": "autocut_review_v1",
            "source": self.image_path,
            "reading_order": self.reading_order,
            "count": len(exported),
            "review_square_512": self.review_square.isChecked(),
            "boxes": exported,
        }
        (output / "review_manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if len(exported) == len(self.boxes):
            QMessageBox.information(self, "审核图已导出", f"请在 review 文件夹中删除错误图片：\n{output}")
        else:
            QMessageBox.information(self, "导出已取消", f"已导出 {len(exported)} 张审核图：\n{output}")

    def sync_review(self) -> None:
        if self.image is None or not self.editor.box_items():
            QMessageBox.warning(self, "尚无字符框", "请先打开原图并完成自动检测，或载入工程。")
            return
        selected = QFileDialog.getExistingDirectory(self, "选择字框审核文件夹")
        if not selected:
            return
        selected_path = Path(selected)
        root = selected_path.parent if selected_path.name.lower() == "review" else selected_path
        review_dir = root / "review" if (root / "review").is_dir() else selected_path
        manifest_path = root / "review_manifest.json"
        if not manifest_path.exists():
            QMessageBox.warning(self, "缺少审核清单", "所选位置没有 review_manifest.json。")
            return
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            expected_ids = {int(record["id"]) for record in payload.get("boxes", [])}
            remaining_ids = {int(path.stem) for path in review_dir.glob("*.png") if path.stem.isdigit()}
            missing_ids = expected_ids - remaining_ids
            current_items = {item.box_id: item for item in self.editor.box_items()}
            remove_ids = sorted(missing_ids & set(current_items))
            if not remove_ids:
                QMessageBox.information(self, "同步完成", "没有发现被删除的审核图，字符框保持不变。")
                return
            answer = QMessageBox.question(
                self,
                "确认同步删除",
                f"审核文件夹中少了 {len(remove_ids)} 张图片。\n"
                f"同步后会删除对应的 {len(remove_ids)} 个字符框，是否继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            self.editor.begin_box_change()
            old_items = sorted(self.editor.box_items(), key=lambda item: item.box_id)
            old_labels = [char for char in self.names_text.toPlainText() if not char.isspace()]
            kept_labels = [old_labels[index] for index, item in enumerate(old_items) if item.box_id not in remove_ids and index < len(old_labels)]
            for box_id in remove_ids:
                self.editor.scene().removeItem(current_items[box_id])
            self.editor.renumber(self.reading_order)
            self.editor.end_box_change()
            if old_labels:
                self.names_text.setPlainText("\n".join(kept_labels))
            self.boxes = self.editor.boxes()
            self.update_box_labels()
            self.info.setText(f"审核同步完成\n已删除：{len(remove_ids)} 个错误框\n保留：{len(self.boxes)} 个字符框")
            QMessageBox.information(self, "同步完成", f"已删除 {len(remove_ids)} 个错误框，保留 {len(self.boxes)} 个。")
        except Exception as exc:
            QMessageBox.critical(self, "同步失败", str(exc))

    def run_local_ocr(self) -> None:
        if self.image is None or not self.editor.box_items():
            return
        try:
            if self.ocr_engine is None:
                self.statusBar().showMessage("正在加载本地 OCR 模型…")
                QApplication.processEvents()
                try:
                    from rapidocr_onnxruntime import RapidOCR
                except ImportError:
                    if not ensure_ocr_component(self):
                        self.statusBar().showMessage("OCR 组件未安装")
                        return
                    import importlib
                    importlib.invalidate_caches()
                    from rapidocr_onnxruntime import RapidOCR
                self.ocr_engine = RapidOCR()
            self.editor.renumber(self.reading_order)
            boxes = self.editor.boxes()
            progress = QProgressDialog("正在逐框进行本地 OCR…", "取消", 0, len(boxes), self)
            progress.setWindowModality(Qt.WindowModal)
            guesses: list[str] = []
            batch_size = 32
            for start in range(0, len(boxes), batch_size):
                if progress.wasCanceled():
                    break
                batch_boxes = boxes[start:start + batch_size]
                crops = []
                for box in batch_boxes:
                    crop = self.image[box.y:box.y + box.height, box.x:box.x + box.width]
                    if self.reading_order == "horizontal_left_to_right":
                        normalized = binary_light_on_dark(crop)
                    else:
                        normalized = binary_background_correct(crop)
                    crops.append(cv2.cvtColor(square_crop(normalized), cv2.COLOR_GRAY2BGR))
                results, _ = self.ocr_engine.text_recognizer(crops)
                for text, score in results:
                    text = str(text).strip()
                    chinese = next((char for char in text if "\u3400" <= char <= "\u9fff"), None)
                    guesses.append(chinese or "□")
                progress.setValue(min(start + len(batch_boxes), len(boxes)))
                QApplication.processEvents()
            while len(guesses) < len(boxes):
                guesses.append("□")
            self.name_export.setChecked(True)
            self.names_text.setPlainText("\n".join(guesses))
            self.show_name_labels.setChecked(True)
            recognized = sum(guess != "□" for guess in guesses)
            self.statusBar().showMessage(f"本地 OCR 完成：猜出 {recognized}/{len(boxes)} 个，请人工校正")
            QMessageBox.information(self, "OCR 初标完成", f"共处理 {len(boxes)} 个框，初步猜出 {recognized} 个。\n□ 表示未能识别，请人工填写。")
        except ImportError as exc:
            QMessageBox.critical(self, "OCR 组件加载失败", f"OCR 组件缺少运行文件：\n{exc}\n\n请重新安装 OCR 组件。")
        except Exception as exc:
            QMessageBox.critical(self, "OCR 失败", str(exc))

    def export_results(self) -> None:
        if self.image is None or not self.editor.box_items() or not self.image_path:
            return
        self.editor.renumber(self.reading_order)
        self.boxes = self.editor.boxes()
        labels: list[str] | None = None
        if self.name_export.isChecked():
            labels = [char for char in self.names_text.toPlainText() if not char.isspace()]
            if len(labels) != len(self.boxes):
                QMessageBox.warning(
                    self,
                    "字名数量不一致",
                    f"当前有 {len(self.boxes)} 个字符框，但字名内容有 {len(labels)} 个字符。\n"
                    "请增删字名或修正字符框后再导出。",
                )
                return
        requested_folder_name = self.export_folder_name.text().strip()
        if not requested_folder_name:
            QMessageBox.warning(self, "缺少文件夹名称", "请填写导出文件夹名。")
            return
        folder_name = safe_character_filename(requested_folder_name, Path(self.image_path).stem + "_切字结果")
        if folder_name != requested_folder_name:
            self.export_folder_name.setText(folder_name)
        default = str(Path(self.image_path).with_name(folder_name))
        folder = QFileDialog.getExistingDirectory(self, "选择导出位置", str(Path(default).parent))
        if not folder:
            return
        output = Path(folder) / folder_name
        crops = output / "crops"
        crops.mkdir(parents=True, exist_ok=True)
        filename_stems = character_filename_stems(labels, [box.id for box in self.boxes]) if labels is not None else []
        box_records = []
        for index, box in enumerate(self.boxes):
            crop = self.image[box.y:box.y + box.height, box.x:box.x + box.width]
            if self.binary_export.isChecked():
                if self.reading_order == "horizontal_left_to_right":
                    crop = binary_light_on_dark(crop, self.threshold_offset.value())
                else:
                    crop = binary_background_correct(crop, self.threshold_offset.value())
                if self.clean_export.isChecked():
                    crop = clean_binary_image(
                        crop,
                        self.clean_black_strength.value(),
                        self.clean_white_strength.value(),
                    )
            if self.square_export.isChecked():
                selected_size = int(self.square_size.currentData())
                crop = square_crop(crop, selected_size or None)
            label = labels[index] if labels is not None else None
            if label is None:
                filename = f"{box.id:04d}.png"
            else:
                filename = f"{filename_stems[index]}.png"
            write_image(crops / filename, crop)
            record = asdict(box)
            record["label"] = label
            record["filename"] = filename
            box_records.append(record)
        self.preview = draw_preview(self.image, self.boxes)
        write_image(output / "preview_numbered.png", self.preview)
        payload = {
            "source": self.image_path,
            "reading_order": self.reading_order,
            "count": len(self.boxes),
            "export_options": {
                "binary": self.binary_export.isChecked(),
                "threshold_offset": self.threshold_offset.value() if self.binary_export.isChecked() else 0,
                "clean": self.binary_export.isChecked() and self.clean_export.isChecked(),
                "clean_black_strength": self.clean_black_strength.value() if self.binary_export.isChecked() and self.clean_export.isChecked() else 0,
                "clean_white_strength": self.clean_white_strength.value() if self.binary_export.isChecked() and self.clean_export.isChecked() else 0,
                "square": self.square_export.isChecked(),
                "square_size": int(self.square_size.currentData()) if self.square_export.isChecked() else 0,
                "name_by_character": self.name_export.isChecked(),
            },
            "boxes": box_records,
        }
        (output / "boxes.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        QMessageBox.information(self, "导出完成", f"已导出 {len(self.boxes)} 个字符：\n{output}")
        self.statusBar().showMessage(f"导出完成：{output}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    if len(sys.argv) > 1 and Path(sys.argv[1]).exists():
        window.load_image_path(sys.argv[1])
    window.show()
    sys.exit(app.exec())
