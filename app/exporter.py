# -*- coding: utf-8 -*-
"""整卷导出：一卷一个文件，支持 txt / docx。

docx 采用零依赖手写 OOXML（zipfile + 最小部件集），不引入 python-docx / lxml。
写盘统一走「临时文件 + os.replace」，失败不留半截文件。
"""
from __future__ import annotations

import io
import os
import tempfile
import time
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from app.config import DEFAULT_FONT_FAMILY

INVALID_FILENAME_CHARS = set('\\/:*?"<>|')
MAX_NAME_CHARS = 80
TXT_FORMAT = "txt"
DOCX_FORMAT = "docx"


def sanitize_filename(name: str, max_chars: int = MAX_NAME_CHARS,
                      fallback: str = "未命名卷") -> str:
    """把卷名 / 书名 / 文件名里的非法字符与控制字符替换为下划线，并做长度截断。"""
    raw = str(name or "")
    cleaned = "".join(
        "_"
        if (ch in INVALID_FILENAME_CHARS or ord(ch) < 32 or ord(ch) == 127)
        else ch
        for ch in raw
    )
    cleaned = cleaned.strip().rstrip(".").strip()
    if not cleaned:
        cleaned = fallback
    return cleaned[:max_chars]


def _normalize_text(content: str) -> str:
    return str(content or "").replace("\r\n", "\n").replace("\r", "\n")


def _body_lines(content: str) -> list[str]:
    text = _normalize_text(content).rstrip("\n")
    if not text.strip():
        return []
    return text.split("\n")


def build_txt(chapters: list[tuple[str, str]]) -> bytes:
    """txt：章名前空一行、章后空一行、章与章之间空两行；空章只留章名。

    UTF-8 带 BOM + CRLF，双击记事本不乱码。
    """
    lines: list[str] = []
    for index, (title, content) in enumerate(chapters):
        if index == 0:
            lines.append("")
        else:
            lines.extend(["", ""])
        lines.append(str(title or "").strip() or "未命名章节")
        lines.append("")
        lines.extend(_body_lines(content))
    payload = "\r\n".join(lines) + "\r\n"
    return payload.encode("utf-8-sig")


def _xml_text(value: str) -> str:
    """转义 XML 特殊字符，并剔除 XML 1.0 不允许的控制字符。"""
    cleaned = "".join(
        ch
        for ch in str(value or "")
        if ch in "\t\n\r" or ord(ch) >= 32
    )
    return escape(cleaned)


def _paragraph(text: str, run_props: str, para_props: str) -> str:
    body = "" if not text else f'<w:t xml:space="preserve">{_xml_text(text)}</w:t>'
    # rFonts / sz / b 等必须包在 <w:rPr> 里，否则 Word 会当成非法段落忽略
    run = f"<w:r><w:rPr>{run_props}</w:rPr>{body}</w:r>" if body else ""
    return f"<w:p><w:pPr>{para_props}</w:pPr>{run}</w:p>"


