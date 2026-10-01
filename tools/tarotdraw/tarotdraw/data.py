# D:\Repositories\Manager\AtriumPyTools\tools\tarotdraw\tarotdraw\data.py
"""数据目录定位与 JSON 读取。

数据目录（`tarotdraw/data/`）是**包目录的兄弟目录**，即：

    tools/tarotdraw/
    ├── tarotdraw/          <- 本包
    └── data/
        ├── cards/          <- 78 张牌面 jpg（major/ + 四个花色目录）
        ├── json/           <- index.json + 3 个牌义 deck
        └── guides/         <- 3 份原始 markdown 资料

解析顺序（第一个命中者胜出）：
1. 环境变量 ``TAROTDRAW_DATA``
2. 包目录的兄弟目录 ``<package>/../data``
3. 仓库根相对路径 ``AtriumPyTools/tools/tarotdraw/data``
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ENV_DATA_DIR = "TAROTDRAW_DATA"

#: 牌面图相对 index.json 中 ``image`` 字段的根
CARDS_SUBDIR = "cards"
JSON_SUBDIR = "json"
GUIDES_SUBDIR = "guides"

INDEX_FILE = "index.json"


class DataError(RuntimeError):
    """数据目录缺失或结构不合法。"""


@dataclass(frozen=True)
class DataPaths:
    """解析后的数据目录结构。"""

    root: Path
    cards: Path
    json: Path
    guides: Path

    @property
    def index_file(self) -> Path:
        return self.json / INDEX_FILE

    def card_image(self, relative: str) -> Path:
        """把 index.json 里的 ``cards/major/ma01.jpg`` 解析为绝对路径。"""
        return (self.cards.parent / relative).resolve()


def _candidate_roots() -> list[Path]:
    here = Path(__file__).resolve()
    return [
        here.parent.parent / "data",                     # tools/tarotdraw/data
        here.parents[3] / "tools" / "tarotdraw" / "data",  # 仓库根回退
    ]


def resolve_data_root(root: str | os.PathLike[str] | None = None) -> Path:
    """定位数据根目录（``.../tarotdraw/data``）。"""
    if root is not None:
        candidate = Path(root).expanduser().resolve()
        if not candidate.is_dir():
            raise DataError(f"数据目录不存在：{candidate}")
        return candidate

    env_value = os.environ.get(ENV_DATA_DIR, "").strip()
    if env_value:
        candidate = Path(env_value).expanduser().resolve()
        if not candidate.is_dir():
            raise DataError(f"环境变量 {ENV_DATA_DIR} 指向的目录不存在：{candidate}")
        return candidate

    for candidate in _candidate_roots():
        if (candidate / JSON_SUBDIR / INDEX_FILE).is_file():
            return candidate.resolve()

    tried = "\n".join(f"  - {c}" for c in _candidate_roots())
    raise DataError(
        "找不到 tarotdraw 数据目录（应包含 json/index.json）。已尝试：\n"
        f"{tried}\n"
        f"可用环境变量 {ENV_DATA_DIR} 显式指定。"
    )


def data_root(root: str | os.PathLike[str] | None = None) -> DataPaths:
    """返回 :class:`DataPaths`，并校验子目录存在。"""
    base = resolve_data_root(root)
    paths = DataPaths(
        root=base,
        cards=base / CARDS_SUBDIR,
        json=base / JSON_SUBDIR,
        guides=base / GUIDES_SUBDIR,
    )
    if not paths.index_file.is_file():
        raise DataError(f"缺少牌面索引文件：{paths.index_file}")
    if not paths.cards.is_dir():
        raise DataError(f"缺少牌面图目录：{paths.cards}")
    return paths


def load_json(path: str | os.PathLike[str]) -> Any:
    """以 UTF-8 读取 JSON（牌义含中文，必须显式 utf-8）。"""
    p = Path(path)
    if not p.is_file():
        raise DataError(f"JSON 文件不存在：{p}")
    with p.open("r", encoding="utf-8") as handle:
        return json.load(handle)
