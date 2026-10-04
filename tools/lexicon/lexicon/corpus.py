# D:\Repositories\Manager\AtriumPyTools\tools\lexicon\lexicon\corpus.py
"""从开放语料建立**例句**与**搭配**（当前专做介词/小品词搭配）。

数据源：Tatoeba 英文句子导出（CC BY 2.0 FR，<https://tatoeba.org>）。
一次扫描同时产出两样东西，因为两者都只需要「这个句子里有我的词」这一个判断。

* ``example``     —— 每个词挑几条真实例句（按长度贴近目标值优选）
* ``collocation`` —— 统计「词 + 介词/小品词」的出现次数与占比

**这些是从语料统计出来的原创衍生数据，不复制任何词典内容。**
介词是最高频的词类，统计结果比编者挑选更贴近真实用法。
"""

from __future__ import annotations

import bz2
import datetime as _dt
import hashlib
import re
import urllib.request
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths as PP
from . import schema as S
from .build import BuildError, open_wordbook
from .enrich import cache_dir, sha256_file

TATOEBA_URL = (
    "https://downloads.tatoeba.org/exports/per_language/eng/eng_sentences.tsv.bz2"
)
TATOEBA_BZ2 = "eng_sentences.tsv.bz2"
SOURCE_ID = "tatoeba"

#: 介词 + 常见动词小品词。两类合在一起，因为对学习者而言
#: 「depend **on**」与「carry **out**」是同一种需求。
PREPOSITIONS = frozenset(
    """about above across after against along alongside amid among around as at
    before behind below beneath beside besides between beyond but by despite down
    during except for from in inside into like near of off on onto opposite out
    outside over past per since through throughout till to toward towards under
    underneath unlike until up upon versus via with within without
    away back forward apart aside together ahead""".split()
)

_TOKEN_RE = re.compile(r"[a-z][a-z'\-]*")
_STRIP_RE = re.compile(r"[^a-z0-9]")

DEFAULT_MAX_EXAMPLES = 3
MIN_LEN = 20
MAX_LEN = 160
#: 例句长度优选目标：太短没语境，太长背不动
PREFERRED_LEN = 60


class CorpusError(RuntimeError):
    """语料前置条件不满足。"""


# --------------------------------------------------------------------------- #
# 下载
# --------------------------------------------------------------------------- #


def tatoeba_bz2_path() -> Path:
    return cache_dir() / TATOEBA_BZ2


def fetch_tatoeba(
    force: bool = False, url: str = TATOEBA_URL, dest: str | Path | None = None
) -> dict:
    """下载 Tatoeba 英文句子导出（bz2，约 25 MB）到缓存。"""
    target = Path(dest).expanduser() if dest else tatoeba_bz2_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and not force:
        return {
            "path": str(target),
            "bytes": target.stat().st_size,
            "sha256": sha256_file(target),
            "downloaded": False,
            "url": url,
        }
    request = urllib.request.Request(url, headers={"User-Agent": "lexicon/0.1"})
    with urllib.request.urlopen(request, timeout=900) as response:
        payload = response.read()
    target.write_bytes(payload)
    return {
        "path": str(target),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "downloaded": True,
        "url": url,
    }


