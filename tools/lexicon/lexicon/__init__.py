# D:\Repositories\Manager\AtriumPyTools\tools\lexicon\lexicon\__init__.py
"""lexicon — 雅思词库工具链（纯标准库，无 UI 框架）。

对外只有这一套接口；两个数据位置的定位集中在 :mod:`lexicon.paths`，
未来集成进 ``AtriumSteward`` 时只需替换该模块或由 steward 注入路径。

词库侧（原文 → wordbook.db → 补全）::

    from lexicon import (
        import_source, build_wordbook, build_enrichment, build_corpus,
        fetch_ecdict, fetch_tatoeba, lookup, stats,
    )

    import_source(r"D:\\System\\Downloads\\IELTS Word List.txt")
    build_wordbook()      # -> BuildReport
    build_enrichment()    # -> EnrichReport（ECDICT 补全）
    build_corpus()        # -> CorpusReport（例句 + 介词搭配）
    lookup("depend")      # -> 词条 + 补全 + 例句 + 搭配

koolearn 侧（分层词表合并与认词判定）::

    from lexicon import data_paths, merge_master, build_layers, verify, DrillServer

    paths = data_paths()
    merge_master(paths)   # -> MergeReport（幂等）
    build_layers(paths)   # -> LayerReport
    verify(paths)         # -> [] 表示不变量全部通过
    DrillServer(paths, pool="pack", limit=100).serve()

命令行::

    python -m lexicon rebuild          # build + enrich + corpus
    python -m lexicon lookup depend
    python -m lexicon all              # 上面 + merge + layers + qa
    python -m lexicon drill
"""

from .build import (
    BuildError,
    BuildReport,
    apply_overrides,
    build_wordbook,
    import_source,
    load_overrides,
    lookup,
    open_wordbook,
    stats,
)
from .corpus import (
    PREPOSITIONS,
    TATOEBA_URL,
    CorpusError,
    CorpusReport,
    build_corpus,
    fetch_tatoeba,
    iter_sentences,
    tatoeba_bz2_path,
)
from .drill import (
    ANSWERABLE,
    DEFAULT_LIMIT,
    STATUS_KNOWN,
    STATUS_UNKNOWN,
    STATUS_UNSURE,
    STATUS_UNTESTED,
    DrillServer,
    DrillSession,
    DrillState,
)
from .enrich import (
    ECDICT_URL,
    EnrichError,
    EnrichReport,
    build_enrichment,
    cache_dir,
    decode_exchange,
    ecdict_path,
    fetch_ecdict,
    stripword,
)
from .merge import (
    BACKUP_KEEP,
    CORE_MIN_BOOKS,
    XDF_LAYER,
    LayerReport,
    MergeError,
    MergeReport,
    backup_files,
    build_layers,
    export_master_json,
    merge_master,
    prune_backups,
    tier_of,
)
from .parse import (
    Entry,
    Issue,
    ParseError,
    ParseResult,
    Phonetic,
    Sense,
    annotate_duplicates,
    duplicate_lemmas,
    parse_entry_line,
    parse_text,
)
from .paths import (
    ALL_FILES,
    BASE,
    LISTENING,
    MASTER,
    PACK,
    READING,
    WRITING,
    DataError,
    DataPaths,
    data_paths,
    data_root,
    default_data_dir,
    koolearn_dir,
    resolve_data_root,
)
from .qa import verify
from .schema import SCHEMA_VERSION, connect
from .sources import (
    exact_key,
    load_pool,
    load_xdf_words,
    norm_key,
    read_csv,
    write_csv,
)

__version__ = "0.2.0"

__all__ = [
    "__version__",
    # 词库构建
    "build_wordbook",
    "BuildReport",
    "BuildError",
    "import_source",
    "apply_overrides",
    "load_overrides",
    # 读取
    "lookup",
    "stats",
    "open_wordbook",
    "connect",
    "SCHEMA_VERSION",
    # 外部补全（ECDICT）
    "build_enrichment",
    "EnrichReport",
    "EnrichError",
    "fetch_ecdict",
    "ecdict_path",
    "cache_dir",
    "decode_exchange",
    "stripword",
    "ECDICT_URL",
    # 开放语料（例句与搭配）
    "build_corpus",
    "CorpusReport",
    "CorpusError",
    "fetch_tatoeba",
    "tatoeba_bz2_path",
    "iter_sentences",
    "PREPOSITIONS",
    "TATOEBA_URL",
    # 解析
    "parse_text",
    "parse_entry_line",
    "annotate_duplicates",
    "duplicate_lemmas",
    "Entry",
    "Sense",
    "Phonetic",
    "Issue",
    "ParseResult",
    "ParseError",
    # 路径
    "data_root",
    "data_paths",
    "koolearn_dir",
    "default_data_dir",
    "resolve_data_root",
    "DataPaths",
    "DataError",
    "MASTER",
    "BASE",
    "PACK",
    "LISTENING",
    "READING",
    "WRITING",
    "ALL_FILES",
    # 分层词表
    "read_csv",
    "write_csv",
    "load_pool",
    "load_xdf_words",
    "exact_key",
    "norm_key",
    "merge_master",
    "build_layers",
    "export_master_json",
    "backup_files",
    "prune_backups",
    "BACKUP_KEEP",
    "verify",
    "tier_of",
    "MergeReport",
    "LayerReport",
    "MergeError",
    "CORE_MIN_BOOKS",
    "XDF_LAYER",
    # 认词判定
    "DrillServer",
    "DrillSession",
    "DrillState",
    "DEFAULT_LIMIT",
    "ANSWERABLE",
    "STATUS_UNTESTED",
    "STATUS_UNSURE",
    "STATUS_KNOWN",
    "STATUS_UNKNOWN",
]
