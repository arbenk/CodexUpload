from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from psd_analyzer import FileReport


def preview_text(reports: list[FileReport]) -> str:
    blocks = []
    for report in reports:
        lines = [report.file_name]
        if report.error:
            lines.append(f"查询失败：{report.error}")
        elif not report.fonts:
            lines.append("未发现文字字体")
        else:
            for font in report.fonts:
                missing = " - 【未安装】" if font.missing else ""
                lines.append(f"{font.display_name} - {font.file_name}{missing}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def load_data(path: Path) -> dict[str, FileReport]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {key: FileReport.from_dict(value) for key, value in raw.get("files", {}).items()}
    except Exception:
        return {}


def save_data(path: Path, reports: dict[str, FileReport]) -> None:
    payload = {
        "version": 1,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "files": {key: value.to_dict() for key, value in reports.items()},
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def write_txt(path: Path, reports: list[FileReport]) -> None:
    path.write_text(preview_text(reports) + "\n", encoding="utf-8-sig")


def write_html(path: Path, reports: list[FileReport]) -> None:
    tables = []
    for report in reports:
        rows = []
        if report.error:
            rows.append(
                f'<tr class="error"><td colspan="3">查询失败：{html.escape(report.error)}</td></tr>'
            )
        elif not report.fonts:
            rows.append(
                '<tr class="empty"><td colspan="3">未发现文字字体</td></tr>'
            )
        else:
            for font in report.fonts:
                warning = '<span class="warning" title="字体未安装">▲<b>!</b></span>' if font.missing else ""
                state = "未安装" if font.missing else "已安装"
                rows.append(
                    '<tr>'
                    f'<td>{warning}{html.escape(font.display_name)}</td>'
                    f'<td>{html.escape(font.file_name)}</td><td>{state}</td></tr>'
                )
        tables.append(
            f'<table><thead><tr class="file-title"><th colspan="3" title="{html.escape(report.path)}">{html.escape(report.file_name)}</th></tr>'
            '<tr><th>字体名称</th><th>字体文件</th><th>状态</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>'
        )
    document = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>PSD 字体查询报告</title>
<style>body{{margin:0;background:#f3f4f6;color:#24272b;font:14px "Microsoft YaHei",sans-serif}}main{{max-width:1100px;margin:auto;padding:28px}}h1{{font-size:24px;color:#202328}}table{{width:100%;margin:18px 0 26px;border-collapse:separate;border-spacing:0;overflow:hidden;border:1px solid #cfd3d8;border-radius:7px;background:#fff;box-shadow:0 2px 7px rgba(0,0,0,.06)}}th,td{{padding:10px 12px;border-bottom:1px solid #e2e5e9;text-align:left}}th{{background:#eef0f3;color:#4b5158}}.file-title th{{background:#dfe3e8;color:#202328;font-size:16px}}tbody tr:last-child td{{border-bottom:0}}tbody tr:hover{{background:#f7f8fa}}.warning{{position:relative;display:inline-block;margin-right:7px;color:#f2bd2d;font-size:14px}}.warning b{{position:absolute;left:5px;top:4px;color:#443900;font-size:8px}}.error{{color:#b42318}}.empty{{color:#747b84}}</style></head><body><main><h1>PSD 字体查询报告</h1>{''.join(tables)}</main></body></html>'''
    path.write_text(document, encoding="utf-8")
