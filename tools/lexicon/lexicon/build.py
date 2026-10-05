# D:\Repositories\Manager\AtriumPyTools\tools\lexicon\lexicon\build.py
"""构建词库：原文 → wordbook.db + needs_review.csv + build_report.json。

构建是**幂等**的：每次都从原文完整重建词典层，不做增量合并。
理由：词典层是派生产物，重建成本极低；增量合并才是 bug 与状态漂移的来源。
人工修订通过 ``overrides/overrides.json`` 表达，不直接改库。
"""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import json
import shutil
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import parse as P
from . import paths as PP
from . import schema as S

PARSER_VERSION = "0.1.0"

#: 归档到 raw/ 的默认文件名
DEFAULT_SOURCE_NAME = "ielts_xdf_2015.txt"
OVERRIDES_FILENAME = "overrides.json"

SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}


class BuildError(RuntimeError):
    """原文缺失或构建后自检不通过。"""


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #


@dataclass
class BuildReport:
    data_root: str
    source_file: str
    source_sha256: str
    built_at: str
    parser_version: str
    schema_version: int
    total_lines: int
    parsed_entries: int
    unparsed_lines: int
    words: int
    units: int
    senses: int
    phrases: int
    occurrences: int
    list_numbers: list[int]
    issue_counts: dict[str, int] = field(default_factory=dict)
    severity_counts: dict[str, int] = field(default_factory=dict)
    collections: dict[str, int] = field(default_factory=dict)
    overrides: dict[str, int] = field(default_factory=dict)
    db_path: str = ""
    review_csv: str = ""
    report_json: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    def summary_lines(self) -> list[str]:
        lines = [
            f"数据目录    {self.data_root}",
            f"原文        {self.source_file}",
            f"原文 sha256 {self.source_sha256[:16]}…",
            f"总行数      {self.total_lines}",
            f"解析词条    {self.parsed_entries}",
            f"未识别行    {self.unparsed_lines}",
            f"唯一词条    {self.words}（短语 {self.phrases}）",
            f"背诵单元    {self.units}（词头+词性，如 corrupt[v.]）",
            f"义项        {self.senses}",
            f"原书出处    {self.occurrences}（{len(self.list_numbers)} 个 Word List）",
            f"词单        {len(self.collections)} 个",
        ]
        if self.overrides:
            parts = "、".join(f"{k}={v}" for k, v in self.overrides.items() if v)
            if parts:
                lines.append(f"人工修订    {parts}")
        if self.severity_counts:
            parts = "、".join(
                f"{k}={v}" for k, v in sorted(self.severity_counts.items())
            )
            lines.append(f"解析信号    {parts}")
        return lines


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def read_source_text(path: Path) -> str:
    """读取原文；兼容带 BOM 的 UTF-8。"""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise BuildError(f"无法以 UTF-8/GB18030 解码：{path}")


def import_source(
    source: str | Path, data_dir: str | Path | None = None, name: str | None = None
) -> dict:
    """把原文归档到 ``<数据目录>/raw/``，返回来源信息。"""
    src = Path(source).expanduser().resolve()
    if not src.is_file():
        raise BuildError(f"原文不存在：{src}")

    paths = PP.data_paths(data_dir, create=True)
    target = paths.source_file(name or src.name)
    if src != target:
        shutil.copy2(src, target)
    return {
        "source": str(target),
        "archived_from": str(src),
        "sha256": sha256_file(target),
        "bytes": target.stat().st_size,
    }


def load_overrides(paths: PP.DataPaths) -> dict:
    """读取人工修订文件；不存在时返回空结构。"""
    path = paths.overrides / OVERRIDES_FILENAME
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"修订文件不可解析：{path}（{exc}）") from exc
    if not isinstance(payload, dict):
        raise BuildError(f"修订文件顶层必须是对象：{path}")
    return payload