def build_docx(
    chapters: list[tuple[str, str]],
    font_family: str = "",
    font_size: float = 12.0,
    line_spacing: float = 1.55,
    latin_font: str = "",
) -> bytes:
    """生成最小可用的 docx 包：标题 Heading1 + 章前分页 + 首行缩进两字 + 行距倍数。"""
    east_font = (font_family or DEFAULT_FONT_FAMILY).strip() or DEFAULT_FONT_FAMILY
    latin = (latin_font or east_font).strip() or east_font
    base_half_points = max(2, round(float(font_size) * 2))
    title_half_points = max(base_half_points + 2, round(float(font_size) * 1.3 * 2))
    line_twips = max(240, round(float(line_spacing) * 240))
    first_line_twips = round(float(font_size) * 2 * 20)

    base_run = (
        f'<w:rFonts w:ascii="{_xml_text(latin)}" w:hAnsi="{_xml_text(latin)}" '
        f'w:eastAsia="{_xml_text(east_font)}" w:cs="{_xml_text(latin)}"/>'
        f'<w:sz w:val="{base_half_points}"/><w:szCs w:val="{base_half_points}"/>'
    )
    title_run = (
        f'<w:rFonts w:ascii="{_xml_text(latin)}" w:hAnsi="{_xml_text(latin)}" '
        f'w:eastAsia="{_xml_text(east_font)}" w:cs="{_xml_text(latin)}"/>'
        f'<w:b/><w:bCs/><w:sz w:val="{title_half_points}"/>'
        f'<w:szCs w:val="{title_half_points}"/>'
    )
    body_props = (
        f'<w:spacing w:before="0" w:after="0" w:line="{line_twips}" w:lineRule="auto"/>'
        f'<w:ind w:firstLineChars="200" w:firstLine="{first_line_twips}"/>'
    )
    title_props_base = (
        f'<w:pStyle w:val="Heading1"/>'
        f'<w:spacing w:before="240" w:after="120" w:line="{line_twips}" w:lineRule="auto"/>'
        f'<w:ind w:firstLineChars="0" w:firstLine="0"/>'
    )

    paragraphs: list[str] = []
    for index, (title, content) in enumerate(chapters):
        heading = str(title or "").strip() or "未命名章节"
        page_break = "" if index == 0 else "<w:pageBreakBefore/>"
        paragraphs.append(_paragraph(heading, title_run, title_props_base + page_break))
        for line in _body_lines(content):
            paragraphs.append(_paragraph(line, base_run, body_props))

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(paragraphs)
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1800" w:bottom="1440" w:left="1800" '
        'w:header="851" w:footer="992" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:docDefaults><w:rPrDefault><w:rPr>"
        f'<w:rFonts w:ascii="{_xml_text(latin)}" w:hAnsi="{_xml_text(latin)}" '
        f'w:eastAsia="{_xml_text(east_font)}" w:cs="{_xml_text(latin)}"/>'
        f'<w:sz w:val="{base_half_points}"/><w:szCs w:val="{base_half_points}"/>'
        '<w:lang w:val="en-US" w:eastAsia="zh-CN"/></w:rPr></w:rPrDefault>'
        "<w:pPrDefault><w:pPr>"
        f'<w:spacing w:before="0" w:after="0" w:line="{line_twips}" w:lineRule="auto"/>'
        "</w:pPr></w:pPrDefault></w:docDefaults>"
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/><w:qFormat/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1">'
        '<w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
        '<w:next w:val="Normal"/><w:qFormat/>'
        '<w:pPr><w:keepNext/><w:outlineLvl w:val="0"/></w:pPr>'
        f"<w:rPr><w:b/><w:bCs/><w:sz w:val=\"{title_half_points}\"/>"
        f'<w:szCs w:val="{title_half_points}"/></w:rPr></w:style>'
        "</w:styles>"
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    document_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/styles.xml", styles_xml)
        archive.writestr("word/_rels/document.xml.rels", document_rels)
    return buffer.getvalue()


def build_volume_payload(
    fmt: str,
    chapters: list[tuple[str, str]],
    settings=None,
) -> bytes:
    if fmt == DOCX_FORMAT:
        return build_docx(
            chapters,
            font_family=str(getattr(settings, "font_family", "") or ""),
            font_size=float(getattr(settings, "font_size", 12.0) or 12.0),
            line_spacing=float(getattr(settings, "line_spacing", 1.55) or 1.55),
            latin_font=str(getattr(settings, "font_latin", "") or ""),
        )
    return build_txt(chapters)


