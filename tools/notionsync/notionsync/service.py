"""notionsync.service — CLI 与 MCP server 共用的业务层。

这一层只做「把参数变成结果」，不关心调用方是命令行还是 MCP，因此两条入口
的行为必然一致。
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from .client import NotionClient, NotionError, extract_id, page_title, plain_text
from .config import ConfigError, Workspace, WorkspaceRegistry, load_registry
from .markdown import blocks_to_markdown, markdown_to_blocks, page_to_markdown

OBJECT_TYPES = ("page", "data_source")


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------
def make_registry(config_path: str | pathlib.Path | None = None) -> WorkspaceRegistry:
    path = pathlib.Path(config_path) if config_path else None
    return load_registry(path)


def client_for(registry: WorkspaceRegistry, workspace: str | None = None) -> NotionClient:
    ws: Workspace = registry.resolve(workspace)
    return NotionClient(ws.token, api_version=ws.api_version, key=ws.key, label=ws.label)


def redact(text: str, registry: WorkspaceRegistry | None = None) -> str:
    """从任意文本中抹掉 token 明文，避免错误信息泄漏凭据。"""
    if registry is not None:
        for ws in registry.all():
            if ws.token:
                text = text.replace(ws.token, "***")
    return text


def _error(message: str, registry: WorkspaceRegistry | None = None) -> dict:
    return {"error": redact(message, registry)}


# ---------------------------------------------------------------------------
# 规范化输出
# ---------------------------------------------------------------------------
def normalize_search_result(item: dict) -> dict:
    object_type = item.get("object")
    if object_type == "data_source":
        title = plain_text(item.get("title")) or item.get("name") or ""
        url = item.get("url") or ""
    else:
        title = page_title(item)
        url = item.get("url") or ""
    return {
        "id": item.get("id"),
        "object": object_type,
        "title": title,
        "url": url,
        "parent": item.get("parent"),
        "last_edited_time": item.get("last_edited_time"),
    }


def summarize_row(row: dict) -> dict:
    """把数据库行压成可读摘要（保留原始 properties）。"""
    return {
        "id": row.get("id"),
        "title": page_title(row),
        "url": row.get("url"),
        "last_edited_time": row.get("last_edited_time"),
        "properties": row.get("properties"),
    }


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------
def workspaces_info(registry: WorkspaceRegistry) -> list[dict]:
    rows = []
    for ws in registry.all():
        entry = {"key": ws.key, "label": ws.label, "apiVersion": ws.api_version,
                 "configSource": registry.source, "reachable": None, "workspaceName": None,
                 "error": None}
        try:
            client = NotionClient(ws.token, api_version=ws.api_version, key=ws.key)
            entry["reachable"] = True
            entry["workspaceName"] = client.workspace_name
        except NotionError as exc:
            entry["reachable"] = False
            entry["error"] = redact(str(exc), registry)
        rows.append(entry)
    return rows


def search(registry: WorkspaceRegistry, workspace: str | None, query: str,
           object_type: str | None = None, limit: int = 50) -> dict:
    if object_type and object_type not in OBJECT_TYPES:
        return _error(f"object_type 只能是 {OBJECT_TYPES} 之一", registry)
    try:
        client = client_for(registry, workspace)
        items = client.search_all(query, object_type=object_type, limit=limit)
    except (NotionError, ConfigError, ValueError) as exc:
        return _error(str(exc), registry)
    return {
        "workspace": client.key,
        "query": query,
        "count": len(items),
        "results": [normalize_search_result(item) for item in items],
    }


def read_page(registry: WorkspaceRegistry, workspace: str | None, page_id: str,
              include_title: bool = True) -> dict:
    try:
        client = client_for(registry, workspace)
        page = client.get_page(page_id)
        body = blocks_to_markdown(client, client.list_children(page["id"]))
    except (NotionError, ConfigError, ValueError) as exc:
        return _error(str(exc), registry)
    title = page_title(page)
    markdown = f"# {title}\n\n{body}" if (include_title and title) else body
    return {
        "workspace": client.key,
        "page_id": page["id"],
        "title": title,
        "url": page.get("url"),
        "last_edited_time": page.get("last_edited_time"),
        "markdown": markdown,
    }


def read_block(registry: WorkspaceRegistry, workspace: str | None, block_id: str) -> dict:
    try:
        client = client_for(registry, workspace)
        blocks = client.list_children(block_id)
        markdown = blocks_to_markdown(client, blocks)
    except (NotionError, ConfigError, ValueError) as exc:
        return _error(str(exc), registry)
    return {"workspace": client.key, "block_id": extract_id(block_id), "markdown": markdown}


def list_databases(registry: WorkspaceRegistry, workspace: str | None, query: str = "",
                   limit: int = 50) -> dict:
    return search(registry, workspace, query, object_type="data_source", limit=limit)


def describe_database(registry: WorkspaceRegistry, workspace: str | None,
                      database_id: str) -> dict:
    try:
        client = client_for(registry, workspace)
        container = client.get_database(database_id)
        sources = []
        for item in container.get("data_sources") or []:
            sources.append({"id": item.get("id"), "name": item.get("name")})
    except NotionError as exc:
        # 传入的可能已经是 data source ID
        try:
            source = client.get_data_source(database_id)
        except (NotionError, ConfigError, ValueError):
            return _error(str(exc), registry)
        return {
            "workspace": client.key,
            "database_id": None,
            "data_source": source,
            "data_sources": [{"id": source.get("id"),
                              "name": plain_text(source.get("title")) or source.get("name")}],
        }
    except (ConfigError, ValueError) as exc:
        return _error(str(exc), registry)
    title = plain_text(container.get("title"))
    return {
        "workspace": client.key,
        "database_id": container.get("id"),
        "title": title,
        "url": container.get("url"),
        "data_sources": sources,
    }


def create_database(registry: WorkspaceRegistry, workspace: str | None, parent_page_id: str,
                    title: str, properties: Any, icon: str | None = None) -> dict:
    """在页面下新建数据库。``properties`` 是 data source 的属性 schema。"""
    if isinstance(properties, str):
        try:
            properties = json.loads(properties)
        except json.JSONDecodeError as exc:
            return _error(f"properties 不是合法 JSON：{exc}", registry)
    if not isinstance(properties, dict) or not properties:
        return _error("properties 必须是非空对象（data source 属性 schema）", registry)
    try:
        client = client_for(registry, workspace)
        database = client.create_database(parent_page_id, title, properties, icon=icon)
    except (NotionError, ConfigError, ValueError) as exc:
        return _error(str(exc), registry)
    return {
        "workspace": client.key,
        "database_id": database.get("id"),
        "url": database.get("url"),
        "title": title,
        "data_sources": [{"id": item.get("id"), "name": item.get("name")}
                         for item in (database.get("data_sources") or [])],
    }


def query_database(registry: WorkspaceRegistry, workspace: str | None,
                   database_id: str, filter: Any = None, sorts: Any = None,
                   limit: int = 100) -> dict:
    try:
        client = client_for(registry, workspace)
        source_ids = client.resolve_data_source_ids(database_id)
        rows: list[dict] = []
        for source_id in source_ids:
            rows.extend(client.query_all(source_id, filter=filter, sorts=sorts, limit=limit))
    except (NotionError, ConfigError, ValueError) as exc:
        return _error(str(exc), registry)
    rows = rows[:limit]
    return {
        "workspace": client.key,
        "data_sources": source_ids,
        "count": len(rows),
        "results": [summarize_row(row) for row in rows],
    }


def list_comments(registry: WorkspaceRegistry, workspace: str | None, page_id: str) -> dict:
    try:
        client = client_for(registry, workspace)
        comments = client.list_comments(page_id)
    except (NotionError, ConfigError, ValueError) as exc:
        return _error(str(exc), registry)
    return {
        "workspace": client.key,
        "page_id": extract_id(page_id),
        "count": len(comments),
        "results": [
            {
                "id": item.get("id"),
                "created_time": item.get("created_time"),
                "created_by": (item.get("created_by") or {}).get("id"),
                "text": plain_text(item.get("rich_text")),
            }
            for item in comments
        ],
    }


# ---------------------------------------------------------------------------
# 写
# ---------------------------------------------------------------------------
def create_page(registry: WorkspaceRegistry, workspace: str | None, parent_id: str | None,
                title: str, parent_type: str = "page", markdown: str | None = None,
                icon: str | None = None, title_property_name: str | None = None) -> dict:
    if parent_type not in ("page", "data_source", "workspace"):
        return _error("parent_type 只能是 page、data_source 或 workspace", registry)
    try:
        client = client_for(registry, workspace)
        children = markdown_to_blocks(markdown) if markdown else None
    except (ConfigError, ValueError) as exc:
        return _error(str(exc), registry)
    try:
        page = client.create_page(parent_id, title, parent_type=parent_type,
                                  children=children, icon=icon,
                                  title_property_name=title_property_name)
    except NotionError as exc:
        return _error(str(exc), registry)
    return {"workspace": client.key, "page_id": page.get("id"), "url": page.get("url"),
            "title": title, "blocks_written": len(children or [])}


def set_title(registry: WorkspaceRegistry, workspace: str | None, page_id: str,
              title: str) -> dict:
    try:
        client = client_for(registry, workspace)
        client.set_page_title(page_id, title)
    except (NotionError, ConfigError, ValueError) as exc:
        return _error(str(exc), registry)
    return {"workspace": client.key, "page_id": extract_id(page_id), "title": title, "ok": True}


def update_properties(registry: WorkspaceRegistry, workspace: str | None, page_id: str,
                      properties: Any) -> dict:
    if isinstance(properties, str):
        try:
            properties = json.loads(properties)
        except json.JSONDecodeError as exc:
            return _error(f"properties 不是合法 JSON：{exc}", registry)
    if not isinstance(properties, dict):
        return _error("properties 必须是对象", registry)
    try:
        client = client_for(registry, workspace)
        page = client.update_page(page_id, properties=properties)
    except (NotionError, ConfigError, ValueError) as exc:
        return _error(str(exc), registry)
    return {"workspace": client.key, "page_id": page.get("id"), "ok": True,
            "properties": page.get("properties")}


def append_markdown(registry: WorkspaceRegistry, workspace: str | None, block_id: str,
                    markdown: str) -> dict:
    try:
        client = client_for(registry, workspace)
        children = markdown_to_blocks(markdown)
        if not children:
            return _error("markdown 解析后没有任何块", registry)
        created = client.append_children(block_id, children)
    except (NotionError, ConfigError, ValueError) as exc:
        return _error(str(exc), registry)
    return {"workspace": client.key, "block_id": extract_id(block_id),
            "blocks_written": len(created), "ok": True}


def replace_page_content(registry: WorkspaceRegistry, workspace: str | None, page_id: str,
                         markdown: str) -> dict:
    """清空页面正文并写入新内容。标题（title 属性）不受影响。"""
    try:
        client = client_for(registry, workspace)
        children = markdown_to_blocks(markdown)
        removed = client.clear_page_content(page_id)
        created = client.append_children(page_id, children) if children else []
    except (NotionError, ConfigError, ValueError) as exc:
        return _error(str(exc), registry)
    return {"workspace": client.key, "page_id": extract_id(page_id), "removed": removed,
            "blocks_written": len(created), "ok": True}


def delete_block(registry: WorkspaceRegistry, workspace: str | None, block_id: str) -> dict:
    try:
        client = client_for(registry, workspace)
        client.delete_block(block_id)
    except (NotionError, ConfigError, ValueError) as exc:
        return _error(str(exc), registry)
    return {"workspace": client.key, "block_id": extract_id(block_id), "ok": True}


def archive_page(registry: WorkspaceRegistry, workspace: str | None, page_id: str,
                 restore: bool = False) -> dict:
    """把页面移入回收站；``restore=True`` 时还原。"""
    try:
        client = client_for(registry, workspace)
        page = client.update_page(page_id, in_trash=not restore)
    except (NotionError, ConfigError, ValueError) as exc:
        return _error(str(exc), registry)
    return {"workspace": client.key, "page_id": page.get("id"),
            "trashed": not restore, "ok": True}


def create_comment(registry: WorkspaceRegistry, workspace: str | None, page_id: str,
                   text: str) -> dict:
    try:
        client = client_for(registry, workspace)
        comment = client.create_comment(page_id, text)
    except (NotionError, ConfigError, ValueError) as exc:
        return _error(str(exc), registry)
    return {"workspace": client.key, "comment_id": comment.get("id"), "ok": True}