def _phonetic_from_text(text: str, source: str | None = None) -> P.Phonetic:
    """把 '/…/' 这类字符串还原为 Phonetic（识别定界符以定来源）。"""
    stripped = text.strip()
    for left, right, name in (("/", "/", "lingoes"), ("[", "]", "baidu"), ("{", "}", "book")):
        if stripped.startswith(left) and stripped.endswith(right) and len(stripped) > 2:
            return P.Phonetic(stripped[1:-1], source or name)
    return P.Phonetic(stripped, source or "manual")


def _senses_from_override(raw_senses: list[dict]) -> list[P.Sense]:
    senses: list[P.Sense] = []
    for index, item in enumerate(raw_senses, start=1):
        gloss = str(item.get("gloss_cn", "")).strip()
        if not gloss:
            continue
        pos = item.get("pos")
        phonetics = [
            _phonetic_from_text(text) for text in item.get("phonetics", []) or []
        ]
        senses.append(
            P.Sense(
                pos=str(pos).strip() if pos else None,
                gloss_cn=gloss,
                sense_no=int(item.get("sense_no", index)),
                phonetics=phonetics,
                raw_segment="(override)",
            )
        )
    return senses


def apply_overrides(
    entries: list[P.Entry], overrides: dict
) -> tuple[list[P.Entry], dict[str, int]]:
    """应用人工修订：drop / patches / add。返回 (词条, 统计)。"""
    stats = {"dropped": 0, "patched": 0, "added": 0}
    dropped_keys = {P._normalise_lemma(str(k)) for k in overrides.get("drop", [])}
    patches = {
        P._normalise_lemma(str(k)): v for k, v in (overrides.get("patches") or {}).items()
    }

    result: list[P.Entry] = []
    for entry in entries:
        if entry.lemma_key in dropped_keys:
            stats["dropped"] += 1
            continue
        patch = patches.get(entry.lemma_key)
        if patch:
            stats["patched"] += 1
            if "display" in patch:
                entry.display = str(patch["display"])
                entry.lemma_key = P._normalise_lemma(entry.display)
                entry.kind = (
                    "phrase" if P._RE_HEAD_SEP.search(entry.display) else "word"
                )
            if "kind" in patch:
                entry.kind = str(patch["kind"])
            if "tags" in patch:
                entry.tags = [str(t) for t in patch["tags"]]
            if "phonetic" in patch:
                phon = patch["phonetic"]
                if phon:
                    entry.phonetics = [
                        _phonetic_from_text(str(phon), patch.get("phonetic_src"))
                    ]
                else:
                    entry.phonetics = []
            if "senses" in patch:
                entry.senses = _senses_from_override(patch["senses"] or [])
            entry.issues.append(
                P.Issue(entry.line_no, "overridden", "info", "该词条已人工修订", entry.raw)
            )
        result.append(entry)
        patches.pop(entry.lemma_key, None)

    for key in patches:
        raise BuildError(f"修订文件 patches 中的 {key!r} 在原书中不存在")

    for index, item in enumerate(overrides.get("add", []) or [], start=1):
        display = str(item.get("display") or item.get("lemma") or "").strip()
        if not display:
            raise BuildError("修订文件 add 项缺少 lemma/display")
        senses = _senses_from_override(item.get("senses") or [])
        if not senses:
            raise BuildError(f"修订文件 add 项 {display!r} 没有任何义项")
        phonetics = [
            _phonetic_from_text(str(item["phonetic"]), item.get("phonetic_src"))
        ] if item.get("phonetic") else []
        result.append(
            P.Entry(
                display=display,
                lemma_key=P._normalise_lemma(display),
                kind=str(
                    item.get("kind")
                    or ("phrase" if P._RE_HEAD_SEP.search(display) else "word")
                ),
                tags=[str(t) for t in item.get("tags", []) or []],
                list_no=0,
                seq_no=index,
                line_no=0,
                raw="(override)",
                phonetics=phonetics,
                senses=senses,
                issues=[
                    P.Issue(
                        0,
                        "user_added",
                        "info",
                        f"{display} 来自人工新增（{item.get('collection') or '未归类'}）",
                        "(override)",
                    )
                ],
            )
        )
        stats["added"] += 1

    return result, stats


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def _first_word_phonetics(occurrences: list[P.Entry]) -> list[P.Phonetic]:
    """合并同词多次出现时的音标：按出现顺序去重。"""
    seen: set[str] = set()
    ordered: list[P.Phonetic] = []
    for entry in occurrences:
        for phon in entry.phonetics:
            if phon.delimited not in seen:
                seen.add(phon.delimited)
                ordered.append(phon)
    return ordered


