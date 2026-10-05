"""notionsync.markdown — Notion blocks ⇄ Markdown 的轻量互转。

支持范围（读取）
----------------
段落、一二三级标题、无序/有序列表、待办、折叠块、引用、callout、代码块、
分割线、图片/视频/文件/PDF/书签、子页面、表格、分栏（拍平）、同步块、公式。

支持范围（写入）
----------------
``markdown_to_blocks`` 只解析最常见的骨架：#/##/###、``-`` / ``*`` / ``+`` 无序、
``1.`` 有序、``- [ ]`` / ``- [x]`` 待办、``>`` 引用、``` 围栏代码、``---`` 分割线、
普通段落，以及两空格缩进的嵌套列表。其余语法按段落原样保留，绝不静默丢内容。
"""

from __future__ import annotations

import re
from typing import Any

INDENT_UNIT = 2          # 一级缩进宽度
MAX_INLINE_DEPTH = 200   # 渲染递归保护

# 这些块没有自己的可见文本，只渲染其子块
CONTAINER_TYPES = {"column_list", "column", "synced_block", "template"}
# 这些块类型不展开子块
LEAF_TYPES = {
    "divider", "image", "video", "audio", "file", "pdf", "bookmark", "embed",
    "equation", "link_preview", "table_row", "unsupported",
}


# ---------------------------------------------------------------------------
# rich_text → inline markdown
# ---------------------------------------------------------------------------
def _inline(rich: list[dict] | None) -> str:
    if not rich:
        return ""
    parts: list[str] = []
    for item in rich:
        text = item.get("plain_text")
        if text is None:
            text = (item.get("text") or {}).get("content", "")
        if not text:
            continue
        annotations = item.get("annotations") or {}
        if annotations.get("code"):
            text = f"`{text}`"
        if annotations.get("bold"):
            text = f"**{text}**"
        if annotations.get("italic"):
            text = f"*{text}*"
        if annotations.get("strikethrough"):
            text = f"~~{text}~~"
        link = (item.get("text") or {}).get("link") or item.get("href")
        if item.get("type") == "mention":
            link = link or item.get("href")
        if link and link != text:
            url = link.get("url") if isinstance(link, dict) else link
            if url:
                text = f"[{text}]({url})"
        parts.append(text)
    return "".join(parts)


def _quote_block(lines: list[str]) -> list[str]:
    return [f"> {line}" if line else ">" for line in lines]


# ---------------------------------------------------------------------------
# blocks → markdown
# ---------------------------------------------------------------------------
def blocks_to_markdown(client, blocks: list[dict], *, depth: int = 0) -> str:
    """把一组 block 渲染成 Markdown，递归展开子块。"""
    if depth > MAX_INLINE_DEPTH:
        return "<!-- 递归过深，已截断 -->\n"
    out: list[str] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        btype = block.get("type") or "unsupported"
        data = block.get(btype) or {}

        if btype == "numbered_list_item":
            # 连续的 numbered_list_item 重新编号
            start = index
            counter = 1
            while index < len(blocks) and blocks[index].get("type") == "numbered_list_item":
                item = blocks[index]
                payload = item.get("numbered_list_item") or {}
                indent = "  " * depth
                out.append(f"{indent}{counter}. {_inline(payload.get('rich_text'))}")
                out.extend(_child_lines(client, item, depth + 1))
                counter += 1
                index += 1
            out.append("")
            if index == start:
                index += 1
            continue

        rendered = _render_block(client, block, btype, data, depth)
        if rendered:
            out.extend(rendered)
        index += 1
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def _child_lines(client, block: dict, depth: int) -> list[str]:
    if not block.get("has_children"):
        return []
    children = client.list_children(block["id"])
    if not children:
        return []
    text = blocks_to_markdown(client, children, depth=depth).rstrip("\n")
    return text.splitlines()


