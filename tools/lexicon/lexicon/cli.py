# D:\Repositories\Manager\AtriumPyTools\tools\lexicon\lexicon\cli.py
"""lexicon 命令行入口。

CLI 只**打印**结果，不打开外部程序（AtriumPyTools 红线）。

分两组：

词库侧（原文 → wordbook.db，再补全）
    import / fetch-ecdict / fetch-tatoeba / build / enrich / corpus
    rebuild（= build + enrich + corpus）/ stats / lookup

koolearn 侧（分层词表合并与认词判定）
    merge / layers / qa / pools / drill

一次全做
    all（rebuild + merge + layers + qa）
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .build import (
    DEFAULT_SOURCE_NAME,
    BuildError,
    build_wordbook,
    import_source,
    lookup,
    stats,
)
from .corpus import (
    DEFAULT_MAX_EXAMPLES,
    TATOEBA_URL,
    CorpusError,
    build_corpus,
    fetch_tatoeba,
)
from .drill import DEFAULT_LIMIT, DrillServer
from .enrich import (
    ECDICT_URL,
    EnrichError,
    build_enrichment,
    fetch_ecdict,
)
from .merge import (
    BACKUP_KEEP,
    MergeError,
    backup_files,
    build_layers,
    export_master_json,
    merge_master,
)
from .paths import DataError, config_file, data_paths
from .qa import verify
from .sources import load_pool

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_DATA = 3
EXIT_QA = 4

POOL_CHOICES = (
    "pack", "master", "base", "listening", "reading", "writing", "xdf", "remaining",
)


def _ensure_utf8_stdout() -> None:
    """让 stdout/stderr 的编码与它们**实际要写入的地方**匹配。

    * 附着控制台时 → 用控制台自己的编码（中文 Windows 是 cp936）。
      否则 cmd 会按 cp936 解码我们输出的 UTF-8 字节，中文全变乱码
      （``词库`` → ``璇嶅簱``）。用 ``os.device_encoding`` 取，这样即使外部设了
      ``PYTHONUTF8=1`` 或 ``python -X utf8``（会强制 stdout=UTF-8），也能纠回来。
    * 被重定向到文件/管道时 → UTF-8，这是最通用的选择。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            if stream.isatty():
                device = os.device_encoding(stream.fileno())
                current = (stream.encoding or "").lower().replace("-", "")
                if device and current != device.lower().replace("-", ""):
                    reconfigure(encoding=device, errors="replace")
                continue
        except (ValueError, OSError):
            pass
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass


# --------------------------------------------------------------------------- #
# 参数
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lexicon",
        description="雅思词库工具链：构建词库、外部补全、例句与搭配、认词判定、分层词表合并。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python -m lexicon import \"D:\\System\\Downloads\\IELTS Word List.txt\"\n"
            "  python -m lexicon fetch-ecdict\n"
            "  python -m lexicon fetch-tatoeba\n"
            "  python -m lexicon rebuild          # build + enrich + corpus\n"
            "  python -m lexicon lookup depend\n"
            "  python -m lexicon merge && python -m lexicon layers && python -m lexicon qa\n"
            "  python -m lexicon drill --pool pack\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"lexicon {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data-dir", metavar="DIR", help="词库数据目录（默认见 lexicon.config.json）"
    )
    common.add_argument(
        "--koolearn-dir", metavar="DIR",
        help="koolearn 分层词表目录（默认见 lexicon.config.json）",
    )

    # -- 词库侧 ----------------------------------------------------------- #

    p_import = sub.add_parser("import", parents=[common], help="把原文归档到 <数据目录>/raw/")
    p_import.add_argument("source", help="原始词表文件路径")
    p_import.add_argument("--name", default=None, help=f"归档文件名（默认 {DEFAULT_SOURCE_NAME}）")

    p_fetch_e = sub.add_parser(
        "fetch-ecdict", help="下载 ECDICT（MIT 许可的免费英汉双解词典库）到工具缓存"
    )
    p_fetch_e.add_argument("--force", action="store_true", help="已存在也重新下载")
    p_fetch_e.add_argument("--url", default=None, help="覆盖下载地址")

    p_fetch_t = sub.add_parser(
        "fetch-tatoeba", help="下载 Tatoeba 英文句子导出（CC BY 2.0 FR）到工具缓存"
    )
    p_fetch_t.add_argument("--force", action="store_true", help="已存在也重新下载")
    p_fetch_t.add_argument("--url", default=None, help="覆盖下载地址")

    p_build = sub.add_parser("build", parents=[common], help="解析原文并重建 wordbook.db")
    p_build.add_argument("--source", default=None, help="直接指定原文路径（跳过 raw/）")
    p_build.add_argument("--json", action="store_true", help="输出 JSON 报告")

    p_enrich = sub.add_parser(
        "enrich", parents=[common],
        help="用 ECDICT 补全详细注释（英文释义/词形/词频/考试标签）",
    )
    p_enrich.add_argument("--ecdict", default=None, help="直接指定 ECDICT CSV 路径")
    p_enrich.add_argument("--json", action="store_true", help="输出 JSON 报告")

    p_corpus = sub.add_parser(
        "corpus", parents=[common],
        help="从开放语料建例句与介词搭配（写 example/collocation 表）",
    )
    p_corpus.add_argument("--corpus", default=None, help="直接指定语料 bz2 路径")
    p_corpus.add_argument(
        "--max-examples", type=int, default=DEFAULT_MAX_EXAMPLES,
        help=f"每词最多收录几条例句，默认 {DEFAULT_MAX_EXAMPLES}",
    )
    p_corpus.add_argument("-v", "--verbose", action="store_true", help="打印扫描进度")
    p_corpus.add_argument("--json", action="store_true", help="输出 JSON 报告")

    p_rebuild = sub.add_parser(
        "rebuild", parents=[common], help="build + enrich + corpus（重建词库并补全）"
    )
    p_rebuild.add_argument("--source", default=None, help="直接指定原文路径")
    p_rebuild.add_argument(
        "--skip-corpus", action="store_true",
        help="重建后不补例句/搭配 —— 注意结果是空表，不是保留旧数据",
    )
    p_rebuild.add_argument("--json", action="store_true", help="输出 JSON 报告")

    p_stats = sub.add_parser("stats", parents=[common], help="打印词库概览")
    p_stats.add_argument("--json", action="store_true", help="输出 JSON")

    p_lookup = sub.add_parser(
        "lookup", parents=[common], help="查词：释义 + 补全 + 例句 + 搭配"
    )
    p_lookup.add_argument("term", help="要查的词或短语")
    p_lookup.add_argument("--json", action="store_true", help="输出 JSON")

    # -- koolearn 侧 ------------------------------------------------------ #

    p_merge = sub.add_parser("merge", parents=[common], help="把词库合并进 0 号总表（幂等）")
    p_merge.add_argument(
        "--no-xdf-listening", action="store_true",
        help="不把原书 * 标记计入 listen_books（默认计入）",
    )
    p_merge.add_argument("--no-backup", action="store_true", help="跳过自动备份")
    p_merge.add_argument(
        "--keep-backups", type=int, default=BACKUP_KEEP, metavar="N",
        help=f"自动备份最多保留几份，默认 {BACKUP_KEEP}（_backup_original/ 永不删）",
    )
    p_merge.add_argument(
        "--force", action="store_true",
        help="跳过「已合并过」的安全拦截；重跑本身幂等，不会重复计数",
    )
    p_merge.add_argument("--json", action="store_true", help="输出 JSON 报告")

    p_layers = sub.add_parser("layers", parents=[common], help="按新计数重建 1–5 号分层文件")
    p_layers.add_argument("--backup", action="store_true", help="重建前先备份")
    p_layers.add_argument(
        "--keep-backups", type=int, default=BACKUP_KEEP, metavar="N",
        help=f"自动备份最多保留几份，默认 {BACKUP_KEEP}",
    )
    p_layers.add_argument("--json", action="store_true", help="输出 JSON 报告")

    p_export = sub.add_parser(
        "export-json", parents=[common], help="把 0 号总表导出为 ielts_layered.json（layers 会自动做）"
    )
    p_export.add_argument("--output", default=None, help="输出路径，默认 <koolearn_dir>/ielts_layered.json")

    sub.add_parser("qa", parents=[common], help="校验分层文件不变量")
    sub.add_parser("pools", parents=[common], help="列出各词池词数")

    p_drill = sub.add_parser(
        "drill", parents=[common],
        help="启动认词判定网页工具（A 不认识 / S 见过但不熟 / D 认识）",
    )
    p_drill.add_argument("--pool", choices=POOL_CHOICES, default="pack",
                         help="词池，默认 pack（2 号推荐包）")
    p_drill.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                         help=f"累计多少个后停止一轮，默认 {DEFAULT_LIMIT}")
    p_drill.add_argument(
        "--retest", choices=("none", "unlearned", "unknown", "all"), default="none",
        help="重测范围：none 只测待检测（默认）／unlearned 不熟+不认识／unknown 仅不认识／all 全部",
    )
    p_drill.add_argument(
        "--count-unsure", action="store_true",
        help="把「见过但不熟」也计入停止上限（默认只统计「不认识」）",
    )
    p_drill.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    p_drill.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")

    # -- 全流程 ----------------------------------------------------------- #

    p_all = sub.add_parser(
        "all", parents=[common], help="rebuild + merge + layers + qa（可重复执行）"
    )
    p_all.add_argument("--source", default=None, help="直接指定原文路径")
    p_all.add_argument(
        "--skip-corpus", action="store_true",
        help="重建后不补例句/搭配 —— 注意结果是空表，不是保留旧数据",
    )
    p_all.add_argument("--no-xdf-listening", action="store_true", help="不计入听力桶")
    p_all.add_argument("--no-backup", action="store_true", help="跳过 koolearn 备份")
    p_all.add_argument(
        "--keep-backups", type=int, default=BACKUP_KEEP, metavar="N",
        help=f"自动备份最多保留几份，默认 {BACKUP_KEEP}",
    )
    p_all.add_argument("--force", action="store_true", help="允许重复合并")
    p_all.add_argument("--json", action="store_true", help="输出 JSON 报告")

    return parser