def resolve_source(paths: PP.DataPaths, source: str | Path | None = None) -> Path:
    """定位要解析的原文。

    顺序：显式指定 → ``raw/<默认名>`` → ``raw/`` 下唯一的文件。
    最后一条是必要的易用性兜底：用户直接 ``import "IELTS Word List.txt"`` 时
    归档名与默认名不同，否则 ``build`` 会莫名其妙找不到文件。
    """
    if source is not None:
        candidate = Path(source).expanduser().resolve()
        if not candidate.is_file():
            raise BuildError(f"原文不存在：{candidate}")
        return candidate

    default = paths.source_file(DEFAULT_SOURCE_NAME)
    if default.is_file():
        return default

    candidates = sorted(f for f in paths.raw.glob("*") if f.is_file())
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise BuildError(
            f"{paths.raw} 下没有原文。\n"
            '先执行：python -m lexicon import "<原文件路径>"'
        )
    listing = "\n".join(f"  {c}" for c in candidates)
    raise BuildError(f"raw/ 下有多个原文，请用 --source 指定其一：\n{listing}")


def build_wordbook(
    source: str | Path | None = None, data_dir: str | Path | None = None
) -> BuildReport:
    """从原文完整重建词库。"""
    paths = PP.data_paths(data_dir, create=True)
    source_path = resolve_source(paths, source)

    text = read_source_text(source_path)
    result = P.parse_text(text)
    P.annotate_duplicates(result)

    if not result.entries:
        raise BuildError(f"未从 {source_path} 解析出任何词条，请检查文件格式")

    overrides = load_overrides(paths)
    entries, override_stats = apply_overrides(result.entries, overrides)

    # ---- 合并同词的多次出现 ----
    merged: dict[str, list[P.Entry]] = {}
    for entry in entries:
        merged.setdefault(entry.lemma_key, []).append(entry)

    db_path = paths.wordbook
    if db_path.exists():
        db_path.unlink()

    conn = S.connect(db_path)
    try:
        S.apply_schema(conn)
        now = _utc_now()
        tag_ids: dict[str, int] = {}

        word_rows: list[tuple] = []
        unit_rows: list[tuple] = []
        sense_rows: list[tuple] = []
        occurrence_rows: list[tuple] = []
        fts_rows: list[tuple] = []
        tag_links: list[tuple] = []
        collection_members: dict[str, list[int]] = {}
        next_word_id = 1
        next_unit_id = 1
        next_sense_id = 1
        word_id_by_key: dict[str, int] = {}

        for lemma_key, group in merged.items():
            head = group[0]
            phonetics = _first_word_phonetics(group)

            word_id = next_word_id
            next_word_id += 1
            word_id_by_key[lemma_key] = word_id

            word_rows.append(
                (
                    word_id,
                    P._normalise_lemma(head.display),
                    lemma_key,
                    head.display,
                    head.kind,
                    phonetics[0].delimited if phonetics else None,
                    phonetics[0].source if phonetics else None,
                    json.dumps([p.delimited for p in phonetics[1:]], ensure_ascii=False)
                    if len(phonetics) > 1
                    else None,
                    P.SOURCE_ID,
                    min(e.line_no for e in group),
                    now,
                )
            )

            # 义项：同词多次出现时按出现顺序合并去重
            seen_senses: set[tuple[str | None, str]] = set()
            merged_senses: list[P.Sense] = []
            for entry in group:
                for sense in entry.senses:
                    dedupe = (sense.pos, sense.gloss_cn)
                    if dedupe in seen_senses:
                        continue
                    seen_senses.add(dedupe)
                    merged_senses.append(sense)

            # 背诵单元：按词性归组，音标随词性走。
            #
            # 原书里一个音标标记一个「读音组」的起点，其后所有词性共用它，
            # 直到出现下一个音标。所以：
            #   corrupt /kəˈrʌpt/ v. … a. …        → v. 和 a. 都是 /kəˈrʌpt/
            #   desert /ˈdezət/ n. … a. … /dɪˈzɜːt/ v. …  → a. 继承 /ˈdezət/
            #   reject /rɪˈdʒekt/ vt. … /ˈriːdʒekt/ n. …  → 两者各自独立
            units: list[dict] = []
            unit_index_by_pos: dict[str | None, int] = {}
            active: list[P.Phonetic] = []
            for sense in merged_senses:
                if sense.phonetics:
                    active = list(sense.phonetics)
                index = unit_index_by_pos.get(sense.pos)
                if index is None:
                    index = len(units)
                    unit_index_by_pos[sense.pos] = index
                    units.append({"pos": sense.pos, "phonetics": list(active), "glosses": []})
                slot = units[index]
                if not slot["phonetics"] and active:
                    slot["phonetics"] = list(active)
                if sense.gloss_cn not in slot["glosses"]:
                    slot["glosses"].append(sense.gloss_cn)

            unit_id_by_pos: dict[str | None, int] = {}
            for pos_no, slot in enumerate(units, start=1):
                pos = slot["pos"]
                own = slot["phonetics"]
                body = "；".join(slot["glosses"])
                unit_id = next_unit_id
                next_unit_id += 1
                unit_id_by_pos[pos] = unit_id
                unit_rows.append(
                    (
                        unit_id,
                        word_id,
                        pos,
                        own[0].delimited if own else None,
                        own[0].source if own else None,
                        json.dumps([p.delimited for p in own[1:]], ensure_ascii=False)
                        if len(own) > 1
                        else None,
                        f"{pos} {body}" if pos else body,
                        body,
                        pos_no,
                        f"{lemma_key}[{pos}]" if pos else lemma_key,
                    )
                )

            gloss_parts: list[str] = []
            for sense_no, sense in enumerate(merged_senses, start=1):
                sense_rows.append(
                    (
                        next_sense_id,
                        word_id,
                        unit_id_by_pos.get(sense.pos),
                        sense.pos,
                        sense.gloss_cn,
                        sense_no,
                        sense.phonetic,
                        sense.raw_segment,
                    )
                )
                next_sense_id += 1
                gloss_parts.append(sense.gloss_cn)

            fts_rows.append((word_id, P._normalise_lemma(head.display), " ".join(gloss_parts)))

            for entry in group:
                occurrence_rows.append((word_id, entry.line_no, entry.list_no, entry.seq_no))
                collection_members.setdefault(f"List {entry.list_no:02d}", []).append(word_id)

            collection_members.setdefault("全库", []).append(word_id)
            if head.kind == "phrase":
                collection_members.setdefault("短语", []).append(word_id)

            for tag in head.tags:
                if tag not in tag_ids:
                    cursor = conn.execute(
                        "INSERT INTO tag(name) VALUES (?)", (tag,)
                    )
                    tag_ids[tag] = int(cursor.lastrowid)
                tag_links.append((word_id, tag_ids[tag]))

        conn.executemany(
            "INSERT INTO word(id, lemma, lemma_key, display, kind, phonetic,"
            " phonetic_src, phonetic_alt, source, source_line, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            word_rows,
        )
        conn.executemany(
            "INSERT INTO pos_entry(id, word_id, pos, phonetic, phonetic_src, phonetic_alt,"
            " gloss_cn, gloss_body, pos_no, entry_key) VALUES (?,?,?,?,?,?,?,?,?,?)",
            unit_rows,
        )
        conn.executemany(
            "INSERT INTO sense(id, word_id, pos_entry_id, pos, gloss_cn, sense_no,"
            " phonetic, raw_segment) VALUES (?,?,?,?,?,?,?,?)",
            sense_rows,
        )
        conn.executemany(
            "INSERT INTO occurrence(word_id, line_no, list_no, seq_no) VALUES (?,?,?,?)",
            occurrence_rows,
        )
        conn.executemany(
            "INSERT INTO word_fts(rowid, lemma, gloss) VALUES (?,?,?)", fts_rows
        )
        if tag_links:
            conn.executemany(
                "INSERT OR IGNORE INTO word_tag(word_id, tag_id) VALUES (?,?)", tag_links
            )

        # ---- 词单：内置 + 人工定义 ----
        custom = overrides.get("collections") or {}
        for name, keys in custom.items():
            members: list[int] = []
            for raw_key in keys:
                key = P._normalise_lemma(str(raw_key))
                if key not in word_id_by_key:
                    raise BuildError(f"词单 {name!r} 引用了不存在的词：{raw_key!r}")
                members.append(word_id_by_key[key])
            collection_members[str(name)] = members

        collection_counts: dict[str, int] = {}
        for name, members in collection_members.items():
            unique_members = sorted(set(members))
            cursor = conn.execute(
                "INSERT INTO collection(name, note, builtin) VALUES (?,?,?)",
                (name, None, 1 if name.startswith("List ") or name in ("全库", "短语") else 0),
            )
            collection_id = int(cursor.lastrowid)
            conn.executemany(
                "INSERT OR IGNORE INTO collection_word(collection_id, word_id, added_at)"
                " VALUES (?,?,?)",
                [(collection_id, wid, now) for wid in unique_members],
            )
            collection_counts[name] = len(unique_members)

        # ---- 解析信号 ----
        issue_rows = [
            (issue.line_no, issue.severity, issue.code, issue.detail, issue.raw)
            for issue in result.issues
        ]
        conn.executemany(
            "INSERT INTO parse_issue(line_no, severity, code, detail, raw) VALUES (?,?,?,?,?)",
            issue_rows,
        )

        # ---- 自检：不能静默丢数据 ----
        counts = S.table_counts(conn)
        if counts["word"] != len(merged):
            raise BuildError(
                f"自检失败：唯一词条 {len(merged)} 与实际写入 {counts['word']} 不一致"
            )
        if counts["occurrence"] != len(entries):
            raise BuildError(
                f"自检失败：词条 {len(entries)} 与出处记录 {counts['occurrence']} 不一致"
            )
        if counts["sense"] != len(sense_rows):
            raise BuildError(
                f"自检失败：义项 {len(sense_rows)} 与实际写入 {counts['sense']} 不一致"
            )
        if counts["pos_entry"] != len(unit_rows):
            raise BuildError(
                f"自检失败：背诵单元 {len(unit_rows)} 与实际写入 {counts['pos_entry']} 不一致"
            )

        severity_counts: dict[str, int] = {}
        issue_counts: dict[str, int] = {}
        for issue in result.issues:
            severity_counts[issue.severity] = severity_counts.get(issue.severity, 0) + 1
            issue_counts[issue.code] = issue_counts.get(issue.code, 0) + 1

        report = BuildReport(
            data_root=str(paths.root),
            source_file=str(source_path),
            source_sha256=sha256_file(source_path),
            built_at=now,
            parser_version=PARSER_VERSION,
            schema_version=S.SCHEMA_VERSION,
            total_lines=result.total_lines,
            parsed_entries=len(result.entries),
            unparsed_lines=len(result.unparsed),
            words=counts["word"],
            units=counts["pos_entry"],
            senses=counts["sense"],
            phrases=sum(1 for group in merged.values() if group[0].kind == "phrase"),
            occurrences=counts["occurrence"],
            list_numbers=sorted(n for n in result.list_numbers if n > 0),
            issue_counts=issue_counts,
            severity_counts=severity_counts,
            collections=collection_counts,
            overrides=override_stats,
            db_path=str(db_path),
        )

        meta = {
            "schema_version": str(S.SCHEMA_VERSION),
            "parser_version": PARSER_VERSION,
            "source_id": P.SOURCE_ID,
            "source_file": str(source_path),
            "source_sha256": report.source_sha256,
            "built_at": now,
            "words": str(report.words),
            "units": str(report.units),
            "senses": str(report.senses),
            "occurrences": str(report.occurrences),
        }
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?,?)", sorted(meta.items())
        )
        conn.commit()
    finally:
        conn.close()

    report.review_csv = str(write_review_csv(result.issues, paths))
    report.report_json = str(write_report_json(report, paths))
    return report


