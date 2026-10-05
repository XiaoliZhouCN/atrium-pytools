"""notionsync.mcp — 把 notionsync 的能力以 MCP stdio 服务器暴露出去。

为什么是 stdio + 自定义服务器
-----------------------------
Notion 的官方 MCP 走 OAuth，一次授权只能绑定**一个** workspace；DSH 侧的
``dsh-notion-mcp`` 又把凭据 ref 硬编码成 ``NOTION_OAUTH``，无法并存两套。本模块
自建服务器后，多个 workspace 的 Token 全部由本进程持有，DSH（经
``@deepseek-ai/dsh-mcp-client``）与 Trae（``mcp.json``）挂载同一个进程即可同时
读写 A、B 两个空间。

协议要点
--------
* stdio 传输使用**换行分隔的 JSON-RPC 2.0**，不是 Content-Length 分帧。
* stdout 只允许出现协议消息；一切日志走 stderr。
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from . import __version__, service
from .config import ConfigError, WorkspaceRegistry, load_registry

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {"2025-06-18", "2025-03-26", "2024-11-05"}

CONFIG_HINT = (
    "notionsync 还没有可用的工作空间配置。请在你的终端里运行：\n"
    "  python \"<仓库>\\tools\\notionsync\\setup_tokens.py\"\n"
    "它会写入 ~/.notionsync/workspaces.json，或用 NOTIONSYNC_CONFIG 指定别的路径。"
)

#: MCP stdio 规定消息为 UTF-8。Windows 上管道默认用 ANSI 代码页（cp936），
#: 中文描述会被客户端解成乱码，所以必须显式把流改成 UTF-8；万一改不动，
#: 退化成纯 ASCII 转义，保证语义不坏。
_JSON_KW: dict = {"ensure_ascii": False}


def force_utf8_streams() -> bool:
    """把 stdin/stdout 切到 UTF-8。返回是否成功。"""
    ok = True
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8", errors="strict")  # type: ignore[union-attr]
        except (AttributeError, OSError, ValueError):
            ok = False
    if not ok:
        _JSON_KW["ensure_ascii"] = True
    return ok

_WORKSPACE_PROP = {
    "type": "string",
    "description": "工作空间 key（如 A / B）。仅配置了一个空间时可省略。",
}


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TOOLS: list[dict] = [
    {
        "name": "workspaces_list",
        "description": "列出已配置的 Notion 工作空间（含 key、标签、Token 所属空间名与连通性）。",
        "inputSchema": _schema({}),
    },
    {
        "name": "notion_search",
        "description": "在指定工作空间内搜索页面或数据库（data source）。",
        "inputSchema": _schema(
            {
                "workspace": _WORKSPACE_PROP,
                "query": {"type": "string", "description": "关键词；留空则列出全部。"},
                "object_type": {"type": "string", "enum": ["page", "data_source"],
                                "description": "限定对象类型。"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            ["query"],
        ),
    },
    {
        "name": "notion_read_page",
        "description": "读取一个页面的完整正文，返回 Markdown（含标题）。",
        "inputSchema": _schema(
            {
                "workspace": _WORKSPACE_PROP,
                "page_id": {"type": "string", "description": "页面 ID 或 Notion URL。"},
                "include_title": {"type": "boolean", "default": True},
            },
            ["page_id"],
        ),
    },
    {
        "name": "notion_read_block",
        "description": "读取某个块的子块并渲染成 Markdown（用于局部读取）。",
        "inputSchema": _schema(
            {"workspace": _WORKSPACE_PROP,
             "block_id": {"type": "string", "description": "块 ID 或页面 ID。"}},
            ["block_id"],
        ),
    },
    {
        "name": "notion_create_page",
        "description": "新建页面：parent_type=page 时在页面下建子页，=data_source 时在数据库里建一行，"
                       "=workspace 时建工作空间级页面（PAT 会落在「私人」区，无需 parent_id）。",
        "inputSchema": _schema(
            {
                "workspace": _WORKSPACE_PROP,
                "parent_id": {"type": "string",
                              "description": "父页面 ID / data source ID；parent_type=workspace 时可省略。"},
                "parent_type": {"type": "string",
                                "enum": ["page", "data_source", "workspace"],
                                "default": "page"},
                "title": {"type": "string"},
                "markdown": {"type": "string", "description": "页面正文，Markdown 子集。"},
                "title_property_name": {
                    "type": "string",
                    "description": "写数据库行时的标题属性名（如「名称」）。留空则自动读取 schema。",
                },
                "icon": {"type": "string", "description": "emoji，可选。"},
            },
            ["title"],
        ),
    },
    {
        "name": "notion_set_title",
        "description": "修改页面标题。",
        "inputSchema": _schema(
            {"workspace": _WORKSPACE_PROP,
             "page_id": {"type": "string"},
             "title": {"type": "string"}},
            ["page_id", "title"],
        ),
    },
    {
        "name": "notion_update_properties",
        "description": "更新页面/数据库行的属性（Notion properties 原始 JSON）。",
        "inputSchema": _schema(
            {"workspace": _WORKSPACE_PROP,
             "page_id": {"type": "string"},
             "properties": {"type": "object", "description": "properties 对象。"}},
            ["page_id", "properties"],
        ),
    },
    {
        "name": "notion_append",
        "description": "向页面或块末尾追加内容（Markdown 子集）。",
        "inputSchema": _schema(
            {"workspace": _WORKSPACE_PROP,
             "block_id": {"type": "string", "description": "页面 ID 或块 ID。"},
             "markdown": {"type": "string"}},
            ["block_id", "markdown"],
        ),
    },
    {
        "name": "notion_replace_content",
        "description": "清空页面正文并写入新 Markdown（标题属性不变）。",
        "inputSchema": _schema(
            {"workspace": _WORKSPACE_PROP,
             "page_id": {"type": "string"},
             "markdown": {"type": "string"}},
            ["page_id", "markdown"],
        ),
    },
    {
        "name": "notion_delete_block",
        "description": "删除一个块（移入回收站）。",
        "inputSchema": _schema(
            {"workspace": _WORKSPACE_PROP, "block_id": {"type": "string"}},
            ["block_id"],
        ),
    },
    {
        "name": "notion_archive_page",
        "description": "把页面移入回收站，或用 restore=true 还原。",
        "inputSchema": _schema(
            {"workspace": _WORKSPACE_PROP,
             "page_id": {"type": "string"},
             "restore": {"type": "boolean", "default": False}},
            ["page_id"],
        ),
    },
    {
        "name": "notion_list_databases",
        "description": "列出数据库（data source），可选关键词过滤。",
        "inputSchema": _schema(
            {"workspace": _WORKSPACE_PROP,
             "query": {"type": "string", "default": ""},
             "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}},
        ),
    },
    {
        "name": "notion_describe_database",
        "description": "查看数据库容器的结构与其 data source 列表。",
        "inputSchema": _schema(
            {"workspace": _WORKSPACE_PROP,
             "database_id": {"type": "string", "description": "database 或 data source ID/URL。"}},
            ["database_id"],
        ),
    },
    {
        "name": "notion_create_database",
        "description": "在页面下新建数据库；properties 为 data source 的属性 schema。",
        "inputSchema": _schema(
            {
                "workspace": _WORKSPACE_PROP,
                "parent_page_id": {"type": "string", "description": "父页面 ID 或 URL。"},
                "title": {"type": "string"},
                "properties": {"type": "object",
                               "description": '属性 schema，如 {"名称":{"title":{}}}'},
                "icon": {"type": "string"},
            },
            ["parent_page_id", "title", "properties"],
        ),
    },
    {
        "name": "notion_query_database",
        "description": "查询数据库行；自动把 database ID 解析为 data source。",
        "inputSchema": _schema(
            {
                "workspace": _WORKSPACE_PROP,
                "database_id": {"type": "string"},
                "filter": {"type": "object", "description": "Notion filter 对象，可选。"},
                "sorts": {"type": "array", "items": {"type": "object"}},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            },
            ["database_id"],
        ),
    },
    {
        "name": "notion_list_comments",
        "description": "列出页面上的评论。",
        "inputSchema": _schema(
            {"workspace": _WORKSPACE_PROP, "page_id": {"type": "string"}},
            ["page_id"],
        ),
    },
    {
        "name": "notion_create_comment",
        "description": "在页面上发表评论。",
        "inputSchema": _schema(
            {"workspace": _WORKSPACE_PROP,
             "page_id": {"type": "string"},
             "text": {"type": "string"}},
            ["page_id", "text"],
        ),
    },
]

TOOL_NAMES = {tool["name"] for tool in TOOLS}


class ToolError(RuntimeError):
    pass


def _require(arguments: dict, name: str) -> Any:
    if name not in arguments or arguments[name] in (None, ""):
        raise ToolError(f"缺少必填参数 {name}")
    return arguments[name]


def dispatch(name: str, arguments: dict, registry: WorkspaceRegistry) -> Any:
    """执行一个工具调用，返回可 JSON 序列化的结果。"""
    args = arguments or {}
    workspace = args.get("workspace")

    if name == "workspaces_list":
        return {"configSource": registry.source, "workspaces": service.workspaces_info(registry)}
    if name == "notion_search":
        return service.search(registry, workspace, str(args.get("query") or ""),
                              args.get("object_type"), int(args.get("limit") or 50))
    if name == "notion_read_page":
        return service.read_page(registry, workspace, str(_require(args, "page_id")),
                                 bool(args.get("include_title", True)))
    if name == "notion_read_block":
        return service.read_block(registry, workspace, str(_require(args, "block_id")))
    if name == "notion_create_page":
        return service.create_page(
            registry, workspace, args.get("parent_id"),
            str(_require(args, "title")), str(args.get("parent_type") or "page"),
            args.get("markdown"), args.get("icon"), args.get("title_property_name"),
        )
    if name == "notion_set_title":
        return service.set_title(registry, workspace, str(_require(args, "page_id")),
                                 str(_require(args, "title")))
    if name == "notion_update_properties":
        return service.update_properties(registry, workspace, str(_require(args, "page_id")),
                                         _require(args, "properties"))
    if name == "notion_append":
        return service.append_markdown(registry, workspace, str(_require(args, "block_id")),
                                       str(_require(args, "markdown")))
    if name == "notion_replace_content":
        return service.replace_page_content(registry, workspace,
                                            str(_require(args, "page_id")),
                                            str(_require(args, "markdown")))
    if name == "notion_delete_block":
        return service.delete_block(registry, workspace, str(_require(args, "block_id")))
    if name == "notion_archive_page":
        return service.archive_page(registry, workspace, str(_require(args, "page_id")),
                                    bool(args.get("restore", False)))
    if name == "notion_list_databases":
        return service.list_databases(registry, workspace, str(args.get("query") or ""),
                                      int(args.get("limit") or 50))
    if name == "notion_describe_database":
        return service.describe_database(registry, workspace,
                                         str(_require(args, "database_id")))
    if name == "notion_create_database":
        return service.create_database(registry, workspace,
                                       str(_require(args, "parent_page_id")),
                                       str(_require(args, "title")),
                                       _require(args, "properties"), args.get("icon"))
    if name == "notion_query_database":
        return service.query_database(registry, workspace, str(_require(args, "database_id")),
                                      args.get("filter"), args.get("sorts"),
                                      int(args.get("limit") or 100))
    if name == "notion_list_comments":
        return service.list_comments(registry, workspace, str(_require(args, "page_id")))
    if name == "notion_create_comment":
        return service.create_comment(registry, workspace, str(_require(args, "page_id")),
                                      str(_require(args, "text")))
    raise ToolError(f"未知工具 {name}")


# ---------------------------------------------------------------------------
# JSON-RPC / MCP 循环
# ---------------------------------------------------------------------------
def _result(request_id: Any, payload: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_text(value: Any) -> str:
    return json.dumps(value, indent=2, default=str, **_JSON_KW)


def handle_message(message: dict, registry: WorkspaceRegistry | None) -> dict | None:
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        requested = str(params.get("protocolVersion") or PROTOCOL_VERSION)
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        return _result(request_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "notionsync", "version": __version__},
        })
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        # 配置缺失时仍然保持服务器存活、工具面完整，只在调用点报明确错误。
        # 这样 DSH / Trae 永远能正常挂载本服务器，不会因为还没填 Token 就挂掉。
        if registry is None:
            return _result(request_id, {
                "content": [{"type": "text", "text": CONFIG_HINT}], "isError": True,
            })
        if name not in TOOL_NAMES:
            return _result(request_id, {
                "content": [{"type": "text", "text": f"未知工具：{name}"}],
                "isError": True,
            })
        try:
            payload = dispatch(name, arguments, registry)
        except ToolError as exc:
            return _result(request_id, {
                "content": [{"type": "text", "text": str(exc)}], "isError": True,
            })
        except ConfigError as exc:
            return _result(request_id, {
                "content": [{"type": "text", "text": f"配置错误：{exc}"}], "isError": True,
            })
        except ValueError as exc:
            return _result(request_id, {
                "content": [{"type": "text", "text": f"参数错误：{exc}"}], "isError": True,
            })
        except Exception as exc:  # noqa: BLE001 - 绝不让工具异常打断服务器
            return _result(request_id, {
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                "isError": True,
            })
        is_error = isinstance(payload, dict) and "error" in payload and len(payload) <= 2
        return _result(request_id, {
            "content": [{"type": "text", "text": _tool_text(payload)}],
            "isError": bool(is_error),
        })
    if method in ("resources/list", "prompts/list"):
        key = "resources" if method.startswith("resources") else "prompts"
        return _result(request_id, {key: []})
    return _error(request_id, -32601, f"不支持的方法：{method}")


def serve(instream: TextIO | None = None, outstream: TextIO | None = None,
          registry: WorkspaceRegistry | None = None) -> int:
    """运行 stdio 循环，直到 stdin 关闭。

    配置缺失**不会**终止进程：工具体仍然完整挂载，调用时返回可读的配置提示。
    """
    instream = instream or sys.stdin
    outstream = outstream or sys.stdout
    if registry is None:
        try:
            registry = load_registry()
        except ConfigError as exc:
            registry = None
            print(f"[notionsync] 尚无可用配置：{exc}", file=sys.stderr)

    for raw in instream:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            outstream.write(json.dumps(_error(None, -32700, "JSON 解析失败"), **_JSON_KW) + "\n")
            outstream.flush()
            continue
        if not isinstance(message, dict):
            continue
        if registry is None:
            # 配置可能在本服务器启动之后才写入（先接线、后填 Token 是常态）。
            # 每个请求都重试一次读取，这样填好凭据后**无需重启** DSH / Trae。
            try:
                registry = load_registry()
                print("[notionsync] 已加载工作空间配置", file=sys.stderr)
            except ConfigError:
                pass
        try:
            response = handle_message(message, registry)
        except Exception as exc:  # noqa: BLE001
            response = _error(message.get("id"), -32603, f"内部错误：{exc}")
        if response is not None:
            outstream.write(json.dumps(response, **_JSON_KW) + "\n")
            outstream.flush()
    return 0


def main() -> int:
    force_utf8_streams()
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
