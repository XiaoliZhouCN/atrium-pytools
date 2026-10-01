# tarotdraw —— 架构设计

> **文档版本**：v1.0
> **创建日期**：2026-02-14
> **目标路径**：`tools/tarotdraw/docs/ARCHITECTURE_DESIGN.md`
> **状态**：**已实现**（v0.1.0，单元测试 45 项全绿）

---

## 一、职责边界

### 1.1 做什么

| 能力 | 说明 |
| :-- | :-- |
| 抽牌 | 从 78 张牌中无放回抽取 n 张（1..78），返回纯数据 |
| 正逆位 | 默认 50% 逆位，可关闭或调整概率；位置由种子决定，可复现 |
| 牌阵 | 内置 `single` / `three` / `situation` / `cross`，任意张数自动生成位置名 |
| 牌义 | 三种 deck（通用指南 / Ethereal Visions / Universal Waite）任选或全选 |
| 呈现 | 终端文本（含颜色、CJK 对齐、自动折行）；单文件离线 HTML（可点击翻牌） |
| 导出 | JSON（供 steward 等外部程序消费）；HTML 文件 |

### 1.2 明确不做

| 不做 | 原因 / 归属 |
| :-- | :-- |
| UI 框架（PySide6 / Tkinter / Qt） | AtriumPyTools 红线，只返回纯数据 |
| 打开浏览器 / 系统看图程序 | 红线：不调用外部程序；HTML 由用户自行打开 |
| 自动打开生成的文件、写入剪贴板 | 同上，保持 CLI 可组合、可管道 |
| 接入 steward | 本期为独立可运行脚本（需求 2），数据与逻辑自包含 |
| 在线牌义 / 图片抓取 | 完全离线，素材随工具自带 |
| 修改上游数据 | `data/` 从 ChestTarot 原样拷贝，缺口如实保留并登记在测试中 |
| 占卜结果解读 / AI 解牌 | 不在本期范围；只给牌义原文 |

---

## 二、分层与模块

### 2.1 依赖方向（严格单向，无环）

```
                  cli.py  /  __main__.py
                   │            │
       ┌───────────┼────────────┴────────────┐
       ▼           ▼                         ▼
 render_text.py  render_html.py           engine.py
       │           │                    ┌────┼────────┐
       └───────────┴──────► engine ◄────┘    │        │
                             │               │        │
                      spreads.py          decks.py  cards.py
                             │               │        │
                             └───────────────┴────────┘
                                       data.py
                                    （数据目录定位）
```

- `data.py` 是唯一接触文件系统的模块，其余模块只消费 `DataPaths`。
- `render_*` 只**读** `Reading`，不改变抽牌结果；两者互不依赖。
- `engine.py` 依赖 `cards` / `decks` / `spreads`，不被它们反向依赖。

### 2.2 模块清单

| 模块 | 职责 | 关键接口 |
| :-- | :-- | :-- |
| `data.py` | 解析数据根目录、读写 JSON | `data_root()`、`resolve_data_root()`、`load_json()`、`DataPaths` |
| `cards.py` | 78 张牌索引与派生字段 | `all_cards()`、`get_card()`、`card_ids()`、`Card` |
| `decks.py` | 牌义 deck 加载（带缓存） | `load_deck()`、`load_decks()`、`deck_keys()`、`Deck` |
| `spreads.py` | 牌阵定义与解析 | `SPREADS`、`resolve_spread()`、`get_spread()`、`default_spread_for()` |
| `engine.py` | 抽牌、正逆位、牌义投影 | `draw_cards()`、`draw()`、`readings_for()`、`Reading`、`DrawnCard` |
| `render_text.py` | 终端渲染 | `render_reading()`、`render_spreads()`、`render_decks()`、`Palette` |
| `render_html.py` | 单文件 HTML 渲染 | `render_html()`、`write_html()`、`image_data_uri()` |
| `cli.py` | 参数解析、退出码、打印 | `main(argv) -> int`、`build_parser()` |

### 2.3 数据目录定位优先级

1. 显式参数 `--data-dir` / `data_root(root)`
2. 环境变量 `TAROTDRAW_DATA`
3. 包目录兄弟目录 `<package>/../data`
4. 仓库根回退 `AtriumPyTools/tools/tarotdraw/data`

---

## 三、数据模型

### 3.1 磁盘布局