def write_review_csv(issues: list[P.Issue], paths: PP.DataPaths) -> Path:
    """导出人工校验清单。

    使用 ``utf-8-sig``：这份 CSV 是要用 Excel 打开逐条过目的工作清单，
    带 BOM 才能让 Excel 正确识别中文。
    """
    path = paths.review_csv
    rows = sorted(
        (issue.as_row() for issue in issues),
        key=lambda row: (
            SEVERITY_ORDER.get(str(row["severity"]), 9),
            str(row["code"]),
            int(row["line_no"]),  # type: ignore[arg-type]
        ),
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["line_no", "severity", "code", "detail", "raw"]
        )
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_report_json(report: BuildReport, paths: PP.DataPaths) -> Path:
    path = paths.report_json
    path.write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- #
# 读取侧（供后续背单词工具使用）
# --------------------------------------------------------------------------- #


def open_wordbook(data_dir: str | Path | None = None) -> sqlite3.Connection:
    paths = PP.data_paths(data_dir)
    if not paths.wordbook.is_file():
        raise BuildError(
            f"词库不存在：{paths.wordbook}\n先执行：python -m lexicon build"
        )
    return S.connect(paths.wordbook)


def lookup(term: str, data_dir: str | Path | None = None) -> dict | None:
    """按词形查词；命中返回词条 + 义项 + 出处 + 标签。"""
    key = P._normalise_lemma(term)
    if not key:
        return None
    conn = open_wordbook(data_dir)
    try:
        row = conn.execute(
            "SELECT * FROM word WHERE lemma_key = ?", (key,)
        ).fetchone()
        if row is None:
            # FTS 回退：容忍标点/空格差异。用户输入可能含 FTS 语法字符，失败即视为未命中。
            try:
                hit = conn.execute(
                    "SELECT rowid FROM word_fts WHERE word_fts MATCH ? LIMIT 1",
                    (f'"{key}"',),
                ).fetchone()
            except sqlite3.OperationalError:
                hit = None
            if hit is None:
                return None
            row = conn.execute(
                "SELECT * FROM word WHERE id = ?", (hit["rowid"],)
            ).fetchone()
        return _word_payload(conn, row)
    finally:
        conn.close()


