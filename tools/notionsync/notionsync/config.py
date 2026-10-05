"""notionsync 配置：多工作空间凭据的发现与解析。

凭据查找顺序（先命中者胜）
--------------------------
1. 环境变量 ``NOTIONSYNC_CONFIG`` 指向的 JSON 文件
2. ``~/.notionsync/workspaces.json``
3. ``tools/notionsync/temp/workspaces.json``（仓库内本地覆盖，已在 .gitignore 忽略）

单条凭据也可用环境变量覆盖：``NOTIONSYNC_TOKEN_<KEY>``
（KEY 会先做规范化：大写、非字母数字转下划线。例如 key ``A`` → ``NOTIONSYNC_TOKEN_A``）。

配置文件格式::

    {
      "version": 1,
      "apiVersion": "2025-09-03",
      "workspaces": [
        {"key": "A", "label": "个人空间",       "token": "ntn_xxx"},
        {"key": "B", "label": "Shirley's Dashboard", "token": "ntn_yyy"}
      ]
    }

``token`` 允许写 ``"env:VAR_NAME"`` 形式来间接引用环境变量，便于避免明文落盘。
"""

from __future__ import annotations

import json
import os
import pathlib
import re
from dataclasses import dataclass, field

#: 默认 Notion API 版本。2025-09-03 起 databases 拆分为 data source，
#: 查询数据库必须走 /v1/data_sources/{id}/query。
DEFAULT_API_VERSION = "2025-09-03"

CONFIG_ENV = "NOTIONSYNC_CONFIG"
TOKEN_ENV_PREFIX = "NOTIONSYNC_TOKEN_"


class ConfigError(RuntimeError):
    """配置缺失或格式错误。"""


def _norm_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", key).strip("_").upper()


def candidate_config_paths() -> list[pathlib.Path]:
    """按优先级返回可能的配置文件路径。"""
    paths: list[pathlib.Path] = []
    env_path = os.environ.get(CONFIG_ENV)
    if env_path:
        paths.append(pathlib.Path(env_path).expanduser())
    paths.append(pathlib.Path(os.path.expanduser("~")) / ".notionsync" / "workspaces.json")
    # tools/notionsync/notionsync/config.py -> parents[1] == tools/notionsync
    paths.append(pathlib.Path(__file__).resolve().parents[1] / "temp" / "workspaces.json")
    return paths


def find_config_path() -> pathlib.Path | None:
    for path in candidate_config_paths():
        if path.is_file():
            return path
    return None


def _resolve_token(raw: str) -> str:
    """支持 ``env:VAR`` 间接引用，其余按字面处理。"""
    raw = (raw or "").strip()
    if raw.startswith("env:"):
        name = raw[4:].strip()
        value = os.environ.get(name, "").strip()
        if not value:
            raise ConfigError(f"环境变量 {name} 为空，但配置引用了它")
        return value
    return raw


@dataclass(frozen=True)
class Workspace:
    """一个 Notion 工作空间的读写凭据。"""

    key: str
    label: str
    token: str = field(repr=False)
    api_version: str = DEFAULT_API_VERSION

    @property
    def env_key(self) -> str:
        return TOKEN_ENV_PREFIX + _norm_key(self.key)

    def describe(self) -> dict:
        """可安全打印的描述（绝不包含 token 明文）。"""
        return {
            "key": self.key,
            "label": self.label,
            "apiVersion": self.api_version,
            "token": f"{self.token[:4]}…{self.token[-4:]} (len={len(self.token)})"
            if self.token
            else "(empty)",
        }


class WorkspaceRegistry:
    """多工作空间注册表。"""

    def __init__(self, workspaces: list[Workspace], source: str = "") -> None:
        if not workspaces:
            raise ConfigError("没有任何工作空间配置")
        self._items: dict[str, Workspace] = {}
        for ws in workspaces:
            if _norm_key(ws.key) in self._items:
                raise ConfigError(f"工作空间 key 重复：{ws.key}")
            self._items[_norm_key(ws.key)] = ws
        self.source = source

    # -- 查询 ---------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._items)

    @property
    def keys(self) -> list[str]:
        return list(self._items)

    def all(self) -> list[Workspace]:
        return list(self._items.values())

    def resolve(self, key: str | None = None) -> Workspace:
        """按 key 取工作空间；只有单个配置时允许省略 key。"""
        if key:
            found = self._items.get(_norm_key(key))
            if found is None:
                raise ConfigError(
                    f"未知工作空间 {key!r}；已配置：{', '.join(self.keys)}"
                )
            return found
        if len(self._items) == 1:
            return next(iter(self._items.values()))
        raise ConfigError(f"配置了多个工作空间，必须指定其一：{', '.join(self.keys)}")

    def describe(self) -> list[dict]:
        return [ws.describe() for ws in self.all()]


def load_registry(path: pathlib.Path | None = None) -> WorkspaceRegistry:
    """加载工作空间注册表。"""
    config_path = path or find_config_path()
    if config_path is None:
        raise ConfigError(
            "找不到 notionsync 配置文件。请创建 ~/.notionsync/workspaces.json，"
            f"或用 {CONFIG_ENV} 指定路径。"
        )
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{config_path} 不是合法 JSON：{exc}") from exc

    default_version = str(raw.get("apiVersion") or DEFAULT_API_VERSION)
    entries = raw.get("workspaces")
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{config_path} 的 workspaces 必须是非空数组")

    workspaces: list[Workspace] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ConfigError(f"workspaces[{index}] 必须是对象")
        key = str(entry.get("key") or "").strip()
        if not key:
            raise ConfigError(f"workspaces[{index}] 缺少 key")
        env_override = os.environ.get(TOKEN_ENV_PREFIX + _norm_key(key), "").strip()
        token = env_override or _resolve_token(str(entry.get("token") or ""))
        workspaces.append(
            Workspace(
                key=key,
                label=str(entry.get("label") or key),
                token=token,
                api_version=str(entry.get("apiVersion") or default_version),
            )
        )
    return WorkspaceRegistry(workspaces, source=str(config_path))
