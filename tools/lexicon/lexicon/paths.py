# D:\Repositories\Manager\AtriumPyTools\tools\lexicon\lexicon\paths.py
"""数据位置定位 —— 本工具**唯一**决定读写路径的地方。

本工具有两个数据位置，性质不同：

``root``
    词库目录：``raw/`` 原文、``wordbook.db``、``overrides/``、构建报告。
    **内容资产**，放在 ``AtriumNote``。

``koolearn``
    koolearn 分层词表目录：``0_``–``5_`` CSV、``qa_check.py``，
    以及认词判定的运行时状态（``drill_state.json``）。

解析顺序（第一个命中者胜出）：显式参数 → 环境变量 → 工具目录下的
``lexicon.config.json`` → 内置默认值。未来集成进 ``AtriumSteward`` 时只改本模块。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

ENV_DATA_DIR = "LEXICON_DATA_DIR"
ENV_KOLEARN_DIR = "LEXICON_KOLEARN_DIR"
CONFIG_FILENAME = "lexicon.config.json"

#: 词库数据目录（内容资产，放 AtriumNote）
VOCABULARIES_SUBDIR = "education/language/vocabularies"
DEFAULT_DATA_DIR = Path(
    r"D:\Repositories\Manager\AtriumNote\education\language\vocabularies"
)
#: koolearn 分层词表目录 —— 与 wordbook.db 同处 AtriumNote。
#: 两个位置都是内容数据，必须待在一起；代码仓库里只放代码、文档与人工定稿的配置。
DEFAULT_KOLEARN_DIR = DEFAULT_DATA_DIR / "koolearn-ielts"

RAW_SUBDIR = "raw"
OVERRIDES_SUBDIR = "overrides"
WORDBOOK_FILENAME = "wordbook.db"
REVIEW_FILENAME = "needs_review.csv"
REPORT_FILENAME = "build_report.json"

#: koolearn 分层文件（数字前缀是排序用的，读写都带前缀）
MASTER = "0_ielts_layered_master.csv"
BASE = "1_L1_base_vocabulary.csv"
PACK = "2_study_pack_recommended.csv"
LISTENING = "3_L2_listening_core.csv"
READING = "4_L2_reading_core.csv"
WRITING = "5_L2_writing_core.csv"

LAYER_FILES = (BASE, LISTENING, READING, WRITING)
ALL_FILES = (MASTER, BASE, PACK, LISTENING, READING, WRITING)

#: 0 号总表的 JSON 派生视图（抓取脚本与 export_master_json 都写它）
LAYERED_JSON = "ielts_layered.json"
#: 抓取快照：119 本书名与技能分组
RAW_TAGS = "raw_tags.json"

DRILL_STATE = "drill_state.json"


class DataError(RuntimeError):
    """数据目录缺失或结构不合法。"""


def config_file() -> Path:
    """工具目录下的配置文件（``tools/lexicon/lexicon.config.json``）。"""
    return Path(__file__).resolve().parent.parent / CONFIG_FILENAME


def _read_config() -> dict:
    path = config_file()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataError(f"配置文件不可读：{path}（{exc}）") from exc
    if not isinstance(payload, dict):
        raise DataError(f"配置顶层必须是对象：{path}")
    return payload


def _locate(
    explicit: str | os.PathLike[str] | None,
    env_name: str,
    config_key: str,
    default: Path,
    must_exist: bool,
    create: bool,
) -> Path:
    if explicit is not None:
        target = Path(explicit).expanduser()
    else:
        env_value = os.environ.get(env_name, "").strip()
        target = (
            Path(env_value).expanduser()
            if env_value
            else Path(str(_read_config().get(config_key) or default))
        )
    if create:
        target.mkdir(parents=True, exist_ok=True)
    if must_exist and not target.is_dir():
        raise DataError(
            f"目录不存在：{target}\n"
            f"可用命令行参数指定，或设置 {env_name}，或改配置 {config_file()}"
        )
    return target.resolve()


def data_root(
    path: str | os.PathLike[str] | None = None, create: bool = False
) -> Path:
    """词库数据目录（``AtriumNote/.../vocabularies``）。"""
    return _locate(
        path, ENV_DATA_DIR, "data_dir", DEFAULT_DATA_DIR,
        must_exist=not create, create=create,
    )


def koolearn_dir(
    path: str | os.PathLike[str] | None = None, create: bool = False
) -> Path:
    """koolearn 分层词表目录。"""
    return _locate(
        path, ENV_KOLEARN_DIR, "koolearn_dir", DEFAULT_KOLEARN_DIR,
        must_exist=not create, create=create,
    )


def default_data_dir() -> Path:
    """不访问文件系统，返回声明的默认词库目录。"""
    return DEFAULT_DATA_DIR


def resolve_data_root(path: str | os.PathLike[str] | None = None) -> Path:
    """定位词库数据根目录（不创建）。"""
    return data_root(path)


@dataclass(frozen=True)
class DataPaths:
    """解析后的两个数据位置。"""

    root: Path
    koolearn: Path

    # -- 词库侧 ----------------------------------------------------------- #

    @property
    def raw(self) -> Path:
        return self.root / RAW_SUBDIR

    @property
    def overrides(self) -> Path:
        return self.root / OVERRIDES_SUBDIR

    @property
    def wordbook(self) -> Path:
        return self.root / WORDBOOK_FILENAME

    @property
    def review_csv(self) -> Path:
        return self.root / REVIEW_FILENAME

    @property
    def report_json(self) -> Path:
        return self.root / REPORT_FILENAME

    def source_file(self, name: str) -> Path:
        return self.raw / name

    def ensure(self) -> "DataPaths":
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw.mkdir(exist_ok=True)
        self.overrides.mkdir(exist_ok=True)
        return self

    # -- koolearn 侧 ------------------------------------------------------ #

    def file(self, name: str) -> Path:
        return self.koolearn / name

    @property
    def master(self) -> Path:
        return self.file(MASTER)

    @property
    def pack(self) -> Path:
        return self.file(PACK)

    @property
    def drill_state(self) -> Path:
        return self.koolearn / DRILL_STATE


def data_paths(
    root: str | os.PathLike[str] | None = None,
    koolearn: str | os.PathLike[str] | None = None,
    create: bool = False,
) -> DataPaths:
    """一次解析两个数据位置。``create=True`` 时词库目录不存在也会建。"""
    paths = DataPaths(
        root=data_root(root, create=create),
        koolearn=koolearn_dir(koolearn, create=create),
    )
    return paths.ensure() if create else paths
