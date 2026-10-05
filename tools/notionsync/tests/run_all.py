"""notionsync 全部测试的总入口。

一次跑完六套：
  * ``unit``           离线单测（不需要 Token、不触网）
  * ``mcp-stdio``      进程级 MCP 握手探针
  * ``e2e-mock``       对本地 mock Notion API 的完整读写（不需要真实凭据）
  * ``client-configs`` 读 Trae / DSH 已落盘的配置，按其中命令原样拉起并握手
  * ``live``           对真实 Notion 的读写（需要 ~/.notionsync/workspaces.json）
  * ``fidelity``       真实 Notion 的格式保真度与无损写回（需要 Token）

``live`` 若因缺配置退出 2 会被记为 **SKIP** 而不是失败，所以填 Token 前也能直接跑。

运行::

    python tools\\notionsync\\tests\\run_all.py
    python tools\\notionsync\\tests\\run_all.py --only unit e2e-mock

产物：``tools/notionsync/temp/report_all.json`` 与 ``temp/logs/<suite>.log``。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve()
TESTS_DIR = HERE.parent
TOOL_DIR = HERE.parents[1]
TEMP_DIR = TOOL_DIR / "temp"
LOG_DIR = TEMP_DIR / "logs"

SUITES: list[tuple[str, str, str]] = [
    ("unit", "test_unit.py", "离线单测"),
    ("mcp-stdio", "test_mcp_stdio.py", "MCP 握手探针"),
    ("e2e-mock", "test_e2e_mock.py", "mock Notion 端到端读写"),
    ("client-configs", "test_client_configs.py", "按真实部署配置验证三端接线"),
    ("live", "test_live.py", "真实 Notion 读写（需要 Token）"),
    ("fidelity", "test_fidelity_notion.py", "真实 Notion 格式保真度与无损写回（需要 Token）"),
    ("feishu", "test_feishu.py", "飞书云文档读写与格式保真（需要 lark-cli 授权）"),
    ("feishu-mock", "test_feishu_mock.py", "飞书链路 mock 端到端（无需授权）"),
    ("atriumsync", "test_atriumsync.py", "AtriumNote 同步层离线单测（命名/XML/幂等）"),
]

#: 缺配置时以退出码 2 表示 SKIP 的套件
SKIPPABLE = {"live", "fidelity", "feishu"}


def run_suite(name: str, script: str) -> tuple[str, int, str]:
    log_path = LOG_DIR / f"{name}.log"
    proc = subprocess.run(
        [sys.executable, str(TESTS_DIR / script)],
        cwd=str(TOOL_DIR), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    log_path.write_text((proc.stdout or "") + "\n--- stderr ---\n" + (proc.stderr or ""),
                        encoding="utf-8")
    if proc.returncode == 0:
        status = "PASS"
    elif proc.returncode == 2 and name in SKIPPABLE:
        status = "SKIP"
    else:
        status = "FAIL"
    return status, proc.returncode, str(log_path)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="notionsync 全部测试")
    parser.add_argument("--only", nargs="*", help="只跑指定套件名")
    parser.add_argument("--profile", help="传给飞书套件的 LARK_PROFILE（本机多飞书应用时用）")
    args = parser.parse_args()

    if args.profile:
        # 子套件用 os.environ 起进程，设一次即可让整条飞书链路走指定 profile
        os.environ["LARK_PROFILE"] = args.profile
        print(f"LARK_PROFILE = {args.profile}")

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    selected = [s for s in SUITES if not args.only or s[0] in args.only]
    if args.only:
        unknown = set(args.only) - {s[0] for s in SUITES}
        if unknown:
            print(f"未知套件：{sorted(unknown)}；可选 {[s[0] for s in SUITES]}")
            return 2

    print("=" * 70)
    print("notionsync 全部测试")
    print("=" * 70)
    rows = []
    started = time.time()
    for name, script, label in selected:
        print(f"\n>>> {name} — {label}")
        status, code, log = run_suite(name, script)
        mark = {"PASS": "✅", "SKIP": "⏭️", "FAIL": "❌"}[status]
        print(f"    {mark} {status} (exit={code})  日志：{log}")
        if status == "FAIL":
            tail = pathlib.Path(log).read_text(encoding="utf-8", errors="replace")
            print("    --- 末尾 15 行 ---")
            for line in tail.strip().splitlines()[-15:]:
                print("    " + line)
        rows.append({"suite": name, "label": label, "status": status, "exit": code,
                     "log": log})

    report = {"suite": "notionsync all", "durationSeconds": round(time.time() - started, 1),
              "ok": all(r["status"] != "FAIL" for r in rows), "results": rows}
    path = TEMP_DIR / "report_all.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 70)
    for row in rows:
        print(f"  {row['status']:<5} {row['suite']}")
    passed = sum(1 for r in rows if r["status"] == "PASS")
    skipped = sum(1 for r in rows if r["status"] == "SKIP")
    failed = sum(1 for r in rows if r["status"] == "FAIL")
    print(f"\n通过 {passed} / 跳过 {skipped} / 失败 {failed}；报告：{path}")
    print("=" * 70)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
