from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

from psd_tools import PSDImage
from psd_tools.api.layers import TypeLayer
from psd_tools.constants import Compression
from psd_tools.psd.image_data import ImageData
from psd_tools.psd.layer_and_mask import ChannelData

from font_database import InstalledFont

logging.getLogger("psd_tools").setLevel(logging.ERROR)


@dataclass
class FontUsage:
    postscript_name: str
    family: str
    style: str
    file_name: str
    missing: bool
    layers: list[str]

    @property
    def display_name(self) -> str:
        if not self.style or self.style.casefold() in self.family.casefold():
            return self.family or self.postscript_name
        return f"{self.family} {self.style}".strip()


@dataclass
class FileReport:
    path: str
    file_name: str
    file_size: int
    modified_ns: int
    fonts: list[FontUsage]
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "FileReport":
        return cls(
            path=value["path"],
            file_name=value.get("file_name", Path(value["path"]).name),
            file_size=value.get("file_size", 0),
            modified_ns=value.get("modified_ns", 0),
            fonts=[FontUsage(**font) for font in value.get("fonts", [])],
            error=value.get("error", ""),
        )


@contextmanager
def metadata_only_psd() -> Iterator[None]:
    original_channel_read = ChannelData.read.__func__
    original_image_read = ImageData.read.__func__

    def read_channel(cls, fp, length=0, **kwargs):
        compression = Compression(int.from_bytes(fp.read(2), "big"))
        if length > 0:
            fp.seek(length, os.SEEK_CUR)
        return cls(compression=compression, data=b"")

    def read_image(cls, fp, **kwargs):
        header = fp.read(2)
        compression = Compression(int.from_bytes(header, "big")) if len(header) == 2 else Compression.RAW
        fp.seek(0, os.SEEK_END)
        return cls(compression=compression, data=b"")

    ChannelData.read = classmethod(read_channel)
    ImageData.read = classmethod(read_image)
    try:
        yield
    finally:
        ChannelData.read = classmethod(original_channel_read)
        ImageData.read = classmethod(original_image_read)


def _layer_fonts(layer: TypeLayer):
    used = []
    try:
        for paragraph in layer.typesetting:
            for run in paragraph.runs:
                font = run.style.font
                if font and font.postscript_name:
                    used.append(font)
    except Exception:
        try:
            used.extend(layer.typesetting.fonts)
        except Exception:
            pass
    unique = {}
    for font in used:
        unique[font.postscript_name.casefold()] = font
    return unique.values()


def analyze_file(path: str, installed: dict[str, InstalledFont]) -> FileReport:
    source = Path(path)
    stat = source.stat()
    report = FileReport(str(source.resolve()), source.name, stat.st_size, stat.st_mtime_ns, [])
    try:
        with metadata_only_psd():
            psd = PSDImage.open(source)
        usages: dict[str, FontUsage] = {}
        for layer in psd.descendants():
            if not isinstance(layer, TypeLayer):
                continue
            for font in _layer_fonts(layer):
                ps_name = font.postscript_name
                key = ps_name.casefold()
                known = installed.get(key)
                if key not in usages:
                    usages[key] = FontUsage(
                        postscript_name=ps_name,
                        family=known.family if known else (font.family or ps_name),
                        style=known.style if known else font.style,
                        file_name=known.file_name if known else ps_name,
                        missing=known is None,
                        layers=[],
                    )
                if layer.name not in usages[key].layers:
                    usages[key].layers.append(layer.name)
        # The PostScript font identifier is stored in the PSD whether or not the
        # font is installed, so its sort position stays stable across refreshes.
        report.fonts = sorted(usages.values(), key=lambda item: item.postscript_name.casefold())
    except Exception as error:
        report.error = f"{type(error).__name__}: {error}"
    return report
