# lexicon

雅思词库工具链。**代码在本仓库，词库数据在 `AtriumNote`。**

一条链做四件事：

1. **建库** —— 手工抄录的词表 → 结构化 SQLite（词条 / 背诵单元 / 义项 / 音标 / 出处）
2. **补全** —— 用 ECDICT 补英文释义、词形变化、词频、柯林斯星级、考试标签
3. **给语料** —— 从 Tatoeba 抽真实例句与**介词搭配**
4. **用起来** —— 合并进 koolearn 分层词表，并用 A/S/D 网页工具做认词判定

两个数据位置由 `lexicon/paths.py` **单点**决定，当前取自
`lexicon.config.json`；未来集成进 `AtriumSteward` 时只改这一处。
**两者都是内容数据，都在仓库外面**（与仓库 README 的分工一致：本仓库只放代码与文档）：

```json
{
  "data_dir":     "D:/Repositories/Manager/AtriumNote/education/language/vocabularies",
  "koolearn_dir": "D:/Repositories/Manager/AtriumNote/education/language/vocabularies/koolearn-ielts"
}
```

| 位置 | 放什么 | 谁生成 |
| :-- | :-- | :-- |
| `data_dir` | 原文存档 `raw/`、`wordbook.db`、`needs_review.csv`、`overrides/` | `import` / `build` |
| `data_dir/koolearn-ielts/` | 分层词表 `0_`–`5_` CSV、`ielts_layered.json`、`raw_tags.json` | `scripts/crawl_koolearn_ielts.py` + `merge` / `layers` |
| 同上（不入库） | `.cache/`（抓取缓存）、`_backup_*/`（合并前备份） | 抓取 / `merge` |
| 同上（个人数据） | `drill_state.json`、`drill_unlearned_*.txt` | `drill` |

仓库里只留三样东西：代码、文档、以及**不可再生的人工定稿配置**
（`data/skill_map.json`，119 本书的技能归属）。

## 产物与实测数字

| 产物 | 位置 | 说明 |
| :-- | :-- | :-- |
| `raw/ielts_xdf_2015.txt` | `data_dir` | 原文存档，只读，记录 sha256 |
| `wordbook.db` | `data_dir` | 词库，可从原文完整重建 |
| `needs_review.csv` | `data_dir` | 人工校验清单，error→warn→info 排序 |
| `overrides/overrides.json` | `data_dir` | 人工修订，构建时最后覆盖 |
| `0_`–`5_*.csv` | `koolearn_dir` | 分层词表（见下） |
| `drill_state.json` | `koolearn_dir` | 认词判定进度（**个人数据，勿入库**） |

| 指标 | 数值 |
| :-- | --: |
| 词条 / 背诵单元 / 义项 | 3610 / 4392 / 8238 |
| ECDICT 补全 | 3605（**99.9%**，精确 3392 + 模糊 213） |
| 例句 | 10,297 条，覆盖 3501 词（**97.0%**） |
| 介词搭配 | 14,209 条，覆盖 2794 词 |

外部数据源只放在**工具目录 `.cache/`**（已 gitignore），不进任何仓库：

