# AtriumPyTools

`Manager` 工作区的 **Python 轻量工具与脚本层**。

上位依据：`AtriumSteward/Docs/ARCHITECTURE_DESIGN.md`（Python Tool Layer）
工作区规范：`AtriumSteward/Docs/WORKSPACE_SPECIFICATION.md`

## 边界

本仓库收纳：小工具 CLI、文本处理脚本、原型算法、AI 辅助脚本、自动化 glue code。

本仓库**不**收纳：

| 不收纳 | 应放置位置 |
| :-- | :-- |
| 宿主主控逻辑、常驻基础设施 | `AtriumSteward` |
| 重型工具、大型可视化编辑器 | `AtriumCppTools` |
| 复杂多媒体承载与长期状态管理 | `AtriumCppTools` |
| 笔记内容与附件 | `AtriumNote` / `Storage` |

**红线**：严禁引入 UI 框架（`PySide6` 等）；严禁返回 UI 控件，只返回纯数据。

## 工具清单

| 工具 | 状态 | 说明 |
| :-- | :-- | :-- |
| `tools/colorpicker` | 有实现 | 跨平台屏幕取色，macOS + Windows；纯 Python，无 UI 框架 |
| `tools/tarotdraw` | 有实现 | 抽塔罗牌；78 张牌面 + 3 套牌义自带，终端排版 / 单文件离线 HTML；纯标准库 |
| `tools/lexicon` | 有实现 | 雅思词库工具链：原文 → SQLite 词条/**背诵单元(`词头[词性]`)**/义项/音标/出处；用 ECDICT(MIT) 补英文释义/词形/词频/考试标签，用 Tatoeba(CC BY) 抽例句与**介词搭配**；合并进 koolearn 分层词表并提供 A/S/D 认词判定网页工具；零第三方依赖 |
| `tools/filechecker` | **仅占位** | 无任何实现，详见该目录 `docs/ARCHITECTURE_DESIGN.md` |

### lexicon

**雅思词库工具链**，一条链做四件事：建库 → 补全 → 给语料 → 用起来。
原来拆成 `vocabdrill` 与 `ieltsvocab` 两个目录，现已合并为单一工具。

**代码在本仓库，两个数据位置都在仓库外**（由 `tools/lexicon/lexicon.config.json` 单点指定）：
词库数据与 koolearn 分层词表都在 `AtriumNote/education/language/vocabularies/` 下。
仓库里只留代码、文档，以及不可再生的人工定稿配置（`tools/lexicon/data/skill_map.json`）。

两个外部数据源只放工具目录 `.cache/`（gitignore，约 87 MB）：

