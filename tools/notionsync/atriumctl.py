"""atriumsync / atriumctl.py — Notion ⇄ 本地 ⇄ 飞书 的命令行入口。

用法::

    python atriumctl.py pull                     # Notion → AtriumNote（全部）
    python atriumctl.py pull --only home tech    # 只跑部分
    python atriumctl.py status                   # 看本地三个目录的现状
    python atriumctl.py formats <md 路径>        # 查看某个文档的格式 sidecar

Notion 是唯一真源；``pull`` 只写本地，不反向改 Notion。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))

from atriumsync import paths, pull, push                  # noqa: E402
from larksync import LarkClient                          # noqa: E402
from notionsync import service                           # noqa: E402
from notionsync.config import ConfigError                # noqa: E402

TOOLS = ["home", "tech", "ai"]


def _registry():
    return service.make_registry()


def cmd_pull(args) -> int:
    try:
        registry = _registry()
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    only = args.only or TOOLS
    unknown = set(only) - set(TOOLS)
    if unknown:
        print(f"未知的 --only 值：{sorted(unknown)}；可选 {TOOLS}", file=sys.stderr)
        return 2

    print(f"工作根目录：{paths.ROOT}")
    report: dict = {}
    if "home" in only:
        print("[1/3] B 空间 1 级页面 → notion_home_pages/")
        report["home_pages"] = pull.pull_home_pages(registry)
        for item in report["home_pages"]:
            print(f"        {item['md'].name}   格式项={item['blocks']}")
    if "tech" in only:
        print("[2/3] Technology Gallery → technology/")
        report["technology"] = pull.export_database(
            registry, paths.TECHNOLOGY_DB, paths.TECHNOLOGY,
            csv_name="technology_gallery.csv", columns=paths.TECH_COLUMNS,
            pages_subdir="pages")
        print(f"        {report['technology']['csv'].name}  "
              f"行数={report['technology']['rows']}")
    if "ai" in only:
        print("[3/3] AI 工作相关 → ai_work_space/")
        report["ai_work_space"] = pull.pull_ai_work_space(registry)
        daily = report["ai_work_space"]["daily"]
        print(f"        {daily['csv'].name}  行数={daily['rows']}")
        print(f"        {report['ai_work_space']['tasks'].name}")

    if args.json:
        print(json.dumps(_serializable(report), ensure_ascii=False, indent=2))
    print("\n完成。Notion 是唯一真源；本次只写本地。")
    return 0


def _serializable(value):
    if isinstance(value, dict):
        return {k: _serializable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serializable(v) for v in value]
    if isinstance(value, pathlib.Path):
        return paths.rel_to_root(value)
    return value


def cmd_status(args) -> int:
    print(f"工作根目录：{paths.ROOT}")
    for name, directory in (("notion_home_pages", paths.HOME_PAGES),
                            ("technology", paths.TECHNOLOGY),
                            ("ai_work_space", paths.AI_WORK_SPACE)):
        exists = directory.is_dir()
        md = sorted(directory.rglob("*.md")) if exists else []
        csvs = sorted(directory.rglob("*.csv")) if exists else []
        # sidecar 可能落在目录自身或子目录（如 technology/pages/_formats）
        sidecars = sorted(directory.rglob("*.formats.json")) if exists else []
        flag = "OK " if exists else "缺失"
        print(f"[{flag}] {name:<20} md={len(md):<4} csv={len(csvs):<3} 格式sidecar={len(sidecars)}")
        for path in csvs:
            print(f"        csv : {paths.rel_to_root(path)}")
        for path in md[:5]:
            print(f"        md  : {paths.rel_to_root(path)}")
        if len(md) > 5:
            print(f"        md  : … 其余 {len(md) - 5} 个")
    return 0


def cmd_formats(args) -> int:
    target = pathlib.Path(args.path)
    if target.is_dir():
        target = target / paths.FORMATS_DIRNAME
    if not target.exists():
        print(f"找不到：{target}", file=sys.stderr)
        return 1
    text = target.read_text(encoding="utf-8")
    payload = json.loads(text)
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:4000])
    return 0


def cmd_push(args) -> int:
    """本地 Markdown → 飞书文档。

    默认推 ``notion_home_pages/ShirleysKnowledgeRepo.md`` 到那篇
    ``Shirley's Knowledge Repo``（飞书版）。用 ``--dry-run`` 先看计划再动手。
    """
    targets = args.md or [str(paths.HOME_PAGES / "ShirleysKnowledgeRepo.md")]
    client = LarkClient(identity="user", profile=args.profile or paths.FEISHU_PROFILE,
                        cwd=str(HERE.parent))
    state = client.readiness()
    if not (state["configured"] and state["authorized"]):
        print(f"飞书未就绪（profile={client.profile}）：{state['problems']}", file=sys.stderr)
        return 2

    exit_code = 0
    for item in targets:
        md_path = pathlib.Path(item)
        if not md_path.is_absolute():
            md_path = paths.ROOT / item
        if not md_path.is_file():
            print(f"找不到本地文件：{md_path}", file=sys.stderr)
            exit_code = 1
            continue
        sidecar = md_path.parent / paths.FORMATS_DIRNAME / f"{md_path.stem}.formats.json"
        url = args.url or paths.FEISHU_TARGETS.get(
            paths.rel_to_root(md_path), paths.FEISHU_HOME_DOC)
        print(f"推送 {paths.rel_to_root(md_path)} → {url}")
        result = push.push_markdown(md_path, feishu_url=url, client=client,
                                    sidecar=sidecar, dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("error") or result.get("skipped"):
            exit_code = 1
    if not args.dry_run and exit_code == 0:
        print("\n飞书已更新；实际发出的 DocxXML 会写到同目录的 *.feishu.xml 供排版优化对照。")
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atriumctl",
                                     description="Notion ⇄ 本地(AtriumNote) ⇄ 飞书 同步")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("pull", help="Notion → 本地（唯一真源是 Notion）")
    p.add_argument("--only", nargs="*", choices=TOOLS, help="只跑指定部分")
    p.add_argument("--json", action="store_true")

    sub.add_parser("status", help="查看本地三个目录的现状")

    p = sub.add_parser("push", help="本地 Markdown → 飞书（按 Notion 布局）")
    p.add_argument("--md", nargs="*", help="要推送的本地 md（相对工作根目录或绝对路径）")
    p.add_argument("--url", help="飞书目标文档 URL（默认那篇 Shirley's Knowledge Repo）")
    p.add_argument("--profile", help=f"lark-cli profile（默认 {paths.FEISHU_PROFILE}）")
    p.add_argument("--dry-run", action="store_true", help="只输出计划，不改飞书")

    p = sub.add_parser("formats", help="查看某个文档的格式 sidecar")
    p.add_argument("path", help="md 文件或其所在目录")

    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    if args.command == "pull":
        return cmd_pull(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "push":
        return cmd_push(args)
    if args.command == "formats":
        return cmd_formats(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
