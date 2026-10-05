# D:\Repositories\Manager\AtriumPyTools\tools\lexicon\lexicon\parse.py
"""雅思词表解析：原文行 → 结构化词条与义项。

输入是《雅思词汇词根+联想记忆法（乱序便携版）》手工抄录的纯文本，每行形如：

    <词头>[*] [音标] [词性. 释义[；释义…]] [音标] [词性. 释义…] …

同一行可能含多个词性、每个词性可能含多个义项、一个词性可能挂多个音标（异读 /
一书一网络两种来源）。三种音标定界符的意义见原文 README：

    /…/  Lingoes「牛津高阶英汉双解词典」
    […]  百度翻译
    {…}  根据书中音标拼写

本模块是**纯函数**：不读文件、不写数据库，只把文本变成数据，便于单测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

#: 词库来源标识（未来接入别的词书时换这里）
SOURCE_ID = "xdf-ielts-2015"

# --------------------------------------------------------------------------- #
# 定界符与识别规则
# --------------------------------------------------------------------------- #

#: /…/ 音标：必须成对出现，内容不含 / 与换行
_RE_SLASH_PHONETIC = re.compile(r"/([^/\n]{1,80})/")
#: […] 与 {…} 音标：内容不含定界符与换行
_RE_BRACKET_PHONETIC = re.compile(r"\[([^\]\n]{1,80})\]")
_RE_BRACE_PHONETIC = re.compile(r"\{([^\}\n]{1,80})\}")
#: 漏写右括号的音标，如原文 L1788 `landfill* ['lændfɪl    n. 垃圾堆`。
#: 只认「方括号内是不含空白的单段且含国际音标专用字符」，避免把 [the C-] 误判。
_RE_UNCLOSED_BRACKET = re.compile(r"\[\s*([^\]\s]{1,40})(?=\s|$)")

#: 各来源音标的右定界符，用于吸收原文多写的定界符（如 L370 `[ˈkæmpfaɪə(r)]]`）
_CLOSING_DELIMITER = {"lingoes": "/", "baidu": "]", "book": "}"}

#: 国际音标专用字符。用于把音标方括号与用法说明方括号（[pl.] / [the C-] /
#: [常用于被动语态]）区分开——后者在本书里大量出现，误判会污染音标字段。
_IPA_CHARS = frozenset("ˈˌːəɪʊɛɔæʌɑɒɜʃʒθðŋˏˊˋ'")

#: 中文（用于判定「释义区」起点、以及排除含中文的伪音标）
_RE_CJK = re.compile(r"[\u4e00-\u9fff]")

#: 词性标记。长写优先，避免 a. / ad. 之类的歧义；允许 n./vt. 这种斜杠组合。
_POS_ATOM = r"(?:adj|adv|aux|art|conj|prep|pron|int|num|vt|vi|ad|n|v|a)"
_RE_POS = re.compile(
    rf"(?<![A-Za-z]){_POS_ATOM}\.(?:\s*/\s*{_POS_ATOM}\.)*"
)
#: 允许前置空白的词性探测（音标与词性之间常有多个空格）
_RE_POS_AHEAD = re.compile(rf"\s*{_POS_ATOM}\.")

#: 章节标题
_RE_SECTION = re.compile(r"^\s*Word\s+List\s+(\d+)\s*$", re.IGNORECASE)

#: 义项分隔符：全角分号 / 半角分号
_RE_SENSE_SPLIT = re.compile(r"[；;]")

#: 短语/多词词头
_RE_HEAD_SEP = re.compile(r"[ /]")

MAX_HEADWORD_CHARS = 40


class ParseError(RuntimeError):
    """原文结构不可解析。"""


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Phonetic:
    """一个音标及其来源。"""

    text: str
    source: str  # lingoes | baidu | book | manual
    #: 原文定界符笔误时记录修复方式：missing_close | extra_close
    fix_note: str | None = None

    @property
    def delimited(self) -> str:
        """带原始定界符的展示形式。"""
        marks = {"lingoes": ("/", "/"), "baidu": ("[", "]"), "book": ("{", "}")}
        left, right = marks.get(self.source, ("/", "/"))
        return f"{left}{self.text}{right}"


@dataclass
class Sense:
    """一个义项：`词性 + 释义`。词库内容的最小可背单位。"""

    pos: str | None  # 'n.' / 'vt.' / 'a./ad.' / None（短语常无词性）
    gloss_cn: str
    sense_no: int
    phonetics: list[Phonetic] = field(default_factory=list)
    raw_segment: str = ""

    @property
    def phonetic(self) -> str | None:
        """首选音标（带定界符）。"""
        return self.phonetics[0].delimited if self.phonetics else None


@dataclass
class Entry:
    """一个词条（原文一行）。"""

    display: str  # 展示形：easy-going
    lemma_key: str  # 归一键：小写、压缩空格、去 *
    kind: str  # word | phrase
    tags: list[str]  # 目前仅有 listening（原文 * 标记）
    list_no: int  # 原书章节号 1..48
    seq_no: int  # 章内序号
    line_no: int  # 原文行号（1-based）
    raw: str  # 原文整行
    phonetics: list[Phonetic]
    senses: list[Sense]
    issues: list["Issue"]

    @property
    def phonetic(self) -> str | None:
        return self.phonetics[0].delimited if self.phonetics else None

    @property
    def phonetic_alt(self) -> list[str]:
        return [p.delimited for p in self.phonetics[1:]]


@dataclass(frozen=True)
class Issue:
    """解析质量信号，进入 needs_review 清单。"""

    line_no: int
    code: str
    severity: str  # error | warn | info
    detail: str
    raw: str

    def as_row(self) -> dict[str, object]:
        return {
            "line_no": self.line_no,
            "severity": self.severity,
            "code": self.code,
            "detail": self.detail,
            "raw": self.raw,
        }


@dataclass
class ParseResult:
    entries: list[Entry]
    issues: list[Issue]
    list_numbers: list[int]
    total_lines: int
    header_lines: int
    blank_lines: int
    unparsed: list[tuple[int, str]]

    @property
    def sense_count(self) -> int:
        return sum(len(e.senses) for e in self.entries)


# --------------------------------------------------------------------------- #
# 音标与「释义区」定位
# --------------------------------------------------------------------------- #


def _find_phonetics(text: str) -> list[tuple[int, int, Phonetic]]:
    """定位全部音标，返回 (start, end, Phonetic)，按位置排序。

    难点：`[…]` 在本文件里既表示音标又表示用法说明。判定规则：
    1. 含中文 → 用法说明（[pl.] / [常用于被动语态] / [the C-]）；
    2. 含国际音标专用字符 → 音标；
    3. 紧跟词性标记 → 音标（兜住全 ASCII 的少数音标，如 [briŋ aut] 之外的情况）。
    """
    spans: list[tuple[int, int, Phonetic]] = []

    for m in _RE_SLASH_PHONETIC.finditer(text):
        content = m.group(1)
        if _RE_CJK.search(content):
            continue
        spans.append((m.start(), m.end(), Phonetic(content, "lingoes")))

    for m in _RE_BRACE_PHONETIC.finditer(text):
        content = m.group(1)
        if _RE_CJK.search(content):
            continue
        spans.append((m.start(), m.end(), Phonetic(content, "book")))

    closed_brackets = [
        (m.start(), m.end()) for m in _RE_BRACKET_PHONETIC.finditer(text)
    ]

    def _inside_closed_bracket(position: int) -> bool:
        return any(start <= position < end for start, end in closed_brackets)

    for m in _RE_BRACKET_PHONETIC.finditer(text):
        content = m.group(1)
        if _RE_CJK.search(content):
            continue
        has_ipa = bool(_IPA_CHARS & set(content))
        # 少数全 ASCII 音标（如 [fju:mz]）不含音标专用字符，靠「后面跟词性」兜住。
        # 注意音标与词性之间通常有多个空格，必须允许前置空白。
        followed_by_pos = bool(_RE_POS_AHEAD.match(text, m.end()))
        if has_ipa or followed_by_pos:
            spans.append((m.start(), m.end(), Phonetic(content, "baidu")))

    for m in _RE_UNCLOSED_BRACKET.finditer(text):
        if _inside_closed_bracket(m.start()):
            continue
        content = m.group(1)
        if _RE_CJK.search(content) or not (_IPA_CHARS & set(content)):
            continue
        spans.append(
            (m.start(), m.end(), Phonetic(content, "baidu", fix_note="missing_close"))
        )

    spans.sort(key=lambda item: item[0])

    # 吸收原文多写的右定界符：`[ˈkæmpfaɪə(r)]]` → 音标区间吃掉多出的那个 ]
    adjusted: list[tuple[int, int, Phonetic]] = []
    for start, end, phon in spans:
        closing = _CLOSING_DELIMITER.get(phon.source)
        extra = False
        while closing and end < len(text) and text[end] == closing:
            end += 1
            extra = True
        if extra:
            phon = Phonetic(phon.text, phon.source, fix_note="extra_close")
        adjusted.append((start, end, phon))
    return adjusted


def _mask(text: str, spans: list[tuple[int, int, object]]) -> str:
    """把给定区间替换为等长空格，保持其余字符下标不变。"""
    if not spans:
        return text
    chars = list(text)
    for start, end, _ in spans:
        for i in range(start, end):
            chars[i] = " "
    return "".join(chars)


def _definition_start(text: str, phonetics: list[tuple[int, int, Phonetic]]) -> int:
    """返回释义区起点下标；其后即为「音标 + 词性 + 释义」序列。"""
    candidates: list[int] = []
    if phonetics:
        candidates.append(phonetics[0][0])
    cjk = _RE_CJK.search(text)
    if cjk:
        candidates.append(cjk.start())
    masked = _mask(text, [(s, e, None) for s, e, _ in phonetics])
    pos = _RE_POS.search(masked)
    if pos:
        candidates.append(pos.start())
    return min(candidates) if candidates else len(text)


def _normalise_lemma(head: str) -> str:
    """归一键：小写、统一撇号、压缩空白。用于去重与查词。"""
    text = head.strip().lower().replace("\u2019", "'").replace("\u2018", "'")
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- #
# 单行解析
# --------------------------------------------------------------------------- #


def parse_entry_line(line: str, list_no: int, seq_no: int, line_no: int) -> Entry | None:
    """解析一行词条；无法识别为词条时返回 None。"""
    text = line.rstrip("\r\n").strip()
    if not text:
        return None

    phonetic_spans = _find_phonetics(text)
    start = _definition_start(text, phonetic_spans)

    raw_head = text[:start].strip()
    issues: list[Issue] = []

    star = raw_head.endswith("*")
    if star:
        raw_head = raw_head[:-1].strip()
    # 少数词条尾部残留的分隔符
    raw_head = raw_head.rstrip(":：,，;；").strip()

    if not raw_head:
        return None

    display = raw_head
    lemma_key = _normalise_lemma(display)
    if not lemma_key:
        return None

    kind = "phrase" if _RE_HEAD_SEP.search(display) else "word"
    tags = ["listening"] if star else []

    if _RE_CJK.search(display):
        issues.append(
            Issue(line_no, "headword_has_cjk", "error", f"词头含中文：{display!r}", text)
        )
    if len(display) > MAX_HEADWORD_CHARS:
        issues.append(
            Issue(
                line_no,
                "headword_too_long",
                "warn",
                f"词头 {len(display)} 字符，疑似切分错误：{display!r}",
                text,
            )
        )

    senses, sense_issues = _parse_senses(text[start:], line_no, text)
    issues.extend(sense_issues)

    if not phonetic_spans and kind == "word":
        # 词组本来就没有音标（`roll film 胶卷`），按词组记即可，不算缺陷；
        # 因此只对单词报 no_phonetic。
        issues.append(
            Issue(line_no, "no_phonetic", "warn", f"{display} 无音标", text)
        )
    elif len(phonetic_spans) > 1:
        issues.append(
            Issue(
                line_no,
                "multi_phonetic",
                "info",
                f"{display} 有 {len(phonetic_spans)} 个音标："
                + " ".join(phon.delimited for _, _, phon in phonetic_spans),
                text,
            )
        )

    for _, _, phon in phonetic_spans:
        # 原文定界符笔误已由解析器确定性修复，产物无需人工处理，故记 info 供追溯。
        if phon.fix_note == "missing_close":
            issues.append(
                Issue(
                    line_no,
                    "unclosed_phonetic",
                    "info",
                    f"原文音标缺少右定界符，已自动修复为 {phon.delimited}",
                    text,
                )
            )
        elif phon.fix_note == "extra_close":
            issues.append(
                Issue(
                    line_no,
                    "stray_phonetic_delimiter",
                    "info",
                    f"原文音标多写右定界符，已自动规范为 {phon.delimited}",
                    text,
                )
            )
        if _RE_CJK.search(phon.text):
            issues.append(
                Issue(line_no, "phonetic_has_cjk", "error", f"音标含中文：{phon.delimited}", text)
            )

    if not senses:
        issues.append(
            Issue(line_no, "no_sense", "error", f"{display} 未解析出任何义项", text)
        )

    return Entry(
        display=display,
        lemma_key=lemma_key,
        kind=kind,
        tags=tags,
        list_no=list_no,
        seq_no=seq_no,
        line_no=line_no,
        raw=text,
        phonetics=[phon for _, _, phon in phonetic_spans],
        senses=senses,
        issues=issues,
    )


def _parse_senses(
    definition: str, line_no: int, raw: str
) -> tuple[list[Sense], list[Issue]]:
    """把「音标 + 词性. 释义」序列拆成义项。

    约定：音标写在它所修饰的词性**之前**，因此每个音标归属「其后的第一个词性」。
    实测例证：

        reject /rɪˈdʒekt/ vt. 拒绝  /ˈriːdʒekt/ n. 被拒货品
        inland {ˈɪnlænd} a. 内陆的  /ˏɪnˈlænd/ ad. 向内地
        desert /ˈdezət/ n. 沙漠  a. 沙漠的  /dɪˈzɜːt/ v. 舍弃   # a. 无音标
    """
    issues: list[Issue] = []
    phonetics = _find_phonetics(definition)
    masked = _mask(definition, [(s, e, None) for s, e, _ in phonetics])
    markers = list(_RE_POS.finditer(masked))

    senses: list[Sense] = []

    if not markers:
        gloss = _clean_gloss(_strip_phonetic_text(definition, phonetics))
        if gloss:
            senses.append(
                Sense(
                    pos=None,
                    gloss_cn=gloss,
                    sense_no=1,
                    phonetics=[p for _, _, p in phonetics],
                    raw_segment=definition.strip(),
                )
            )
        else:
            issues.append(
                Issue(line_no, "empty_gloss", "error", "无词性标记且释义为空", raw)
            )
        return senses, issues

    counter = 0
    for index, marker in enumerate(markers):
        prev_end = markers[index - 1].end() if index > 0 else 0
        next_start = markers[index + 1].start() if index + 1 < len(markers) else len(definition)

        own = [p for s, e, p in phonetics if prev_end <= s < marker.start()]
        segment_end = next_start
        body_start = marker.end()
        body = definition[body_start:segment_end]
        # 音标偏移量是相对整段 definition 的，裁剪到 body 时必须同步平移，
        # 否则会切错位置、把音标残渣留在释义里。
        inner = [
            (s - body_start, e - body_start, phon)
            for s, e, phon in phonetics
            if body_start <= s < segment_end
        ]
        body = _strip_phonetic_text(body, inner)

        pos = re.sub(r"\s*/\s*", "/", marker.group(0).strip())
        fragments = [frag.strip() for frag in _RE_SENSE_SPLIT.split(body)]
        fragments = [frag for frag in fragments if frag]

        if not fragments:
            issues.append(
                Issue(
                    line_no,
                    "empty_gloss",
                    "error",
                    f"词性 {pos} 下无释义",
                    raw,
                )
            )
            continue

        for frag in fragments:
            gloss = _clean_gloss(frag)
            if not gloss:
                continue
            counter += 1
            senses.append(
                Sense(
                    pos=pos,
                    gloss_cn=gloss,
                    sense_no=counter,
                    phonetics=own if len(fragments) == 1 else [],
                    raw_segment=definition[marker.start() : segment_end].strip(),
                )
            )
        if len(fragments) > 1:
            # 词性挂了多个义项：音标属于整个词性，分别落到每个义项上
            for sense in senses[-len(fragments) :]:
                sense.phonetics = list(own)

    return senses, issues


def _strip_phonetic_text(text: str, spans: list[tuple[int, int, Phonetic]]) -> str:
    """去掉音标并压缩空白，保留释义文本。"""
    if not spans:
        return text
    out: list[str] = []
    cursor = 0
    for start, end, _ in sorted(spans):
        if start < cursor:
            continue
        out.append(text[cursor:start])
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def _clean_gloss(gloss: str) -> str:
    """清理释义：去首尾标点与空白，压缩内部空白。"""
    text = re.sub(r"\s+", " ", gloss).strip()
    text = text.strip("：:；;,，、/ ")
    return text.strip()


# --------------------------------------------------------------------------- #
# 全文件解析
# --------------------------------------------------------------------------- #


def parse_text(text: str) -> ParseResult:
    """解析整份词表文本。"""
    lines = text.splitlines()
    entries: list[Entry] = []
    issues: list[Issue] = []
    list_numbers: list[int] = []
    unparsed: list[tuple[int, str]] = []

    current_list = 0
    seq_no = 0
    header_lines = 0
    blank_lines = 0
    started = False

    for offset, line in enumerate(lines, start=1):
        stripped = line.strip()

        section = _RE_SECTION.match(stripped)
        if section:
            number = int(section.group(1))
            if number not in list_numbers:
                list_numbers.append(number)
            # 同一 Word List 标题重复出现时不重置章内序号，避免重号
            if number != current_list:
                seq_no = 0
            current_list = number
            started = True
            header_lines += 1
            continue

        if not stripped:
            blank_lines += 1
            continue

        if not started:
            # 第一个 "Word List NN" 之前是原文 README 块
            header_lines += 1
            continue

        entry = parse_entry_line(line, current_list, seq_no + 1, offset)
        if entry is None:
            unparsed.append((offset, stripped))
            continue

        seq_no += 1
        entries.append(entry)
        issues.extend(entry.issues)

    return ParseResult(
        entries=entries,
        issues=issues,
        list_numbers=list_numbers,
        total_lines=len(lines),
        header_lines=header_lines,
        blank_lines=blank_lines,
        unparsed=unparsed,
    )


def duplicate_lemmas(entries: list[Entry]) -> dict[str, list[Entry]]:
    """归一键 → 同键词条列表（仅返回出现次数 > 1 的）。"""
    buckets: dict[str, list[Entry]] = {}
    for entry in entries:
        buckets.setdefault(entry.lemma_key, []).append(entry)
    return {key: group for key, group in buckets.items() if len(group) > 1}


def annotate_duplicates(result: ParseResult) -> None:
    """给重复出现的词条追加一条 info 级信号（原地修改）。

    同一词出现在多个 Word List 是原书的复现设计，不是录入错误，因此只记 info。
    """
    for key, group in duplicate_lemmas(result.entries).items():
        line_nos = "、".join(str(e.line_no) for e in group)
        detail = f"归一键 {key!r} 出现 {len(group)} 次（行 {line_nos}）"
        for entry in group:
            entry.issues.append(
                Issue(entry.line_no, "duplicate_lemma", "info", detail, entry.raw)
            )
    result.issues = [issue for entry in result.entries for issue in entry.issues]


def iter_entries(text: str) -> Iterator[Entry]:
    """便捷迭代器。"""
    yield from parse_text(text).entries
