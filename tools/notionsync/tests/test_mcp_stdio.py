"""进程级 MCP 握手自检 —— 真正把 mcp_server.py 当子进程拉起来跑 JSON-RPC。

这一层验证的正是 DSH / Trae 实际使用的路径：换行分隔的 JSON-RPC over stdio、
initialize → tools/list → tools/call，以及「stdout 只出现协议消息」。

跑两个阶段：
1. **有配置**（假 Token）：工具能调用，返回结构化结果（Notion 返回 401 属预期）。
2. **无配置**：服务器必须**照常启动**、工具面完整，只有调用时报配置提示——
   否则 DSH / Trae 会在还没填 Token 时挂掉。

运行::

    python tools\\notionsync\\tests\\test_mcp_stdio.py

产物：``tools/notionsync/temp/report_mcp_stdio.json``
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

SERVER = TOOL_DIR / "mcp_server.py"
PROBE_CONFIG = TEMP_DIR / "_mcp_probe_config.json"

CHECKS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def spawn(env_extra: dict[str, str]) -> subprocess.Popen:
    env = dict(os.environ)
    env.update(env_extra)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=env, cwd=str(TOOL_DIR),
    )


def rpc(proc: subprocess.Popen, message: dict, *, expect_reply: bool = True) -> dict | None:
    proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
    proc.stdin.flush()
    if not expect_reply:
        return None
    line = proc.stdout.readline()
    if not line:
        return None
    return json.loads(line)


def phase_with_config() -> None:
    print("\n--- 阶段 1：有配置（假 Token）---")
    proc = spawn({"NOTIONSYNC_CONFIG": str(PROBE_CONFIG)})
    try:
        response = rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": "2025-06-18",
                                         "capabilities": {},
                                         "clientInfo": {"name": "probe", "version": "0"}}})
        check("initialize 握手", bool(response and response["result"]["serverInfo"]["name"]
                                     == "notionsync"),
              json.dumps(response.get("result", {}) if response else None,
                         ensure_ascii=False)[:150])
        check("协议版本协商",
              bool(response) and response["result"]["protocolVersion"] == "2025-06-18",
              response["result"]["protocolVersion"] if response else "")

        rpc(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"},
            expect_reply=False)

        response = rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "ping"})
        check("ping", bool(response) and "result" in response)

        response = rpc(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
        tools = (response or {}).get("result", {}).get("tools", [])
        names = {t["name"] for t in tools}
        check("tools/list 返回工具", len(tools) >= 10, f"{len(tools)} 个工具")
        expected = {"workspaces_list", "notion_search", "notion_read_page",
                    "notion_create_page", "notion_append", "notion_replace_content",
                    "notion_query_database", "notion_archive_page",
                    "notion_create_comment"}
        missing = expected - names
        check("关键工具齐全", not missing, f"缺：{sorted(missing)}" if missing else "")
        check("每个工具有 inputSchema",
              all(isinstance(t.get("inputSchema"), dict) for t in tools))

        # Windows 管道默认 cp936，会让中文描述变乱码；这里做回归断言。
        search_tool = next((t for t in tools if t["name"] == "notion_search"), {})
        desc = search_tool.get("description", "")
        garbled = any(marker in desc for marker in ("锟", "\ufffd", "ï¿½"))
        check("中文描述按 UTF-8 正确传输", "工作空间" in desc and not garbled, desc[:70])

        response = rpc(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                              "params": {"name": "workspaces_list", "arguments": {}}})
        content = (response or {}).get("result", {}).get("content", [])
        text = content[0]["text"] if content else ""
        try:
            payload = json.loads(text)
            check("tools/call 结构化返回", "workspaces" in payload,
                  f"reachable={[w.get('reachable') for w in payload.get('workspaces', [])]}")
        except json.JSONDecodeError:
            check("tools/call 结构化返回", False, text[:150])

        response = rpc(proc, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                              "params": {"name": "nope", "arguments": {}}})
        check("未知工具返回 isError", bool((response or {}).get("result", {}).get("isError")))

        response = rpc(proc, {"jsonrpc": "2.0", "id": 6, "method": "bogus/method"})
        check("未知方法返回 -32601",
              bool(response) and response.get("error", {}).get("code") == -32601)
    finally:
        _shutdown(proc)
    check("阶段 1 进程正常退出", proc.returncode == 0, f"returncode={proc.returncode}")
    stderr = proc.stderr.read()
    check("阶段 1 stderr 无堆栈", "Traceback" not in stderr, stderr.strip()[:150])


def phase_without_config() -> None:
    print("\n--- 阶段 2：无配置（未填 Token）---")
    missing_path = TEMP_DIR / "_does_not_exist_config.json"
    if missing_path.exists():
        missing_path.unlink()
    # 必须把 HOME/USERPROFILE 也隔离掉：真机上一旦存在
    # ~/.notionsync/workspaces.json，服务器就会（正确地）回退到它，
    # 这个"无配置"分支根本测不到。
    empty_home = TEMP_DIR / "_no_config_home"
    empty_home.mkdir(parents=True, exist_ok=True)
    proc = spawn({"NOTIONSYNC_CONFIG": str(missing_path),
                  "USERPROFILE": str(empty_home), "HOME": str(empty_home)})
    try:
        response = rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": "2025-06-18",
                                         "capabilities": {},
                                         "clientInfo": {"name": "probe", "version": "0"}}})
        check("无配置时仍能 initialize",
              bool(response and response["result"]["serverInfo"]["name"] == "notionsync"))

        response = rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        count = len((response or {}).get("result", {}).get("tools", []))
        check("无配置时工具面依然完整", count >= 10, f"{count} 个工具")

        response = rpc(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                              "params": {"name": "notion_search",
                                         "arguments": {"query": "x"}}})
        result = (response or {}).get("result", {})
        text = (result.get("content") or [{}])[0].get("text", "")
        check("无配置时调用给出可读提示而非崩溃",
              bool(result.get("isError")) and "setup_tokens" in text, text.splitlines()[0][:110])
    finally:
        _shutdown(proc)
    check("阶段 2 进程正常退出", proc.returncode == 0, f"returncode={proc.returncode}")


def _shutdown(proc: subprocess.Popen) -> None:
    try:
        proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    PROBE_CONFIG.write_text(json.dumps({
        "version": 1,
        "workspaces": [{"key": "PROBE", "label": "protocol probe",
                        "token": "ntn_probe_not_a_real_token"}],
    }, ensure_ascii=False), encoding="utf-8")

    print("=" * 70)
    print(f"子进程：{sys.executable} {SERVER}")
    print("=" * 70)

    phase_with_config()
    phase_without_config()

    passed = sum(1 for c in CHECKS if c["ok"])
    report = {"suite": "notionsync mcp stdio", "ok": passed == len(CHECKS),
              "passed": passed, "total": len(CHECKS), "checks": CHECKS,
              "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}
    path = TEMP_DIR / "report_mcp_stdio.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + "=" * 70)
    print(f"结果：{passed}/{len(CHECKS)} 通过；报告：{path}")
    print("=" * 70)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