def iter_sentences(path: Path | None = None):
    """迭代 ``(id, text)``。Tatoeba 导出格式为 ``id\\tlang\\ttext``。"""
    source = Path(path) if path else tatoeba_bz2_path()
    if not source.is_file():
        raise CorpusError(
            f"找不到语料：{source}\n先执行：python -m lexicon fetch-tatoeba"
        )
    with bz2.open(source, "rt", encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t", 2)
            if len(parts) == 3:
                yield parts[0], parts[2]


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #


@dataclass
class CorpusReport:
    source: str = SOURCE_ID
    source_file: str = ""
    sentences: int = 0
    words_total: int = 0
    words_with_example: int = 0
    examples: int = 0
    words_with_collocation: int = 0
    collocations: int = 0
    top_preposition_share: list[tuple[str, int]] = field(default_factory=list)
    built_at: str = ""
    db_path: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    def summary_lines(self) -> list[str]:
        lines = [
            f"来源        {self.source}（{self.source_file}）",
            f"扫描句子    {self.sentences}",
            f"目标词条    {self.words_total}",
            f"例句        {self.examples} 条，覆盖 {self.words_with_example} 词"
            f"（{self.words_with_example / self.words_total * 100:.1f}%）"
            if self.words_total
            else f"例句        {self.examples} 条",
            f"搭配        {self.collocations} 条，覆盖 {self.words_with_collocation} 词",
        ]
        if self.top_preposition_share:
            top = "、".join(f"{p}({n})" for p, n in self.top_preposition_share[:8])
            lines.append(f"最常见介词  {top}")
        return lines


# --------------------------------------------------------------------------- #
# 扫描
# --------------------------------------------------------------------------- #


def _pick_examples(candidates: list[tuple[int, str]], limit: int) -> list[tuple[int, str]]:
    """按「长度贴近 PREFERRED_LEN」优选，返回前 limit 条。"""
    return sorted(candidates, key=lambda item: (abs(len(item[1]) - PREFERRED_LEN), len(item[1])))[
        :limit
    ]


def scan_corpus(
    data_dir,
    sentences,
    max_examples: int = DEFAULT_MAX_EXAMPLES,
    progress_every: int = 0,
    on_progress=None,
) -> tuple[list[tuple], list[tuple], CorpusReport]:
    """一次扫全语料。

    返回 ``(example_rows, collocation_rows, report)``：

    * ``example_rows`` 为 ``(word_id, sentence_id, text, seq)``
    * ``collocation_rows`` 为 ``(word_id, pattern, hits, word_hits, total_hits)``

    性能：语料有 200 万句、约 2000 万 token，因此
    ① token→word_id 的解析结果**带缓存**（语料词表远小于 token 总数），
    ② 短语匹配按「首词」建索引，只在首词命中时才做子串检查。
    """
    conn = open_wordbook(data_dir)
    try:
        words = conn.execute("SELECT id, lemma_key FROM word ORDER BY id").fetchall()
    finally:
        conn.close()

    token_index: dict[str, int] = {}
    strip_index: dict[str, int] = {}
    phrase_by_first: dict[str, list[tuple[str, int]]] = defaultdict(list)
    total_words = 0
    for row in words:
        wid = int(row["id"])
        key = row["lemma_key"]
        total_words += 1
        if " " in key:
            phrase_by_first[key.split(" ", 1)[0]].append((key, wid))
        else:
            token_index.setdefault(key, wid)
            strip_index.setdefault(_STRIP_RE.sub("", key), wid)

    #: token -> word_id，0 表示「不是我们要的词」（负缓存）
    resolve_cache: dict[str, int] = {}

    def resolve(token: str) -> int:
        cached = resolve_cache.get(token)
        if cached is not None:
            return cached
        wid = token_index.get(token, 0)
        if not wid:
            wid = strip_index.get(_STRIP_RE.sub("", token), 0)
        resolve_cache[token] = wid
        return wid

    example_candidates: dict[int, list[tuple[int, str]]] = defaultdict(list)
    word_hits: Counter = Counter()
    prep_hits: Counter = Counter()
    pair_hits: Counter = Counter()
    sentence_count = 0

    for raw_id, text in sentences:
        sentence_count += 1
        if on_progress and progress_every and sentence_count % progress_every == 0:
            on_progress(sentence_count)
        if not (MIN_LEN <= len(text) <= MAX_LEN):
            continue
        lowered = text.lower()
        tokens = _TOKEN_RE.findall(lowered)
        if not tokens:
            continue

        matched: dict[int, int] = {}
        for token in tokens:
            wid = resolve(token)
            if wid:
                matched[wid] = matched.get(wid, 0) + 1

        # 短语：只在首词出现时才做子串检查
        for token in set(tokens):
            for phrase, wid in phrase_by_first.get(token, ()):
                if phrase in lowered:
                    matched[wid] = matched.get(wid, 0) + 1

        if not matched:
            continue

        sentence_id = int(raw_id) if str(raw_id).isdigit() else sentence_count
        for wid, count in matched.items():
            word_hits[wid] += count
            # 例句只收「恰好出现一次」的句子，避免一词多现看不出用法
            if count == 1 and len(example_candidates[wid]) < max_examples * 5:
                example_candidates[wid].append((sentence_id, text))

        # 搭配：词 + 紧随其后的介词/小品词
        for index, token in enumerate(tokens):
            if index + 1 >= len(tokens):
                break
            following = tokens[index + 1]
            if following not in PREPOSITIONS:
                continue
            wid = resolve(token)
            if not wid:
                continue
            prep_hits[wid] += 1
            pair_hits[(wid, f"{token} {following}")] += 1

    example_rows: list[tuple] = []
    for wid, candidates in example_candidates.items():
        for seq, (sentence_id, text) in enumerate(_pick_examples(candidates, max_examples)):
            example_rows.append((wid, sentence_id, text, seq))

    collocation_rows = [
        (wid, pattern, hits, word_hits[wid], prep_hits[wid])
        for (wid, pattern), hits in pair_hits.items()
    ]

    top_share: Counter = Counter()
    best: dict[int, tuple[int, str]] = {}
    for (wid, pattern), hits in pair_hits.items():
        current = best.get(wid)
        if current is None or hits > current[0]:
            best[wid] = (hits, pattern)
    for _hits, pattern in best.values():
        top_share[pattern.split()[-1]] += 1

    report = CorpusReport(
        sentences=sentence_count,
        words_total=total_words,
        words_with_example=len(example_candidates),
        examples=len(example_rows),
        words_with_collocation=len({wid for wid, *_ in collocation_rows}),
        collocations=len(collocation_rows),
        top_preposition_share=top_share.most_common(12),
    )
    return example_rows, collocation_rows, report


# --------------------------------------------------------------------------- #
# 落库
# --------------------------------------------------------------------------- #


def build_corpus(
    data_dir: str | Path | None = None,
    corpus: str | Path | None = None,
    max_examples: int = DEFAULT_MAX_EXAMPLES,
    verbose: bool = False,
) -> CorpusReport:
    """扫描语料并写入 ``example`` / ``collocation`` 表。

    与 ``enrich`` 一样必须在 ``build`` 之后运行。
    """
    paths = PP.data_paths(data_dir, create=True)
    if not paths.wordbook.is_file():
        raise BuildError(f"词库不存在：{paths.wordbook}\n先执行：python -m lexicon build")

    source = Path(corpus) if corpus else tatoeba_bz2_path()

    def report_progress(count: int) -> None:
        if verbose:
            print(f"  …已扫描 {count:,} 句", flush=True)

    rows, collocations, result = scan_corpus(
        paths.root,
        iter_sentences(source),
        max_examples=max_examples,
        progress_every=500_000 if verbose else 0,
        on_progress=report_progress,
    )
    result.source_file = str(source)
    result.built_at = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    result.db_path = str(paths.wordbook)

    conn = open_wordbook(data_dir)
    try:
        S.apply_schema(conn)
        conn.execute("DELETE FROM example WHERE source = ?", (SOURCE_ID,))
        conn.execute("DELETE FROM collocation WHERE source = ?", (SOURCE_ID,))
        conn.executemany(
            "INSERT INTO example(word_id, source_id, text_en, text_zh, source, seq)"
            " VALUES (?,?,?,NULL,?,?)",
            [(wid, sid, text, SOURCE_ID, seq) for wid, sid, text, seq in rows],
        )
        conn.executemany(
            "INSERT INTO collocation(word_id, kind, pattern, hits, word_hits, total_hits, source)"
            " VALUES (?,?,?,?,?,?,?)",
            [
                (wid, "prep", pattern, hits, total, prep_total, SOURCE_ID)
                for wid, pattern, hits, total, prep_total in collocations
            ],
        )
        now = result.built_at
        conn.executemany(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)",
            sorted(
                {
                    "corpus_source": SOURCE_ID,
                    "corpus_built_at": now,
                    "corpus_examples": str(result.examples),
                    "corpus_collocations": str(result.collocations),
                }.items()
            ),
        )
        conn.commit()

        stored_examples = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM example WHERE source = ?", (SOURCE_ID,)
            ).fetchone()["n"]
        )
        stored_collocations = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM collocation WHERE source = ?", (SOURCE_ID,)
            ).fetchone()["n"]
        )
        if stored_examples != result.examples or stored_collocations != result.collocations:
            raise CorpusError(
                f"自检失败：例句 {result.examples}→{stored_examples}，"
                f"搭配 {result.collocations}→{stored_collocations}"
            )
    finally:
        conn.close()
    return result


__all__ = [
    "DEFAULT_MAX_EXAMPLES",
    "PREPOSITIONS",
    "SOURCE_ID",
    "TATOEBA_URL",
    "CorpusError",
    "CorpusReport",
    "build_corpus",
    "fetch_tatoeba",
    "iter_sentences",
    "scan_corpus",
    "tatoeba_bz2_path",
]