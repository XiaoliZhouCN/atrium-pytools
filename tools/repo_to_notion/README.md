# repo_to_notion

把本地仓库（默认 `D:\Repositories`）的**目录结构与文档**同步到 Notion。

当前版式（v3，2026-10-05 起）→ 目标页
[AI Generated Tree](https://app.notion.com/p/AI-Generated-Tree-3efe47bbdac580ca954cc1adc2adbcb7)：

```
本地仓库文档地图 · 目录 464 个（可折叠） · 文件页 71 个
▶ DeepseekHarness                     ← 点一下展开 / 收起
    ▶ deepseek-harness
    ▶ shirleyzh-dsh-sessions
▶ Manager ⋯
    ▶ AtriumCppTools
        ▶ docs
            - 📄 BRANCH_MODEL.md      ← 文件子页面，点开即全文
        ▶ tools
            - 📄 README.md
```

* **每个目录 = 一个 toggle（▶ 可折叠）**，子目录是嵌套 toggle，可逐层展开/收起。
* **每个文件 = 一个子页面**，以「📄 页面 mention」列表项挂在所属目录的 toggle 里。
* 目录名后的 `⋯` 表示该目录还有更深的层级、本次未展开。

> 历史版式：v2 用 `heading_1/2/3` 当目录（`push_outline_to_notion.py`）、
> v1 用带分级编号的文件夹页（`scan_repo_docs.py` + `push_to_notion.py`）。
> 两套脚本都保留在目录里，改 `--parent` 即可复用。

---

## 1. 文件清单

| 文件 | 作用 |
| --- | --- |
| `scan_repo_outline.py` | **扫描**：目录全展开 + 按策略挑出要建页的文档 → `out/outline.json/txt`、`out/files.csv` |
| `push_tree_to_notion.py` | **v3 写入**：清空目标页 → 建容器页与文件页 → 按层 BFS 写出可折叠 toggle 树 |
| `verify_tree.py` | **v3 校验**：逐层核对整棵 toggle 树（数量/名称/链接目标）+ 容器页 + 正文抽样 |
| `push_outline_to_notion.py`、`verify_outline.py` | v2 写入 / 校验（heading 版式） |
| `scan_repo_docs.py`、`push_to_notion.py`、`verify_sync.py` | v1 扫描 / 写入 / 校验（编号文件夹页版式）；`push_to_notion.py` 同时提供各版本复用的公共函数 |
| `selftest.py` | 不联网自检块清洗规则（表格 / 图片 / UTF-16 长度 / 代码语言） |
| `out/` | 清单、state、日志、校验报告 |

依赖仓库里已有的 [`notionsync`](../notionsync/)（纯标准库 Notion 客户端 + Markdown→Blocks）
与 `~/.notionsync/workspaces.json`，无需 pip 安装。

---

## 2. 扫描策略

| 根目录 | 策略 | 行为 |
| --- | --- | --- |
| `DeepseekHarness`、`Forks`、`Storage` | `shallow` | **只列第一层目录名**，不再下钻，不建文件页 |
| `Manager/AtriumNote` | `limited` | 目录全展开；只给 `AGENTS.md`、`README.md` 以及 `Docs/` 下的文件建页，其余目录只留层级 |
| 其余（`Manager/*`、`Projects`） | `full` | 目录全展开；文档名命中（README/AGENTS/DESIGN/ARCHITECTURE/SPEC/CONVENTION/STYLE/NAMING/GUIDELINE/PRINCIPLE/…）或位于 docs 类目录（`docs` `doc` `documentation` `guide(s)` `manual` `handbook` `wiki` `spec(s)` `reference` `design` `architecture`）下的文件建页 |

排除：`node_modules`、构建产物（`build` `dist` `obj` `CMakeFiles` `*_autogen` `*.dir`）、
缓存、第三方（`vendor` `third_party` `builtin_shaders-*`）、Unity 生成目录（`Library` `Temp` `Logs`）、
所有隐藏目录（`.git` `.github` `.agents` …）。

`--max-dir-depth`（默认 8）限制目录展开深度：Unity/生成树能到 15 层，不设上限时会有
1400+ 条且大半是第三方资源目录。

---

## 3. 用法

```powershell
$py = "C:\Users\ChestNut\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
$tool = "D:\Repositories\Manager\AtriumPyTools\tools\repo_to_notion"
$page = "3efe47bbdac580ca954cc1adc2adbcb7"     # AI Generated Tree

# ① 扫描（仓库结构变了就重扫）
& $py "$tool\scan_repo_outline.py" --print-outline

# ② 看写入计划，不碰 Notion
& $py "$tool\push_tree_to_notion.py" --parent $page --dry-run

# ③ 写入 / 更新：清空目标页正文 → 复用或新建文件页 → 重写折叠树
& $py "$tool\push_tree_to_notion.py" --parent $page

# ④ 校验（逐层核对整棵树）
& $py "$tool\verify_tree.py" --parent $page
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--parent` | **必填**，目标页面 ID 或 URL |
| `--max-dir-depth N` | 目录展开深度上限（0 = 不限，仅扫描脚本） |
| `--reset` / `--no-reset` | 是否先清空目标页正文、归档无关子页面（默认开；**已知容器页会被保留**） |
| `--workers` / `--throttle` | 并发线程数（默认 4）/ 全局请求间隔下限（默认 0.34s ≈ 3 req/s） |
| `--max-blocks` / `--full-content` | 单页正文块上限（默认 900）/ 不截断 |
| `--dry-run` | 只打印计划，不写 Notion、不动 state |

### 断点续传与「活的」地图

`out/tree_state.json` 记录容器页、每个文件页的 id、正文是否已写。重跑时：

* 已存在的文件页会先 `GET` 确认还活着，**被删掉的会自动重建**；
* 磁盘上已消失的文档，其文件页会被当作**孤儿页归档**；
* 目标页正文先清空再重写，所以不会出现重复的 toggle 树。

---

## 4. 写入 Notion 的兜底规则

* toggle 的 id 只有写进去才知道 → 按层 BFS：第 1 层写到页面下，拿到 id 后并发写下一层
  （Notion 单次请求最多嵌套两层，必须逐层来）。
* rich_text 单段上限 2000 **UTF-16 码元**（emoji 占 2）→ 按码元切段。
* 单个 `table` 最多 100 行 → 超长表格拆成多张，后续分片重复表头。
* 非法图片 URL → 降级为含 URL 的普通段落（否则整页 400）。
* 代码块语言不在 Notion 枚举内 → 降级 `plain text`。
* 单次追加 100 块 → `notionsync` 自动分批；429 / 5xx → 指数退避重试。

以上规则由 `selftest.py` 覆盖。

---

## 5. 最近一次运行（2026-10-05）

| 项目 | 数值 |
| --- | --- |
| 扫描 | 目录 **464** 个（展开到第 8 层，59 个标记 `⋯`），文件页 **71** 个 |
| 写入 | 容器页 1 + 文件页 71 + toggle **464** 个，目标页正文 9 块（说明 2 + 分隔线 + 5 个顶层 toggle） |
| 失败 | 0 |
| 校验 | 逐层遍历 174 个父块：子 toggle 459 + 顶层 5 = 464 = 目录数；文件项 71 = 文件页数；容器页 71 个子页面标题全对；正文抽样正常 |

期间处理的两次内容变动：上一版 1731 个编号页与 72 个文件页被手动删除 → 归档孤儿、
按需重建；`AtriumPyTools/vocab/**`（2 篇 README）与 `pi-dsh-architecture/**` 从磁盘删除
→ 重新扫描后自动从树里移除并归档对应文件页。
