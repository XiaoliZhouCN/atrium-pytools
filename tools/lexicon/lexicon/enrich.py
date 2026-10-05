# D:\Repositories\Manager\AtriumPyTools\tools\lexicon\lexicon\enrich.py
"""用外部词库补全词条：ECDICT → ``enrichment`` 表。

为什么是 ECDICT：它是**免费英汉双解词典数据库**（76 万词条），以 **MIT 许可**发布
（<https://github.com/skywind3000/ECDICT>），明确允许使用、复制、修改、分发。
字段覆盖我们缺的东西：英文释义、词形变化、柯林斯星级、牛津 3000 标记、
考试标签（含 ielts）、BNC 与当代语料库双词频。

**不碰任何付费词典内容**（如牛津高阶网络版），理由见项目 README。

设计要点：

* 66 MB 的 CSV 只当作**下载缓存**放在工具目录 ``.cache/``，不进任何仓库、不进 git；
* 词库里只落**命中的那一部分**（约 3600 行），所以 ``wordbook.db`` 不会被撑大；
* 补全是**独立步骤**（``enrich`` 子命令），因为 ``build`` 会重建整库。
"""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import sqlite3
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths as PP
from . import schema as S
from .build import BuildError, open_wordbook
from .parse import _normalise_lemma

ECDICT_URL = "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv"
ECDICT_FILENAME = "ecdict.csv"
SOURCE_ID = "ecdict"
CACHE_DIRNAME = ".cache"

#: 词形变化代码 → 中文标签（见 ECDICT README）
EXCHANGE_LABELS = {
    "p": "过去式",
    "d": "过去分词",
    "i": "现在分词",
    "3": "第三人称单数",
    "r": "比较级",
    "t": "最高级",
    "s": "复数",
    "0": "原形",
    "1": "原形变体",
}

#: ECDICT 音标里混入的西里尔／希腊形近字母 → 国际音标
_PHONETIC_FIXES = str.maketrans({"\u04d9": "\u0259", "\u0473": "\u0259"})


class EnrichError(RuntimeError):
    """补全前置条件不满足。"""


def cache_dir() -> Path:
    """工具目录下的下载缓存（应加入 .gitignore）。"""
    return Path(__file__).resolve().parent.parent / CACHE_DIRNAME


def ecdict_path() -> Path:
    return cache_dir() / ECDICT_FILENAME


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_ecdict(
    force: bool = False, url: str = ECDICT_URL, dest: str | Path | None = None
) -> dict:
    """下载 ECDICT CSV 到缓存目录；已存在则跳过（``force`` 强制重下）。"""
    target = Path(dest).expanduser() if dest else ecdict_path()
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


def stripword(word: str) -> str:
    """ECDICT 的模糊匹配键：只保留字母数字并转小写。"""
    return "".join(ch for ch in word if ch.isalnum()).lower()


def decode_exchange(text: str) -> str:
    """把 ``d:corrupted/i:corrupting`` 解码成可读的「过去分词 corrupted；现在分词 corrupting」。"""
    if not text:
        return ""
    parts: list[str] = []
    for item in text.split("/"):
        if ":" not in item:
            continue
        code, _, value = item.partition(":")
        code = code.strip()
        value = value.strip()
        if not value:
            continue
        parts.append(f"{EXCHANGE_LABELS.get(code, code)} {value}")
    return "；".join(parts)


def _norm_phonetic(text: str) -> tuple[str, bool]:
    """统一音标里的形近字母；返回 (音标, 是否修正过)。"""
    if not text:
        return "", False
    fixed = text.translate(_PHONETIC_FIXES)
    return fixed, fixed != text


def _int_or_none(value: str | None) -> int | None:
    try:
        text = str(value or "").strip()
        return int(text) if text else None
    except ValueError:
        return None