```
tools/tarotdraw/data/
├── cards/
│   ├── major/ma01.jpg … ma22.jpg            # 22
│   ├── wands/wands01.jpg … wands14.jpg      # 14
│   ├── cups/cups01.jpg … cups14.jpg         # 14
│   ├── swords/swords01.jpg … swords14.jpg   # 14
│   └── pentacles/pentacles01.jpg … 14.jpg   # 14
├── json/
│   ├── index.json                           # 78 条：id / en / zh / image
│   ├── general_guide.json                   # 78 条牌义
│   ├── ethereal_visions.json                # 78 条牌义
│   └── universal_waite.json                 # 78 条牌义
└── guides/                                  # 3 份原始 markdown（仅供人查阅）
```

总计 85 个文件、约 52 MiB，与上游 sha256 逐一相同。

### 3.2 牌义记录（三个 deck 同构）

```json
{
  "id": "ma01",
  "name": "The Fool",
  "astro": "Uranus",
  "element": "",
  "direction": "",
  "intro": "The Fool is the spirit of innocence ...",
  "upright": "Potential, new beginnings, opportunity, adventure",
  "reversed": "Carelessness, negligence, uncertainty, apathy"
}
```

### 3.3 内存模型

```python
Card(id, en, zh, image)                    # 不可变；suit/rank/is_major/element 为派生属性
DrawnCard(card, position, orientation, index, meanings: dict[str, dict[str, str]])
Reading(spread, cards: tuple[DrawnCard, ...], decks, seed, allow_reversed, requested_n)
```

`Reading.as_dict()` 输出 JSON 友好的纯数据：

```json
{
  "tool": "tarotdraw",
  "spread": {"key": "three", "positions": ["过去", "现在", "未来"], "...": "..."},
  "n": 3, "seed": 42, "allow_reversed": true, "data_root": "...",
  "cards": [
    {"id": "cups06", "zh": "圣杯六", "position": "过去", "orientation": "upright",
     "reversed": false, "meanings": {"general_guide": {"label": "...", "upright": "...", "reversed": "..."}}}
  ]
}
```

> 命名注意：`reversed` 是**布尔**；牌义文本在 `meanings[deck]["upright"|"reversed"]`。
> ChestTarot 原后端把布尔叫 `upright`，两处语义不同，已在代码 docstring 中标注。

---

## 四、外部接口

### 4.1 CLI 参数

见 `tools/tarotdraw/README.md` 的参数表（`n`、`--spread`、`--decks`、`--seed`、
`--upright-only`、`--reversed-ratio`、`--html`、`--no-embed`、`--json`、`--lang`、
`--intro`、`--width`、`--color`、`--data-dir`、`--list-spreads`、`--list-decks`、
`--version`）。

### 4.2 退出码

| 码 | 含义 |
| :-- | :-- |
| `0` | 成功 |
| `2` | 用法错误（张数/牌阵/deck 非法；argparse 与 `DrawError` 共用） |
| `3` | 数据目录不可用（缺失、结构不合法） |

路径与种子等可恢复信息写 **stderr**，结果数据写 **stdout**，便于 `>` 与管道。

### 4.3 Python API

`tarotdraw/__init__.py` 导出抽牌、数据、牌阵三组 API；渲染函数经模块级
`__getattr__` **懒加载**（纯数据调用方不会拉起 base64/HTML 相关代码）。

---

## 五、构建与依赖

| 项 | 值 |
| :-- | :-- |
| Python | >= 3.11（实际验证 3.14.2） |
| 第三方依赖 | **无**（纯标准库：argparse / json / random / base64 / html / mimetypes / unicodedata） |
| 测试依赖 | 无（标准库 `unittest`） |
| 打包 | `pyproject.toml` + setuptools；入口 `tarotdraw = "tarotdraw.cli:main"` |
| 虚拟环境 | 工作区唯一环境 `Manager\.venv`，不得另建 |
| 启动脚本 | `AtriumPyTools\launcher\run_tarotdraw.bat`（工具内 `run_tarotdraw.bat` 委托之） |

### 5.1 已验证的部署陷阱（重要）