| 来源 | 许可 | 补什么 |
| :-- | :-- | :-- |
| [ECDICT](https://github.com/skywind3000/ECDICT) 63 MB | MIT | 英文释义、词形变化、柯林斯星级、牛津3000 标记、考试标签（含 ielts）、BNC/当代双词频。命中 **99.9%** |
| [Tatoeba](https://tatoeba.org) 24 MB | CC BY 2.0 FR | 203.8 万英文句 → **10,297 条例句**（覆盖 97%）+ **14,209 条介词搭配** |

介词搭配是语料统计的**原创衍生数据**（`depend on` 95% / `depend upon` 5%、`comply with` 100%），
不复制任何词典内容。**不爬付费词典**（如牛津高阶网络版）——即便只自己用、不开源，
系统性整本复制仍属侵权且违反其服务条款。

```powershell
run_lexicon.bat fetch-ecdict      # 一次性下载 63 MB
run_lexicon.bat fetch-tatoeba     # 一次性下载 24 MB
run_lexicon.bat rebuild           # build + enrich + corpus
run_lexicon.bat lookup depend     # 释义 + 补全 + 例句 + 搭配
run_lexicon.bat merge             # 合并进 koolearn 0 号总表（幂等）
run_lexicon.bat layers            # 重建 1-5 号分层文件
run_lexicon.bat qa                # 分层不变量校验
run_lexicon.bat drill             # A/S/D 认词判定（自动开浏览器，支持回溯改判）
run_lexicon.bat all               # 全套，可重复执行
```

> `build` 会重建整库，补全/例句/搭配随之清空，须重跑 `enrich`/`corpus`——这就是
> `rebuild` 与 `all` 的用途。

认词判定：只显示单词，**A = 不认识 / S = 见过但不熟 / D = 认识 / Esc = 停止本轮**，
**`Backspace`／`←` 回溯改判**（本轮判定存成栈，可一直退到本轮第一个词；已结束的本轮会被
重新打开）。累计不认识达到 `--limit`（默认 100）自动收工（`--count-unsure` 让"不熟"也计入）；
判定逐词立即落盘。收工把**「不认识」与「见过但不熟」合并成一个清单**（不认识在前）输出。

对外 API（`lexicon/__init__.py` 暴露，只返回纯数据）：

```python
from lexicon import (
    import_source, build_wordbook, build_enrichment, build_corpus,
    fetch_ecdict, fetch_tatoeba, lookup, stats,
    data_paths, merge_master, build_layers, verify, DrillServer,
)
```

零第三方依赖（HTTP 服务用标准库 `http.server`，页面为内置单文件 HTML；
语料扫描 200 万句 43 秒）。
测试：`python -X utf8 -m unittest discover -s tests -t .`（168 项）。

背单词调度（SM-2 / 复习日志）与例句挖空题型尚未实现。

### tarotdraw

对外 API（由 `tarotdraw/__init__.py` 暴露）：

```python
from tarotdraw import draw, draw_cards, render_reading, render_html

draw(3, spread="three", seed=42) -> dict      # 纯数据，可直接 json.dumps
draw_cards(3, seed=42) -> Reading             # 结构化结果
render_reading(reading) -> str                # 终端文本
render_html(reading) -> str                   # 单文件离线 HTML
```

CLI：

```powershell
run_tarotdraw.bat 3                       # 三张牌：过去 / 现在 / 未来
run_tarotdraw.bat --spread cross          # 凯尔特十字
run_tarotdraw.bat 1 --html draw.html      # 导出单文件 HTML（牌面图内嵌）
run_tarotdraw.bat --list-spreads
```

零第三方依赖（仅标准库），自带 78 张牌面图与 3 套牌义数据（拷自 `Projects\ChestTarot`，
sha256 逐一校验）。终端输出以一行牌面一览结尾（`牌面一览：圣杯六·正位，太阳·逆位，…`）。
测试：`python -m unittest discover -s tests -t .`（56 项）。
**未接入 steward**，为独立可运行脚本。

> **PYTHONPATH 陷阱**：`tools\tarotdraw\` 目录本身无 `__init__.py`，若只把 `tools` 加进
> `PYTHONPATH`，它会被当作命名空间包并遮蔽真正的包，报
> `No module named tarotdraw.__main__`。须把 `tools\tarotdraw` 放在 `tools` 之前，
> 或 `pip install -e`。启动脚本已处理。

### colorpicker

对外 API（由 `colorpicker/__init__.py` 暴露）：

```python
from colorpicker import sample_at, sample_at_cursor

sample_at(x: int, y: int) -> dict   # {'x','y','r','g','b'}，线性光强度，float
sample_at_cursor() -> dict          # 同上，坐标来自当前鼠标位置
```

CLI：

```powershell
colorpicker --x 100 --y 200      # 采样指定坐标
colorpicker --cursor              # 跟随鼠标
colorpicker --digits 6            # 小数位数
```

平台分层：`platform/mac/`（ScreenCaptureKit via PyObjC）与 `platform/win/`（`win32api`）。
`core.py` 目前仅有模块 docstring，色彩算法尚未实现——HDR / EDR / Excel 导出等功能见该工具 `docs/ARCHITECTURE_DESIGN.md` 的规划。

## 安装

`colorpicker` 是一个**独立子包**，其顶层导入名就是 `colorpicker`，因此必须安装子目录而非仓库根包：

```powershell
& "D:\Repositories\Manager\.venv\Scripts\python.exe" -m pip install -e "D:\Repositories\Manager\AtriumPyTools\tools\colorpicker"
```

工作区唯一虚拟环境是 `Manager/.venv`，不得创建独立环境。

## 已知问题（待修）

| 问题 | 位置 | 影响 |
| :-- | :-- | :-- |
| 仓库根 `__init__.py` 的内容是一段 `pyproject.toml` 片段（`[tool.setuptools.packages.find]`），不是合法 Python | `__init__.py` | 任何 `import AtriumPyTools` 都会失败 |
| 包名与描述仍是旧名 | `pyproject.toml`：`name = "ChestPyTools"`、`description = "Chest 工作区纯 Python 工具库"` | 与 `Atrium*` 命名断层 |
| 启动脚本指向不存在的虚拟环境 | `launcher/run_colorpicker.bat` 使用 `AtriumSteward\.venv` | 该路径不存在，脚本必然失败；应改为 `Manager\.venv` |
| 源码注释带旧路径标记 | `tools/colorpicker/__init__.py`、`tools/__init__.py`、`tools/colorpicker/cli.py` | 文档与代码不一致 |
| `cli.py`（根）为 0 字节，但 `pyproject.toml` 声明了 `chest-cli = "cli:main"` | `cli.py` | 入口脚本不可用 |
| 工具根目录与包同名，仅把 `tools` 加进 `PYTHONPATH` 会命中命名空间包 | `tools/tarotdraw/` | `-m tarotdraw` 报 `No module named tarotdraw.__main__`；已在启动脚本中规避并写入该工具文档 |

## 新工具开发流程

1. 在 `tools/<name>/` 建目录，**命名不加 `Atrium` 前缀**（与 `colorpicker` / `filechecker` 对齐）。
2. 提供 `docs/ARCHITECTURE_DESIGN.md`，按占位模板中的小节要求补全。
3. 提供独立 `pyproject.toml`，暴露稳定 API 与 CLI 入口。
4. 无 UI 框架依赖，只返回纯数据。
5. 记录在本文档的「工具清单」表中。

## 目录结构

```text
AtriumPyTools/
├── pyproject.toml              # 仓库级打包（当前有旧名与编码问题）
├── README.md                   # 本文件
├── cli.py                      # 空，与 pyproject 声明的入口不符
├── launcher/
│   ├── run_colorpicker.bat
│   ├── run_tarotdraw.bat
│   └── run_lexicon.bat
└── tools/
    ├── colorpicker/            # 有实现
    │   ├── __init__.py         # 公开 API
    │   ├── cli.py  core.py  profile.py
    │   ├── pyproject.toml      # 子包打包（name = "colorpicker"）
    │   ├── platform/{base.py,win/,mac/}
    │   └── docs/ARCHITECTURE_DESIGN.md
    ├── tarotdraw/              # 有实现（自带数据的工具）
    │   ├── tarotdraw/          # 包：cli / data / cards / decks / spreads / engine / render_*
    │   ├── data/               # 78 张牌面图 + 4 个 json + 3 份 markdown
    │   ├── tests/              # unittest，56 项
    │   ├── run_tarotdraw.bat   # 工具内入口（委托 launcher）
    │   ├── pyproject.toml
    │   └── docs/ARCHITECTURE_DESIGN.md
    ├── lexicon/                # 有实现（两个数据位置都在 AtriumNote，不在本仓库）
    │   ├── lexicon/            # 包：paths / parse / schema / build / enrich / corpus
    │   │                       #     / sources / merge / qa / drill / cli
    │   ├── scripts/            # 抓取侧脚本（koolearn 分层词表）
    │   ├── data/skill_map.json # 119 本书的技能归属，人工定稿
    │   ├── docs/               # 分层词表数据说明
    │   ├── .cache/             # ECDICT + Tatoeba 下载缓存（gitignore，约 87 MB）
    │   ├── tests/              # unittest，168 项
    │   ├── lexicon.config.json # 两个数据位置的单点声明
    │   ├── run_lexicon.bat     # 工具内入口（委托 launcher）
    │   ├── pyproject.toml
    │   └── README.md
    └── filechecker/
        └── docs/ARCHITECTURE_DESIGN.md   # 仅占位，无实现
```

> 目录大小写：本仓库使用小写 `docs/`，`AtriumSteward` 与 `AtriumCppTools` 使用 `Docs/`。该不一致已登记为工作区待决项（`WORKSPACE_SPECIFICATION.md` §8 W1）。
