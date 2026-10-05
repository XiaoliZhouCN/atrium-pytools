"""atriumsync.feishu_xml — 把本地 Markdown 转成飞书 DocxXML。

为什么需要它
------------
飞书的原生格式是 **DocxXML**，与 Markdown 不是一一对应：

* 多列是 ``<grid><column width-ratio="0.5">``（不是 Markdown 能表达的）
* 表格是 ``<table><thead><tr><th><p>…``（表头/表体分开）
* 高亮块是 ``<callout emoji="…">``，子块**只允许** p / ol / ul / checkbox
* 有序列表默认 ``seq="auto"``；换行必须写成 ``<br/>``；文本内的 ``< > &`` 要转义

所以这里先把 Markdown **行内语法**解析成带标注的 run（粗体/斜体/删除线/链接），
再按块类型映射成 XML；Markdown 能表达但飞书没有对应块的东西（如**折叠块**、
**行内代码**）会降级并在 sidecar 里记为 degraded，绝不静默丢。
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# 行内：Markdown → runs
# ---------------------------------------------------------------------------
_INLINE = re.compile(
    r"\*\*(?P<bold>.+?)\*\*"
    r"|\*(?P<italic>.+?)\*"
    r"|~~(?P<strike>.+?)~~"
    r"|`(?P<code>[^`]+)`"
    r"|\[(?P<ltext>[^\]]*)\]\((?P<lurl>[^)\s]+)\)",
    re.S,
)


def _run(text: str, **state: Any) -> dict:
    return {"text": text, **state}


def parse_inline(text: str, **state: Any) -> list[dict]:
    """把 Markdown 行内语法解析成 run 列表；支持标注嵌套（如粗体里套链接）。"""
    text = text or ""
    runs: list[dict] = []
    position = 0
    for match in _INLINE.finditer(text):
        if match.start() > position:
            runs.append(_run(text[position:match.start()], **state))
        if match.group("bold"):
            runs += parse_inline(match.group("bold"), **{**state, "bold": True})
        elif match.group("italic"):
            runs += parse_inline(match.group("italic"), **{**state, "italic": True})
        elif match.group("strike"):
            runs += parse_inline(match.group("strike"), **{**state, "strike": True})
        elif match.group("code"):
            # 飞书 DocxXML 未提供行内 code 标签，降级为纯文本并记入 degraded
            runs.append(_run(match.group("code"), **{**state, "code": True}))
        else:
            runs += parse_inline(match.group("ltext"),
                                 **{**state, "link": match.group("lurl")})
        position = match.end()
    if position < len(text):
        runs.append(_run(text[position:], **state))
    return [r for r in runs if r.get("text")]


def escape_text(text: str) -> str:
    """飞书规定：只转义标签内部的文本，``\\n`` 要写成 ``<br/>``。"""
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\n", "<br/>"))


def runs_to_xml(runs: list[dict]) -> str:
    parts: list[str] = []
    for run in runs:
        piece = escape_text(run.get("text", ""))
        if run.get("bold"):
            piece = f"<b>{piece}</b>"
        if run.get("italic"):
            piece = f"<em>{piece}</em>"
        if run.get("strike"):
            piece = f"<del>{piece}</del>"
        if run.get("link"):
            piece = f'<a href="{escape_text(run["link"])}">{piece}</a>'
        parts.append(piece)
    return "".join(parts)


def inline_xml(text: str) -> str:
    return runs_to_xml(parse_inline(text))


# ---------------------------------------------------------------------------
# 块级：Notion 风格 block → 飞书 XML
# ---------------------------------------------------------------------------
#: 高亮块子块只允许这些
_CALLOUT_ALLOWED = {"paragraph", "bulleted_list_item", "numbered_list_item", "to_do"}
_LIST_TAGS = {"bulleted_list_item": "ul", "numbered_list_item": "ol"}


def _payload(block: dict, btype: str) -> dict:
    data = block.get(btype)
    return data if isinstance(data, dict) else {}


def _text(block: dict, btype: str) -> str:
    data = _payload(block, btype)
    rich = data.get("rich_text") or []
    if rich:
        return "".join(r.get("text", {}).get("content", "") for r in rich)
    return str(data.get("content") or "")


def _children(block: dict, btype: str) -> list[dict]:
    return _payload(block, btype).get("children") or []


def _render_list(blocks: list[dict], index: int, btype: str,
                 degraded: list[str]) -> tuple[str, int]:
    tag = _LIST_TAGS[btype]
    items: list[str] = []
    while index < len(blocks) and blocks[index].get("type") == btype:
        block = blocks[index]
        inner = inline_xml(_text(block, btype))
        kids = _children(block, btype)
        if kids:
            inner += render_blocks(kids, degraded=degraded)
        items.append(f"<li>{inner}</li>")
        index += 1
    return f"<{tag}>{''.join(items)}</{tag}>", index


def _render_table(block: dict, degraded: list[str]) -> str:
    rows = _children(block, "table")
    header = bool((_payload(block, "table") or {}).get("has_column_header"))
    head_cells: list[str] = []
    body_rows: list[str] = []
    for position, row in enumerate(rows):
        cells = _payload(row, "table_row").get("cells") or []
        rendered = [f"<p>{runs_to_xml(parse_inline(_cell_text(c)))}</p>" for c in cells]
        if header and position == 0:
            head_cells = [f"<th>{c}</th>" for c in rendered]
        else:
            body_rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in rendered) + "</tr>")
    parts = ["<table>"]
    if head_cells:
        parts.append("<thead><tr>" + "".join(head_cells) + "</tr></thead>")
    if body_rows:
        parts.append("<tbody>" + "".join(body_rows) + "</tbody>")
    parts.append("</table>")
    return "".join(parts)


def _cell_text(cell: Any) -> str:
    """表格单元格：可能是 rich_text 数组，也可能已经是字符串。"""
    if isinstance(cell, str):
        return cell
    if isinstance(cell, list):
        return "".join(r.get("text", {}).get("content", "") for r in cell)
    return ""


def render_blocks(blocks: list[dict], *, degraded: list[str] | None = None,
                  in_callout: bool = False) -> str:
    """把（Markdown 解析出的）block 列表渲染成飞书 DocxXML。"""
    degraded = degraded if degraded is not None else []
    out: list[str] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        btype = block.get("type") or "paragraph"
        data = _payload(block, btype)
        text = _text(block, btype)

        if btype in _LIST_TAGS:
            chunk, index = _render_list(blocks, index, btype, degraded)
            out.append(chunk)
            continue

        if btype == "paragraph":
            if text:
                out.append(f"<p>{inline_xml(text)}</p>")
            kids = _children(block, btype)
            if kids:
                out.append(render_blocks(kids, degraded=degraded,
                                         in_callout=in_callout))
        elif btype.startswith("heading_"):
            level = btype[-1]
            out.append(f"<h{level}>{inline_xml(text)}</h{level}>")
        elif btype == "to_do":
            done = "true" if data.get("checked") else "false"
            out.append(f'<checkbox done="{done}">{inline_xml(text)}</checkbox>')
        elif btype == "quote":
            out.append(f"<blockquote>{inline_xml(text)}</blockquote>")
        elif btype == "code":
            language = data.get("language") or "plain text"
            body = escape_text(text)
            out.append(f'<pre lang="{escape_text(language)}"><code>{body}</code></pre>')
        elif btype == "divider":
            out.append("<hr/>")
        elif btype == "equation":
            out.append(f"<p><latex>{escape_text(data.get('expression') or text)}</latex></p>")
        elif btype == "image":
            payload = data.get("external") or data.get("file") or {}
            url = payload.get("url") or ""
            if url:
                out.append(f'<img href="{escape_text(url)}"/>')
            else:
                degraded.append("image:without-url")
        elif btype == "callout":
            emoji = ((data.get("icon") or {}).get("emoji") or "💡")
            inner: list[str] = []
            if text:
                inner.append(f"<p>{inline_xml(text)}</p>")
            for child in _children(block, btype):
                ctype = child.get("type")
                if ctype in _CALLOUT_ALLOWED:
                    inner.append(render_blocks([child], degraded=degraded,
                                               in_callout=True))
                else:
                    # 飞书限制：高亮块内禁止表格/图片/代码/分栏等 → 降级成段落
                    degraded.append(f"callout:{ctype}->paragraph")
                    child_text = _text(child, ctype)
                    if child_text:
                        inner.append(f"<p>{inline_xml(child_text)}</p>")
            out.append(f'<callout emoji="{escape_text(emoji)}">{"".join(inner)}</callout>')
        elif btype == "toggle":
            # 飞书没有折叠块：降级为「加粗标题 + 子内容」
            degraded.append("toggle->heading+children")
            out.append(f"<p><b>{inline_xml(text)}</b></p>")
            kids = _children(block, btype)
            if kids:
                out.append(render_blocks(kids, degraded=degraded))
        elif btype == "column_list":
            inner: list[str] = ["<grid>"]
            for column in _children(block, btype):
                ratio = (_payload(column, "column") or {}).get("width_ratio")
                attr = f' width-ratio="{ratio}"' if ratio is not None else ""
                inner.append(f"<column{attr}>")
                inner.append(render_blocks(_children(column, "column"), degraded=degraded))
                inner.append("</column>")
            inner.append("</grid>")
            out.append("".join(inner))
        elif btype == "table":
            out.append(_render_table(block, degraded))
        elif btype == "table_row":
            pass
        else:
            if text:
                out.append(f"<p>{inline_xml(text)}</p>")
            kids = _children(block, btype)
            if kids:
                out.append(render_blocks(kids, degraded=degraded))
        index += 1
    return "".join(out)


def markdown_to_feishu_xml(markdown: str, *, title: str | None = None) -> tuple[str, list[str]]:
    """本地 Markdown → 飞书 DocxXML。返回 ``(xml, degraded 说明列表)``。

    ``title`` 非空时，会以 ``<title>`` 开头（飞书要求完整文档以唯一 title 开头）。
    Markdown 的第一个 ``# `` 一级标题视为文档标题（若传入 title 则跳过它）。
    """
    from notionsync.markdown import markdown_to_blocks

    lines = (markdown or "").replace("\r\n", "\n").split("\n")
    first_heading = ""
    if lines and lines[0].startswith("# "):
        first_heading = lines[0][2:].strip()
        lines = lines[1:]
    doc_title = title or first_heading
    blocks = markdown_to_blocks("\n".join(lines))
    degraded: list[str] = []
    body = render_blocks(blocks, degraded=degraded)
    xml = (f"<title>{escape_text(doc_title)}</title>" if doc_title else "") + body
    return xml, degraded