def _word_payload(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    word_id = int(row["id"])
    # 背诵单元：词头 + 词性，每个单元带自己的音标与释义
    units = [
        {
            "key": unit["entry_key"],
            "pos": unit["pos"],
            "phonetic": unit["phonetic"],
            "phonetic_src": unit["phonetic_src"],
            "phonetic_alt": json.loads(unit["phonetic_alt"]) if unit["phonetic_alt"] else [],
            "gloss_cn": unit["gloss_cn"],
            "gloss_body": unit["gloss_body"],
        }
        for unit in conn.execute(
            "SELECT entry_key, pos, phonetic, phonetic_src, phonetic_alt,"
            " gloss_cn, gloss_body FROM pos_entry WHERE word_id = ? ORDER BY pos_no",
            (word_id,),
        )
    ]
    senses = [
        {
            "pos": sense["pos"],
            "gloss_cn": sense["gloss_cn"],
            "sense_no": sense["sense_no"],
            "phonetic": sense["phonetic"],
        }
        for sense in conn.execute(
            "SELECT pos, gloss_cn, sense_no, phonetic FROM sense"
            " WHERE word_id = ? ORDER BY sense_no",
            (word_id,),
        )
    ]
    occurrences = [
        {"list_no": o["list_no"], "seq_no": o["seq_no"], "line_no": o["line_no"]}
        for o in conn.execute(
            "SELECT list_no, seq_no, line_no FROM occurrence"
            " WHERE word_id = ? ORDER BY list_no, seq_no",
            (word_id,),
        )
    ]
    tags = [
        t["name"]
        for t in conn.execute(
            "SELECT t.name FROM word_tag wt JOIN tag t ON t.id = wt.tag_id"
            " WHERE wt.word_id = ? ORDER BY t.name",
            (word_id,),
        )
    ]
    examples = [
        {"text_en": e["text_en"], "text_zh": e["text_zh"], "source": e["source"]}
        for e in conn.execute(
            "SELECT text_en, text_zh, source FROM example"
            " WHERE word_id = ? ORDER BY seq, id",
            (word_id,),
        )
    ]
    collocations = [
        {
            "kind": c["kind"],
            "pattern": c["pattern"],
            "hits": c["hits"],
            "word_hits": c["word_hits"],
            # 该词出现时后面接这个词形 的概率
            "share": round(c["hits"] / c["word_hits"], 4) if c["word_hits"] else None,
            # 该词后面接介词/小品词时，选这个词形 的概率（更能说明「该配哪个介词」）
            "share_of_preps": round(c["hits"] / c["total_hits"], 4)
            if c["total_hits"]
            else None,
        }
        for c in conn.execute(
            "SELECT kind, pattern, hits, word_hits, total_hits FROM collocation"
            " WHERE word_id = ? ORDER BY kind, hits DESC",
            (word_id,),
        )
    ]
    # 局部导入：enrich 依赖本模块的 open_wordbook，顶层互相导入会成环
    from .enrich import enrichment_for

    return {
        "id": word_id,
        "lemma": row["lemma"],
        "display": row["display"],
        "kind": row["kind"],
        "phonetic": row["phonetic"],
        "phonetic_src": row["phonetic_src"],
        "phonetic_alt": json.loads(row["phonetic_alt"]) if row["phonetic_alt"] else [],
        "source": row["source"],
        "source_line": row["source_line"],
        "tags": tags,
        "entries": units,
        "senses": senses,
        "occurrences": occurrences,
        "enrichment": enrichment_for(conn, word_id),
        "examples": examples,
        "collocations": collocations,
    }


def stats(data_dir: str | Path | None = None) -> dict:
    """词库概览：直接读库，不依赖构建报告。"""
    conn = open_wordbook(data_dir)
    try:
        meta = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}
        counts = {
            table: int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
            for table in (
                "word",
                "pos_entry",
                "sense",
                "occurrence",
                "collection",
                "parse_issue",
                "enrichment",
                "example",
                "collocation",
            )
        }
        enrichment_by_source = {
            row["source"]: int(row["n"])
            for row in conn.execute(
                "SELECT source, COUNT(*) AS n FROM enrichment GROUP BY source ORDER BY source"
            )
        }
        by_kind = {
            row["kind"]: int(row["n"])
            for row in conn.execute("SELECT kind, COUNT(*) AS n FROM word GROUP BY kind")
        }
        by_pos = {
            (row["pos"] or "(无词性)"): int(row["n"])
            for row in conn.execute(
                "SELECT pos, COUNT(*) AS n FROM sense GROUP BY pos ORDER BY n DESC"
            )
        }
        top_collections = [
            {"name": row["name"], "count": int(row["n"])}
            for row in conn.execute(
                "SELECT c.name, COUNT(cw.word_id) AS n FROM collection c"
                " LEFT JOIN collection_word cw ON cw.collection_id = c.id"
                " GROUP BY c.id ORDER BY n DESC, c.name"
            )
        ]
        by_issue = {
            row["code"]: int(row["n"])
            for row in conn.execute(
                "SELECT code, COUNT(*) AS n FROM parse_issue GROUP BY code ORDER BY n DESC"
            )
        }
        return {
            "meta": meta,
            "counts": counts,
            "by_kind": by_kind,
            "by_pos": by_pos,
            "collections": top_collections,
            "issues": by_issue,
            "enrichment": enrichment_by_source,
        }
    finally:
        conn.close()