def _render_block(client, block: dict, btype: str, data: dict, depth: int) -> list[str]:
    indent = "  " * depth
    text = _inline(data.get("rich_text"))

    if btype == "paragraph":
        return [indent + text] if text else ([] if not block.get("has_children")
                                             else _child_lines(client, block, depth))
    if btype in ("heading_1", "heading_2", "heading_3"):
        level = int(btype[-1])
        return [f"{'#' * level} {text}"]
    if btype == "bulleted_list_item":
        return [f"{indent}- {text}"] + _child_lines(client, block, depth + 1)
    if btype == "to_do":
        mark = "x" if data.get("checked") else " "
        return [f"{indent}- [{mark}] {text}"] + _child_lines(client, block, depth + 1)
    if btype == "toggle":
        # 折叠块用 <details>/<summary> 表达：纯 Markdown 没有折叠语义，
        # 若渲染成 "- 文本" 就会在写回时退化成普通列表项。
        inner = _child_lines(client, block, 0)
        return (["<details>", f"<summary>{text}</summary>"]
                + ["\t" + ln if ln else "" for ln in inner]
                + ["</details>"])
    if btype == "quote":
        return _quote_block(text.splitlines() or [""]) + _child_lines(client, block, depth)
    if btype == "callout":
        emoji = (data.get("icon") or {}).get("emoji", "")
        inner = ([text] if text else []) + _child_lines(client, block, 0)
        return ([f'<callout icon="{emoji}">']
                + ["\t" + ln if ln else "" for ln in inner]
                + ["</callout>"])
    if btype == "code":
        language = data.get("language") or ""
        return [f"```{language}", text, "```"]
    if btype == "divider":
        return ["---"]
    if btype == "equation":
        return [f"$$ {data.get('expression', '')} $$"]
    if btype in ("image", "video", "audio", "file", "pdf"):
        payload = data.get(data.get("type") or btype) or {}
        url = payload.get("url") or ""
        caption = _inline(data.get("caption"))
        label = caption or url or btype
        return [f"![{label}]({url})" if btype == "image" else f"[{label}]({url})"]
    if btype == "bookmark":
        return [f"[{_inline(data.get('caption')) or data.get('url', '')}]({data.get('url', '')})"]
    if btype == "embed":
        return [f"<{data.get('url', '')}>"]
    if btype == "link_preview":
        return [f"<{data.get('url', '')}>"]
    if btype == "child_page":
        return [f"- 📄 [{data.get('title') or block.get('id')}]"
                f"(https://www.notion.so/{str(block.get('id')).replace('-', '')})"]
    if btype == "child_database":
        return [f"- 🗄️ {data.get('title') or block.get('id')}"]
    if btype == "table":
        return _render_table(client, block)
    if btype == "column_list":
        return _render_column_list(client, block)
    if btype == "column":
        # 游离的 column（少见）：只渲染内容，不额外加标记
        return _child_lines(client, block, depth)
    if btype == "synced_block":
        return _child_lines(client, block, depth)
    if btype == "template":
        return _child_lines(client, block, depth)
    if btype == "unsupported":
        return ["<!-- 不支持的块类型 -->"]
    # 兜底：尽量不丢内容
    if text:
        return [f"{indent}{text}"]
    return _child_lines(client, block, depth)


def _render_table(client, block: dict) -> list[str]:
    rows = client.list_children(block["id"])
    if not rows:
        return []
    lines: list[str] = []
    for position, row in enumerate(rows):
        cells = (row.get("table_row") or {}).get("cells") or []
        rendered = [_inline(cell).replace("|", "\\|") for cell in cells]
        lines.append("| " + " | ".join(rendered) + " |")
        if position == 0 and (block.get("table") or {}).get("has_column_header"):
            lines.append("| " + " | ".join("---" for _ in rendered) + " |")
    return lines


def _render_column_list(client, block: dict) -> list[str]:
    """把 column_list 渲染成 <columns>/<column> 结构。

    纯 Markdown 没有分栏语义；若像以前那样把各列拍平，写回时就再也拼不回分栏。
    这里采用与官方 Notion MCP 一致的标记，因此同一份文本两边都认。
    """
    columns = client.list_children(block["id"])
    lines = ["<columns>"]
    for col in columns:
        ratio = (col.get("column") or {}).get("width_ratio")
        attr = ""
        if ratio:
            try:
                attr = f' ratio="{round(float(ratio) * 100)}"'
            except (TypeError, ValueError):
                attr = ""
        lines.append(f"\t<column{attr}>")
        inner = blocks_to_markdown(client, client.list_children(col["id"])).rstrip("\n")
        for line in inner.splitlines():
            lines.append("\t\t" + line if line.strip() else "")
        lines.append("\t</column>")
    lines.append("</columns>")
    return lines


