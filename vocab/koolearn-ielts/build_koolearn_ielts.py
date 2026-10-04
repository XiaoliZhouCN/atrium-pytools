# -*- coding: utf-8 -*-
"""从 koolearn 在线词典构建雅思分层词表（含权重）。

产出（同目录）：
    raw_tags.json              原始抓取快照（审计/复现）
    ielts_layered.json         主数据（meta + 词条）
    ielts_layered_master.csv   全量主表
    L1_base_vocabulary.csv     底座：Vocabulary 5000/10000/22000
    L2_listening_core.csv      听力核心（≥5 本收录）
    L2_reading_core.csv        阅读核心（≥5 本收录）
    L2_writing_core.csv        写作核心（≥5 本收录）

数据来源：https://www.koolearn.com/dict/
权重口径：一个词被多少本词书收录 = 该词的核心度（站点不提供词频，以此代理）。
"""
from __future__ import annotations

import csv
import gzip
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
BASE = "https://www.koolearn.com"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".cache")

IELTS_CATEGORY = "fenlei_2_105"  # 雅思词汇书
CATEGORY_PAGES = 40

# 底座：Vocabulary 系列（level 越小越基础）
SERIES = {
    "Vocabulary 5000": (338, 1),
    "Vocabulary 10000": (340, 2),
    "Vocabulary 22000": (339, 3),
}

# 技能分桶：默认读同目录 skill_map.json（人工定稿，可审计/可改）。
# 缺失时退回关键词启发式，并把草稿写出来供人工核对。
SKILL_MAP_FILE = os.path.join(HERE, "skill_map.json")
SKILL_KEYWORDS = {
    "听力": ("听力", "听写"),
    "口语": ("口语",),
    "阅读": ("阅读",),
    "写作": ("写作", "作文"),
}

CORE_MIN_BOOKS = 5      # "核心"阈值：被 ≥5 本同技能词书收录
TIER_CUTS = [(10, "S"), (5, "A"), (3, "B"), (2, "C"), (1, "D")]

RE_ITEM = re.compile(r'<a class="word" href="/dict/wd_(\d+)\.html">([^<]+)</a>')
RE_BLOCK = re.compile(
    r'<div class="word-title">(.*?)</div>\s*<div class="word-box">', re.S
)
RE_TAG_LINK = re.compile(r'href="/dict/tag_(\d+)_1\.html"')


def get(url: str) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            if attempt == 2:
                return ""
            time.sleep(1.0 * (attempt + 1))
    return ""


def enumerate_ielts_tags() -> dict[str, str]:
    """雅思分类下的 {tag_id: 书名}。"""
    out: dict[str, str] = {}
    for page in range(1, CATEGORY_PAGES + 1):
        html = get(f"{BASE}/dict/{IELTS_CATEGORY}_{page}.html")
        if not html:
            break
        body = html.split('class="left-content"', 1)[-1].split('class="i-page"', 1)[0]
        for m in RE_BLOCK.finditer(body):
            head = m.group(1)
            link = RE_TAG_LINK.search(head)
            if not link:
                continue
            title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", head))
            out.setdefault(link.group(1), title.replace("更多", "").strip())
        if f"{IELTS_CATEGORY}_{page + 1}.html" not in html:
            break
        time.sleep(0.3)
    return out


def fetch_tag_words(tag_id: str) -> list[list[str]]:
    """某个词表的 [[wd_id, word], ...]，带磁盘缓存。"""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"tag_{tag_id}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    words, seen, page = [], set(), 1
    while True:
        html = get(f"{BASE}/dict/tag_{tag_id}_{page}.html")
        if not html:
            break
        body = html.split('class="left-content"', 1)[-1].split('class="i-page"', 1)[0]
        for wid, word in RE_ITEM.findall(body):
            if wid not in seen:
                seen.add(wid)
                words.append([wid, word.strip()])
        if f"/dict/tag_{tag_id}_{page + 1}.html" not in html:
            break
        page += 1
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(words, fh, ensure_ascii=False)
    return words


def tier_of(n: int) -> str:
    for cut, label in TIER_CUTS:
        if n >= cut:
            return label
    return "D"