# --------------------------------------------------------------------------- #
# 打印
# --------------------------------------------------------------------------- #


def _print_lookup(payload: dict) -> None:
    """按「词头[词性] / 音标 / 释义（含词性）」逐条列出背诵单元，再列补全与语料信息。"""
    tags = f"  [{', '.join(payload['tags'])}]" if payload["tags"] else ""
    print(f"{payload['display']}{tags}")
    for unit in payload["entries"]:
        phon = unit["phonetic"] or "(无音标)"
        extra = "  变体: " + " ".join(unit["phonetic_alt"]) if unit["phonetic_alt"] else ""
        print(f"  {unit['key']:<24} {phon}{extra}")
        print(f"      {unit['gloss_cn']}")

    enrich = payload.get("enrichment")
    if enrich:
        print(f"\n  --- {enrich['source']} 补全（匹配方式 {enrich['matched_by']}）---")
        if enrich["source_word"] and enrich["source_word"].lower() != payload["lemma"]:
            print(f"  对应词形  {enrich['source_word']}")
        if enrich["phonetic"]:
            print(f"  补充音标  {enrich['phonetic']}")
        if enrich["translation"]:
            print(f"  中文释义  {enrich['translation']}")
        if enrich["definition"]:
            print(f"  英文释义  {enrich['definition']}")
        if enrich["exchange_cn"]:
            print(f"  词形变化  {enrich['exchange_cn']}")
        stars = f"{'★' * enrich['collins']}" if enrich["collins"] else "—"
        ranks = []
        if enrich["bnc"]:
            ranks.append(f"BNC #{enrich['bnc']}")
        if enrich["frq"]:
            ranks.append(f"当代 #{enrich['frq']}")
        print(
            f"  柯林斯    {stars}"
            f" ｜ 牛津3000 {'是' if enrich['oxford'] else '否'}"
            f" ｜ 词频 {' '.join(ranks) or '—'}"
        )
        if enrich["tags"]:
            print(f"  考试标签  {' '.join(enrich['tags'])}")

    for example in payload.get("examples") or []:
        print(f"  例句      {example['text_en']}")
        if example["text_zh"]:
            print(f"            {example['text_zh']}")

    for collocation in (payload.get("collocations") or [])[:6]:
        of_preps = (
            f"接介词时占 {collocation['share_of_preps'] * 100:.0f}%"
            if collocation["share_of_preps"]
            else "—"
        )
        print(f"  搭配      {collocation['pattern']}（{of_preps}｜语料 {collocation['hits']} 次）")

    lists = ", ".join(f"List {o['list_no']:02d}" for o in payload["occurrences"])
    print(f"\n  出处: {lists or '（人工新增）'}")


