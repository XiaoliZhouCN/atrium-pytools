# tarotdraw

**抽塔罗牌小工具** —— 纯 Python 标准库实现，无第三方依赖、无 UI 框架。

- 78 张牌面数据自带（`data/`），离线可用
- 三种经典牌阵 + 任意张数自定义牌阵
- 正位 / 逆位随机（可关闭），三种牌义来源可任选
- 终端彩色排版输出，或导出**单文件离线 HTML**（牌面图内嵌 base64，可点击翻牌看牌义）
- 带 `--seed` 可完整复现同一次抽牌

## 快速开始

```powershell
# 一键运行（推荐）
D:\Repositories\Manager\AtriumPyTools\launcher\run_tarotdraw.bat 3

# 或直接调用模块
$env:PYTHONPATH = "D:\Repositories\Manager\AtriumPyTools\tools\tarotdraw"
D:\Repositories\Manager\.venv\Scripts\python.exe -m tarotdraw 3
```

## 常用用法

```powershell
run_tarotdraw.bat                            # 抽 1 张（今日指引）
run_tarotdraw.bat 3                          # 三张牌：过去 / 现在 / 未来
run_tarotdraw.bat --spread situation         # 现状 / 建议 / 结果
run_tarotdraw.bat --spread cross             # 凯尔特十字（10 张）
run_tarotdraw.bat 5 --seed 42                # 抽 5 张，可复现
run_tarotdraw.bat 1 --upright-only           # 只出正位
run_tarotdraw.bat 1 --html draw.html         # 另存单文件 HTML
run_tarotdraw.bat 1 --html out\ --no-embed   # 导出到目录，图片用相对路径
run_tarotdraw.bat 3 --json                   # 输出 JSON 给其它程序消费
run_tarotdraw.bat --list-spreads             # 列出牌阵
run_tarotdraw.bat --list-decks               # 列出牌义来源
```

## CLI 参数

| 参数 | 说明 |
| :-- | :-- |
| `n` | 抽牌张数 1..78；省略时按牌阵张数（默认 1） |
| `-s, --spread NAME` | 牌阵：`single` / `three` / `situation` / `cross`，也接受别名 `3`、`10`、`celtic`、`sao` 与中文名 |
| `-d, --decks LIST` | 牌义来源，逗号分隔，默认全部三个 |
| `--seed N` | 随机种子；输出里总会回报本次实际种子 |
| `--upright-only` | 只出正位 |
| `--reversed-ratio P` | 逆位概率 0..1，默认 0.5 |
| `--html PATH` | 导出单文件 HTML；PATH 可为文件或目录 |
| `--no-embed` | HTML 不内嵌图片，改用相对路径 `data/` |
| `--json` | 输出 JSON |
| `--lang zh\|en\|both` | 文本语言（默认 zh） |
| `--intro` | 附带牌面简介 |
| `--width N` | 文本排版宽度（默认跟随终端） |
| `--color auto\|always\|never` | 颜色策略（默认 auto；也尊重 `NO_COLOR`/`FORCE_COLOR`） |
| `--data-dir DIR` | 覆盖数据目录 |
| `--list-spreads` / `--list-decks` | 列出牌阵 / 牌义来源 |
| `--version` | 版本号 |

## Python API

```python
from tarotdraw import draw, draw_cards, render_reading, render_html

# 纯数据（dict，可直接 json.dumps / 传给 steward）
payload = draw(3, spread="three", seed=42)

# 结构化对象（Reading / DrawnCard）
reading = draw_cards(3, spread="three", seed=42)
reading.cards[0].card.zh          # '圣杯六'
reading.cards[0].orientation_zh   # '正位' / '逆位'
reading.cards[0].meaning("ethereal_visions")["upright"]

# 渲染
text = render_reading(reading, color=False, width=84)
html = render_html(reading)                      # 单文件 HTML 字符串
```

公开 API 一览：`draw`、`draw_cards`、`readings_for`、`Reading`、`DrawnCard`、
`Card`、`get_card`、`all_cards`、`card_ids`、`spread_list`、`spread_keys`、
`get_spread`、`resolve_spread`、`load_deck`、`load_decks`、`deck_keys`、
`deck_label`、`render_reading`、`render_spreads`、`render_decks`、
`render_html`、`write_html`、`data_root`。

## 数据来源

`data/` 由 `Projects\ChestTarot` 原样拷贝而来（sha256 逐个校验）：

| 目录 | 内容 | 数量 |
| :-- | :-- | :-- |
| `data/cards/` | 牌面图 `major/` + `wands|cups|swords|pentacles/` | 78 jpg |
| `data/json/index.json` | id / 英文名 / 中文名 / 图片相对路径 | 78 条 |
| `data/json/*.json` | 牌义 deck（`general_guide`、`ethereal_visions`、`universal_waite`） | 3 × 78 条 |
| `data/guides/` | 原始 markdown 资料（供人查阅，程序不解析） | 3 份 |

已知数据缺口（上游原样保留，未做篡改，测试中已登记）：
`universal_waite` 的 `cups02` 正逆位牌义为空；工具会正常展示其余两个 deck 的牌义。
上游 `personal_notes.json` 全部牌义为空，未纳入本工具数据。

## 测试

标准库 `unittest`，无需额外依赖：

```powershell
cd D:\Repositories\Manager\AtriumPyTools\tools\tarotdraw
D:\Repositories\Manager\.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

## 安装（可选）

```powershell
D:\Repositories\Manager\.venv\Scripts\python.exe -m pip install -e "D:\Repositories\Manager\AtriumPyTools\tools\tarotdraw"
tarotdraw 3
```

> ⚠️ **不要**把 `AtriumPyTools\tools` 单独加进 `PYTHONPATH` 就完事。
> `tools\tarotdraw\` 目录本身没有 `__init__.py`，会被 Python 视为**命名空间包**并遮蔽
> 真正的包 `tools\tarotdraw\tarotdraw\`，症状是
> `No module named tarotdraw.__main__; 'tarotdraw' is a package and cannot be directly executed`。
> 正确做法是把 `tools\tarotdraw` 放在 `PYTHONPATH` **之前**（`run_tarotdraw.bat` 已这样处理）。

## 目录结构

```text
tools/tarotdraw/
├── tarotdraw/                  # 包
│   ├── __init__.py             # 公开 API
│   ├── __main__.py             # python -m tarotdraw
│   ├── cli.py                  # CLI 入口
│   ├── data.py                 # 数据目录定位
│   ├── cards.py                # 78 张牌面索引
│   ├── decks.py                # 牌义 deck 加载
│   ├── spreads.py              # 牌阵定义
│   ├── engine.py               # 抽牌引擎（洗牌/正逆位/牌义投影）
│   ├── render_text.py          # 终端渲染
│   └── render_html.py          # 单文件 HTML 渲染
├── data/                       # 素材（拷自 ChestTarot）
├── docs/ARCHITECTURE_DESIGN.md # 设计文档
├── tests/test_tarotdraw.py     # 45 个 unittest
├── pyproject.toml
├── run_tarotdraw.bat           # 工具内便捷入口
└── README.md
```

## 边界

按 AtriumPyTools 规范，本工具**只产出数据与文本/HTML 字符串**：

- 不引入 UI 框架（`PySide6` 等）
- 不调用系统程序、不自动打开浏览器 —— HTML 生成后由用户自行打开
- 不接入 steward：本次为独立可运行脚本，数据与逻辑自包含
