"""atriumsync.formats — 抽取 **markdown 表达不了** 的格式信息。

为什么需要这一层
----------------
Notion / 飞书里有大量 Markdown 承载不了或会失真的东西：

* 富文本的**颜色**（Notion 的 ``annotations.color``：red / yellow_background / …）
* 高亮块的 **emoji 图标**与背景色
* 分栏的 **width_ratio**
* 表格的 ``table_width`` / 表头标志 / 列宽
* 代码块的 **caption**
* 页面 **icon / cover**
* 数据库属性的 **类型与选项颜色**（select 的 color、multi_select 的选项集合）
* 本转换器**尚未支持**的块类型（必须显式记下来，绝不能静默丢）

这些全部写进与 md 同级的 ``_formats/<名字>.formats.json``，供后续「格式排布优化」使用。
Markdown 本身能表达的东西（标题层级、粗斜体、列表、链接、代码语言、待办勾选、
分栏比例我们用 ``<column ratio>`` 表达）**不重复记录**，避免 sidecar 膨胀。
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

#: 这些块类型的内容已被 Markdown（含我们定义的扩展标记）完整承载
_MARKDOWN_NATIVE = {
    "paragraph", "heading_1", "heading_2", "heading_3",
    "bulleted_list_item", "numbered_list_item", "to_do", "quote",
    "code", "divider", "equation", "image", "video", "audio", "file", "pdf",
    "bookmark", "embed", "link_preview", "child_page", "child_database",
    "table", "table_row", "column_list", "column", "callout", "toggle",
    "template", "synced_block", "unsupported",
}


def _run_colors(rich: list[dict] | None) -> list[dict]:
    """只记录**颜色非默认**的文本片段（其余靠 Markdown 的粗斜体/链接表达）。"""
    out: list[dict] = []
    for index, item in enumerate(rich or []):
        annotations = item.get("annotations") or {}
        color = annotations.get("color")
        if color and color != "default":
            out.append({"run": index,
                        "text": item.get("plain_text") or "",
                        "color": color})
    return out


def block_extras(block: dict) -> dict | None:
    """返回单个块中 Markdown 承载不了的格式；没有则返回 None。"""
    btype = block.get("type") or "unsupported"
    data = block.get(btype) or {}
    extras: dict[str, Any] = {}

    colors = _run_colors(data.get("rich_text"))
    if colors:
        extras["rich_text_colors"] = colors

    if btype == "callout":
        if data.get("icon"):
            extras["icon"] = data["icon"]
        if data.get("color") and data["color"] != "default":
            extras["color"] = data["color"]
    elif btype == "column":
        if data.get("width_ratio") is not None:
            extras["width_ratio"] = data["width_ratio"]
    elif btype == "table":
        for key in ("table_width", "has_column_header", "has_row_header"):
            if data.get(key) is not None:
                extras[key] = data[key]
    elif btype == "code":
        if data.get("caption"):
            extras["caption"] = data["caption"]
    elif btype == "synced_block":
        if data.get("synced_from"):
            extras["synced_from"] = data["synced_from"]
    elif btype in ("image", "video", "audio", "file", "pdf"):
        if data.get("caption"):
            extras["caption"] = data["caption"]
        extras["source_type"] = data.get("type")
    elif btype in ("bookmark", "embed", "link_preview"):
        if data.get("caption"):
            extras["caption"] = data["caption"]

    # 转换器不认识/只做兜底处理的块，必须留原始载荷，否则等于静默丢内容
    if btype not in _MARKDOWN_NATIVE:
        extras["unsupported_block"] = True
        extras["raw"] = block.get(btype)

    return extras or None


#: 这些块类型**不是本文档的内容**，而是独立文档，必须停止递归。
#: （``Shirley's Knowledge Repo`` 就嵌了 child_page，早期版本会误把子页面的表格
#: 算进父页面的 sidecar，路径与归属全错。）
_SEPARATE_DOCUMENTS = {"child_page", "child_database"}


def collect_block_formats(client, block_id: str, *, path: str = "",
                          depth: int = 0) -> list[dict]:
    """遍历块树，返回 ``[{path, type, extras}, …]``；只保留有 extras 的块。

    ``path`` 用 ``0.2.1`` 这样的下标链，能精确定位到块（块 ID 会随编辑变化，下标不会）。
    **不进入 child_page / child_database**：那是别的文档，会有各自的 sidecar。
    """
    entries: list[dict] = []
    for index, block in enumerate(client.list_children(block_id)):
        here = f"{path}.{index}" if path else str(index)
        btype = block.get("type")
        extras = block_extras(block)
        if extras:
            entries.append({"path": here, "blockType": btype,
                            "blockId": block.get("id"), "extras": extras})
        if block.get("has_children") and (btype or "") not in _SEPARATE_DOCUMENTS:
            entries.extend(collect_block_formats(client, block["id"], path=here,
                                                 depth=depth + 1))
    return entries


def page_extras(page: dict) -> dict:
    """页面级、Markdown 承载不了的属性。"""
    out: dict[str, Any] = {}
    if page.get("icon"):
        out["icon"] = page["icon"]
    if page.get("cover"):
        out["cover"] = page["cover"]
    return out


def property_formats(properties: dict | None) -> dict:
    """数据库行的完整属性（含 select 选项颜色），供后续精确回写与排版使用。"""
    out: dict[str, Any] = {}
    for name, prop in (properties or {}).items():
        ptype = prop.get("type")
        entry: dict[str, Any] = {"type": ptype}
        if ptype == "select":
            select = prop.get("select") or {}
            entry["value"] = select.get("name")
            entry["color"] = select.get("color")
        elif ptype == "multi_select":
            entry["value"] = [{"name": o.get("name"), "color": o.get("color")}
                              for o in prop.get("multi_select") or []]
        elif ptype == "status":
            status = prop.get("status") or {}
            entry["value"] = status.get("name")
            entry["color"] = status.get("color")
        elif ptype in ("title", "rich_text"):
            runs = prop.get(ptype) or []
            entry["value"] = "".join(r.get("plain_text") or "" for r in runs)
            links = [r.get("href") for r in runs if r.get("href")]
            if links:
                entry["links"] = links
            colors = _run_colors(runs)
            if colors:
                entry["rich_text_colors"] = colors
        elif ptype == "number":
            entry["value"] = prop.get("number")
        elif ptype == "checkbox":
            entry["value"] = prop.get("checkbox")
        elif ptype == "url":
            entry["value"] = prop.get("url")
        elif ptype == "date":
            date = prop.get("date") or {}
            entry["value"] = date.get("start")
            if date.get("end"):
                entry["end"] = date["end"]
        elif ptype in ("people", "files", "relation", "formula", "rollup"):
            entry["value"] = prop.get(ptype)
        else:
            entry["value"] = prop.get(ptype)
        out[name] = entry
    return out


def write_formats(path: pathlib.Path, payload: dict) -> pathlib.Path:
    """把 sidecar 写到指定路径（UTF-8 无 BOM，缩进 2）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path