def _print_stats(payload: dict) -> None:
    meta = payload["meta"]
    print(f"来源        {meta.get('source_file', '?')}")
    print(f"构建时间    {meta.get('built_at', '?')}")
    print(f"词条        {payload['counts']['word']}")
    print(f"背诵单元    {payload['counts']['pos_entry']}（词头+词性）")
    print(f"义项        {payload['counts']['sense']}")
    print(f"原书出处    {payload['counts']['occurrence']}")
    print(f"类型        {payload['by_kind']}")
    print("主要词单    " + "、".join(f"{c['name']}={c['count']}" for c in payload["collections"][:6]))
    enrich = payload.get("enrichment") or {}
    if enrich:
        print("外部补全    " + "、".join(f"{k}={v}" for k, v in enrich.items()))
        print(f"例句        {payload['counts']['example']}")
        print(f"搭配        {payload['counts']['collocation']}")
    else:
        print("外部补全    未做（可运行：python -m lexicon enrich）")
    if payload["issues"]:
        print("解析信号    " + "、".join(f"{k}={v}" for k, v in payload["issues"].items()))


# --------------------------------------------------------------------------- #
# koolearn 子命令
# --------------------------------------------------------------------------- #


def _split_paths(args):
    return data_paths(args.data_dir, args.koolearn_dir)


def _cmd_merge(args) -> int:
    report = merge_master(
        _split_paths(args),
        xdf_listening=not args.no_xdf_listening,
        backup=not args.no_backup,
        force=args.force,
        keep_backups=args.keep_backups,
    )
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return EXIT_OK
    print("=== 0 号总表合并 ===")
    for line in report.summary_lines():
        print("  " + line)
    if report.added:
        print(f"  新增词条明细（{len(report.added)}）：" + "，".join(report.added))
    if report.matched_normalized:
        pairs = "；".join(f"{a} → {b}" for a, b in report.matched_normalized)
        print(f"  模糊匹配并词：{pairs}")
    return EXIT_OK


def _cmd_layers(args) -> int:
    report = build_layers(
        _split_paths(args), backup=args.backup, keep_backups=args.keep_backups
    )
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return EXIT_OK
    print("=== 1–5 号分层重建 ===")
    for line in report.summary_lines():
        print("  " + line)
    return EXIT_OK


def _cmd_qa(args) -> int:
    failures = verify(_split_paths(args))
    print("=== 分层文件不变量校验 ===")
    for item in failures:
        print(f"  FAIL {item}")
    if failures:
        print(f"\n失败 {len(failures)} 项")
        return EXIT_QA
    print("  OK   全部通过")
    return EXIT_OK


def _cmd_pools(args) -> int:
    paths = _split_paths(args)
    print("=== 词池 ===")
    for name in POOL_CHOICES:
        try:
            rows = load_pool(name, paths)
        except (ValueError, OSError) as exc:
            print(f"  {name:<10} 不可用（{exc}）")
            continue
        print(f"  {name:<10} {len(rows):>6} 词")
    return EXIT_OK