def _one_line(text: str | None) -> str:
    """把 ECDICT 的多行释义压成一行可读形式。

    ECDICT 用的是**字面量** ``\\n``（反斜杠 + n）分隔释义，不是真换行——
    直接 splitlines() 不会切分，会把 ``\\n`` 原样漏到输出里。两种都兼容。
    """
    if not text:
        return ""
    # 先合并连续的字面量 \n（含 \r\n 的转义形态），再统一切分
    normalized = str(text).replace("\\r\\n", "\n").replace("\\n", "\n")
    return " / ".join(part.strip() for part in normalized.splitlines() if part.strip())


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #


@dataclass
class EnrichReport:
    source: str = SOURCE_ID
    source_file: str = ""
    source_sha256: str = ""
    scanned: int = 0
    targets: int = 0
    matched: int = 0
    by_exact: int = 0
    by_strip: int = 0
    unmatched: list[str] = field(default_factory=list)
    phonetic_fixed: int = 0
    built_at: str = ""
    db_path: str = ""

    @property
    def coverage(self) -> float:
        return (self.matched / self.targets * 100) if self.targets else 0.0

    def as_dict(self) -> dict:
        d = asdict(self)
        d["coverage_percent"] = round(self.coverage, 2)
        return d

    def summary_lines(self) -> list[str]:
        return [
            f"来源        {self.source}（{self.source_file}）",
            f"来源 sha256 {self.source_sha256[:16]}…",
            f"扫描词条    {self.scanned}",
            f"目标词条    {self.targets}",
            f"命中        {self.matched}（{self.coverage:.1f}%）"
            f" = 精确 {self.by_exact} + 模糊 {self.by_strip}",
            f"未命中      {len(self.unmatched)}",
            f"音标字形修正 {self.phonetic_fixed}",
        ]


# --------------------------------------------------------------------------- #
# 扫描与落库
# --------------------------------------------------------------------------- #


