"""从当前 Python 环境生成可按需下载的 Windows OCR 组件包。"""

import argparse
import hashlib
import importlib.util
import json
import shutil
import tempfile
import zipfile
from pathlib import Path


MODULES = [
    "rapidocr_onnxruntime",
    "onnxruntime",
    "pyclipper",
    "shapely",
    "yaml",
    "PIL",
    "flatbuffers",
    "packaging",
    "google",
]


def module_path(name: str) -> Path:
    spec = importlib.util.find_spec(name)
    if spec is None:
        raise RuntimeError(f"当前环境缺少组件依赖：{name}")
    if spec.submodule_search_locations:
        return Path(next(iter(spec.submodule_search_locations)))
    if spec.origin:
        return Path(spec.origin)
    raise RuntimeError(f"无法定位组件依赖：{name}")


def copy_module(name: str, staging: Path) -> None:
    source = module_path(name)
    destination = staging / (source.name if source.is_dir() else source.name)
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)
    else:
        shutil.copy2(source, destination)
    if name == "shapely":
        libraries = source.parent / "shapely.libs"
        if libraries.is_dir():
            shutil.copytree(libraries, staging / libraries.name, dirs_exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="autoword-ocr-") as temporary:
        staging = Path(temporary)
        for name in MODULES:
            copy_module(name, staging)
        shutil.copy2(module_path("six"), staging / "six.py")
        metadata = {
            "id": "rapidocr-windows-x64-py314-v1",
            "python": "3.14",
            "platform": "windows-x64",
            "engine": "rapidocr_onnxruntime 1.2.3",
            "runtime": "onnxruntime 1.30.0",
        }
        (staging / "component-package.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in staging.rglob("*"):
                if path.is_file() and "__pycache__" not in path.parts:
                    archive.write(path, path.relative_to(staging))
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(json.dumps({"path": str(args.output), "size_bytes": args.output.stat().st_size, "sha256": digest}, indent=2))


if __name__ == "__main__":
    main()