def _skipped_corpus_note() -> str:
    """``--skip-corpus`` 的真实后果要说清楚。

    ``build`` 会重建整个 wordbook.db，所以「跳过语料扫描」得到的是**空表**，
    而不是保留上一次的例句/搭配。只写「已跳过」会让人以为数据还在。
    """
    return (
        "⚠ 已跳过语料扫描：build 已重建整库，例句/搭配现在是 0 条。\n"
        "  需要时补跑（约 20 秒）：run_lexicon.bat corpus"
    )


def _cmd_rebuild(args) -> int:
    """build + enrich + corpus。"""
    build = build_wordbook(source=args.source, data_dir=args.data_dir)
    enrich = build_enrichment(data_dir=args.data_dir)
    corpus = None if args.skip_corpus else build_corpus(data_dir=args.data_dir)
    if args.json:
        payload = {"build": build.as_dict(), "enrich": enrich.as_dict()}
        if corpus is not None:
            payload["corpus"] = corpus.as_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK
    for line in build.summary_lines():
        print(line)
    print(f"词库        {build.db_path}")
    print("\n=== ECDICT 补全 ===")
    for line in enrich.summary_lines():
        print("  " + line)
    if corpus is not None:
        print("\n=== 开放语料：例句与搭配 ===")
        for line in corpus.summary_lines():
            print("  " + line)
    else:
        print("\n" + _skipped_corpus_note())
    return EXIT_OK


