from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import uuid
import winreg
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from ctypes import wintypes

from font_database import InstalledFont


FONT_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
RECYCLE_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PSD-Font-Reporter" / "FontRecycleBin"
METADATA_PATH = RECYCLE_ROOT / "index.json"


@dataclass
class FontFileEntry:
    path: str
    file_name: str
    display_name: str
    language_rank: int
    registry_records: list[dict]


@dataclass
class RecycledFont:
    id: str
    backup_path: str
    original_path: str
    file_name: str
    display_name: str
    deleted_at: str
    registry_records: list[dict]


def _registry_rows():
    roots = (
        ("HKCU", winreg.HKEY_CURRENT_USER, Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"),
        ("HKLM", winreg.HKEY_LOCAL_MACHINE, Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"),
    )
    for hive_name, hive, base in roots:
        try:
            with winreg.OpenKey(hive, FONT_KEY, 0, winreg.KEY_READ) as key:
                count = winreg.QueryInfoKey(key)[1]
                for index in range(count):
                    name, data, value_type = winreg.EnumValue(key, index)
                    if not isinstance(data, str):
                        continue
                    path = Path(os.path.expandvars(data))
                    if not path.is_absolute():
                        path = base / path
                    yield hive_name, name, data, value_type, path.resolve()
        except OSError:
            continue


def installed_font_files(database: dict[str, InstalledFont]) -> list[FontFileEntry]:
    faces: dict[str, list[InstalledFont]] = {}
    faces_by_name: dict[str, list[InstalledFont]] = {}
    for font in database.values():
        faces.setdefault(os.path.normcase(font.file_path), []).append(font)
        faces_by_name.setdefault(font.file_name.casefold(), []).append(font)
    registered: dict[str, tuple[Path, list[dict]]] = {}
    for hive, reg_name, reg_data, reg_type, path in _registry_rows():
        if path.suffix.casefold() not in {".ttf", ".otf", ".ttc", ".otc"} or not path.is_file():
            continue
        key = os.path.normcase(str(path))
        registered.setdefault(key, (path, []))[1].append({
            "hive": hive, "key": FONT_KEY, "name": reg_name,
            "data": reg_data, "type": reg_type,
        })
    result = []
    for key, (path, registry_records) in registered.items():
        records = faces.get(key, []) or faces_by_name.get(path.name.casefold(), [])
        names = sorted({record.display_name for record in records}, key=str.casefold)
        display = " / ".join(names) if names else registry_records[0]["name"].rsplit(" (", 1)[0]
        rank = min((record.language_rank for record in records), default=9)
        result.append(FontFileEntry(str(path), path.name, display, rank, registry_records))
    return result


def load_recycle_bin() -> list[RecycledFont]:
    if not METADATA_PATH.is_file():
        return []
    try:
        data = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
        result = []
        for item in data.get("items", []):
            if "registry_records" not in item and "registry_hive" in item:
                item["registry_records"] = [{
                    "hive": item.pop("registry_hive"), "key": item.pop("registry_key"),
                    "name": item.pop("registry_name"), "data": item.pop("registry_data"),
                    "type": item.pop("registry_type"),
                }]
            result.append(RecycledFont(**item))
        return result
    except Exception:
        return []


def save_recycle_bin(items: list[RecycledFont]) -> None:
    RECYCLE_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = METADATA_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps({"version": 1, "items": [asdict(item) for item in items]}, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(METADATA_PATH)


class SHELLEXECUTEINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
    ]


def run_elevated(request_path: Path) -> dict:
    if getattr(sys, "frozen", False):
        helper = Path(sys.executable).with_name("PSD-Font-Helper.exe")
        executable = str(helper if helper.is_file() else Path(sys.executable))
        parameters = f'"{request_path}"' if helper.is_file() else f'--font-helper "{request_path}"'
    else:
        executable = sys.executable
        main_path = Path(__file__).with_name("main.py")
        parameters = f'"{main_path}" --font-helper "{request_path}"'
    info = SHELLEXECUTEINFO()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040
    info.lpVerb = "runas"
    info.lpFile = executable
    info.lpParameters = parameters
    info.nShow = 0
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        raise OSError("管理员授权已取消或无法启动。")
    ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 120000)
    ctypes.windll.kernel32.CloseHandle(info.hProcess)
    response_path = request_path.with_suffix(".response.json")
    if not response_path.is_file():
        raise OSError("字体操作没有返回结果。")
    result = json.loads(response_path.read_text(encoding="utf-8"))
    response_path.unlink(missing_ok=True)
    request_path.unlink(missing_ok=True)
    return result


def delete_fonts(entries: list[FontFileEntry]) -> tuple[list[RecycledFont], list[str]]:
    RECYCLE_ROOT.mkdir(parents=True, exist_ok=True)
    existing = load_recycle_bin()
    prepared = []
    errors = []
    for entry in entries:
        try:
            item_id = uuid.uuid4().hex
            backup = RECYCLE_ROOT / f"{item_id}_{entry.file_name}"
            shutil.copy2(entry.path, backup)
            recycled = RecycledFont(
                item_id, str(backup), entry.path, entry.file_name, entry.display_name,
                datetime.now().isoformat(timespec="seconds"), entry.registry_records,
            )
            prepared.append(recycled)
        except Exception as error:
            errors.append(f"{entry.file_name}：备份失败：{error}")
    if not prepared:
        return [], errors
    request_path = RECYCLE_ROOT / f"request-{uuid.uuid4().hex}.json"
    request_path.write_text(json.dumps({"operation": "delete", "items": [asdict(item) for item in prepared]}, ensure_ascii=False), encoding="utf-8")
    try:
        result = run_elevated(request_path)
    except Exception as error:
        for item in prepared:
            Path(item.backup_path).unlink(missing_ok=True)
        return [], errors + [str(error)]
    succeeded_ids = set(result.get("succeeded", []))
    succeeded = [item for item in prepared if item.id in succeeded_ids]
    for item in prepared:
        if item.id not in succeeded_ids:
            Path(item.backup_path).unlink(missing_ok=True)
    errors.extend(result.get("errors", []))
    save_recycle_bin(existing + succeeded)
    return succeeded, errors


def restore_fonts(items: list[RecycledFont]) -> tuple[list[str], list[str]]:
    if not items:
        return [], []
    request_path = RECYCLE_ROOT / f"request-{uuid.uuid4().hex}.json"
    request_path.write_text(json.dumps({"operation": "restore", "items": [asdict(item) for item in items]}, ensure_ascii=False), encoding="utf-8")
    try:
        result = run_elevated(request_path)
    except Exception as error:
        return [], [str(error)]
    succeeded_ids = set(result.get("succeeded", []))
    remaining = [item for item in load_recycle_bin() if item.id not in succeeded_ids]
    for item in items:
        if item.id in succeeded_ids:
            Path(item.backup_path).unlink(missing_ok=True)
    save_recycle_bin(remaining)
    return list(succeeded_ids), result.get("errors", [])


def clear_recycle_bin() -> list[str]:
    errors = []
    for item in load_recycle_bin():
        try:
            path = Path(item.backup_path).resolve()
            if path.parent != RECYCLE_ROOT.resolve():
                raise ValueError("备份路径不在临时回收站内")
            path.unlink(missing_ok=True)
        except Exception as error:
            errors.append(f"{item.file_name}：{error}")
    if not errors:
        save_recycle_bin([])
    return errors


def _hive(value: str):
    if value == "HKCU":
        return winreg.HKEY_CURRENT_USER
    if value == "HKLM":
        return winreg.HKEY_LOCAL_MACHINE
    raise ValueError("无效注册表根")


def _allowed_font_path(path: Path) -> bool:
    roots = [
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts",
    ]
    return any(path.parent == root.resolve() for root in roots)


def run_font_helper(request_file: str) -> int:
    request_path = Path(request_file).resolve()
    response_path = request_path.with_suffix(".response.json")
    succeeded, errors = [], []
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        operation = request.get("operation")
        for raw in request.get("items", []):
            item = RecycledFont(**raw)
            try:
                original = Path(item.original_path).resolve()
                backup = Path(item.backup_path).resolve()
                if not _allowed_font_path(original) or backup.parent != RECYCLE_ROOT.resolve():
                    raise ValueError("字体路径校验失败")
                for record in item.registry_records:
                    if record.get("key") != FONT_KEY:
                        raise ValueError("注册表路径校验失败")
                if operation == "delete":
                    if not original.is_file() or not backup.is_file():
                        raise FileNotFoundError("字体原文件或备份不存在")
                    ctypes.windll.gdi32.RemoveFontResourceW(str(original))
                    original.unlink()
                    for record in item.registry_records:
                        with winreg.OpenKey(_hive(record["hive"]), record["key"], 0, winreg.KEY_SET_VALUE) as key:
                            winreg.DeleteValue(key, record["name"])
                elif operation == "restore":
                    if original.exists():
                        raise FileExistsError("目标字体已存在，未覆盖现有安装")
                    if not backup.is_file():
                        raise FileNotFoundError("回收站备份不存在")
                    for record in item.registry_records:
                        with winreg.OpenKey(_hive(record["hive"]), record["key"], 0, winreg.KEY_QUERY_VALUE) as key:
                            try:
                                winreg.QueryValueEx(key, record["name"])
                                raise FileExistsError(f"字体注册记录已存在：{record['name']}")
                            except FileNotFoundError:
                                pass
                    shutil.copy2(backup, original)
                    for record in item.registry_records:
                        with winreg.OpenKey(_hive(record["hive"]), record["key"], 0, winreg.KEY_SET_VALUE) as key:
                            winreg.SetValueEx(key, record["name"], 0, record["type"], record["data"])
                    ctypes.windll.gdi32.AddFontResourceW(str(original))
                else:
                    raise ValueError("未知字体操作")
                succeeded.append(item.id)
            except Exception as error:
                try:
                    if operation == "delete" and backup.is_file():
                        if not original.exists():
                            shutil.copy2(backup, original)
                        for record in item.registry_records:
                            with winreg.OpenKey(_hive(record["hive"]), record["key"], 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
                                try:
                                    winreg.QueryValueEx(key, record["name"])
                                except FileNotFoundError:
                                    winreg.SetValueEx(key, record["name"], 0, record["type"], record["data"])
                        ctypes.windll.gdi32.AddFontResourceW(str(original))
                    elif operation == "restore" and original.exists():
                        original.unlink(missing_ok=True)
                except Exception as rollback_error:
                    error = RuntimeError(f"{error}；回滚失败：{rollback_error}")
                errors.append(f"{item.file_name}：{error}")
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001D, 0, 0, 0x0002, 3000, None)
    except Exception as error:
        errors.append(str(error))
    response_path.write_text(json.dumps({"succeeded": succeeded, "errors": errors}, ensure_ascii=False), encoding="utf-8")
    return 0 if not errors else 1