| 文件 | 体积 | 来源与许可 |
| :-- | --: | :-- |
| `.cache/ecdict.csv` | 63 MB | [ECDICT](https://github.com/skywind3000/ECDICT)，**MIT**，76 万词条 |
| `.cache/eng_sentences.tsv.bz2` | 24 MB | [Tatoeba](https://tatoeba.org) 英文句子，**CC BY 2.0 FR**，203.8 万句 |

## 背诵单元：`词头[词性]`

背单词的调度单位是 `word × pos`，记录格式为
**`词头[词性]` + 音标 + 释义（含词性）**：

```text
reject
  reject[vt.]              /rɪˈdʒekt/
      vt. 拒绝
  reject[n.]               /ˈriːdʒekt/
      n. 被拒货品，不合格品
```

异读词因此天然按词性分开。音标遵循原书的**读音组**约定：一个音标标记读音组
起点，其后词性共用，直到出现下一个音标——所以 `corrupt[a.]` 继承 `/kəˈrʌpt/`，
`desert[a.]` 继承 `/ˈdezət/`（`a desert island`），而 `desert[v.]` 是 `/dɪˈzɜːt/`。

**词组不单列表**：`word.kind='phrase'` + 内置词单 `短语`（38 条）已足够，
且词组与单词共用同一套调度（`pos_entry` 的 `pos` 为空、无音标）。

解析器对原文两处定界符笔误**自动修复并记 info**（非 error），不静默吞掉。

## 外部补全（ECDICT）

```text
corrupt
  --- ecdict 补全（匹配方式 exact）---
  补充音标  kə'rʌpt
  中文释义  a. 腐败的, 贪污的, 讹误充斥的 / vt. 使腐烂, 腐蚀, 使恶化 / vi. 腐烂, 堕落
  英文释义  v. corrupt morally or by intemperance or sensuality / v. alter from the original …
  词形变化  过去分词 corrupted；现在分词 corrupting；过去式 corrupted；第三人称单数 corrupts；复数 corrupts；比较级 corrupter
  柯林斯    ★★ ｜ 牛津3000 否 ｜ 词频 BNC #7571 当代 #5850
  考试标签  gk cet6 ky ielts gre
```

两个实现细节：ECDICT 用**字面量 `\n`**（反斜杠+n）分隔释义而非真换行；
音标里混了**西里尔形近字母** `ә`（U+04D9），已统一为国际音标 `ə`（1986 条）。

**不碰任何付费词典内容**（如牛津高阶网络版）——即便只自己用、不开源，
系统性整本复制仍属侵权且违反其服务条款。

## 开放语料：例句与介词搭配

从 Tatoeba 的 203.8 万英文句子里**一次扫描同时产出**两样东西：

* **例句**：每词最多 3 条，只收「该词恰好出现一次」的句子（一词多现看不出用法），
  按长度贴近 60 字符优选。
* **介词搭配**：统计「词 + 紧随其后的介词/小品词」，给出两个占比——
  `share_of_preps`（该词后面接介词时选这个词形的概率）才是回答「该配哪个介词」的那个。

```text
depend
  例句      We depend on foreign nations for our natural resources.
  搭配      depend on（接介词时占 95%｜语料 197 次）
  搭配      depend upon（接介词时占 5%｜语料 10 次）
comply
  搭配      comply with（接介词时占 100%｜语料 31 次）
```

**这些是从语料统计出来的原创衍生数据，不复制任何词典内容。** 介词是最高频的
词类，统计结果比编者挑选更贴近真实用法。

扫描性能：200 万句 / 约 2000 万 token，纯 Python **43 秒**。靠两处优化——
token→word_id 的解析结果带负缓存，短语按「首词」建索引、只在首词命中时才做子串检查。

## koolearn 分层词表

把本词书当作**一本额外的雅思词书**合并进 koolearn 抓取的分层表：

| 规则 | 效果 |
| :-- | :-- |
| 本词书收录的词 | `ielts_books += 1` |
| 原书 `*` 标记（听力词汇，1619 词） | 额外 `listen_books += 1` |
| 总表没有的词（19 个） | 追加，`wd_id`/`url` 留空，靠 `sources` 列区分 |
| `spot on` 与 koolearn 的 `spot-on` | 按「抹掉空格/连字符」的唯一模糊匹配并成一个词 |

**合并幂等**：已标记 `xdf` 的行不会重复计数，重跑只补新词头（`--force` 仅跳过安全拦截）。
分层文件由总表重建，因此始终满足
`2 号 = 1 号 ∪ 3 号 ∪ 4 号 ∪ 5 号 ∪（仅本词书覆盖的 L3-xdf 词）`，有 `qa` 子命令校验。

`ielts_layered.json` 是 0 号总表的**派生视图**（字段同名、数字转成原生类型），
所以 `layers` 会**顺带重新导出**它，避免留下旧快照——早期版本就踩过这个坑：
合并后 CSV 已是 26,747 词，JSON 却还是 26,728 词的旧快照。单独重导出用 `export-json`。

实测：0 号 26728→**26747**（+19）；3 号 2732→**2832**（100 词从 4 本跨到 5 本阈值）；
2 号 5838→**7260**（`L3-xdf` 层 1366 词）；`tier` 变化 265 词。

> `skills` 用四个技能桶，`layers` 只用听力/阅读/写作三个——口语桶样本太少
> （≥5 本收录仅 29 词），koolearn 原本就不为它出文件。

## 认词判定工具

* 只显示单词本身，不带释义
* **A = 不认识（左）／S = 见过但不熟（下）／D = 认识（右）**，`Esc` 停止本轮
* **回溯改判：`Backspace` 或 `←` 退回上一个词**；本轮判定存成**栈**，可一直退到本轮
  第一个词。已结束的本轮会被重新打开（上限进度同步回退）；重测模式下回溯还原到
  **本轮之前**的状态，不会把之前判过的词抹成「待检测」
* 累计「不认识」达到 `--limit`（默认 100）自动收工；`--count-unsure` 让「不熟」也计入
* 判定**逐词立即落盘**，中途停止只更新已判定的词，未判定保持「待检测」
* 收工把**「不认识」与「见过但不熟」合并成一个清单**（不认识在前，逗号分隔）输出，
  同时写 `drill_unlearned_r*.txt`

词池（`--pool`，默认 `pack`）：

| 名称 | 内容 | 词数 |
| :-- | :-- | --: |
| `pack` | 2 号推荐背诵包 | 7260 |
| `remaining` | 仅 `L3-xdf` 层 | 1366 |
| `xdf` | 本词书全部 | 3610 |
| `master` / `base` / `listening` / `reading` / `writing` | 对应 0/1/3/4/5 号 | — |

## 用法

```powershell
# 一次性下载外部数据源
run_lexicon.bat fetch-ecdict          # 63 MB
run_lexicon.bat fetch-tatoeba         # 24 MB

# 建库并补全
run_lexicon.bat rebuild               # build + enrich + corpus
run_lexicon.bat lookup depend         # 释义 + 补全 + 例句 + 搭配
run_lexicon.bat stats

# 分层词表
run_lexicon.bat merge
run_lexicon.bat layers
run_lexicon.bat qa
run_lexicon.bat pools
run_lexicon.bat export-json            # 0 号总表 → ielts_layered.json（layers 已自动做）

# 认词判定（自动开浏览器）
run_lexicon.bat drill
run_lexicon.bat drill --pool remaining --limit 50

# 全套（可重复执行）
run_lexicon.bat all
```

也可 `python -m lexicon <命令>`（需把本目录加入 `PYTHONPATH`）。

> **`build` 会重建整库**，补全/例句/搭配随之清空，必须重跑 `enrich` 与 `corpus`
> ——这正是 `rebuild` 与 `all` 存在的原因。派生数据都能从数据源重建，不需要备份。

测试：`python -X utf8 -m unittest discover -s tests -t .`（168 项）

## 对外 API

单一入口，只返回纯数据：

```python
from lexicon import (
    # 词库侧
    import_source, build_wordbook, build_enrichment, build_corpus,
    fetch_ecdict, fetch_tatoeba, lookup, stats,
    # koolearn 侧
    data_paths, merge_master, build_layers, export_master_json, verify, DrillServer,
)

paths = data_paths()
build_wordbook()        # -> BuildReport
build_enrichment()      # -> EnrichReport
build_corpus()          # -> CorpusReport
lookup("depend")        # -> dict
merge_master(paths)     # -> MergeReport（幂等）
build_layers(paths)     # -> LayerReport（顺带重导出 ielts_layered.json）
export_master_json(paths)   # 单独重导出 JSON
verify(paths)           # -> [] 表示不变量全通过
DrillServer(paths, pool="pack", limit=100).serve()
```

`lookup()` 的字段：

| 字段 | 内容 |
| :-- | :-- |
| `entries` | 背诵单元 `{key, pos, phonetic, phonetic_alt, gloss_cn, gloss_body}` |
| `senses` | 更细的义项（按 `；` 拆分） |
| `occurrences` | 原书出处（List 号与行号） |
| `enrichment` | ECDICT 补全：英文释义、词形变化、词频、柯林斯星级、牛津3000、考试标签 |
| `examples` | 真实例句（英文） |
| `collocations` | 介词搭配，含 `share_of_preps` |

尚未实现：背单词调度（SM-2 / FSRS）、复习日志、复习会话 CLI、例句挖空题型
（`example` 表已就绪）。学习进度将另存 `user.db`，与词典层物理分离。

## 目录

```text
tools/lexicon/
├── lexicon/
│   ├── paths.py      # 两个数据位置的唯一决定点
│   ├── parse.py      # 原文 → 结构化（纯函数）
│   ├── schema.py     # SQLite DDL + 最小迁移
│   ├── build.py      # 建库 / 查词 / 概览
│   ├── enrich.py     # ECDICT 补全
│   ├── corpus.py     # 语料 → 例句与搭配
│   ├── sources.py    # koolearn CSV 读写与词形归一
│   ├── merge.py      # 合并进总表、重建分层、导出 JSON
│   ├── qa.py         # 分层不变量校验
│   ├── drill.py      # A/S/D 认词判定（标准库 http.server + 内置单文件页面）
│   └── cli.py        # 全部子命令
├── scripts/
│   └── crawl_koolearn_ielts.py   # 抓取侧脚本：重跑会覆盖分层文件（见其 docstring）
├── data/
│   └── skill_map.json            # 119 本书的技能归属，人工定稿，必须版本化
├── docs/
│   └── koolearn-layered-list.md  # 分层词表的数据说明
├── tests/            # unittest，168 项
├── .cache/           # 外部数据源缓存（gitignore，约 87 MB）
├── lexicon.config.json
├── run_lexicon.bat
└── pyproject.toml
```
