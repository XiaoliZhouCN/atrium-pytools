"""按**真实部署的客户端配置**拉起 MCP 服务器并握手。

前几套测试验证的是「我会不会写正确的命令」；这一套验证的是「**机器上已经落盘的配置本身
能不能用**」——直接读配置文件，把里面写的 command / args / env 原样执行：

* Trae：``%APPDATA%\\Trae\\User\\mcp.json`` 里的 ``mcpServers.notionsync``
* DSH ：``~/.dsh/profiles/desktop/cordis.patch.yml`` 里的 ``mcp-notionsync`` 行

这样即使我不启动 Trae，也能证明 Trae 侧接线正确；DSH 侧同理（它另有活体调用证据）。

缺少配置时该端记为 SKIP 而不是失败。运行::

    python tools\\notionsync\\tests\\test_client_configs.py

产物：``tools/notionsync/temp/report_client_configs.json``
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve()
TOOL_DIR = HERE.parents[1]
TEMP_DIR = TOOL_DIR / "temp"
sys.path.insert(0, str(TOOL_DIR))

TRAE_CONFIG = pathlib.Path(os.path.expandvars(r"%APPDATA%\Trae\User\mcp.json"))
DSH_PROFILE_DIR = pathlib.Path(os.path.expanduser("~")) / ".dsh" / "profiles" / "desktop"
DSH_PATCH = DSH_PROFILE_DIR / "cordis.patch.yml"

CHECKS: list[dict] = []
EXPECTED_TOOLS = {
    "workspaces_list", "notion_search", "notion_read_page", "notion_create_page",
    "notion_append", "notion_replace_content", "notion_query_database",
    "notion_archive_page", "notion_create_comment", "notion_update_properties",
    "notion_set_title", "notion_delete_block", "notion_read_block",
    "notion_describe_database", "notion_create_database", "notion_list_databases",
    "notion_list_comments",
}


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def handshake(label: str, command: str, args: list[str], env: dict[str, str]) -> bool:
    """按给定命令拉起服务器，走一遍 initialize → tools/list → tools/call。"""
    merged = dict(os.environ)
    merged.update({k: str(v) for k, v in (env or {}).items()})
    merged.setdefault("PYTHONUTF8", "1")
    merged.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        proc = subprocess.Popen([command, *args], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", env=merged)
    except (OSError, FileNotFoundError) as exc:
        check(f"{label}：命令可执行", False, f"{command} 无法启动：{exc}")
        return False

    seq = 0

    def rpc(method: str, params=None, *, notify=False):
        nonlocal seq
        message: dict = {"jsonrpc": "2.0", "method": method}
        if not notify:
            seq += 1
            message["id"] = seq
        if params is not None:
            message["params"] = params
        proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        if notify:
            return None
        line = proc.stdout.readline()
        if not line:
            return None
        return json.loads(line)

    try:
        init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "config-probe", "version": "0"}})
        ok = bool(init and init.get("result", {}).get("serverInfo", {}).get("name")
                  == "notionsync")
        check(f"{label}：initialize 握手", ok,
              f"cmd={pathlib.Path(command).name} args={len(args)} 项")
        if not ok:
            return False

        rpc("notifications/initialized", notify=True)

        listed = rpc("tools/list", {})
        tools = (listed or {}).get("result", {}).get("tools", [])
        names = {t["name"] for t in tools}
        check(f"{label}：工具面完整（{len(tools)} 个）", names == EXPECTED_TOOLS,
              f"缺：{sorted(EXPECTED_TOOLS - names)}" if names != EXPECTED_TOOLS else "")

        # 中文必须完好——这正是 cp936 那个坑的回归点
        desc = next((t["description"] for t in tools if t["name"] == "notion_search"), "")
        check(f"{label}：中文描述无乱码",
              "工作空间" in desc and not any(m in desc for m in ("锟", "\ufffd")),
              desc[:60])

        call = rpc("tools/call", {"name": "workspaces_list", "arguments": {}})
        result = (call or {}).get("result", {})
        text = (result.get("content") or [{}])[0].get("text", "")
        configured = (TOOL_DIR / "temp" / "workspaces.json").exists() or (
            pathlib.Path(os.path.expanduser("~")) / ".notionsync" / "workspaces.json").exists()
        if configured:
            check(f"{label}：workspaces_list 可调用", not result.get("isError"),
                  text.splitlines()[0][:90])
        else:
            check(f"{label}：未配 Token 时给出可读提示而非崩溃",
                  bool(result.get("isError")) and "setup_tokens" in text,
                  text.splitlines()[0][:90])
        return True
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        stderr = proc.stderr.read()
        check(f"{label}：stderr 无异常堆栈", "Traceback" not in stderr, stderr.strip()[:120])


def probe_trae() -> None:
    print("\n--- Trae：读取真实 mcp.json ---")
    if not TRAE_CONFIG.is_file():
        check("Trae：配置文件存在", False, str(TRAE_CONFIG))
        return
    check("Trae：配置文件存在", True, str(TRAE_CONFIG))
    data = json.loads(TRAE_CONFIG.read_text(encoding="utf-8"))
    entry = (data.get("mcpServers") or {}).get("notionsync")
    if not entry:
        check("Trae：mcpServers 里有 notionsync 条目", False, "条目缺失")
        return
    check("Trae：mcpServers 里有 notionsync 条目", True,
          json.dumps(entry.get("args"), ensure_ascii=False))
    handshake("Trae", entry["command"], list(entry.get("args") or []),
              dict(entry.get("env") or {}))


def probe_dsh() -> None:
    print("\n--- DSH：读取真实 cordis.patch.yml ---")
    if not DSH_PATCH.is_file():
        check("DSH：patch 文件存在", False, str(DSH_PATCH))
        return
    check("DSH：patch 文件存在", True, str(DSH_PATCH))

    script = (
        "const y=require('js-yaml'),fs=require('fs');"
        "const d=y.load(fs.readFileSync(process.argv[1],'utf8'));"
        "const rows=(d||[]).flatMap(e=>e.insert||[]);"
        "const row=rows.find(e=>e.id==='mcp-notionsync');"
        "process.stdout.write(JSON.stringify(row||null));"
    )
    proc = subprocess.run(["node", "-e", script, str(DSH_PATCH)],
                          cwd=str(DSH_PROFILE_DIR), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not proc.stdout.strip():
        check("DSH：patch 可被 YAML 解析", False, (proc.stderr or "")[:150])
        return
    check("DSH：patch 可被 YAML 解析", True, "js-yaml OK")
    row = json.loads(proc.stdout)
    if not row:
        check("DSH：存在 mcp-notionsync 行", False, "未找到")
        return
    config = row.get("config") or {}
    check("DSH：存在 mcp-notionsync 行", True,
          f'name={row.get("name")} serverName={config.get("serverName")}')
    check("DSH：serverName 为 notionsync", config.get("serverName") == "notionsync",
          str(config.get("serverName")))
    check("DSH：transport 为 stdio", config.get("transport") == "stdio",
          str(config.get("transport")))
    handshake("DSH", config["command"], list(config.get("args") or []),
              dict(config.get("env") or {}))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("按真实部署配置验证三端接线")
    print("=" * 70)

    probe_trae()
    probe_dsh()

    passed = sum(1 for c in CHECKS if c["ok"])
    report = {"suite": "notionsync client configs", "ok": passed == len(CHECKS),
              "passed": passed, "total": len(CHECKS), "checks": CHECKS,
              "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}
    path = TEMP_DIR / "report_client_configs.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + "=" * 70)
    print(f"结果：{passed}/{len(CHECKS)} 通过；报告：{path}")
    print("=" * 70)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
