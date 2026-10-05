"""飞书链路的端到端 mock 测试 —— 不需要真实授权。

为什么需要它
------------
飞书授权要占用你的时间（每次二维码 10 分钟），所以**测试脚本本身必须先证明是对的**，
不能拿你的时间去调试我的脚本。

做法：写一个假的 ``lark-cli``（Python 实现，模拟 doctor / auth status / wiki +node-get /
docs +fetch|+create / drive +delete），通过 ``LARK_CLI`` 环境变量注入，然后把
``test_feishu.py`` **当子进程原样跑一遍**，断言它全绿、并且真的走了建/读/写回/删除四步。

这样验证的是：参数拼装、JSON 信封解析、XML 无损往返比较、草稿回收——全部在真实
授权之前就位。

运行::

    python tools\\notionsync\\tests\\test_feishu_mock.py

产物：``tools/notionsync/temp/report_feishu_mock.json``
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
FAKE_DIR = TEMP_DIR / "_fake_lark"
FAKE_CLI = FAKE_DIR / "lark-cli-fake.py"
STORE = FAKE_DIR / "store.json"

FAKE_SOURCE = '''"""假的 lark-cli：只实现 notionsync 用到的几个命令，供 mock 测试注入。"""
import json
import pathlib
import sys
import uuid

STORE = pathlib.Path(__file__).with_name("store.json")

CANNED_TARGET = (
    "<title>目标文档（mock）</title>"
    "<h1>目标文档</h1>"
    "<p>这是 mock 返回的目标文档内容。</p>"
    "<table><thead><tr><th><p>列</p></th></tr></thead>"
    "<tbody><tr><td><p>值</p></td></tr></tbody></table>"
)


def load():
    if STORE.exists():
        data = json.loads(STORE.read_text(encoding="utf-8"))
        data.setdefault("calls", [])
        return data
    return {"docs": {}, "deleted": [], "created": 0, "calls": []}


def save(data):
    STORE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def flag(args, name, default=None):
    if name in args:
        index = args.index(name)
        if index + 1 < len(args):
            return args[index + 1]
    return default


def main():
    raw = sys.argv[1:]
    data = load()
    # 记下原始 argv（去掉噪声 flag），让 mock 测试能断言 --profile 是否传到位
    data.setdefault("calls", []).append(
        [a for a in raw if a not in ("--format", "json")])
    # 全局 flag 排在子命令之前，分发前必须先剥掉，否则 args[:2] 判断全错
    args = list(raw)
    while len(args) >= 2 and args[0] in ("--profile",):
        args = args[2:]
    out = {"ok": True}

    if "doctor" in args:
        out = {"ok": True, "checks": [{"name": "config_file", "status": "pass"},
                                      {"name": "cli_version", "status": "pass",
                                       "message": "fake"}]}
    elif args[:2] == ["auth", "status"]:
        out = {"appId": "cli_fake", "brand": "feishu", "identities": {
            "bot": {"status": "ready", "available": True},
            "user": {"status": "ready", "available": True, "userName": "mock-user",
                     "scope": "wiki:node:retrieve docx:document:readonly"},
        }, "identity": "user"}
    elif args and args[0] == "whoami":
        out = {"identity": "user", "available": True}
    elif args[:2] == ["wiki", "+node-get"]:
        token = flag(args, "--node-token", "")
        out = {"ok": True, "identity": "user", "data": {
            "node_token": "wikcnFAKE", "obj_token": "docxFAKE", "obj_type": "docx",
            "title": "目标文档（mock）", "space_id": "7000000000000000001",
            "url": token}}
    elif args[:2] == ["docs", "+fetch"]:
        doc = flag(args, "--doc", "")
        content = data["docs"].get(doc, CANNED_TARGET)
        out = {"ok": True, "identity": "user", "data": {"document": {
            "document_id": doc, "revision_id": 7, "content": content}}}
    elif args[:2] == ["docs", "+create"]:
        content = flag(args, "--content", "")
        doc_id = "docx" + uuid.uuid4().hex[:12]
        data["docs"][doc_id] = content
        data["created"] += 1
        save(data)
        out = {"ok": True, "identity": "user", "data": {"document": {
            "document_id": doc_id, "revision_id": 1,
            "url": "https://mock.feishu.cn/docx/" + doc_id}}}
    elif args[:2] == ["docs", "+update"]:
        out = {"ok": True, "identity": "user", "data": {"result": "success",
                                                        "updated_blocks_count": 1}}
    elif args[:2] == ["drive", "+delete"]:
        token = flag(args, "--file-token", "")
        data["deleted"].append(token)
        data["docs"].pop(token, None)
        save(data)
        out = {"ok": True, "identity": "user", "data": {"result": "success"}}
    elif args[:2] == ["drive", "+inspect"]:
        out = {"ok": True, "identity": "user", "data": {"type": "docx", "title": "目标文档"}}
    else:
        out = {"ok": True, "argv": args}

    save(data)
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

CHECKS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    FAKE_DIR.mkdir(parents=True, exist_ok=True)
    FAKE_CLI.write_text(FAKE_SOURCE, encoding="utf-8")
    if STORE.exists():
        STORE.unlink()

    print("=" * 70)
    print("飞书链路 mock 端到端测试（注入假 lark-cli）")
    print("=" * 70)

    def run_phase(label: str, extra_args: list[str]) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["LARK_CLI"] = str(FAKE_CLI)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("LARK_PROFILE", None)   # 隔离宿主环境，保证用例可重复
        print(f"\n--- {label} ---")
        proc = subprocess.run(
            [sys.executable, str(HERE.parent / "test_feishu.py"), *extra_args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, cwd=str(TOOL_DIR), timeout=180)
        print(proc.stdout)
        if proc.stderr.strip():
            print("--- stderr ---")
            print(proc.stderr[-1200:])
        return proc

    proc = run_phase("阶段 1：默认 profile", [])
    out = proc.stdout
    check("test_feishu.py 在 mock 下全绿退出", proc.returncode == 0,
          f"exit={proc.returncode}")
    check("就绪检查通过（应用+user 身份）", "[PASS] 就绪检查" in out)
    check("解析知识库链接", "[PASS] 解析知识库链接" in out)
    check("读取目标文档", "[PASS] 读取目标文档 XML" in out)
    check("创建草稿并写入", "[PASS] 创建草稿文档并写入 XML" in out)
    check("读回结构断言", "[PASS] 读回：关键结构标记齐全" in out)
    check("多列 2 个 column", "[PASS] 多列文本：<column> 恰好 2 个" in out)
    check("嵌套列表", "[PASS] 嵌套列表：<li> 内含 <ul>" in out)
    check("表格 th/td", "[PASS] 表格：表头 2 格 / 数据 4 格" in out)
    check("无损写回逐字符一致", "[PASS] 无损写回：读→写→读 完全一致" in out)
    check("草稿已回收", "[清理]" in out)

    store = json.loads(STORE.read_text(encoding="utf-8")) if STORE.exists() else {}
    check("假服务器记录到 2 次创建、2 次删除",
          store.get("created") == 2 and len(store.get("deleted", [])) == 2,
          f"created={store.get('created')} deleted={store.get('deleted')}")
    check("回收后 fake 端不留文档",
          not any(key.startswith("docx") and key not in store.get("deleted", [])
                  for key in store.get("docs", {})),
          f"剩余 {list(store.get('docs', {}))}")
    check("默认阶段没有误传 --profile",
          not any("--profile" in call for call in store.get("calls", [])),
          f"共 {len(store.get('calls', []))} 次调用")

    # 阶段 2：本机存在多个飞书应用时，--profile 必须一路传到 lark-cli
    store["calls"] = []
    STORE.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    proc2 = run_phase("阶段 2：--profile doubao", ["--profile", "doubao"])
    store2 = json.loads(STORE.read_text(encoding="utf-8"))
    check("带 --profile 时同样全绿", proc2.returncode == 0, f"exit={proc2.returncode}")
    check("--profile 确实传到了 lark-cli",
          any("--profile" in call and "doubao" in call for call in store2.get("calls", [])),
          f"共 {len(store2.get('calls', []))} 次调用")
    check("报告里记录了生效 profile",
          '"profile": "doubao"' in (TEMP_DIR / "report_feishu.json").read_text(encoding="utf-8"))

    failures = [c for c in CHECKS if not c["ok"]]
    passed = len(CHECKS) - len(failures)
    report = {"suite": "larksync feishu mock", "ok": not failures,
              "passed": passed, "total": len(CHECKS), "checks": CHECKS,
              "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}
    path = TEMP_DIR / "report_feishu_mock.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + "=" * 70)
    print(f"结果：{passed}/{len(CHECKS)} 通过；报告：{path}")
    print("=" * 70)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
