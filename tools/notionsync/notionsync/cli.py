"""notionsync.cli — 命令行入口。

用法概览::

    python -m notionsync doctor
    python -m notionsync ws
    python -m notionsync search "OpenGL" --workspace B
    python -m notionsync cat <页面URL或ID>
    python -m notionsync ls <页面或数据库>
    python -m notionsync new --parent <ID> --title "标题" --file body.md
    python -m notionsync append <页面> --file body.md
    python -m notionsync write <页面> --file body.md      # 覆盖正文
    python -m notionsync title <页面> "新标题"
    python -m notionsync comment <页面> --text "内容"
    python -m notionsync comments <页面>
    python -m notionsync db <数据库>
    python -m notionsync query <数据库> --filter '{"property":"Topic","select":{"equals":"AI"}}'
    python -m notionsync serve                            # 跑 stdio MCP 服务器
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import __version__, service
from .config import ConfigError


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _read_file(path: str | None) -> str | None:
    if not path:
        return None
    if path == "-":
        return sys.stdin.read()
    return pathlib.Path(path).read_text(encoding="utf-8")


def _emit(value, as_json: bool) -> int:
    """输出结果；出现 error 字段时返回非零退出码。"""
    if as_json:
        _print_json(value)
    else:
        _render(value)
    if isinstance(value, dict) and value.get("error"):
        print(f"错误：{value['error']}", file=sys.stderr)
        return 1
    return 0


def _render(value) -> None:
    """把 service 返回的结构渲染成人读得懂的文本。"""
    if isinstance(value, str):
        print(value)
        return
    if not isinstance(value, dict):
        _print_json(value)
        return

    if "workspaces" in value:
        for item in value["workspaces"]:
            state = "OK " if item.get("reachable") else "FAIL"
            name = item.get("workspaceName") or item.get("error") or ""
            print(f"[{state}] {item['key']:<4} {item.get('label', ''):<24} {name}")
        print(f"配置文件：{value.get('configSource')}")
        return

    if "markdown" in value:
        header = value.get("title") or value.get("block_id") or ""
        if header:
            print(f"# 来源：{header}")
        print(value["markdown"])
        return

    if "results" in value:
        results = value["results"]
        print(f"共 {value.get('count', len(results))} 条（空间 {value.get('workspace')}）")
        for item in results:
            title = item.get("title") or item.get("text") or ""
            print(f"- {title}  <{item.get('url') or item.get('id')}>")
        return

    if "data_sources" in value:
        print(f"数据库：{value.get('title') or value.get('database_id')}")
        for source in value["data_sources"]:
            print(f"  data source: {source.get('name')}  {source.get('id')}")
        return

    _print_json(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="notionsync",
        description="Notion 多工作空间读写工具（Python / DSH / Trae 共用）",
    )
    parser.add_argument("--version", action="version", version=f"notionsync {__version__}")
    parser.add_argument("--config", help="配置文件路径（默认 ~/.notionsync/workspaces.json）")
    parser.add_argument("-w", "--workspace", help="工作空间 key，如 A / B")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="自检：配置、凭据、连通性、工具面")
    sub.add_parser("ws", help="列出已配置的工作空间")
    sub.add_parser("workspaces", help="同 ws")
    sub.add_parser("serve", help="以 stdio 运行 MCP 服务器")

    p = sub.add_parser("search", help="搜索页面或数据库")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--type", choices=["page", "data_source"])
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("cat", help="读取页面正文（Markdown）")
    p.add_argument("page")
    p.add_argument("--no-title", action="store_true")

    p = sub.add_parser("ls", help="列出页面子页 / 数据库行")
    p.add_argument("target")
    p.add_argument("--limit", type=int, default=100)

    p = sub.add_parser("new", help="新建页面或数据库行")
    p.add_argument("--parent", help="父页面 / data source 的 ID 或 URL；--type workspace 时可省略")
    p.add_argument("--title", required=True)
    p.add_argument("--type", choices=["page", "data_source", "workspace"], default="page")
    p.add_argument("--file", help="正文 Markdown 文件；- 表示 stdin")
    p.add_argument("--title-property", dest="title_property",
                   help="写数据库行时的标题属性名（如「名称」）；留空自动读 schema")
    p.add_argument("--icon")

    p = sub.add_parser("append", help="向页面末尾追加 Markdown")
    p.add_argument("target")
    p.add_argument("--file", required=True)

    p = sub.add_parser("write", help="覆盖页面正文")
    p.add_argument("target")
    p.add_argument("--file", required=True)

    p = sub.add_parser("title", help="修改页面标题")
    p.add_argument("target")
    p.add_argument("title")

    p = sub.add_parser("comment", help="发表评论")
    p.add_argument("target")
    p.add_argument("--text", required=True)

    p = sub.add_parser("comments", help="列出评论")
    p.add_argument("target")

    p = sub.add_parser("db", help="查看数据库结构")
    p.add_argument("target")

    p = sub.add_parser("mkdb", help="在页面下新建数据库")
    p.add_argument("--parent", required=True, help="父页面 ID 或 URL")
    p.add_argument("--title", required=True)
    p.add_argument("--schema", required=True,
                   help='属性 schema 的 JSON，如 \'{"名称":{"title":{}}}\'')
    p.add_argument("--icon")

    p = sub.add_parser("query", help="查询数据库")
    p.add_argument("target")
    p.add_argument("--filter", help="Notion filter 的 JSON")
    p.add_argument("--sorts", help="Notion sorts 的 JSON 数组")
    p.add_argument("--limit", type=int, default=100)

    p = sub.add_parser("rm-block", help="删除一个块")
    p.add_argument("block")

    p = sub.add_parser("trash", help="把页面移入回收站")
    p.add_argument("target")

    p = sub.add_parser("restore", help="从回收站还原页面")
    p.add_argument("target")

    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)

    if args.command == "serve":
        from .mcp import main as serve_main

        return serve_main()

    try:
        registry = service.make_registry(args.config)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    workspace = args.workspace
    as_json = args.json
    command = args.command

    if command in ("ws", "workspaces"):
        return _emit({"configSource": registry.source,
                      "workspaces": service.workspaces_info(registry)}, as_json)

    if command == "doctor":
        return _run_doctor(registry, as_json)

    if command == "search":
        return _emit(service.search(registry, workspace, args.query, args.type, args.limit),
                     as_json)
    if command == "cat":
        return _emit(service.read_page(registry, workspace, args.page,
                                       not args.no_title), as_json)
    if command == "ls":
        return _emit(_list(registry, workspace, args.target, args.limit), as_json)
    if command == "new":
        return _emit(service.create_page(
            registry, workspace, args.parent, args.title, args.type,
            _read_file(args.file), args.icon, args.title_property), as_json)
    if command == "append":
        return _emit(service.append_markdown(registry, workspace, args.target,
                                             _read_file(args.file) or ""), as_json)
    if command == "write":
        return _emit(service.replace_page_content(registry, workspace, args.target,
                                                  _read_file(args.file) or ""), as_json)
    if command == "title":
        return _emit(service.set_title(registry, workspace, args.target, args.title), as_json)
    if command == "comment":
        return _emit(service.create_comment(registry, workspace, args.target, args.text),
                     as_json)
    if command == "comments":
        return _emit(service.list_comments(registry, workspace, args.target), as_json)
    if command == "db":
        return _emit(service.describe_database(registry, workspace, args.target), as_json)
    if command == "mkdb":
        return _emit(service.create_database(registry, workspace, args.parent, args.title,
                                             args.schema, args.icon), as_json)
    if command == "query":
        filter_obj = json.loads(args.filter) if args.filter else None
        sorts_obj = json.loads(args.sorts) if args.sorts else None
        return _emit(service.query_database(registry, workspace, args.target, filter_obj,
                                            sorts_obj, args.limit), as_json)
    if command == "rm-block":
        return _emit(service.delete_block(registry, workspace, args.block), as_json)
    if command == "trash":
        return _emit(service.archive_page(registry, workspace, args.target, False), as_json)
    if command == "restore":
        return _emit(service.archive_page(registry, workspace, args.target, True), as_json)

    print(f"未知命令：{command}", file=sys.stderr)
    return 2


def _list(registry, workspace, target: str, limit: int):
    """智能 ls：是数据库就查行，否则列子页。"""
    described = service.describe_database(registry, workspace, target)
    if isinstance(described, dict) and described.get("data_sources"):
        rows = service.query_database(registry, workspace, target, None, None, limit)
        rows["object"] = "database"
        rows["title"] = described.get("title")
        return rows
    read = service.read_block(registry, workspace, target)
    read["object"] = "page"
    return read


def _run_doctor(registry, as_json: bool) -> int:
    """逐项自检，返回 0/1。"""
    from .client import NotionClient, NotionError
    from .mcp import TOOLS

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    check("配置文件", bool(registry.source), registry.source)
    check("工作空间数量", len(registry) > 0, f"{len(registry)} 个：{', '.join(registry.keys)}")
    check("MCP 工具面", len(TOOLS) >= 10, f"{len(TOOLS)} 个工具")

    for ws in registry.all():
        try:
            client = NotionClient(ws.token, api_version=ws.api_version, key=ws.key)
            info = client.self_info()
            name = client.workspace_name
            check(f"空间 {ws.key} 凭据", True, f"workspace={name!r} object={info.get('object')}")
            try:
                page = client.search("", object_type="page", page_size=5)
                count = len(page.get("results") or [])
                check(f"空间 {ws.key} 读取", True, f"search 返回 {count} 条")
            except NotionError as exc:
                check(f"空间 {ws.key} 读取", False, str(exc))
        except ValueError as exc:
            check(f"空间 {ws.key} 凭据", False, str(exc))
        except NotionError as exc:
            detail = str(exc)
            hint = "（Token 失效或未授权）" if exc.unauthorized else ""
            check(f"空间 {ws.key} 凭据", False, detail + hint)
        except Exception as exc:  # noqa: BLE001
            check(f"空间 {ws.key} 凭据", False, f"{type(exc).__name__}: {exc}")

    if as_json:
        _print_json({"configSource": registry.source,
                     "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in checks]})
    else:
        for name, ok, detail in checks:
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        passed = sum(1 for _, ok, _ in checks if ok)
        print(f"\n结果：{passed}/{len(checks)} 通过")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
