# D:\Repositories\Manager\AtriumPyTools\tools\lexicon\lexicon\qa.py
"""对合并后的分层文件做不变量校验（不联网、不写数据）。"""

from __future__ import annotations

from . import paths as PP
from .merge import CORE_MIN_BOOKS, SKILLS, XDF_LAYER, _int, _skills_of, tier_of
from .sources import read_csv


def verify(paths: PP.DataPaths) -> list[str]:
    """返回失败项列表；空列表表示全部通过。"""
    failures: list[str] = []

    def check(ok: bool, message: str) -> None:
        if not ok:
            failures.append(message)

    _, master = read_csv(paths.master)
    _, base = read_csv(paths.file(PP.BASE)) if paths.file(PP.BASE).is_file() else ([], [])
    _, pack = read_csv(paths.file(PP.PACK)) if paths.file(PP.PACK).is_file() else ([], [])
    cores = {}
    for _name, column, _slug in SKILLS:
        target = {
            "listen_books": PP.LISTENING,
            "reading_books": PP.READING,
            "writing_books": PP.WRITING,
        }.get(column)
        if target is None:
            continue
        cores[column] = read_csv(paths.file(target))[1] if paths.file(target).is_file() else []

    def key(row: dict) -> str:
        return (row.get("word") or "").strip().lower()

    master_by = {key(r): r for r in master}

    check(bool(master), "master 为空")
    check(all(r["word"].strip() for r in master), "master 存在空词头")
    check(
        len({key(r) for r in master}) == len(master),
        f"master 词头不唯一（{len(master) - len({key(r) for r in master})} 个重复）",
    )
    check(
        all((r["wd_id"].strip().isdigit() or r["wd_id"].strip() == "") for r in master),
        "master 的 wd_id 既非数字也非空",
    )
    check(
        all(
            r["wd_id"].strip() or r.get("sources") == "xdf"
            for r in master
        ),
        "存在无 wd_id 却不是 xdf 来源的行",
    )

    bad_tier = [r["word"] for r in master if r["tier"] != tier_of(_int(r["ielts_books"]))]
    check(not bad_tier, f"tier 与 ielts_books 不一致（{len(bad_tier)} 条，例：{bad_tier[:3]}）")

    bad_skills = [r["word"] for r in master if r["skills"] != _skills_of(r)]
    check(not bad_skills, f"skills 与技能桶计数不一致（{len(bad_skills)} 条，例：{bad_skills[:3]}）")

    expect_base = {key(r) for r in master if r["in_base"] == "1"}
    check(expect_base == {key(r) for r in base}, f"1 号 ≠ in_base==1（差 {len(expect_base ^ {key(r) for r in base})}）")

    for column, rows in cores.items():
        expect = {key(r) for r in master if _int(r[column]) >= CORE_MIN_BOOKS}
        check(
            expect == {key(r) for r in rows},
            f"{column} 分层 ≠ 计数>={CORE_MIN_BOOKS}（差 {len(expect ^ {key(r) for r in rows})}）",
        )

    core_union = set().union({key(r) for r in base}, *[{key(r) for r in rows} for rows in cores.values()])
    xdf_only = {
        key(r) for r in master if r.get("xdf") == "1" and key(r) not in core_union
    }
    expect_pack = core_union | xdf_only
    check(
        expect_pack == {key(r) for r in pack},
        f"2 号 ≠ 1∪3∪4∪5∪新增xdf（差 {len(expect_pack ^ {key(r) for r in pack})}）",
    )
    check(
        all(r["layers"] for r in pack),
        "2 号存在没有 layers 的行",
    )
    for row in pack:
        layers = (row["layers"] or "").split(";")
        if row["layers"] == XDF_LAYER:
            check(
                master_by.get(key(row), {}).get("xdf") == "1",
                f"{row['word']} 标为 {XDF_LAYER} 但不在我们的词书里",
            )
        else:
            check(
                XDF_LAYER not in layers,
                f"{row['word']} 同时标了 {XDF_LAYER} 与 koolearn 层",
            )

    return failures
