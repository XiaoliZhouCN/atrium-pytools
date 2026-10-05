# D:\Repositories\Manager\AtriumPyTools\tools\lexicon\lexicon\schema.py
"""词库 SQLite 结构定义。

分两层：

* **词典层**（本模块全部表）：词条 / 义项 / 原书出处 / 词单 / 标签 / 解析信号。
  可从原文重建，因此不需要备份。
* **学习层**（后续阶段的 `user.db`）：复习状态与作答日志，必须备份，且不放在这里。
  两层用 `word.id` 关联，因此 **word.id 一旦分配就不再复用**。
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- 构建元信息：来源、校验和、解析器版本、各表条数
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 词条：一个词头一行（同词多次出现已合并）
CREATE TABLE IF NOT EXISTS word (
    id            INTEGER PRIMARY KEY,
    lemma         TEXT    NOT NULL,           -- 规范形（小写）
    lemma_key     TEXT    NOT NULL UNIQUE,    -- 去重与查词键
    display       TEXT    NOT NULL,           -- 展示形，保留大小写与连字符
    kind          TEXT    NOT NULL,           -- word | phrase
    phonetic      TEXT,                       -- 首选音标（含定界符）
    phonetic_src  TEXT,                       -- lingoes | baidu | book | manual
    phonetic_alt  TEXT,                       -- JSON 数组：其余音标（异读 / 多来源）
    source        TEXT    NOT NULL,           -- 词书标识
    source_line   INTEGER,                    -- 首次出现的原文行号
    created_at    TEXT    NOT NULL
);

-- 背诵单元：词头 + 词性。异读词按词性天然分开（reject[vt.] / reject[n.]），
-- 词组 pos 为空、无音标，键就是词头本身（roll film）。
CREATE TABLE IF NOT EXISTS pos_entry (
    id           INTEGER PRIMARY KEY,
    word_id      INTEGER NOT NULL REFERENCES word(id) ON DELETE CASCADE,
    pos          TEXT,                       -- 'v.' / 'a.' / NULL（词组）
    phonetic     TEXT,                       -- 该词性自己的音标
    phonetic_src TEXT,
    phonetic_alt TEXT,                       -- JSON 数组：同一词性下的异读
    gloss_cn     TEXT    NOT NULL,           -- 展示形态：含词性前缀，如 'v. 腐化，腐蚀；使堕落'
    gloss_body   TEXT    NOT NULL,           -- 纯释义，不含词性前缀
    pos_no       INTEGER NOT NULL,
    entry_key    TEXT    NOT NULL UNIQUE,    -- 'corrupt[v.]' / 'roll film'
    UNIQUE (word_id, pos_no)
);

-- 义项：背单词的最小单位（词性 + 释义）
CREATE TABLE IF NOT EXISTS sense (
    id            INTEGER PRIMARY KEY,
    word_id       INTEGER NOT NULL REFERENCES word(id) ON DELETE CASCADE,
    pos_entry_id  INTEGER REFERENCES pos_entry(id) ON DELETE CASCADE,
    pos           TEXT,                          -- 'n.' / 'vt.' / 'a./ad.' / NULL
    gloss_cn      TEXT    NOT NULL,
    sense_no      INTEGER NOT NULL,              -- 词内序号
    phonetic      TEXT,
    raw_segment   TEXT
);

-- 原书出处：一个词可出现在多个 Word List（原书的复现设计）。
-- 主键用 line_no 而非 (list_no, seq_no)：原文可能重复出现同一 Word List 标题，
-- 那样 seq_no 会重号，用行号才能保证一条词条行对应一条出处记录。
CREATE TABLE IF NOT EXISTS occurrence (
    word_id INTEGER NOT NULL REFERENCES word(id) ON DELETE CASCADE,
    line_no INTEGER NOT NULL,
    list_no INTEGER NOT NULL,
    seq_no  INTEGER NOT NULL,
    PRIMARY KEY (word_id, line_no)
);

-- 词单：组织词库的主要手段
CREATE TABLE IF NOT EXISTS collection (
    id      INTEGER PRIMARY KEY,
    name    TEXT    NOT NULL UNIQUE,
    note    TEXT,
    builtin INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS collection_word (
    collection_id INTEGER NOT NULL REFERENCES collection(id) ON DELETE CASCADE,
    word_id       INTEGER NOT NULL REFERENCES word(id) ON DELETE CASCADE,
    added_at      TEXT,
    PRIMARY KEY (collection_id, word_id)
);

-- 标签：目前只有 listening（原文 * 标记），存而不用
CREATE TABLE IF NOT EXISTS tag (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS word_tag (
    word_id INTEGER NOT NULL REFERENCES word(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
    PRIMARY KEY (word_id, tag_id)
);

-- 解析质量信号：与 needs_review.csv 同源
CREATE TABLE IF NOT EXISTS parse_issue (
    id       INTEGER PRIMARY KEY,
    line_no  INTEGER NOT NULL,
    severity TEXT    NOT NULL,   -- error | warn | info
    code     TEXT    NOT NULL,   -- no_phonetic / no_pos / multi_phonetic / ...
    detail   TEXT,
    raw      TEXT
);

-- 查词：FTS5 全文索引（lemma + 全部释义）
CREATE VIRTUAL TABLE IF NOT EXISTS word_fts USING fts5(
    lemma,
    gloss,
    tokenize = 'unicode61'
);

-- 外部词库补全（ECDICT 等）。走 (word_id, source) 主键，便于将来叠加多个来源。
CREATE TABLE IF NOT EXISTS enrichment (
    word_id       INTEGER NOT NULL REFERENCES word(id) ON DELETE CASCADE,
    source        TEXT    NOT NULL,           -- ecdict
    source_word   TEXT,                       -- 在来源里实际命中的词形
    phonetic      TEXT,
    translation   TEXT,                       -- 中文释义，每行一个
    definition    TEXT,                       -- 英文释义，每行一个
    pos           TEXT,                       -- 词性占比，如 n:46/v:54
    collins       INTEGER,                    -- 柯林斯星级
    oxford        INTEGER,                    -- 是否牛津 3000 核心词
    tags          TEXT,                       -- 考试标签 zk/gk/cet4/.../ielts/gre
    bnc           INTEGER,                    -- 英国国家语料库词频序
    frq           INTEGER,                    -- 当代语料库词频序
    exchange      TEXT,                       -- 原样词形变化串
    exchange_cn   TEXT,                       -- 解码后的词形变化
    matched_by    TEXT,                       -- exact | strip | lemma
    source_sha256 TEXT,
    fetched_at    TEXT,
    PRIMARY KEY (word_id, source)
);

-- 例句。text_zh 可空（例句源未必有中文）
CREATE TABLE IF NOT EXISTS example (
    id        INTEGER PRIMARY KEY,
    word_id   INTEGER NOT NULL REFERENCES word(id) ON DELETE CASCADE,
    text_en   TEXT    NOT NULL,
    text_zh   TEXT,
    source    TEXT    NOT NULL,   -- tatoeba | ...
    source_id TEXT,
    seq       INTEGER NOT NULL DEFAULT 0
);

-- 搭配（当前只做介词搭配），由开放语料统计得出，属原创衍生数据
CREATE TABLE IF NOT EXISTS collocation (
    id         INTEGER PRIMARY KEY,
    word_id    INTEGER NOT NULL REFERENCES word(id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL,   -- prep
    pattern    TEXT    NOT NULL,   -- 'depend on'
    hits       INTEGER NOT NULL,   -- 该搭配在语料中出现次数
    word_hits  INTEGER NOT NULL,   -- 该词在语料中出现总次数
    total_hits INTEGER NOT NULL,   -- 该词后面接介词/小品词的总次数
    source     TEXT    NOT NULL,
    UNIQUE (word_id, kind, pattern, source)
);

CREATE INDEX IF NOT EXISTS idx_posentry_word    ON pos_entry(word_id, pos_no);
CREATE INDEX IF NOT EXISTS idx_sense_word       ON sense(word_id);
CREATE INDEX IF NOT EXISTS idx_sense_posentry   ON sense(pos_entry_id);
CREATE INDEX IF NOT EXISTS idx_occurrence_list  ON occurrence(list_no, seq_no);
CREATE INDEX IF NOT EXISTS idx_issue_severity   ON parse_issue(severity, code);
CREATE INDEX IF NOT EXISTS idx_colword_col      ON collection_word(collection_id);
CREATE INDEX IF NOT EXISTS idx_enrich_source    ON enrichment(source);
CREATE INDEX IF NOT EXISTS idx_example_word     ON example(word_id, seq);
CREATE INDEX IF NOT EXISTS idx_collocation_word ON collocation(word_id, hits DESC);
"""

