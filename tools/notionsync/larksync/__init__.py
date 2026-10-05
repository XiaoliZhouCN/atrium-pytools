"""larksync — 飞书云文档的 Python 读写层（基于本机 lark-cli）。

为什么包一层 lark-cli
---------------------
飞书没有像 Notion 那样"拿个 Token 直接打 REST"的轻量路径：走开放平台要么自建应用
（app_id/app_secret + 逐项申请 scope + 发布审批），要么自建 OAuth。而本机 Trae 已自带
**飞书官方 `lark-cli`**，它用 Device Flow 完成个人授权、并内置 `wiki` / `docs` /
`drive` / `markdown` 等域。包一层比自建应用快得多，也不用你填任何密钥。

飞书文档是 **块/XML 模型**（不是 Markdown 原生）：
  * 读：``docs +fetch --doc-format xml --detail full`` 返回 ``content`` + ``reference_map``
  * 写：``docs +update --command ...``（``block_replace`` / ``append`` / ``str_replace`` …）

``content`` 与 ``reference_map`` 属于**同一份响应**，必须整体保留，否则回放会丢资源。

输出契约（来自 lark-shared skill）
---------------------------------
* 成功信封**没有**顶层 ``code``/``msg``；判断成功用 ``ok == true`` 或退出码 0。
* 退出码 10 = 高风险写操作的确认门禁，**不是错误**，需要用户显式同意。
* 绝不把 appSecret / accessToken 打印到终端。
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any

__all__ = ["LarkCliError", "find_cli", "run", "LarkClient", "ENV_PROFILE"]

HIGH_RISK_EXIT = 10

#: 指定 lark-cli profile（多飞书应用时用）。整条链路（CLI / 测试）都认它。
ENV_PROFILE = "LARK_PROFILE"


class LarkCliError(RuntimeError):
    """lark-cli 调用失败，或本机没有可用的 lark-cli。"""

    def __init__(self, message: str, *, error: dict | None = None,
                 exit_code: int | None = None, raw: str = "") -> None:
        super().__init__(message)
        self.error = error or {}
        self.exit_code = exit_code
        self.raw = raw

    @property
    def subtype(self) -> str:
        return str((self.error or {}).get("subtype") or "")

    @property
    def not_configured(self) -> bool:
        return self.subtype == "not_configured"

    @property
    def permission_denied(self) -> bool:
        """资源级无权限（如 bot 访问个人知识库，错误码 131006）。

        这与"应用 scope 没申请"是两回事：重授权或换身份都没用，必须让**资源所有者**
        把文档共享给该身份，或改用资源所有者本人的 user 身份。
        """
        return self.subtype == "permission_denied" or self.error.get("code") == 131006

    @property
    def needs_user_confirmation(self) -> bool:
        return self.exit_code == HIGH_RISK_EXIT


def find_cli() -> str:
    """定位 lark-cli：环境变量 > Trae 插件目录（取最高版本）> PATH。

    ``LARK_CLI`` 也可以指向一个 ``.py`` 文件——这被当作**测试注入点**：用当前解释器
    执行它，从而能在没有真实授权的情况下把整条链路跑通（见
    ``tests/test_feishu_mock.py``）。直接指向 ``.cmd`` 是不行的：cmd.exe 会把 XML 里的
    ``<`` ``>`` 当成重定向符。
    """
    override = os.environ.get("LARK_CLI", "").strip()
    if override and pathlib.Path(override).is_file():
        return override
    plugins = (pathlib.Path(os.path.expanduser("~")) / ".trae" / "plugins"
               / "trae-remote-official" / "lark")
    if plugins.is_dir():
        for version_dir in sorted((d for d in plugins.iterdir() if d.is_dir()),
                                  key=lambda d: d.name, reverse=True):
            exe = version_dir / "bin" / "lark-cli.exe"
            if exe.is_file():
                return str(exe)
            exe = version_dir / "bin" / "lark-cli"
            if exe.is_file():
                return str(exe)
    found = shutil.which("lark-cli")
    if found:
        return found
    raise LarkCliError("找不到 lark-cli（可用环境变量 LARK_CLI 指定可执行文件路径）")


def _try_parse(text: str) -> dict:
    """尽最大努力从一段输出里取出 JSON 信封；取不到返回 {}。

    注意：``lark-cli`` 在**失败时会把 JSON 信封写到 stderr**（成功时写 stdout），
    所以两个流都要试，不能只看 stdout。
    """
    text = (text or "").strip()
    if not text:
        return {}
    candidates = [text]
    start = text.find("{")
    if start > 0:
        candidates.append(text[start:])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return value if isinstance(value, dict) else {"data": value}
    return {}


def cli_argv_prefix(cli: str) -> list[str]:
    """把 CLI 路径翻译成可执行的 argv 前缀（``.py`` 用当前解释器跑）。"""
    if cli.lower().endswith(".py"):
        return [sys.executable, cli]
    return [cli]


def _unknown_format_flag(payload: dict) -> bool:
    """部分子命令（如 doctor）不接受全局 --format，需要去掉后重试。"""
    error = payload.get("error") or {}
    message = str(error.get("message") or "")
    return "--format" in message and "unknown flag" in message


def run(args: list[str], *, stdin_text: str | None = None, cwd: str | None = None,
        timeout: int = 180, fmt: str | None = "json", _retry: bool = True,
        check: bool = True, globals_: list[str] | None = None) -> dict:
    """执行一次 lark-cli 调用并返回解析后的 JSON 信封。

    ``check=False`` 时即使 ``ok=false`` 也原样返回（``doctor`` 这类诊断命令需要）。
    ``globals_`` 放全局 flag（如 ``--profile <name>``），必须排在子命令之前。
    """
    cli = find_cli()
    argv = [*cli_argv_prefix(cli), *(globals_ or []), *args]
    if fmt:
        argv += ["--format", fmt]
    env = dict(os.environ)
    env.setdefault("LARK_CLI_NO_UPDATE_CHECK", "1")
    try:
        proc = subprocess.run(argv, input=stdin_text, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env, cwd=cwd,
                              timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise LarkCliError(f"lark-cli 超时（{timeout}s）：{' '.join(args[:3])}") from exc

    payload = _try_parse(proc.stdout) or _try_parse(proc.stderr)
    if _retry and fmt and _unknown_format_flag(payload):
        return run(args, stdin_text=stdin_text, cwd=cwd, timeout=timeout, fmt=None,
                   _retry=False, check=check, globals_=globals_)
    if not check:
        return payload
    if proc.returncode == HIGH_RISK_EXIT:
        raise LarkCliError(
            f"高风险写操作需要显式确认：{payload.get('action') or ' '.join(args[:3])}",
            error=payload, exit_code=proc.returncode, raw=proc.stdout)
    if proc.returncode != 0 or payload.get("ok") is False:
        error = payload.get("error") or {}
        message = (error.get("message")
                   or (proc.stderr or proc.stdout or "").strip()[:300]
                   or "调用失败")
        hint = error.get("hint")
        raise LarkCliError(f"{message}{f'（{hint}）' if hint else ''}",
                           error=error, exit_code=proc.returncode,
                           raw=(proc.stdout or "") + (proc.stderr or ""))
    return payload


class LarkClient:
    """薄封装：一个方法对应一组常用 lark-cli 调用。"""

    def __init__(self, *, identity: str = "user", cwd: str | None = None,
                 timeout: int = 180, profile: str | None = None) -> None:
        self.identity = identity
        self.cwd = cwd
        self.timeout = timeout
        # 本机可能存在多个飞书应用（profile）。环境变量兜底，便于一条命令切换整条链路。
        self.profile = profile or os.environ.get(ENV_PROFILE) or None

    # -- 基础 ---------------------------------------------------------------
    def _globals(self) -> list[str]:
        return ["--profile", self.profile] if self.profile else []

    def _as(self) -> list[str]:
        return ["--as", self.identity] if self.identity else []

    def _run(self, args: list[str], **kwargs) -> dict:
        kwargs.setdefault("cwd", self.cwd)
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("globals_", self._globals())
        return run(args, **kwargs)

    def call(self, args: list[str], **kwargs) -> dict:
        return self._run([*args, *self._as()], **kwargs)

    def doctor(self) -> dict:
        # doctor 用退出码/ok 表达"有检查项未通过"，属于诊断输出，不应抛异常
        return self._run(["doctor"], check=False)

    def readiness(self) -> dict:
        """汇总当前可用性：CLI 是否找得到、应用是否配置、**user 身份**是否已授权。

        注意：``whoami`` 成功只能说明"有可用身份"，可能是 bot。访问个人知识库必须
        是 user 身份，所以这里以 ``auth status`` 里 user 的状态为准。
        """
        info: dict[str, Any] = {"cli": None, "configured": False, "authorized": False,
                                "botReady": False, "identity": None, "workspace": None,
                                "problems": []}
        try:
            info["cli"] = find_cli()
        except LarkCliError as exc:
            info["problems"].append(str(exc))
            return info

        try:
            checks = self.doctor().get("checks") or []
            failed = [c for c in checks if c.get("status") == "fail"]
            info["configured"] = not any(c.get("name") == "config_file" for c in failed)
            info["problems"] += [f"{c.get('name')}: {c.get('message')}" for c in failed]
        except LarkCliError as exc:
            info["problems"].append(f"doctor: {exc}")

        try:
            status = self.auth_status()
            identities = status.get("identities") or {}
            bot = identities.get("bot") or {}
            user = identities.get("user") or {}
            info["botReady"] = bot.get("available") is True
            info["authorized"] = user.get("available") is True
            info["identity"] = user.get("name") or user.get("user_name")
            if not info["authorized"] and info["configured"]:
                info["problems"].append(
                    "user 身份未授权（bot 就绪但访问个人知识库需要 user）："
                    + str(user.get("message") or "run: lark-cli auth login"))
        except LarkCliError as exc:
            info["problems"].append(f"auth status: {exc}")
        return info

    def config_show(self) -> dict:
        return self._run(["config", "show"])

    def whoami(self) -> dict:
        return self._run(["whoami"])

    def auth_status(self, *, verify: bool = False) -> dict:
        args = ["auth", "status"] + (["--verify"] if verify else [])
        return self._run(args)

    def auth_list(self) -> dict:
        return self._run(["auth", "list"])

    # -- 授权（设备流） -----------------------------------------------------
    def auth_login_start(self, *, scopes: str | None = None,
                         domains: str | None = None) -> dict:
        """发起设备流授权，立刻返回（不阻塞）。返回 device_code / verification_url。"""
        args = ["auth", "login", "--no-wait", "--json"]
        if scopes:
            args += ["--scope", scopes]
        if domains:
            args += ["--domain", domains]
        payload = self._run(args)
        return payload.get("data") or payload

    def auth_login_complete(self, device_code: str, *, timeout: int = 660) -> dict:
        """用 device_code 收尾；会阻塞直到用户在浏览器里确认或超时。"""
        payload = self._run(["auth", "login", "--device-code", device_code, "--json"],
                            timeout=timeout)
        return payload.get("data") or payload

    def auth_qrcode(self, url: str, output: str) -> dict:
        """生成 PNG 二维码；``output`` 需是 cwd 下的相对路径。"""
        return self._run(["auth", "qrcode", url, "--output", output])

    @property
    def configured(self) -> bool:
        """应用配置是否就绪（不看身份是否已授权）。"""
        try:
            return bool(self.doctor().get("ok"))
        except LarkCliError:
            return False

    def app_id(self) -> str | None:
        try:
            return self.config_show().get("appId") or None
        except LarkCliError:
            return None

    def console_auth_url(self) -> str | None:
        """该应用的「权限管理」后台地址——缺 scope 时要人去这里勾选并发布版本。"""
        app = self.app_id()
        return f"https://open.feishu.cn/app/{app}/auth" if app else None

    def describe(self) -> dict:
        """当前生效的 profile / appId（用于日志与报告，不含任何密钥）。"""
        info: dict[str, Any] = {"profile": self.profile or "(default)",
                                "appId": None, "identity": self.identity}
        try:
            info["appId"] = self.app_id()
        except Exception:  # noqa: BLE001 - 诊断信息不该反过来把调用打断
            pass
        return info

    # -- 预检 ---------------------------------------------------------------
    def preflight(self, url_or_token: str) -> dict:
        """一次性判断"现在能不能读这个文档"，并给出可执行的下一步。

        检查顺序刻意如此：先看应用是否配置 → 再看 user 身份是否就绪 → 最后才真的
        去解析节点。每一步失败都给出**具体该做什么**，而不是含糊的"权限不足"。

        这里区分三类**处理方式完全相反**的失败：

        * ``not_configured`` —— 还没建应用 → ``config init``
        * ``missing_scope``   —— 应用没开这个权限 → 开发者后台勾选 + 发布版本
        * ``permission_denied`` —— 资源没共享给该身份 → 让文档所有者共享，换身份/重授权都没用

        另外，飞书**自建应用有「可用范围」**：只有范围内的成员才能授权。所以"某个账号
        登不上"通常是租户/可用范围问题，不是 CLI 的问题。
        """
        result: dict[str, Any] = {"ready": False, "step": None, "message": "",
                                  "hint": "", "console_url": self.console_auth_url(),
                                  "node": None}
        state = self.readiness()
        result["readiness"] = state

        if not state["configured"]:
            result.update(step="config", message="飞书应用尚未配置",
                          hint="运行 lark-cli config init --new（后台，会输出授权链接）")
            return result
        if not state["authorized"]:
            result.update(
                step="auth",
                message="user 身份未授权（bot 就绪但访问个人知识库需要 user）",
                hint=("运行 lark-cli auth login，用**拥有该知识库的账号**完成授权。"
                      "若该账号根本登不上，说明它不在这个应用的**可用范围**内——"
                      "自建应用只服务其所在租户，跨账号体系（如豆包账号）无法授权，"
                      "需要在那个账号的租户下另建应用，或把文档共享给本租户成员"))
            return result

        try:
            payload = self.wiki_node_get(url_or_token)
        except LarkCliError as exc:
            if exc.permission_denied:
                result.update(
                    step="permission",
                    message=f"当前身份无权访问该节点：{exc}",
                    hint=("资源级无权限。让文档/知识库所有者把该节点共享给此身份"
                          "（知识库 → 空间成员；或文档 → 协作）；换身份或重授权都没用"))
            elif exc.subtype == "missing_scope" or exc.error.get("missing_scopes"):
                missing = exc.error.get("missing_scopes") or []
                result.update(
                    step="scope",
                    message=f"应用缺少权限：{', '.join(missing) or exc}",
                    hint=("在开发者后台「权限管理」勾选这些 scope 并**发布版本**，"
                          "然后重新 auth login；或运行 "
                          f"lark-cli auth login --scope \"{' '.join(missing)}\""))
            else:
                result.update(step="resolve", message=str(exc),
                              hint="检查链接是否完整、是否已失效")
            return result

        node = payload.get("data") or {}
        result.update(ready=True, step="ok",
                      message=f"可访问：{node.get('title') or node.get('obj_token')}",
                      node={k: node.get(k) for k in ("node_token", "obj_token", "obj_type",
                                                     "title", "space_id")})
        return result

    # -- Wiki ---------------------------------------------------------------
    def wiki_node_get(self, node_token_or_url: str) -> dict:
        """把 wiki 链接/节点解析成 node_token、obj_token、obj_type。"""
        return self.call(["wiki", "+node-get", "--node-token", node_token_or_url])

    # -- 文档 ---------------------------------------------------------------
    def docs_fetch(self, doc: str, *, doc_format: str = "xml", detail: str = "full",
                   scope: str | None = None, extra: list[str] | None = None) -> dict:
        args = ["docs", "+fetch", "--doc", doc,
                "--doc-format", doc_format, "--detail", detail]
        if scope:
            args += ["--scope", scope]
        if extra:
            args += list(extra)
        return self.call(args)

    def docs_update(self, doc: str, command: str, content: str | None = None, *,
                    block_id: str | None = None, start_block_id: str | None = None,
                    end_block_id: str | None = None, pattern: str | None = None,
                    doc_format: str | None = None, reference_map: str | None = None,
                    extra: list[str] | None = None) -> dict:
        args = ["docs", "+update", "--doc", doc, "--command", command]
        if content is not None:
            args += ["--content", content]
        if block_id is not None:
            args += ["--block-id", block_id]
        if start_block_id is not None:
            args += ["--start-block-id", start_block_id]
        if end_block_id is not None:
            args += ["--end-block-id", end_block_id]
        if pattern is not None:
            args += ["--pattern", pattern]
        if doc_format:
            args += ["--doc-format", doc_format]
        if reference_map is not None:
            args += ["--reference-map", reference_map]
        if extra:
            args += list(extra)
        return self.call(args, stdin_text=None)

    def docs_create(self, content: str, *, doc_format: str = "xml",
                    parent_position: str | None = None,
                    parent_token: str | None = None,
                    title: str | None = None) -> dict:
        args = ["docs", "+create", "--doc-format", doc_format, "--content", content]
        if parent_position:
            args += ["--parent-position", parent_position]
        if parent_token:
            args += ["--parent-token", parent_token]
        if title:
            args += ["--title", title]
        return self.call(args)

    # -- 云空间 -------------------------------------------------------------
    def drive_delete(self, file_token: str, *, file_type: str = "docx",
                     yes: bool = False) -> dict:
        """删除云空间文件（high-risk-write，需 ``yes=True`` 才带 ``--yes``）。"""
        args = ["drive", "+delete", "--file-token", file_token, "--type", file_type]
        if yes:
            args += ["--yes"]
        return self.call(args)

    def resolve_document(self, url_or_token: str) -> dict:
        """把 wiki / docx 链接解析成标题与真实 token。"""
        return self.call(["drive", "+inspect", "--url", url_or_token])