def scan_ecdict(path: Path, targets: set[str]) -> tuple[dict[str, dict], int]:
    """一次扫全表，返回 ``(命中, 总行数)``。

    命中优先级：精确小写 > stripword 模糊（抹掉空格/连字符，兼容 ``spot on`` vs ``spot-on``）。

    不做「按原形回退」：实测 3610 词里精确+模糊已命中 99.9%，回退贡献 0，
    而它需要第二遍扫 66 MB 才能实现——不划算，等覆盖真的不够再加。
    """
    strip_index = {stripword(t): t for t in targets}
    hits: dict[str, dict] = {}
    total = 0

    with Path(path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            total += 1
            raw = (row.get("word") or "").strip()
            if not raw:
                continue
            low = raw.lower()
            if low in targets:
                key, how = low, "exact"
            else:
                key = strip_index.get(stripword(raw))
                how = "strip" if key else ""
            if key and key not in hits:
                hits[key] = {
                    "row": row,
                    "matched_by": how,
                    "source_word": raw,
                }
    return hits, total


def build_enrichment(
    data_dir: str | Path | None = None,
    ecdict: str | Path | None = None,
    source_sha256: str | None = None,
) -> EnrichReport:
    """把 ECDICT 数据补进 ``wordbook.db`` 的 ``enrichment`` 表。

    必须在 ``build`` 之后运行（依赖已分配的 ``word.id``）。
    """
    source = Path(ecdict).expanduser() if ecdict else ecdict_path()
    if not source.is_file():
        raise EnrichError(
            f"找不到 ECDICT 数据：{source}\n先执行：python -m lexicon fetch-ecdict"
        )

    paths = PP.data_paths(data_dir, create=True)
    if not paths.wordbook.is_file():
        raise BuildError(
            f"词库不存在：{paths.wordbook}\n先执行：python -m lexicon build"
        )

    conn = open_wordbook(data_dir)
    try:
        S.apply_schema(conn)
        rows = conn.execute("SELECT id, lemma_key FROM word ORDER BY id").fetchall()
        if not rows:
            raise EnrichError("词库里没有词条，先执行 build")
        id_by_key = {row["lemma_key"]: int(row["id"]) for row in rows}
        targets = set(id_by_key)
        before = int(conn.execute("SELECT COUNT(*) AS n FROM enrichment").fetchone()["n"])

        hits, scanned = scan_ecdict(source, targets)

        digest = source_sha256 or sha256_file(source)
        now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()

        report = EnrichReport(
            source_file=str(source),
            source_sha256=digest,
            scanned=scanned,
            targets=len(targets),
            built_at=now,
            db_path=str(paths.wordbook),
        )

        payload: list[tuple] = []
        for key, hit in sorted(hits.items()):
            row = hit["row"]
            phonetic, was_fixed = _norm_phonetic((row.get("phonetic") or "").strip())
            if was_fixed:
                report.phonetic_fixed += 1
            how = hit["matched_by"]
            if how == "exact":
                report.by_exact += 1
            else:
                report.by_strip += 1
            payload.append(
                (
                    id_by_key[key],
                    SOURCE_ID,
                    hit["source_word"],
                    phonetic or None,
                    _one_line(row.get("translation")) or None,
                    _one_line(row.get("definition")) or None,
                    (row.get("pos") or "").strip() or None,
                    _int_or_none(row.get("collins")),
                    1 if (row.get("oxford") or "").strip() == "1" else 0,
                    (row.get("tag") or "").strip() or None,
                    _int_or_none(row.get("bnc")),
                    _int_or_none(row.get("frq")),
                    (row.get("exchange") or "").strip() or None,
                    decode_exchange(row.get("exchange") or "") or None,
                    how,
                    digest,
                    now,
                )
            )

        conn.execute("DELETE FROM enrichment WHERE source = ?", (SOURCE_ID,))
        conn.executemany(
            "INSERT INTO enrichment(word_id, source, source_word, phonetic, translation,"
            " definition, pos, collins, oxford, tags, bnc, frq, exchange, exchange_cn,"
            " matched_by, source_sha256, fetched_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            payload,
        )

        report.matched = len(payload)
        report.unmatched = sorted(targets - set(hits))

        meta = {
            "enrichment_source": SOURCE_ID,
            "enrichment_sha256": digest,
            "enrichment_built_at": now,
            "enrichment_matched": str(report.matched),
        }
        conn.executemany(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", sorted(meta.items())
        )
        conn.commit()

        after = int(conn.execute("SELECT COUNT(*) AS n FROM enrichment").fetchone()["n"])
        if after != report.matched:
            raise EnrichError(
                f"自检失败：写入 {report.matched} 条，实际 {after} 条（补全前有 {before} 条）"
            )
        return report
    finally:
        conn.close()


def enrichment_for(conn: sqlite3.Connection, word_id: int) -> dict | None:
    """取某个词条的补全数据（没有则返回 None）。"""
    row = conn.execute(
        "SELECT * FROM enrichment WHERE word_id = ? ORDER BY source LIMIT 1",
        (word_id,),
    ).fetchone()
    if row is None:
        return None
    record = dict(row)
    tags = (record.get("tags") or "").split()
    return {
        "source": record["source"],
        "source_word": record["source_word"],
        "phonetic": record["phonetic"],
        "translation": record["translation"],
        "definition": record["definition"],
        "pos": record["pos"],
        "collins": record["collins"],
        "oxford": bool(record["oxford"]),
        "tags": tags,
        "bnc": record["bnc"],
        "frq": record["frq"],
        "exchange": record["exchange"],
        "exchange_cn": record["exchange_cn"],
        "matched_by": record["matched_by"],
    }


__all__ = [
    "ECDICT_URL",
    "SOURCE_ID",
    "EnrichError",
    "EnrichReport",
    "build_enrichment",
    "cache_dir",
    "decode_exchange",
    "ecdict_path",
    "enrichment_for",
    "fetch_ecdict",
    "scan_ecdict",
    "sha256_file",
    "stripword",
]
