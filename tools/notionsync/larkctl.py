"""larksync / larkctl.py — 飞书云文档命令行。

用法::

    python larkctl.py status                       # 就绪度：CLI / 应用配置 / 身份授权
    python larkctl.py node <wiki/docx URL>         # 解析链接 → node_token/obj_token/标题
    python larkctl.py outline <URL>                # 只看目录
    python larkctl.py fetch <URL> --out doc.xml    # 抓正文（默认 XML）
    python larkctl.py fetch <URL> --format markdown --out doc.md
    python larkctl.py create --file body.xml       # 新建文档（XML 默认）
    python larkctl.py append <URL> --file body.xml # 追加内容
    python larkctl.py update <URL> --command block_replace --block-id blkX --file new.xml
    python larkctl.py rm <document_id> --type docx --yes

认证（两步，均需浏览器确认）::

    lark-cli config init --new     # 第 1 步：配置应用
    lark-cli auth login            # 第 2 步：以你本人身份授权

本 CLI 不打印任何密钥。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))

from larksync import LarkCliError, LarkClient   # noqa: E402

TOOL_DIR = HERE.parent
DEFAULT_STATE = TOOL_DIR / "temp" / "feishu_login_state.json"

#: 读/写云文档必需的最小 scope 集。
#: 注意：飞书授权**整批成败**——一次要 50 个 scope，只要一个给不了就一个都不给，
#: 所以这里只列真正用得到的。实测中权限会按需增长（补上 node:retrieve 后又会缺
#: node:read 等），因此这里一次把 wiki / docx / space 的相关项都列上。
DEFAULT_SCOPES = ",".join([
    "wiki:wiki",                   # 查看知识库
    "wiki:wiki:readonly",
    "wiki:node:read",              # 读知识库节点
    "wiki:node:retrieve",          # 解析知识库链接
    "docs:document.content:read",  # 读文档正文
    "docx:document:readonly",      # 读新版文档
    "docx:document:create",        # 建草稿
    "docx:document:write_only",    # 写回
    "space:document:delete",       # 回收草稿
])


def emit(value, as_json: bool) -> int:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    if isinstance(value, dict) and value.get("ok") is False:
        return 1
    return 0


def read_content(path: str | None) -> str:
    if not path:
        return sys.stdin.read()
    if path == "-":
        return sys.stdin.read()
    return pathlib.Path(path).read_text(encoding="utf-8")


def _do_login(client: LarkClient, args) -> int:
    """设备流授权的两步封装。

    第 1 步（默认）：``--no-wait`` 拿到链接就返回，不阻塞，并把 device_code 落盘；
    第 2 步（``--complete``）：用 device_code 收尾，并**明确报告哪些 scope 没授到**。
    """
    state_path = pathlib.Path(args.state)
    if args.complete:
        if not state_path.is_file():
            print(f"找不到暂存文件 {state_path}；先跑一次不带 --complete 的 login",
                  file=sys.stderr)
            return 1
        state = json.loads(state_path.read_text(encoding="utf-8"))
        print(f"用 device_code 收尾（profile={client.profile or '(default)'}）…")
        result = client.auth_login_complete(state["device_code"])
        granted = result.get("granted") or []
        requested = [s for s in (state.get("scopes") or "").split(",") if s]
        missing = [s for s in requested if s not in granted]
        print(json.dumps({"user": result.get("user_name"), "granted": granted,
                          "missing": missing}, ensure_ascii=False, indent=2))
        if missing:
            print("\n⚠️ 以下 scope 未被授予（飞书的授权是整批成败）：")
            for item in missing:
                print(f"   - {item}")
            url = client.console_auth_url()
            if url:
                print(f"\n去开发者后台勾选这些权限并**发布版本**：\n   {url}")
            return 1
        print("\n✅ 全部 scope 已授予，可以跑实测了：")
        suffix = f" --profile {client.profile}" if client.profile else ""
        print(f"   python tests\\test_feishu.py{suffix}")
        return 0

    scopes = args.scope or (None if args.domain else DEFAULT_SCOPES)
    payload = client.auth_login_start(scopes=scopes, domains=args.domain)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({**payload, "scopes": scopes or "",
                                      "profile": client.profile,
                                      "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                                     ensure_ascii=False, indent=2), encoding="utf-8")
    url = payload.get("verification_url") or ""
    print(f"profile          : {client.profile or '(default)'}")
    print(f"verification_url : {url}")
    print(f"expires_in       : {payload.get('expires_in')} 秒")
    print(f"user_code        : {url.split('user_code=')[-1] if 'user_code=' in url else '-'}")
    if args.qrcode:
        try:
            client.auth_qrcode(url, args.qrcode)
            print(f"二维码           : {TOOL_DIR / args.qrcode}")
        except LarkCliError as exc:
            print(f"二维码生成失败：{exc}", file=sys.stderr)
    suffix = f" --profile {client.profile}" if client.profile else ""
    print("\n在浏览器完成授权后回来运行：")
    print(f"   python larkctl.py{suffix} login --complete")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="larkctl", description="飞书云文档读写")
    parser.add_argument("--as", dest="identity", default="user", choices=["user", "bot"],
                        help="身份，默认 user（访问个人知识库必须是 user）")
    parser.add_argument("--profile", help="lark-cli profile 名（本机存在多个飞书应用时用）")
    parser.add_argument("--json", action="store_true", help="原样输出 JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("status", help="就绪度与身份")
    p.add_argument("--url", help="可选：顺带预检这个文档链接现在能不能读")

    p = sub.add_parser("login", help="飞书用户授权（设备流，两步）")
    p.add_argument("--scope", help=f"要申请的 scope（默认：{DEFAULT_SCOPES}）")
    p.add_argument("--domain", help="按业务域申请，如 docs,wiki,drive（与 --scope 二选一）")
    p.add_argument("--complete", action="store_true",
                   help="用上次暂存的 device_code 收尾（授权页确认后再跑这个）")
    p.add_argument("--state", default=str(DEFAULT_STATE), help="device_code 暂存文件")
    p.add_argument("--qrcode", help="顺带生成 PNG 二维码（相对 tools/notionsync 的路径）")
    sub.add_parser("auth", help="查看授权状态")

    p = sub.add_parser("node", help="解析 wiki/docx 链接")
    p.add_argument("target")

    p = sub.add_parser("outline", help="只看文档目录")
    p.add_argument("target")
    p.add_argument("--max-depth", type=int, default=3)

    p = sub.add_parser("fetch", help="抓取正文")
    p.add_argument("target")
    p.add_argument("--format", choices=["xml", "markdown", "im-markdown"], default="xml")
    p.add_argument("--detail", choices=["simple", "with-ids", "full"], default="simple")
    p.add_argument("--scope", default="full",
                   choices=["full", "outline", "range", "keyword", "section"])
    p.add_argument("--out", help="写入文件（正文另存，stdout 只给摘要）")

    p = sub.add_parser("create", help="新建文档")
    p.add_argument("--file", help="内容文件；缺省读 stdin")
    p.add_argument("--format", choices=["xml", "markdown"], default="xml")
    p.add_argument("--parent-position", help="如 my_library")

    p = sub.add_parser("append", help="在文末追加")
    p.add_argument("target")
    p.add_argument("--file")

    p = sub.add_parser("update", help="按指令更新文档")
    p.add_argument("target")
    p.add_argument("--command", required=True,
                   choices=["str_replace", "block_insert_after", "block_replace",
                            "block_delete", "block_move_after", "block_copy_insert_after",
                            "overwrite", "append"])
    p.add_argument("--file", help="替换/插入内容")
    p.add_argument("--block-id")
    p.add_argument("--start-block-id")
    p.add_argument("--end-block-id")
    p.add_argument("--pattern")
    p.add_argument("--format", choices=["xml", "markdown"], default="xml")
    p.add_argument("--reference-map", help="reference_map JSON 或 @file")

    p = sub.add_parser("rm", help="删除云空间文件（高风险）")
    p.add_argument("token")
    p.add_argument("--type", default="docx")
    p.add_argument("--yes", action="store_true", help="确认执行")

    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    client = LarkClient(identity=args.identity, profile=args.profile, cwd=str(TOOL_DIR))

    try:
        if args.command == "login":
            return _do_login(client, args)
        if args.command == "status":
            if args.url:
                return emit(client.preflight(args.url), True)
            return emit(client.readiness(), True)
        if args.command == "auth":
            return emit(client.auth_status(), True)
        if args.command == "node":
            return emit(client.wiki_node_get(args.target), True)
        if args.command == "outline":
            payload = client.docs_fetch(args.target, doc_format="xml", detail="simple",
                                        scope="outline",
                                        extra=["--max-depth", str(args.max_depth)])
            return emit(payload, True)
        if args.command == "fetch":
            payload = client.docs_fetch(args.target, doc_format=args.format,
                                        detail=args.detail, scope=args.scope)
            document = ((payload.get("data") or {}).get("document") or {})
            content = document.get("content") or ""
            if args.out:
                out = pathlib.Path(args.out)
                out.write_text(content, encoding="utf-8")
                print(json.dumps({"ok": True, "file": str(out), "chars": len(content),
                                  "revision_id": document.get("revision_id")},
                                 ensure_ascii=False, indent=2))
                return 0
            return emit(payload, True)
        if args.command == "create":
            return emit(client.docs_create(read_content(args.file), doc_format=args.format,
                                           parent_position=args.parent_position), True)
        if args.command == "append":
            return emit(client.docs_update(args.target, "append", read_content(args.file),
                                           doc_format=args.format), True)
        if args.command == "update":
            return emit(client.docs_update(
                args.target, args.command, read_content(args.file) if args.file else None,
                block_id=args.block_id, start_block_id=args.start_block_id,
                end_block_id=args.end_block_id, pattern=args.pattern,
                doc_format=args.format, reference_map=args.reference_map), True)
        if args.command == "rm":
            if not args.yes:
                print("删除是高风险操作，需要显式 --yes（先确认目标无误）", file=sys.stderr)
                return 1
            return emit(client.drive_delete(args.token, file_type=args.type, yes=True), True)
    except LarkCliError as exc:
        print(json.dumps({"ok": False, "error": {"message": str(exc),
                                                 "subtype": exc.subtype,
                                                 "exit": exc.exit_code}},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        if exc.not_configured:
            print("提示：先运行 `lark-cli config init --new` 配置应用，再 `lark-cli auth login`。",
                  file=sys.stderr)
        if exc.needs_user_confirmation:
            print("提示：这是高风险写操作，需要你确认后追加 --yes 重试。", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