def page_to_markdown(client, page_id: str, *, include_title: bool = True) -> str:
    """把整页渲染成 Markdown。"""
    page = client.get_page(page_id)
    body = blocks_to_markdown(client, client.list_children(page_id))
    if not include_title:
        return body
    from .client import page_title

    title = page_title(page)
    header = f"# {title}\n\n" if title else ""
    return header + body


# ---------------------------------------------------------------------------
# markdown → blocks
# ---------------------------------------------------------------------------
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_TODO_RE = re.compile(r"^(\s*)[-*+]\s+\[([ xX])\]\s*(.*)$")
_NUMBER_RE = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_FENCE_RE = re.compile(r"^\s*```(\w*)\s*$")
_DIVIDER_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")

# 结构化块：这些标记与官方 Notion MCP 的写法一致，因此同一份文本两边都认
_TABLE_ROW_RE = re.compile(r"^\s*\|")
_COLUMNS_OPEN_RE = re.compile(r"^\s*<columns>\s*$")
_COLUMNS_CLOSE_RE = re.compile(r"^\s*</columns>\s*$")
_COLUMN_OPEN_RE = re.compile(r"^\s*<column(?P<attrs>[^>]*)>\s*$")
_COLUMN_CLOSE_RE = re.compile(r"^\s*</column>\s*$")
_CALLOUT_OPEN_RE = re.compile(r"^\s*<callout(?P<attrs>[^>]*)>\s*$")
_CALLOUT_CLOSE_RE = re.compile(r"^\s*</callout>\s*$")
_DETAILS_OPEN_RE = re.compile(r"^\s*<details>\s*$")
_DETAILS_CLOSE_RE = re.compile(r"^\s*</details>\s*$")
_SUMMARY_RE = re.compile(r"^\s*<summary>(?P<text>.*?)</summary>\s*$")
_EQUATION_RE = re.compile(r"^\s*\$\$\s*(?P<expr>.*?)\s*\$\$\s*$")
_IMAGE_RE = re.compile(r"^\s*!\[(?P<alt>[^\]]*)\]\((?P<url>[^)\s]+)\)\s*$")
_SEPARATOR_CELL_RE = re.compile(r"^:?-{2,}:?$")


def _rt(text: str) -> list[dict]:
    return [{"type": "text", "text": {"content": text}}] if text else []


def _block(btype: str, payload: dict, children: list[dict] | None = None) -> dict:
    item: dict[str, Any] = {"object": "block", "type": btype, btype: payload}
    if children:
        item[btype]["children"] = children
    return item


# -- 结构化块的解析辅助 -----------------------------------------------------
def _attr_value(attrs: str, name: str) -> str | None:
    match = re.search(rf'{name}\s*=\s*"([^"]*)"', attrs or "")
    return match.group(1) if match else None


def _dedent(lines: list[str]) -> list[str]:
    """按最小公共缩进去掉一级缩进（读回的列/折叠块内容都带一层 tab）。"""
    indents = [len(line) - len(line.lstrip(" \t")) for line in lines if line.strip()]
    if not indents:
        return lines
    cut = min(indents)
    return [line[cut:] if len(line) >= cut else line for line in lines]


def _consume_tagged(lines: list[str], index: int, open_re, close_re) -> tuple[list[str], int]:
    """从开标签行之后收集内容，直到配对的闭标签；支持同名嵌套。"""
    inside: list[str] = []
    depth = 1
    index += 1
    while index < len(lines):
        line = lines[index]
        if open_re.match(line):
            depth += 1
        elif close_re.match(line):
            depth -= 1
            if depth == 0:
                return inside, index + 1
        inside.append(line)
        index += 1
    return inside, index