def write_atomic(path: str, data: bytes) -> None:
    """先写同目录临时文件，再 os.replace；任何异常都会清掉临时文件。"""
    target = Path(path)
    handle, tmp_path = tempfile.mkstemp(
        dir=str(target.parent), prefix=".~export_", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def export_volume_file(
    path: str,
    fmt: str,
    chapters: list[tuple[str, str]],
    settings=None,
) -> None:
    if not chapters:
        raise ValueError("该卷还没有章节，无法导出")
    payload = build_volume_payload(fmt, chapters, settings)
    write_atomic(path, payload)


# ---------------------------------------------------------------- 整本导出
MIN_BOOK_INDEX_WIDTH = 3
# Windows 传统 MAX_PATH=260，这里预留一点余量给盘符 / 反斜杠
WINDOWS_PATH_LIMIT = 259


class BookExportError(Exception):
    """单章写盘失败：带上已成功数量与失败文件名，供中文提示使用。"""

    def __init__(self, failed_name: str, reason: str, success_count: int,
                 total: int) -> None:
        super().__init__(f"{failed_name}: {reason}")
        self.failed_name = failed_name
        self.reason = reason
        self.success_count = success_count
        self.total = total


def book_index_width(total: int) -> int:
    return max(MIN_BOOK_INDEX_WIDTH, len(str(max(1, int(total)))))


def build_book_filenames(directory: str, titles: list[str], fmt: str) -> list[str]:
    """按「001 章名.txt」规则生成文件名：路径超限截断、同目录重名追加序号。"""
    width = book_index_width(len(titles))
    ext = "." + fmt
    reserved: set[str] = set()
    names: list[str] = []
    base_budget = WINDOWS_PATH_LIMIT - len(str(directory)) - 1
    for index, title in enumerate(titles, start=1):
        prefix = f"{index:0{width}d} "
        stem = sanitize_filename(
            str(title or "").strip() or "未命名章节", max_chars=120,
            fallback="未命名章节",
        )
        budget = base_budget - len(prefix) - len(ext)
        stem = stem[: max(1, budget)] or "未命名章节"
        name = f"{prefix}{stem}{ext}"
        counter = 2
        while name.casefold() in reserved or os.path.exists(
                os.path.join(str(directory), name)):
            suffix = f"_{counter}"
            keep = budget - len(suffix)
            name = f"{prefix}{stem[: max(1, keep)]}{suffix}{ext}"
            counter += 1
        reserved.add(name.casefold())
        names.append(name)
    return names


def timestamped_folder_name(parent_dir: str, base_name: str,
                            stamp: str = "") -> str:
    """生成不冲突的「书名_时间戳」文件夹名（同一秒重复时追加序号）。"""
    marker = stamp or time.strftime("%Y%m%d_%H%M%S")
    candidate = f"{base_name}_{marker}"
    extra = 2
    while os.path.exists(os.path.join(str(parent_dir), candidate)):
        candidate = f"{base_name}_{marker}_{extra}"
        extra += 1
    return candidate


def export_book_directory(
    parent_dir: str,
    folder_name: str,
    fmt: str,
    chapters: list[tuple[str, str]],
    settings=None,
) -> tuple[str, list[str]]:
    """一本书 = 一个文件夹 + 一章一个文件（平铺）。返回 (文件夹, 已写文件列表)。

    任何一章写盘失败都会清掉本次已新建的文件，并抛 BookExportError。
    """
    if not chapters:
        raise ValueError("这本书还没有章节，无法导出")
    root = Path(str(parent_dir)) / str(folder_name)
    root.mkdir(parents=True, exist_ok=True)
    names = build_book_filenames(str(root), [t for t, _ in chapters], fmt)

    created: list[Path] = []
    for name, (title, content) in zip(names, chapters):
        payload = build_volume_payload(fmt, [(title, content)], settings)
        path = root / name
        try:
            write_atomic(str(path), payload)
        except Exception as exc:  # noqa: BLE001 - 失败要清理本次新建文件
            for done in created:
                try:
                    done.unlink()
                except OSError:
                    pass
            raise BookExportError(name, str(exc), len(created), len(chapters)) from exc
        created.append(path)
    return str(root), [str(p) for p in created]
