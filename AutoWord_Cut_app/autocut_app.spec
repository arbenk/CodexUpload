# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_dir = Path(SPECPATH)
a = Analysis(
    [str(project_dir / "autocut_app.py")],
    pathex=[str(project_dir)],
    binaries=[],
    datas=[(str(project_dir / "ocr_component_manifest.json"), ".")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "rapidocr_onnxruntime",
        "onnxruntime",
        "pyclipper",
        "shapely",
        "yaml",
        "PIL",
        "flatbuffers",
        "google.protobuf",
        "six",
    ],
    noarchive=False,
    optimize=0,
)

# Qt6Core 应使用 Windows 自带的 ICU。Codex/Poppler 的 PATH 中也有同名 ICU 78，
# PyInstaller 会误收集它们，导致 QtCore.pyd 启动时出现“找不到指定的程序”。
wrong_icu_names = {"icuuc.dll", "icudt78.dll"}
a.binaries = [
    entry for entry in a.binaries
    if Path(entry[0]).name.lower() not in wrong_icu_names
]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="自动切字工具",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory="_internal",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="自动切字工具",
)