`tools\tarotdraw\` 目录本身没有 `__init__.py`，当 `AtriumPyTools\tools` 位于
`PYTHONPATH` 时，Python 会把 `tools\tarotdraw\` 解析为**命名空间包**，从而遮蔽真正的
包 `tools\tarotdraw\tarotdraw\`。症状：

```
No module named tarotdraw.__main__; 'tarotdraw' is a package and cannot be directly executed
```

**解法**：`PYTHONPATH` 中把 `tools\tarotdraw` 放在 `tools` **之前**（启动脚本已如此设置），
或直接 `pip install -e`。该陷阱已实测复现并记录，非推测。

---

## 六、测试策略

### 6.1 单元 / 集成（45 项，`tests/test_tarotdraw.py`）

| 组 | 覆盖内容 |
| :-- | :-- |
| 数据 | 目录结构、78 张牌面图存在、index 形状（22 大牌 + 4×14）、deck 覆盖全部 id、派生字段、坏目录报错 |
| 引擎 | 默认单张、无放回、同种子可复现、**回报的种子可复现随机抽牌**、正逆位开关与概率极值、非法张数/概率/deck/牌阵、张数不符降级、牌义投影与 deck 一致、JSON 往返 |
| 文本 | 牌名/位向/位置/种子齐全、颜色开关、行宽不超限、语言变体 |
| HTML | 标签闭合（`HTMLParser` 校验）、完全离线（无 http(s) 外链）、base64 数量、**解出的字节是真实 JPEG（SOI/EOI）**、逆位 class、`--no-embed` 相对路径、标题转义 |
| CLI | 各子路径退出码、`--json`、`--html`（文件/目录两种目标）、`--no-embed`、`--spread` 推导张数、非法输入干净报错 |

### 6.2 已知数据缺口（测试中登记，非缺陷）

| 缺口 | 处理 |
| :-- | :-- |
| `universal_waite/cups02` 正逆位牌义为空 | 原样保留；断言其余 deck 正常展示，且渲染不崩 |
| 上游 `personal_notes.json` 全空 | 未纳入 `data/` |

### 6.3 人工验收

1. `run_tarotdraw.bat 3` → 终端三张牌，中文正常、无乱码。
2. `run_tarotdraw.bat 3 --seed 42 --html out\a.html` → 浏览器打开，三张牌面图可见，点击翻牌出牌义，HTML 总大小 ≈ 3 张图 × 4/3。
3. 断网后重复第 2 步 → 牌面图仍显示（base64 内嵌）。
4. 同 `--seed` 连跑两次 → 抽牌结果完全一致。

---

## 七、性能预算

| 指标 | 预算 | 实测（Windows / Python 3.14.2） |
| :-- | :-- | :-- |
| 导入 `tarotdraw`（冷启动，含索引与牌义懒加载） | ≤ 200 ms | 29 ms（不含解释器启动） |
| 抽 1 张（导入完成后） | ≤ 50 ms | 4 ms |
| 抽满 78 张 + 投影 3 deck 牌义 | ≤ 300 ms | 15 ms |
| 单元测试全量 | ≤ 10 s | 0.35 s |
| 单张牌 HTML（内嵌 1 图，约 770 KiB 图） | ≤ 1 s | 43 ms，产物 770 KiB |
| 3 张牌 HTML 体积 | ≈ 图合计 × 1.34 | 2.77 MB（3 图约 2.6 MiB） |
| 内嵌体积上限 | 32 MiB 后自动降级为相对路径 | 由 `--no-embed` / `max_embed_bytes` 控制 |

终端渲染为纯字符串拼接，未引入正则回溯或二次遍历；`render_text` 按显示宽度单遍扫描折行
（CJK 记 2 列，`unicodedata.east_asian_width`）。

---

## 八、风险与未决项

| 项 | 类型 | 说明 / 处置 |
| :-- | :-- | :-- |
| `data/` 与上游 ChestTarot 会漂移 | 风险 | 本期按需求「复制到新目录」刻意解耦；如需同步，另写 `tools/sync_data.py`（**未实现**） |
| 素材 52 MiB 入库体积 | 风险 | 若入 git 建议评估 LFS；当前未加入 `.gitignore`，按需求保留副本 |
| HTML 无翻牌 JS 环境降级 | 风险 | 打印媒体查询与 `:target` 之外无兜底；JS 被禁时牌义不可见（牌面图仍可见）。列为 `pending` |
| 未接入 steward | 未决项 | 需求明确本期不接；`as_dict()` 已按纯数据设计，后续接入成本低 |
| Windows 控制台编码 | 风险 | 启动脚本保持纯 ASCII，中文由 Python 输出；`PYTHONUTF8=1` 已设。非中文 Windows 上字体缺失可能显示方块 |
| 牌阵位置语义单一 | 未决项 | 目前每种牌阵一套固定位置名，暂不支持自定义位置名（`--positions`）。列为 `pending` |
| 图片缺失时静默降级 | 风险 | `render_html` 捕获 `HtmlError` 并在牌面处给出提示，不中断整次渲染 |

---

## 九、后续可扩展点（非本期）

1. `--positions "A,B,C"` 自定义位置名。
2. `tools/sync_data.py`：从 ChestTarot 增量同步素材并打印 diff。
3. 牌阵布局坐标（HTML 网格位置），支持凯尔特十字的真实十字布局。
4. `--out-md` 导出 markdown 抽牌记录，便于写入 `AtriumNote`。
5. steward 适配层：把 `as_dict()` 结果注册为可调用工具。