def _rich_text_of(block: dict) -> list[dict] | None:
    """取出一个块自己的 rich_text；容器型块（表格/分栏）返回 None。"""
    payload = block.get(block.get("type") or "")
    if isinstance(payload, dict) and isinstance(payload.get("rich_text"), list):
        return payload["rich_text"]
    return None


def _split_table_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", text)]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(
        _SEPARATOR_CELL_RE.match(cell.replace(" ", "")) for cell in cells
    )


def _parse_table(lines: list[str], index: int) -> tuple[dict | None, int]:
    raw: list[list[str]] = []
    while index < len(lines) and _TABLE_ROW_RE.match(lines[index]):
        raw.append(_split_table_row(lines[index]))
        index += 1
    if not raw:
        return None, index

    has_header = len(raw) >= 2 and _is_separator_row(raw[1])
    body = ([raw[0]] + raw[2:]) if has_header else raw
    width = max((len(row) for row in body), default=0)
    if width == 0:
        return None, index

    rows: list[dict] = []
    for row in body:
        cells = row + [""] * (width - len(row))
        rows.append(_block("table_row", {"cells": [
            ([{"type": "text", "text": {"content": cell}}] if cell else []) for cell in cells
        ]}))
    return _block("table", {"table_width": width, "has_column_header": has_header,
                            "has_row_header": False}, rows), index


def _parse_columns(inner: list[str]) -> dict | None:
    columns: list[dict] = []
    index = 0
    while index < len(inner):
        opened = _COLUMN_OPEN_RE.match(inner[index])
        if not opened:
            index += 1
            continue
        content, index = _consume_tagged(inner, index, _COLUMN_OPEN_RE, _COLUMN_CLOSE_RE)
        payload: dict[str, Any] = {}
        ratio = _attr_value(opened.group("attrs"), "ratio")
        if ratio:
            try:
                payload["width_ratio"] = round(float(ratio) / 100.0, 4)
            except ValueError:
                pass
        children = markdown_to_blocks("\n".join(_dedent(content)))
        if children:
            payload["children"] = children
        columns.append(_block("column", payload))
    if not columns:
        return None
    return _block("column_list", {}, columns)


