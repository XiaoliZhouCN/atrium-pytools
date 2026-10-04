# D:\Repositories\Manager\AtriumPyTools\tools\lexicon\lexicon\merge.py
"""把我们的词书合并进 koolearn 分层词表，并重建 1–5 号分层文件。

合并口径（用户确认）：

1. 我们的词书是**一本额外的雅思词书** → 它收录的每个词 ``ielts_books += 1``；
2. 原书 ``*`` 标记 = 听力词汇 → 这些词 ``listen_books += 1``（完整注入技能桶）；
3. ``tier`` / ``skills`` 是派生列，必须跟着重算，否则与 0 号不一致；
4. master 里没有的词按新词条追加（``wd_id``/``url`` 留空，靠 ``sources`` 列区分来源）；
5. 2 号 = 1 号 ∪ 3/4/5 号 ∪ （仅被我们词书覆盖的新增词，标为 ``L3-xdf``）。
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths as PP
from .sources import exact_key, load_xdf_words, norm_key, read_csv, write_csv

#: 单类别「核心」阈值：被 ≥5 本同技能词书收录
CORE_MIN_BOOKS = 5
#: tier 分级（与 koolearn 构建脚本一致）
TIER_CUTS = ((10, "S"), (5, "A"), (3, "B"), (2, "C"))
#: 技能桶：(中文名, 列名, 层标签) —— 用于 master 的 ``skills`` 列（任一桶 >0 即计入）
SKILLS = (
    ("听力", "listen_books", "listening"),
    ("口语", "speaking_books", "speaking"),
    ("阅读", "reading_books", "reading"),
    ("写作", "writing_books", "writing"),
)
#: 会产出「单类别核心」分层文件的技能。
#: 口语刻意排除：koolearn 只有 8 本口语词书、≥5 本收录的核心词仅 29 个，样本不足以单独成表，
#: 原构建脚本也只为这三个技能出文件。注意这与上面 ``skills`` 列的口径不同——
#: ``skills`` 仍包含口语归属，``layers`` 不含。
LAYER_SKILLS = (
    ("听力", "listen_books", "listening"),
    ("阅读", "reading_books", "reading"),
    ("写作", "writing_books", "writing"),
)
#: 仅被我们词书覆盖的词所用的层标签
XDF_LAYER = "L3-xdf"
#: 新增的来源标记列。
#: ``xdf`` / ``xdf_listening`` 记录**来源事实**（我们的词书收录了它 / 原书把它标为听力），
#: ``xdf_listen_counted`` 记录**策略是否已生效**（listen_books 是否已 +1）。
#: 分开存是为了让口径可以从关闭改为开启后安全补做，而不用恢复备份。
SOURCE_COLUMNS = ("sources", "xdf", "xdf_listening", "xdf_listen_counted")

PACK_FIELDS = [
    "word", "wd_id", "url", "layers", "layer_count", "tier", "ielts_books",
    "listen_books", "reading_books", "writing_books", "speaking_books",
    "in_base", "base_level", "sources", "xdf",
]


class MergeError(RuntimeError):
    """合并前置条件不满足。"""


def tier_of(books: int) -> str:
    for cut, label in TIER_CUTS:
        if books >= cut:
            return label
    return "D"


def _int(value: object) -> int:
    try:
        return int(str(value).strip() or 0)
    except (TypeError, ValueError):
        return 0


def _skills_of(row: dict) -> str:
    return ";".join(name for name, col, _ in SKILLS if _int(row.get(col)) > 0)


@dataclass
class MergeReport:
    master_before: int = 0
    master_after: int = 0
    matched_existing: int = 0
    matched_normalized: list[list[str]] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    listen_bumped: int = 0
    listen_backfilled: int = 0
    migrated_listen_counted: int = 0
    xdf_listening_total: int = 0
    tier_changed: int = 0
    skills_changed: int = 0
    untouched_skills_drift: int = 0
    backup_dir: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    def summary_lines(self) -> list[str]:
        lines = [
            f"master      {self.master_before} → {self.master_after} 行",
            f"命中已有词  {self.matched_existing}（其中靠模糊匹配 {len(self.matched_normalized)}）",
            f"新增词条    {len(self.added)}",
            f"听力桶 +1   {self.listen_bumped}（我们的 * 标记共 {self.xdf_listening_total}）",
            f"tier 变化   {self.tier_changed}",
            f"skills 变化 {self.skills_changed}",
        ]
        if self.listen_backfilled:
            lines.append(f"听力桶补做  {self.listen_backfilled}（口径变更后重跑）")
        if self.migrated_listen_counted:
            lines.append(
                f"计数标记推断 {self.migrated_listen_counted}"
                f"（首次引入 xdf_listen_counted 列，按已计入处理）"
            )
        if self.untouched_skills_drift:
            lines.append(
                f"⚠ 未触及行的 skills 发生漂移 {self.untouched_skills_drift}（应为 0）"
            )
        if self.backup_dir:
            lines.append(f"备份        {self.backup_dir}")
        return lines


@dataclass
class LayerReport:
    base: int = 0
    listening: int = 0
    reading: int = 0
    writing: int = 0
    pack: int = 0
    pack_xdf_layer: int = 0
    listening_before: int = 0
    pack_before: int = 0
    json_path: str = ""
    json_words: int = 0

    def as_dict(self) -> dict:
        return asdict(self)

    def summary_lines(self) -> list[str]:
        return [
            f"1 号 底座        {self.base} 行",
            f"3 号 听力核心    {self.listening_before} → {self.listening} 行",
            f"4 号 阅读核心    {self.reading} 行",
            f"5 号 写作核心    {self.writing} 行",
            f"2 号 推荐包      {self.pack_before} → {self.pack} 行"
            f"（其中 {XDF_LAYER} 层 {self.pack_xdf_layer} 词）",
            f"JSON 导出        {self.json_words} 词 → {self.json_path}",
        ]


def backup_files(paths: PP.DataPaths) -> Path:
    """把要改写的 CSV 复制到 ``_backup_<时间戳>/``。"""
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = paths.koolearn / f"_backup_{stamp}"
    target.mkdir(parents=True, exist_ok=True)
    for name in PP.ALL_FILES:
        source = paths.file(name)
        if source.is_file():
            shutil.copy2(source, target / name)
    return target


def _new_master_row(word: dict, xdf_listening: bool) -> dict:
    """我们的词书里有、koolearn 没有的词条。"""
    counts = bool(word["listening"]) and xdf_listening
    return {
        "word": word["display"],
        "wd_id": "",
        "url": "",
        "kind": word["kind"],
        "tier": tier_of(1),
        "ielts_books": "1",
        "listen_books": "1" if counts else "0",
        "reading_books": "0",
        "writing_books": "0",
        "speaking_books": "0",
        "skills": "听力" if counts else "",
        "in_base": "0",
        "base_level": "0",
        "base_books": "0",
        "base_source": "",
        "sources": "xdf",
        "xdf": "1",
        "xdf_listening": "1" if word["listening"] else "0",
        "xdf_listen_counted": "1" if counts else "0",
    }


def merge_master(
    paths: PP.DataPaths,
    xdf_listening: bool = True,
    backup: bool = True,
    force: bool = False,
) -> MergeReport:
    """合并我们的词书进 0 号总表。

    **幂等**：已标记 ``xdf == "1"`` 的行不会再给 ``ielts_books`` 加一次，
    因此重复执行安全；重跑只会补充新出现的词头。
    ``force`` 仅用于跳过「已合并过」的安全拦截。

    若首次用 ``xdf_listening=False`` 跑过，之后想改口径，重跑会**补做**听力桶
    （靠 ``xdf_listening`` 列判断是否已计），不会重复计 ``ielts_books``。
    """
    master = paths.master
    if not master.is_file():
        raise MergeError(f"总表不存在：{master}")

    report = MergeReport()
    if backup:
        report.backup_dir = str(backup_files(paths))

    fields, rows = read_csv(master)
    report.master_before = len(rows)

    for column in SOURCE_COLUMNS:
        if column not in fields:
            fields.append(column)

    # 首次引入 xdf_listen_counted 列时，旧数据无法区分「标记为听力」与「已计入」。
    # 按「标记为听力即已计入」推断，否则重跑会把 listen_books 再加一遍。
    counted_column_existed = "xdf_listen_counted" in read_csv(master)[0]
    for row in rows:
        row.setdefault("sources", "koolearn")
        row.setdefault("xdf", "0")
        row.setdefault("xdf_listening", "0")
        if counted_column_existed:
            row.setdefault("xdf_listen_counted", "0")
        else:
            inferred = row["xdf_listening"] if row["xdf"] == "1" else "0"
            row.setdefault("xdf_listen_counted", inferred)
            if inferred == "1":
                report.migrated_listen_counted += 1

    already = sum(1 for row in rows if row["xdf"] == "1")
    if already and not force:
        raise MergeError(
            f"总表里已有 {already} 条我们的词书记录，说明合并执行过了。\n"
            f"再跑一次会把 ielts_books 再加一遍。请先恢复备份"
            f"（数据目录下的 _backup_* 或 _backup_original），"
            f"确认无误后再用 --force 强制继续。"
        )

    exact: dict[str, dict] = {}
    for row in rows:
        exact.setdefault(exact_key(row["word"]), row)
    fuzzy: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        fuzzy[norm_key(row["word"])].append(row)

    words = load_xdf_words(paths.wordbook)
    report.xdf_listening_total = sum(1 for w in words if w["listening"])
    appended: list[dict] = []

    for word in words:
        row = exact.get(word["key"])
        if row is None:
            candidates = fuzzy.get(norm_key(word["key"]), [])
            # 只在目标唯一时才靠模糊匹配并词，避免误并
            if len(candidates) == 1:
                row = candidates[0]
                report.matched_normalized.append([word["display"], row["word"]])

        if row is None:
            appended.append(_new_master_row(word, xdf_listening))
            report.added.append(word["display"])
            continue

        if row["xdf"] == "1":
            # 已合并过：不重复计 ielts_books；但若首次关掉了听力口径，这里补做
            if (
                xdf_listening
                and word["listening"]
                and row["xdf_listen_counted"] != "1"
            ):
                row["listen_books"] = str(_int(row["listen_books"]) + 1)
                row["xdf_listen_counted"] = "1"
                row["xdf_listening"] = "1"
                report.listen_backfilled += 1
            continue
        row["ielts_books"] = str(_int(row["ielts_books"]) + 1)
        row["xdf"] = "1"
        row["sources"] = "koolearn;xdf"
        report.matched_existing += 1
        if word["listening"]:
            row["xdf_listening"] = "1"
            if xdf_listening:
                row["listen_books"] = str(_int(row["listen_books"]) + 1)
                row["xdf_listen_counted"] = "1"
                report.listen_bumped += 1

    rows.extend(appended)

    # 派生列重算：tier 与 skills 必须跟随计数，否则与各分层文件不一致
    for row in rows:
        tier = tier_of(_int(row["ielts_books"]))
        if tier != row["tier"]:
            report.tier_changed += 1
            row["tier"] = tier
        skills = _skills_of(row)
        if skills != row["skills"]:
            if row["xdf"] == "0":
                report.untouched_skills_drift += 1
            report.skills_changed += 1
            row["skills"] = skills

    rows.sort(
        key=lambda r: (
            r["tier"],
            -_int(r["ielts_books"]),
            -_int(r["base_books"]),
            r["word"].lower(),
        )
    )
    write_csv(master, fields, rows)
    report.master_after = len(rows)
    return report


def build_layers(paths: PP.DataPaths, backup: bool = False) -> LayerReport:
    """从 0 号总表重建 1–5 号分层文件。"""
    report = LayerReport()
    if backup:
        backup_files(paths)

    fields, rows = read_csv(paths.master)

    base_rows = [r for r in rows if r["in_base"] == "1"]
    write_csv(
        paths.file(PP.BASE),
        ["word", "wd_id", "url", "base_level", "base_source"],
        base_rows,
    )
    report.base = len(base_rows)

    for _name, column, slug in LAYER_SKILLS:
        target = {
            "listen_books": PP.LISTENING,
            "reading_books": PP.READING,
            "writing_books": PP.WRITING,
        }[column]
        subset = [r for r in rows if _int(r[column]) >= CORE_MIN_BOOKS]
        if column == "listen_books":
            report.listening_before = len(
                [r for r in read_csv(paths.file(target))[1]]
            ) if paths.file(target).is_file() else 0
            report.listening = len(subset)
        elif column == "reading_books":
            report.reading = len(subset)
        else:
            report.writing = len(subset)
        write_csv(
            paths.file(target),
            ["word", "wd_id", "url", column, "tier", "base_level"],
            subset,
        )

    existing_pack = read_csv(paths.file(PP.PACK))[1] if paths.file(PP.PACK).is_file() else []
    report.pack_before = len(existing_pack)

    pack: list[dict] = []
    for row in rows:
        layers: list[str] = []
        if row["in_base"] == "1":
            layers.append(f"L1-base-L{row['base_level']}")
        for _name, column, slug in LAYER_SKILLS:
            if _int(row[column]) >= CORE_MIN_BOOKS:
                layers.append(f"L2-{slug}")
        # 只被我们词书覆盖、且不在任何 koolearn 层里的词 → 单列一层。
        # 用 .get：总表可能尚未合并（没有 xdf 列），此时视为不属于我们词书。
        if not layers and row.get("xdf") == "1":
            layers.append(XDF_LAYER)
        if not layers:
            continue
        item = dict(row)
        item["layers"] = ";".join(layers)
        item["layer_count"] = len(layers)
        pack.append(item)

    pack.sort(
        key=lambda r: (
            -int(r["layer_count"]),
            r["tier"],
            -_int(r["ielts_books"]),
            r["word"].lower(),
        )
    )
    write_csv(paths.file(PP.PACK), PACK_FIELDS, pack)
    report.pack = len(pack)
    report.pack_xdf_layer = sum(1 for r in pack if r["layers"] == XDF_LAYER)

    # JSON 是 0 号的派生视图，必须跟着重建，否则会留旧快照
    exported = export_master_json(paths)
    report.json_path = exported["path"]
    report.json_words = exported["words"]
    return report


# --------------------------------------------------------------------------- #
# JSON 导出
# --------------------------------------------------------------------------- #

#: 导出 JSON 时转成数字的列（CSV 里都是字符串）
JSON_INT_FIELDS = (
    "ielts_books", "listen_books", "reading_books", "writing_books",
    "speaking_books", "in_base", "base_level", "base_books",
    "xdf", "xdf_listening", "xdf_listen_counted",
)


def export_master_json(
    paths: PP.DataPaths, output: str | Path | None = None
) -> dict:
    """把 0 号总表导出为 ``ielts_layered.json``（保留原 meta，刷新计数与时间）。

    这个 JSON 是 0 号的**派生视图**，所以 ``build_layers`` 之后必须重新导出，
    否则会与 CSV 不一致（早期版本就踩过：合并后 JSON 仍是 26,728 词的旧快照）。
    """
    target = Path(output) if output else paths.koolearn / PP.LAYERED_JSON
    _, rows = read_csv(paths.master)

    # 保留抓取侧的 meta（书单、分类、口径说明等），只刷新会变的部分
    meta: dict = {}
    previous_words = None
    if target.is_file():
        try:
            previous = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(previous.get("meta"), dict):
                meta = dict(previous["meta"])
            previous_words = previous.get("words")
        except (OSError, json.JSONDecodeError):
            meta = {}

    words: list[dict] = []
    for row in rows:
        item: dict = {}
        for key, value in row.items():
            if key in JSON_INT_FIELDS:
                item[key] = _int(value)
            else:
                item[key] = value
        words.append(item)

    # 词表和上次一模一样时不刷新时间戳：否则每次 layers/all 都会重写这个
    # 12 MB 文件，git 里永远显示「已修改」，制造 60 万行无意义 diff。
    content_changed = previous_words != words
    if content_changed or not meta.get("generated_at"):
        stamp = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    else:
        stamp = meta["generated_at"]

    counts = dict(meta.get("counts") or {})
    counts["union_words"] = len(rows)
    counts["xdf_words"] = sum(1 for r in rows if r.get("xdf") == "1")
    meta["generated_at"] = stamp
    meta["counts"] = counts
    meta["generated_by"] = "lexicon export-json"
    meta["notes"] = (
        "0 号总表的派生视图。已合并本词书（sources/xdf 列标记来源），"
        "字段含义见 tools/lexicon/docs/koolearn-layered-list.md。"
    )

    payload = {"meta": meta, "words": words}
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if not target.is_file() or target.read_text(encoding="utf-8") != text:
        target.write_text(text, encoding="utf-8")
    return {
        "path": str(target),
        "words": len(words),
        "bytes": target.stat().st_size,
        "generated_at": stamp,
        "changed": content_changed,
    }