REQUIRED_TABLES = (
    "meta",
    "word",
    "pos_entry",
    "sense",
    "occurrence",
    "collection",
    "collection_word",
    "tag",
    "word_tag",
    "parse_issue",
    "enrichment",
    "example",
    "collocation",
)


#: 派生表（enrichment/example/collocation）加列时的最小迁移。
#: 这些表的内容都能从外部数据源重建，所以只需要补列、不需要保数据。
_COLUMN_MIGRATIONS: dict[str, dict[str, str]] = {
    "collocation": {"total_hits": "INTEGER NOT NULL DEFAULT 0"},
}


def connect(path) -> sqlite3.Connection:
    """打开词库连接（Row 工厂 + 外键开启）。"""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _apply_column_migrations(conn: sqlite3.Connection) -> None:
    for table, columns in _COLUMN_MIGRATIONS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue
        for name, declaration in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def apply_schema(conn: sqlite3.Connection) -> None:
    """建立全部表与索引（幂等），并补上缺失的派生表列。"""
    conn.executescript(DDL)
    _apply_column_migrations(conn)


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """各表行数，用于构建后自检。"""
    counts: dict[str, int] = {}
    for name in REQUIRED_TABLES:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()
        counts[name] = int(row["n"])
    return counts


def reset_dictionary(conn: sqlite3.Connection) -> None:
    """清空词典层数据但保留结构（重建时使用）。"""
    for table in (
        "word_fts",
        "word_tag",
        "collection_word",
        "parse_issue",
        "occurrence",
        "sense",
        "pos_entry",
        "word",
        "collection",
        "tag",
        "meta",
        "enrichment",
        "example",
        "collocation",
    ):
        conn.execute(f"DELETE FROM {table}")
    conn.execute(
        "DELETE FROM sqlite_sequence WHERE name IN"
        " ('word','pos_entry','sense','collection','tag','parse_issue')"
    )
    conn.commit()