def _cmd_all(args) -> int:
    """rebuild + merge + layers + qa，可重复执行。"""
    build_report = build_wordbook(source=args.source, data_dir=args.data_dir)
    enrich = build_enrichment(data_dir=args.data_dir)
    corpus = None if args.skip_corpus else build_corpus(data_dir=args.data_dir)

    paths = _split_paths(args)
    backup_dir = ""
    if not args.no_backup:
        backup_dir = str(backup_files(paths, keep=args.keep_backups))

    merge_report = None
    merge_note = ""
    try:
        merge_report = merge_master(
            paths,
            xdf_listening=not args.no_xdf_listening,
            backup=False,
            force=args.force,
        )
        merge_report.backup_dir = backup_dir
    except MergeError as exc:
        # 已合并过不算失败：继续重建分层与校验，两者都是幂等的
        merge_note = str(exc).splitlines()[0]

    layers = build_layers(paths, backup=False)
    failures = verify(paths)

    if args.json:
        payload = {
            "build": build_report.as_dict(),
            "enrich": enrich.as_dict(),
            "layers": layers.as_dict(),
            "merge_error": merge_note,
            "qa_failures": failures,
        }
        if corpus is not None:
            payload["corpus"] = corpus.as_dict()
        if merge_report is not None:
            payload["merge"] = merge_report.as_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_QA if failures else EXIT_OK

    for line in build_report.summary_lines():
        print(line)
    print(f"词库        {build_report.db_path}")
    print("\n=== ECDICT 补全 ===")
    for line in enrich.summary_lines():
        print("  " + line)
    if corpus is not None:
        print("\n=== 开放语料：例句与搭配 ===")
        for line in corpus.summary_lines():
            print("  " + line)
    else:
        print("\n" + _skipped_corpus_note())
    print("\n=== 0 号总表合并 ===")
    if merge_report is not None:
        for line in merge_report.summary_lines():
            print("  " + line)
    else:
        print(f"  跳过：{merge_note}")
    print("\n=== 1–5 号分层重建 ===")
    for line in layers.summary_lines():
        print("  " + line)
    print("\n=== 不变量校验 ===")
    if failures:
        for item in failures:
            print(f"  FAIL {item}")
        return EXIT_QA
    print("  OK   全部通过")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return EXIT_USAGE

    try:
        command = args.command

        if command == "import":
            info = import_source(args.source, data_dir=args.data_dir, name=args.name)
            print(f"已归档：{info['source']}")
            print(f"来源：  {info['archived_from']}")
            print(f"sha256：{info['sha256']}")
            print(f"大小：  {info['bytes']} 字节")
            return EXIT_OK

        if command == "fetch-ecdict":
            info = fetch_ecdict(force=args.force, url=args.url or ECDICT_URL)
            action = "已下载" if info["downloaded"] else "已存在，跳过下载"
            print(f"{action}：{info['path']}")
            print(f"大小：  {info['bytes']} 字节")
            print(f"sha256：{info['sha256']}")
            return EXIT_OK

        if command == "fetch-tatoeba":
            info = fetch_tatoeba(force=args.force, url=args.url or TATOEBA_URL)
            action = "已下载" if info["downloaded"] else "已存在，跳过下载"
            print(f"{action}：{info['path']}")
            print(f"大小：  {info['bytes']} 字节")
            print(f"sha256：{info['sha256']}")
            return EXIT_OK

        if command == "build":
            report = build_wordbook(source=args.source, data_dir=args.data_dir)
            if args.json:
                print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
            else:
                for line in report.summary_lines():
                    print(line)
                print(f"词库        {report.db_path}")
                print(f"校验清单    {report.review_csv}")
                print(f"构建报告    {report.report_json}")
                print("提示：build 会重建整库，补全/例句/搭配需重跑（用 rebuild 或 all 一步到位）")
            return EXIT_OK

        if command == "enrich":
            report = build_enrichment(data_dir=args.data_dir, ecdict=args.ecdict)
            if args.json:
                print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
            else:
                print("=== ECDICT 补全 ===")
                for line in report.summary_lines():
                    print("  " + line)
                if report.unmatched:
                    preview = "，".join(report.unmatched[:30])
                    more = f"…（共 {len(report.unmatched)}）" if len(report.unmatched) > 30 else ""
                    print(f"  未命中预览  {preview}{more}")
            return EXIT_OK

        if command == "corpus":
            report = build_corpus(
                data_dir=args.data_dir,
                corpus=args.corpus,
                max_examples=args.max_examples,
                verbose=args.verbose,
            )
            if args.json:
                print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
            else:
                print("=== 开放语料：例句与搭配 ===")
                for line in report.summary_lines():
                    print("  " + line)
            return EXIT_OK

        if command == "rebuild":
            return _cmd_rebuild(args)

        if command == "stats":
            payload = stats(data_dir=args.data_dir)
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                _print_stats(payload)
            return EXIT_OK

        if command == "lookup":
            payload = lookup(args.term, data_dir=args.data_dir)
            if payload is None:
                print(f"未收录：{args.term}", file=sys.stderr)
                return EXIT_DATA
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                _print_lookup(payload)
            return EXIT_OK

        if command == "merge":
            return _cmd_merge(args)
        if command == "layers":
            return _cmd_layers(args)
        if command == "export-json":
            info = export_master_json(_split_paths(args), args.output)
            print(f"已导出：{info['path']}")
            print(f"词条：  {info['words']}")
            print(f"大小：  {info['bytes']} 字节")
            print(f"状态：  {'已更新' if info['changed'] else '词表未变，内容与时间戳均未改动'}")
            return EXIT_OK
        if command == "qa":
            return _cmd_qa(args)
        if command == "pools":
            return _cmd_pools(args)
        if command == "all":
            return _cmd_all(args)

        if command == "drill":
            DrillServer(
                _split_paths(args),
                pool=args.pool,
                limit=args.limit,
                retest=args.retest,
                host=args.host,
                port=args.port,
                count_unsure=args.count_unsure,
            ).serve()
            return EXIT_OK

    except DataError as exc:
        print(f"[lexicon] 数据目录不可用：{exc}", file=sys.stderr)
        print(f"[lexicon] 配置文件：{config_file()}", file=sys.stderr)
        return EXIT_DATA
    except (BuildError, EnrichError, CorpusError, MergeError, ValueError) as exc:
        print(f"[lexicon] {exc}", file=sys.stderr)
        return EXIT_DATA

    parser.print_help()
    return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
