"""notionsync / setup_tokens.py — 交互式写入多工作空间 Token 配置。

凭据不经过任何聊天窗口：脚本在你自己的终端里读入 Token，立刻用它调
``/v1/users/me`` 验证，并把 **该 Token 实际属于哪个工作空间** 打印出来——
这一步同时帮你确认「哪个 Token 是 A、哪个是 B」。

用法::

    python tools\\notionsync\\setup_tokens.py
    python tools\\notionsync\\setup_tokens.py --token A=ntn_xxx --token B=ntn_yyy
    python tools\\notionsync\\setup_tokens.py --path D:\\somewhere\\workspaces.json

默认写入 ``~/.notionsync/workspaces.json``（Windows 上即
``C:\\Users\\<你>\\.notionsync\\workspaces.json``），并尽力收紧文件权限。
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import pathlib
import stat
import sys

HERE = pathlib.Path(__file__).resolve()
TOOL_DIR = HERE.parent
sys.path.insert(0, str(TOOL_DIR))

from notionsync.client import NotionClient, NotionError   # noqa: E402

DEFAULT_PATH = pathlib.Path(os.path.expanduser("~")) / ".notionsync" / "workspaces.json"


def probe(token: str, api_version: str) -> tuple[bool, str]:
    """返回 (是否有效, 说明)。"""
    try:
        client = NotionClient(token, api_version=api_version)
        info = client.self_info()
        bot = info.get("bot") or {}
        owner = bot.get("owner") or {}
        person = owner.get("user", {}).get("name") if owner.get("type") == "user" else None
        name = bot.get("workspace_name") or info.get("name") or "(未知)"
        parts = [f"workspace={name!r}", f"object={info.get('object')}"]
        if person:
            parts.append(f"owner={person}")
        return True, "  ".join(parts)
    except NotionError as exc:
        return False, str(exc)
    except ValueError as exc:
        return False, str(exc)


def _mask(token: str) -> str:
    return f"{token[:6]}…{token[-4:]}" if len(token) > 12 else "…"


def collect_interactive(api_version: str) -> list[dict]:
    entries: list[dict] = []
    print("按提示逐个录入。直接回车结束。\n")
    index = 1
    while True:
        default_key = "A" if index == 1 else ("B" if index == 2 else f"W{index}")
        key = input(f"[{index}] 工作空间 key（如 A / B）[{default_key}]: ").strip() or default_key
        token = getpass.getpass(f"[{index}] Token（输入不回显）: ").strip()
        if not token:
            break
        ok, detail = probe(token, api_version)
        print(f"    {'✅ 有效' if ok else '❌ 无效'} — {detail}")
        if not ok:
            retry = input("    仍然保存吗？(y/N): ").strip().lower()
            if retry != "y":
                continue
        label = input(f"[{index}] 别名（留空用上面对到的空间名）: ").strip()
        if not label:
            label = detail.split("workspace=")[1].split("'")[1] if "workspace=" in detail else key
        entries.append({"key": key, "label": label, "token": token})
        index += 1
        if index > 4:
            break
    return entries


def collect_from_args(pairs: list[str], api_version: str) -> list[dict]:
    entries: list[dict] = []
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--token 需要 KEY=TOKEN 形式，收到：{pair}")
        key, token = pair.split("=", 1)
        key, token = key.strip(), token.strip()
        ok, detail = probe(token, api_version)
        print(f"[{key}] {'✅ 有效' if ok else '❌ 无效'} — {detail}")
        label = key
        if "workspace=" in detail:
            label = detail.split("workspace=")[1].split("'")[1]
        entries.append({"key": key, "label": label, "token": token})
    return entries


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="写入 notionsync 多工作空间凭据")
    parser.add_argument("--path", default=str(DEFAULT_PATH),
                        help=f"配置文件路径（默认 {DEFAULT_PATH}）")
    parser.add_argument("--api-version", default="2025-09-03")
    parser.add_argument("--token", action="append", default=[],
                        metavar="KEY=TOKEN", help="非交互模式；可重复")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的配置")
    args = parser.parse_args()

    path = pathlib.Path(args.path).expanduser()
    if path.exists() and not args.force:
        print(f"配置已存在：{path}")
        print("如需覆盖请加 --force。当前内容（已脱敏）：")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for entry in raw.get("workspaces", []):
                token = str(entry.get("token") or "")
                print(f"  - {entry.get('key')}: {entry.get('label')}  {_mask(token)}")
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  （读取失败：{exc}）")
        return 0

    entries = (collect_from_args(args.token, args.api_version) if args.token
               else collect_interactive(args.api_version))
    if not entries:
        print("没有录入任何工作空间，未写入。")
        return 1

    payload = {"version": 1, "apiVersion": args.api_version, "workspaces": entries}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    print(f"\n已写入：{path}")
    for entry in entries:
        print(f"  - {entry['key']}: {entry['label']}  {_mask(entry['token'])}")
    print("\n下一步自检：")
    print(f'  python "{TOOL_DIR / "notionctl.py"}" doctor')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
