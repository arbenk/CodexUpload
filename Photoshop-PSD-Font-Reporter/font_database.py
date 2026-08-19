from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fontTools.ttLib import TTCollection, TTFont


SIMPLIFIED = {0x0804, 0x1004}
TRADITIONAL = {0x0404, 0x0C04, 0x1404}


@dataclass(frozen=True)
class InstalledFont:
    postscript_name: str
    family: str
    style: str
    file_name: str
    file_path: str
    language_rank: int

    @property
    def display_name(self) -> str:
        if not self.style or self.style.casefold() in self.family.casefold():
            return self.family or self.postscript_name
        return f"{self.family} {self.style}".strip()


def _language_rank(platform_id: int, language_id: int) -> int:
    if platform_id == 3:
        if language_id in SIMPLIFIED:
            return 0
        if language_id in TRADITIONAL:
            return 1
        if language_id & 0x03FF == 0x0009:
            return 2
    if platform_id == 0:
        return 3
    if platform_id == 1 and language_id == 0:
        return 2
    return 4


def _best_name(font: TTFont, name_ids: tuple[int, ...]) -> tuple[str, int]:
    table = font.get("name")
    if table is None:
        return ""
    winner: tuple[int, int, str] | None = None
    for record in table.names:
        if record.nameID not in name_ids:
            continue
        try:
            value = record.toUnicode().strip().replace("\x00", "")
        except Exception:
            continue
        if not value:
            continue
        candidate = (
            _language_rank(record.platformID, record.langID),
            name_ids.index(record.nameID),
            value,
        )
        if winner is None or candidate[:2] < winner[:2]:
            winner = candidate
    return (winner[2], winner[0]) if winner else ("", 9)


def _font_record(font: TTFont, path: Path) -> InstalledFont | None:
    postscript, _ = _best_name(font, (6,))
    if not postscript:
        return None
    family, family_rank = _best_name(font, (16, 1))
    style, _ = _best_name(font, (17, 2))
    return InstalledFont(postscript, family or postscript, style, path.name, str(path.resolve()), family_rank)


def _font_paths() -> list[Path]:
    roots = []
    windows = os.environ.get("WINDIR")
    local = os.environ.get("LOCALAPPDATA")
    if windows:
        roots.append(Path(windows) / "Fonts")
    if local:
        roots.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.iterdir():
            if path.is_file() and path.suffix.casefold() in {".ttf", ".otf", ".ttc", ".otc"}:
                found.append(path)
    return found


def build_font_database() -> dict[str, InstalledFont]:
    result: dict[str, InstalledFont] = {}
    for path in _font_paths():
        try:
            if path.suffix.casefold() in {".ttc", ".otc"}:
                collection = TTCollection(str(path), lazy=True)
                fonts = collection.fonts
            else:
                collection = None
                fonts = [TTFont(str(path), lazy=True)]
            for font in fonts:
                record = _font_record(font, path)
                if record:
                    result[record.postscript_name.casefold()] = record
                font.close()
            if collection:
                collection.close()
        except Exception:
            continue
    return result