def main() -> None:
    print("① 枚举雅思分类词书…")
    ielts_tags = enumerate_ielts_tags()
    print(f"   雅思分类词书 {len(ielts_tags)} 本")

    # 技能归属：人工定稿映射优先，缺失时退回关键词并落草稿
    if os.path.exists(SKILL_MAP_FILE):
        with open(SKILL_MAP_FILE, encoding="utf-8") as fh:
            smap = json.load(fh)
        excluded = {str(t) for t in smap.get("excluded", {})}
        skills = {
            s: [str(t) for t in ids if str(t) not in excluded]
            for s, ids in smap["skills"].items()
        }
        print(f"   读取 skill_map.json（排除 {len(excluded)} 本）")
    else:
        excluded = set()
        skills = {s: [] for s in SKILL_KEYWORDS}
        for tid, title in ielts_tags.items():
            for skill, kws in SKILL_KEYWORDS.items():
                if any(k in title for k in kws):
                    skills[skill].append(tid)
        with open(SKILL_MAP_FILE, "w", encoding="utf-8") as fh:
            json.dump(
                {"_note": "自动生成的草稿，请人工核对后定稿",
                 "excluded": {}, "skills": skills},
                fh, ensure_ascii=False, indent=1,
            )
        print("   未找到 skill_map.json，已生成草稿")

    active_tags = {t: n for t, n in ielts_tags.items() if t not in excluded}
    assigned = {t for ids in skills.values() for t in ids}
    general_tags = sorted(set(active_tags) - assigned, key=int)
    print(f"   计入雅思库 {len(active_tags)} 本 = 技能 {len(assigned)} 本 + 通用 {len(general_tags)} 本")
    for skill, ids in skills.items():
        print(f"   {skill}标签词书 {len(ids)} 本")

    targets = sorted(set(active_tags) | {str(t) for t, _ in SERIES.values()})
    print(f"\n② 抓取 {len(targets)} 个词表（含底座）…")
    with ThreadPoolExecutor(max_workers=6) as pool:
        fetched = dict(zip(targets, pool.map(fetch_tag_words, targets)))
    total_pairs = sum(len(v) for v in fetched.values())
    print(f"   完成，累计 {total_pairs} 条 (词表,词) 记录")

    # ---- 权重 ----
    print("\n③ 计算权重…")
    ielts_books: Counter = Counter()          # 被多少本雅思词书收录
    bucket_books: dict[str, Counter] = {s: Counter() for s in SKILL_KEYWORDS}
    display: dict[str, str] = {}
    for tid in active_tags:
        for wid, word in fetched.get(tid, []):
            display.setdefault(wid, word)
            ielts_books[wid] += 1
    for skill, ids in skills.items():
        for tid in ids:
            for wid, _ in fetched.get(tid, []):
                bucket_books[skill][wid] += 1

    base_books: Counter = Counter()
    base_level: dict[str, int] = {}
    base_src: dict[str, str] = {}
    for name, (tag, level) in SERIES.items():
        for wid, word in fetched.get(str(tag), []):
            display.setdefault(wid, word)
            base_books[wid] += 1
            if wid not in base_level or level < base_level[wid]:
                base_level[wid] = level
                base_src[wid] = name

    all_words = set(ielts_books) | set(base_books)
    print(f"   雅思词书总词 {len(ielts_books)}；底座 {len(base_books)}；合并去重 {len(all_words)}")

    # ---- 导出 ----
    print("\n④ 导出…")
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "https://www.koolearn.com/dict/",
        "ielts_category": IELTS_CATEGORY,
        "ielts_book_count": len(active_tags),
        "excluded_books": sorted(excluded, key=int),
        "general_book_count": len(general_tags),
        "skill_book_counts": {s: len(ids) for s, ids in skills.items()},
        "series": {n: t for n, (t, _) in SERIES.items()},
        "weight_definition": "ielts_books = 收录该词的雅思词书数量；bucket_books = 该技能桶内收录数",
        "core_min_books": CORE_MIN_BOOKS,
        "tier_cuts": TIER_CUTS,
        "counts": {
            "ielts_words": len(ielts_books),
            "base_words": len(base_books),
            "union_words": len(all_words),
        },
    }

    rows = []
    for wid in all_words:
        counts = {s: bucket_books[s][wid] for s in SKILL_KEYWORDS}
        owned = [s for s in SKILL_KEYWORDS if counts[s] > 0]
        word = display.get(wid, "")
        rows.append({
            "word": word,
            "wd_id": wid,
            "url": f"{BASE}/dict/wd_{wid}.html",
            "kind": "phrase" if re.search(r"[ /]", word) else "word",
            "tier": tier_of(ielts_books.get(wid, 0)),
            "ielts_books": ielts_books.get(wid, 0),
            "listen_books": counts["听力"],
            "reading_books": counts["阅读"],
            "writing_books": counts["写作"],
            "speaking_books": counts["口语"],
            "skills": ";".join(owned),
            "in_base": 1 if wid in base_books else 0,
            "base_level": base_level.get(wid, 0),
            "base_books": base_books.get(wid, 0),
            "base_source": base_src.get(wid, ""),
        })
    rows.sort(key=lambda r: (r["tier"], -r["ielts_books"], -r["base_books"], r["word"].lower()))

    master = os.path.join(HERE, "ielts_layered_master.csv")
    fields = list(rows[0].keys())
    with open(master, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"   ielts_layered_master.csv  {len(rows)} 行")

    # 分层文件
    def dump(name: str, subset: list[dict], cols: list[str]) -> None:
        path = os.path.join(HERE, name)
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(subset)
        print(f"   {name}  {len(subset)} 行")

    base_cols = ["word", "wd_id", "url", "base_level", "base_source"]
    dump("L1_base_vocabulary.csv", [r for r in rows if r["in_base"]], base_cols)

    skill_col = {"听力": "listen_books", "阅读": "reading_books", "写作": "writing_books"}
    slug = {"听力": "listening", "阅读": "reading", "写作": "writing"}
    for skill, col in skill_col.items():
        sub = [r for r in rows if r[col] >= CORE_MIN_BOOKS]
        dump(
            f"L2_{slug[skill]}_core.csv",
            sub,
            ["word", "wd_id", "url", col, "tier", "base_level"],
        )

    # 推荐学习包：L1 底座 ∪ L2 三技能核心（去重，附所属层）
    pack = []
    for r in rows:
        layers = []
        if r["in_base"]:
            layers.append(f"L1-base-L{r['base_level']}")
        for skill, col in skill_col.items():
            if r[col] >= CORE_MIN_BOOKS:
                layers.append(f"L2-{slug[skill]}")
        if layers:
            item = dict(r)
            item["layers"] = ";".join(layers)
            item["layer_count"] = len(layers)
            pack.append(item)
    pack.sort(key=lambda r: (-r["layer_count"], r["tier"], -r["ielts_books"], r["word"].lower()))
    dump(
        "study_pack_recommended.csv",
        pack,
        ["word", "wd_id", "url", "layers", "layer_count", "tier", "ielts_books",
         "listen_books", "reading_books", "writing_books", "speaking_books",
         "in_base", "base_level"],
    )

    payload = {"meta": meta, "words": rows}
    with open(os.path.join(HERE, "ielts_layered.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print("   ielts_layered.json")

    with open(os.path.join(HERE, "raw_tags.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {"ielts_all": ielts_tags, "active": active_tags,
             "skills": skills, "general": general_tags},
            fh, ensure_ascii=False, indent=1,
        )
    print("   raw_tags.json")

    # ---- 摘要 ----
    tier_dist = Counter(r["tier"] for r in rows)
    print("\n=== 权重分布（tier 按被多少本雅思词书收录）===")
    for t in ("S", "A", "B", "C", "D"):
        n = tier_dist.get(t, 0)
        print(f"   {t}: {n:>6} 词")
    print("\n=== 各技能核心（≥5 本）===")
    for skill, col in skill_col.items():
        print(f"   {skill}: {sum(1 for r in rows if r[col] >= CORE_MIN_BOOKS)} 词")
    print(f"\n   L1 底座: {sum(1 for r in rows if r['in_base'])} 词")


if __name__ == "__main__":
    main()