def markdown_to_blocks(markdown: str) -> list[dict]:
    """把 Markdown 转成 Notion block 数组（写入用）。"""
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[dict] = []
    index = 0

    while index < len(lines):
        line = lines[index]

        # 围栏代码
        fence = _FENCE_RE.match(line)
        if fence:
            language = fence.group(1) or "plain text"
            index += 1
            buffer: list[str] = []
            while index < len(lines) and not _FENCE_RE.match(lines[index]):
                buffer.append(lines[index])
                index += 1
            index += 1  # 跳过收尾 ```
            blocks.append(_block("code", {
                "rich_text": _rt("\n".join(buffer)),
                "language": language,
            }))
            continue

        if not line.strip():
            index += 1
            continue

        if _DIVIDER_RE.match(line):
            blocks.append(_block("divider", {}))
            index += 1
            continue

        # -- 结构化块（顺序重要：先长标记，再行内标记）---------------------
        if _COLUMNS_OPEN_RE.match(line):
            inner, index = _consume_tagged(lines, index, _COLUMNS_OPEN_RE, _COLUMNS_CLOSE_RE)
            block = _parse_columns(inner)
            if block:
                blocks.append(block)
            continue

        opened = _CALLOUT_OPEN_RE.match(line)
        if opened:
            inner, index = _consume_tagged(lines, index, _CALLOUT_OPEN_RE, _CALLOUT_CLOSE_RE)
            children = markdown_to_blocks("\n".join(_dedent(inner)))
            payload: dict[str, Any] = {"rich_text": []}
            if children and _rich_text_of(children[0]) is not None:
                payload["rich_text"] = _rich_text_of(children.pop(0)) or []
            icon = _attr_value(opened.group("attrs"), "icon")
            if icon:
                payload["icon"] = {"type": "emoji", "emoji": icon}
            if children:
                payload["children"] = children
            blocks.append(_block("callout", payload))
            continue

        if _DETAILS_OPEN_RE.match(line):
            inner, index = _consume_tagged(lines, index, _DETAILS_OPEN_RE, _DETAILS_CLOSE_RE)
            content = _dedent(inner)
            summary = ""
            if content and _SUMMARY_RE.match(content[0]):
                summary = _SUMMARY_RE.match(content[0]).group("text")
                content = content[1:]
            children = markdown_to_blocks("\n".join(content))
            blocks.append(_block("toggle", {"rich_text": _rt(summary)}, children))
            continue

        if _TABLE_ROW_RE.match(line):
            block, index = _parse_table(lines, index)
            if block:
                blocks.append(block)
            continue

        equation = _EQUATION_RE.match(line)
        if equation:
            blocks.append(_block("equation", {"expression": equation.group("expr")}))
            index += 1
            continue

        image = _IMAGE_RE.match(line)
        if image:
            blocks.append(_block("image", {
                "type": "external",
                "external": {"url": image.group("url")},
                "caption": _rt(image.group("alt")),
            }))
            index += 1
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            blocks.append(_block(f"heading_{level}", {"rich_text": _rt(heading.group(2).strip())}))
            index += 1
            continue

        quote = _QUOTE_RE.match(line)
        if quote:
            buffer = []
            while index < len(lines) and _QUOTE_RE.match(lines[index]):
                buffer.append(_QUOTE_RE.match(lines[index]).group(1))
                index += 1
            blocks.append(_block("quote", {"rich_text": _rt("\n".join(buffer))}))
            continue

        # 列表（含缩进嵌套）
        if (_BULLET_RE.match(line) or _NUMBER_RE.match(line)):
            index = _consume_list(lines, index, blocks, parent_indent=-1)
            continue

        # 普通段落：吞并到下一个空行或块级语法
        buffer = [line.strip()]
        index += 1
        while index < len(lines):
            nxt = lines[index]
            if (not nxt.strip() or _HEADING_RE.match(nxt) or _FENCE_RE.match(nxt)
                    or _DIVIDER_RE.match(nxt) or _QUOTE_RE.match(nxt)
                    or _BULLET_RE.match(nxt) or _NUMBER_RE.match(nxt)
                    or _TABLE_ROW_RE.match(nxt) or _COLUMNS_OPEN_RE.match(nxt)
                    or _CALLOUT_OPEN_RE.match(nxt) or _DETAILS_OPEN_RE.match(nxt)
                    or _EQUATION_RE.match(nxt) or _IMAGE_RE.match(nxt)):
                break
            buffer.append(nxt.strip())
            index += 1
        blocks.append(_block("paragraph", {"rich_text": _rt(" ".join(buffer))}))

    return blocks


def _indent_of(line: str) -> int:
    stripped = line.expandtabs(INDENT_UNIT)
    return len(stripped) - len(stripped.lstrip(" "))


def _consume_list(lines: list[str], index: int, out: list[dict], parent_indent: int) -> int:
    """消费一段（可能嵌套的）列表，追加到 ``out``，返回新的行号。"""
    siblings: list[dict] = []
    base_indent: int | None = None

    while index < len(lines):
        line = lines[index]
        todo = _TODO_RE.match(line)
        bullet = _BULLET_RE.match(line)
        number = _NUMBER_RE.match(line)
        if not (todo or bullet or number):
            break
        indent = _indent_of(line)
        if base_indent is None:
            base_indent = indent
        if indent < base_indent:
            break
        if indent > base_indent:
            # 交给上一层递归，挂到最后一个同级项下
            if not siblings:
                break
            nested_out: list[dict] = []
            index = _consume_list(lines, index, nested_out, parent_indent=base_indent)
            last = siblings[-1]
            last_type = last["type"]
            last[last_type].setdefault("children", []).extend(nested_out)
            continue

        if todo:
            mark, text = todo.group(2), todo.group(3)
            node = _block("to_do", {"rich_text": _rt(text), "checked": mark.lower() == "x"})
        elif number:
            node = _block("numbered_list_item", {"rich_text": _rt(number.group(2))})
        else:
            node = _block("bulleted_list_item", {"rich_text": _rt(bullet.group(2))})
        siblings.append(node)
        index += 1

    out.extend(siblings)
    return index
