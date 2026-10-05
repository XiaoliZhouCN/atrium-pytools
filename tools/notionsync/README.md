# notionsync

把**同一个 Notion 读写层**同时接到三个地方：本机 Python、DSH、Trae Work。

```
                 ┌───────────────────────────────┐
                 │  tools/notionsync/mcp_server.py│   ← 本地 stdio MCP 服务器
                 │  notionsync.client (纯标准库)   │      持有 A、B 两个空间的 Token
                 └───────────┬───────────────────┘
        ┌────────────────────┼────────────────────┐
        │                    │                    │
   DSH (dsh-mcp-client)  Trae (mcp.json)   本机 Python (import / CLI)
```

## 为什么不是"直接接 Notion 官方 MCP"

Notion 的**每一种凭据都只属于一个 workspace**（官方 OAuth 安装、内部连接 Token、
PAT 皆然）。DSH 现有的 `dsh-notion-mcp` 又把凭据 ref 硬编码成 `NOTION_OAUTH`，
一个 profile 里塞不下第二套。所以：

> **两个空间 ⇒ 必须两枚 Token。** 本目录用一个自建 MCP 服务器同时持有两枚，
> 对外只暴露一套工具（每个工具带 `workspace` 参数），三端因此共用同一份实现。

## 凭据怎么选

> **不需要 Notion 订阅，也不需要 Notion 官方托管 MCP 的授权。** 本目录用的是标准
> REST API + 你自己的 API Key（PAT / 内部连接 Token），这条路对免费账号开放。
> 官方托管 MCP（`mcp.notion.com`）是另一条独立通路，与本方案无关。

| | Personal Access Token（PAT，**推荐**） | 内部连接 Internal Connection |
| :-- | :-- | :-- |
| 身份 | 以**你本人**的权限访问 | 以一个独立 bot 用户的权限访问 |
| 页面共享 | **不需要**，你能看到的它就能看到（含私人空间） | **必须逐页共享**（页面 → `•••` → Connections → 添加） |
| 创建入口 | <https://www.notion.so/developers/tokens> → New token | 开发者门户 → Build → Internal connections → Create |
| 有效期 | 7 / 30 / 90 / 180 天或 1 年（默认 1 年） | 无过期，除非吊销 |
| 创建权限 | 免费版仅 workspace owner 可建 | 需 workspace owner |
| 认证方式 | `Authorization: Bearer <token>` + `Notion-Version` | 同左 |

本工具**不区分两者**，都按 Bearer Token 处理。要覆盖「工作空间 + 私人空间」，
PAT 的体验明显更好（无需逐页共享）。

> 目标 API 版本为 `2025-09-03`：该版本起 database 只是容器，数据在 data source 上，
> 查询走 `/v1/data_sources/{id}/query`。本层会自动把 database ID 解析成 data source。
> 旧版 `2022-06-28` 在多 data source 场景会直接报错，故未采用。

## 安装与授权

### 1. 建两枚 Token

在**每个**空间各建一枚（空间 A 一枚、空间 B 一枚）。PAT 的创建入口：

<https://www.notion.so/developers/tokens> → **New token** → 选 workspace → 勾选
**Notion API** 能力 → 复制 Token（**只显示一次**）。

若用内部连接，建好后别忘了把目标页面共享给它：打开页面 → 右上 `•••` →
**Connections** → `+ Add connection`。共享父页面即可继承全部子页。

### 2. 写入本地配置（Token 不进聊天、不进仓库）

```powershell
python "D:\Repositories\Manager\AtriumPyTools\tools\notionsync\setup_tokens.py"
```

脚本在你自己的终端里读取 Token（不回显），立刻调 `/v1/users/me` 验证，并把
**该 Token 实际属于哪个空间**打印出来——顺便帮你确认 A/B 有没有配反。它写入：

```
C:\Users\<你>\.notionsync\workspaces.json
```

非交互写法（注意会进 shell 历史）：

```powershell
python tools\notionsync\setup_tokens.py --force --token A=ntn_xxx --token B=ntn_yyy
```

### 3. 自检

```powershell
python "D:\Repositories\Manager\AtriumPyTools\tools\notionsync\tests\run_all.py"
```

一次跑完五套：**离线单测 45 项**、**MCP 握手探针 16 项**、**mock Notion 端到端 28 项**、
**真实配置接线验证 17 项**、**真实 Notion 读写**。最后一类在缺 Token 时记为 SKIP 而不是失败，
所以填 Token 之前也能直接跑。

```powershell
python "D:\Repositories\Manager\AtriumPyTools\tools\notionsync\notionctl.py" doctor
python "D:\Repositories\Manager\AtriumPyTools\tools\notionsync\tests\test_live.py"
```

`test_live.py` 会真的建一个 `[SCRATCH]` 草稿页，做完读写往返后**自动把它移入回收站**。
加 `--read-only` 可以完全不写。

## 本机 Python 用法

```powershell
cd D:\Repositories\Manager\AtriumPyTools\tools\notionsync

python notionctl.py ws                              # 看已配置的空间与连通性
python notionctl.py search "OpenGL" -w B            # 搜索
python notionctl.py cat <页面URL>                    # 读正文（Markdown）
python notionctl.py ls <页面或数据库URL>              # 列子页 / 列数据库行
python notionctl.py new --parent <ID> --title "标题" --file body.md
python notionctl.py new --parent <数据库ID> --title "新行" --type data_source
python notionctl.py append <页面URL> --file body.md  # 追加
python notionctl.py write  <页面URL> --file body.md  # 覆盖正文
python notionctl.py title  <页面URL> "新标题"
python notionctl.py comment <页面URL> --text "内容"
python notionctl.py db <数据库URL>
python notionctl.py query <数据库URL> --filter '{"property":"Topic","select":{"equals":"AI"}}'
python notionctl.py trash <页面URL>                  # 移入回收站
python notionctl.py --json ...                      # 任意命令加 --json 输出机读结果
```

作为库使用：

```python
import sys
sys.path.insert(0, r"D:\Repositories\Manager\AtriumPyTools\tools\notionsync")

from notionsync import service

registry = service.make_registry()
result = service.read_page(registry, "B", "https://app.notion.com/p/xxxx")
print(result["markdown"])
```

## DSH 接入

DSH 自带通用 MCP 客户端 `@deepseek-ai/dsh-mcp-client`，在 profile 的
`cordis.patch.yml` 尾部追加一行即可（这是 `insert`，不是覆盖已有行列）：

```yaml
- insert:
    - id: mcp-notionsync
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: notionsync
        transport: stdio
        command: 'D:\Applications\Python\Python 3.14\python.exe'
        args:
          - 'D:\Repositories\Manager\AtriumPyTools\tools\notionsync\mcp_server.py'
        failOnStartupError: false
        toolCallTimeoutMs: 120000
        env:
          PYTHONUTF8: '1'
          PYTHONIOENCODING: 'utf-8'
```

> **`env` 里的两行不是可选项。** Windows 管道默认用 ANSI 代码页（cp936），Python 直接
> 往 stdout 写就会把中文工具描述变成乱码；服务端代码里已经强制把流切到 UTF-8，
> 这两行是第二道保险。Trae 那边同理。

文件位置：`C:\Users\<你>\.dsh\profiles\<profile>\cordis.patch.yml`
（桌面 GUI 用的通常是 `desktop`）。该文件受 `dsh-sync-plugin` 版本管理，
改坏了可以直接 `git -C %USERPROFILE%\.dsh checkout -- profiles/desktop/cordis.patch.yml` 回滚。

改完 DSH 会**热加载**，无需重启：工具随即以 `mcp__notionsync__*` 出现，
与原有 `mcp__notion__*` 并存。

> 凭据文件是**每次调用时重试读取**的，所以「先接线、后填 Token」也没问题——
> 填好 `~/.notionsync/workspaces.json` 后不需要为了生效去重启 DSH 或 Trae。
> （注意：只有**配置值**变化才会触发 DSH 重载该条目，改注释不会。）

> 原 `dsh-notion-mcp`（OAuth）只覆盖它授权的那一个空间。两个空间都有 Token 后，
> 它可以保留也可以停用——停用只需从 profile 的 `bundles` 里移除 `dsh-notion-mcp`。

## Trae 接入

编辑 `%APPDATA%\Trae\User\mcp.json`，在 `mcpServers` 下加入：

```json
"notionsync": {
  "command": "D:\\Applications\\Python\\Python 3.14\\python.exe",
  "args": [
    "D:\\Repositories\\Manager\\AtriumPyTools\\tools\\notionsync\\mcp_server.py"
  ],
  "env": {
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8"
  }
}
```

原有的 `"Notion"`（`npx notion-mcp-server`，`NOTION_TOKEN` 为空）只支持单个 Token，
建议删掉或保留不用。Trae Work 里另有官方 Notion 插件（OAuth，同样单空间）。

## AtriumNote 同步（atriumsync）

把 Notion（**唯一真源**）落成本地内容仓库，再按 Notion 布局同步到飞书。

```powershell
cd D:\Repositories\Manager\AtriumPyTools\tools\notionsync
python atriumctl.py pull              # Notion → D:\Repositories\Manager\AtriumNote
python atriumctl.py push --dry-run    # 本地 → 飞书（先看计划）
python atriumctl.py push              # 实际推送
python atriumctl.py status            # 三个目录现状
```

目标结构：

```
AtriumNote\
├── notion_home_pages\   B 空间 1 级页面 → <标题>.md   （Shirley's Knowledge Repo → ShirleysKnowledgeRepo.md）
├── technology\          technology_gallery.csv + pages\<名称>.md（123 行）
└── ai_work_space\       每日资讯整理要求.md、ai_daily_tasks.md、daily_news.csv、daily_news\
    （每个目录下另有 _formats\ 存放格式 sidecar）
```

### 格式 sidecar：Markdown 表达不了的东西

每个 md 旁边一个 `<名字>.formats.json`，只记录 **Markdown 承载不了**的格式，避免膨胀：

| 记录项 | 例子 |
| :-- | :-- |
| 富文本**颜色** | `annotations.color = red / yellow_background` |
| 数据库属性类型与**选项颜色** | `Topic: {value: "色彩与编解码 12:00", color: "green"}` |
| 高亮块 **emoji + 底色** | `callout.icon` / `callout.color` |
| 分栏 **width_ratio**、表格 **表头标志** | `has_column_header`、`table_width` |
| 页面 **icon / cover** | |
| **本转换器不支持的块** | 带 `unsupported_block: true` + 原始载荷（绝不静默丢） |

飞书侧另有 `<名字>.feishu.xml`（**实际发出的 DocxXML**）+ sidecar 的 `feishu` 段
（revision、block id、降级说明）——这就是「之后优化格式排布」的对照依据。

### 两个已修的坑（都有回归测试）

1. **`<li>` 也是块**。飞书把连续列表项表达成 `<ul><li id="…">`，`<ul>` 只是分组、没有 id。
   只按「顶层元素」取 id 会漏掉列表项，`block_replace` 随即报
   `Invalid block range: an intermediate sibling has no block ID`。
2. **区间替换有同级限制**。`block_replace --start --end` 要求首尾是**同一父容器下的直接兄弟块**，
   而列表项与顶层 `<h1>` 不同级。现在改为「首块整体替换 + 逐个删除旧块」，不依赖区间；
   并且**推送前自动备份**飞书原文到 `_formats/_backups/`。

> 重跑 `pull` 是**幂等**的：按 sidecar 里的 `source.pageId` 复用原文件名，
> 只有不同页面撞同名才追加 `-2`、`-3`。

## 飞书云文档（larksync）

飞书没有 Notion 那种「拿个 Token 直接打 REST」的轻量路径。本目录复用的是一条**更省事**的：
本机 Trae 已自带**飞书官方 `lark-cli`**，它用 Device Flow 完成个人授权，内置 `wiki` /
`docs` / `drive` / `markdown` 等域，**不需要你自建应用、不需要 app_secret**。

### 一次性认证（两步，都要浏览器确认）

```powershell
lark-cli config init --new     # 第 1 步：配置应用，会输出授权链接
lark-cli auth login            # 第 2 步：以你本人身份授权
```

或者用本目录封装好的两步命令（会顺带生成二维码、并把「哪些 scope 没授到」明确列出来）：

```powershell
python larkctl.py --profile doubao login --qrcode temp/qr.png   # 第 1 步：拿链接
python larkctl.py --profile doubao login --complete             # 第 2 步：确认后收尾
```

> 只申请真正用得到的 5 个 scope。**飞书的授权是整批成败**：一次要 50 个 scope 时，
> 只要有一个给不了，结果就是一个都不给。所以 `--domain docs,wiki,drive` 这种大范围
> 请求反而更容易全空。

> 访问**个人知识库必须用 `--as user`**（默认已是）。`bot` 身份看到的是应用自己的空间。

### 用法

```powershell
cd D:\Repositories\Manager\AtriumPyTools\tools\notionsync
python larkctl.py status                     # 就绪度：CLI / 应用配置 / 身份授权
python larkctl.py node <wiki链接>             # 解析链接 → node_token/obj_token/标题
python larkctl.py outline <链接>              # 只看目录
python larkctl.py fetch <链接> --out doc.xml  # 抓正文（默认 XML，也可 --format markdown）
python larkctl.py create --file body.xml     # 新建文档
python larkctl.py append <链接> --file body.xml
python larkctl.py rm <doc_id> --type docx --yes
```

Python 里直接用 `larksync.LarkClient`：

```python
import sys
sys.path.insert(0, r"D:\Repositories\Manager\AtriumPyTools\tools\notionsync")
from larksync import LarkClient

client = LarkClient(identity="user")
content = client.docs_fetch("<链接>")["data"]["document"]["content"]   # DocxXML
client.docs_update("<链接>", "append", "<p>追加内容</p>")
```

### 多个飞书账号/租户：用 profile 隔离

飞书**自建应用只服务它所在的那个租户**，而且有「**可用范围**」（只有范围内的成员能授权它，
改范围还需**发布版本**才生效）。所以如果你有两个账号分别在不同租户——比如一个飞书账号、
一个豆包账号（豆包账号确实是飞书账号，但属于**另一个租户**）——**不能共用一个应用**，
必须按租户各建一个：

```powershell
lark-cli config init --new --name doubao    # 追加一个 profile（不动现有的）
python larkctl.py --profile doubao login    # 用该租户的账号授权（两步见下）
```

> **本机实测结论**：豆包账号（`买冰粉的模特`）走通了。它对应的 profile 是 `doubao`
> （应用 `cli_aa4aa3b1ccf8c1ae`），日常飞书操作都用它。

**授权经验（省你时间）**：

- **飞书的授权是整批成败**。一次 `--domain docs,wiki,drive` 展开成约 50 个 scope，只要
  有一个应用给不了，结果就是**一个都不给**。用 `--scope` 精确申请即可。
- **权限会按需增长**。解析知识库链接报 `missing_scope: wiki:node:retrieve`，补上后
  又会缺 `wiki:node:read` 等。本目录默认的 scope 集已经把这些都含进去了。
- 用 `larkctl.py login --complete` 收尾时，它会**明确列出哪些 scope 没授到**并附后台链接。

本目录的整套工具都认 profile：

```powershell
python larkctl.py --profile doubao status --url "<链接>"
python tests\test_feishu.py --profile doubao
python tests\run_all.py --profile doubao
```

或用环境变量一次生效整条链路（含所有子进程）：

```powershell
$env:LARK_PROFILE = "doubao"
```

`python larkctl.py status --url ...` 会明确告诉你是哪一类问题，并给出开发者后台直达链接：

| step | 含义 | 正确动作 |
| :-- | :-- | :-- |
| `config` | 还没建应用 | `config init` |
| `auth` | 身份未授权（含**租户/可用范围**不匹配） | 用该租户的账号 `auth login` |
| `scope` | 应用没开这个权限 | 后台勾选 scope + **发布版本** |
| `permission` | 资源没共享给该身份 | 让所有者共享（换身份/重授权都没用） |

### 排障：分不清「没共享」还是「没申请 scope」？

这两个错误的处理方式完全相反，所以本工具把它分开：

```powershell
python larkctl.py status --url "<文档链接>"
```

按「应用是否配置 → **user 身份**是否授权 → 节点是否可解析」逐级检查，给出具体下一步。
两个容易误判的点：

- **`whoami` 成功 ≠ 可用**：`whoami` 是 `auto` 身份，往往返回 `bot`。bot 就绪完全不代表
  能读你的个人知识库。本工具以 `auth status` 里 **user** 的状态为准。
- **`permission_denied (131006) = 资源没共享**，不是 scope 没申请。此时**换身份、
  重新授权、重试都没用**，必须让资源所有者把该节点/文档共享给这个身份。

如果 `user` 身份已授权但仍报 131006，两条出路：

1. **共享给应用**：飞书里把该知识库节点（知识库 → 空间成员 → 添加应用）或文档
   （协作 → 添加应用）共享给 `cli_aa4aa79611f89bdd`，然后 `--as bot` 调用。
2. **换通路**：用 `drive +export` 导出为本地文件，走「只读 + 本地改写」方案。

### 保真度与无损写回

```powershell
python tests\test_feishu.py
python tests\test_feishu.py --url "<你的链接>" --keep
```

流程与 Notion 那套一致：解析链接 → 读目标文档 → 新建草稿写入覆盖全部构造的 XML →
读回逐项断言（**多列 `<grid><column>`、表格 `<table><thead>`、高亮块、代码、待办、
引用、`<hr>`、`<latex>`**）→ **把读回的 XML 原样再写一篇，再读回逐字符比对** → 回收草稿。

> 飞书的原生格式是 **DocxXML 而不是 Markdown**（`markdown` 域只管云盘里的 `.md` 文件）。
> 所以保真基线是「XML 进、XML 出」，比 Markdown 更直接。

## 两个容易踩的坑

**1. 数据库的标题属性未必叫 `title`。** 中文库通常叫「名称」（你的 Technology Gallery
就是）。写库行时必须用真实属性名，否则 API 直接拒绝。本层会自动读取 data source 的
schema 取真实名字；需要覆盖时用 `--title-property` / MCP 的 `title_property_name`。

**2. database ID ≠ data source ID。** `2025-09-03` 起 database 只是容器。本层在
`query` / `db` / 建行时都会自动把 database ID 解析成它下面的 data source；一个
database 挂了多个 data source 时会被逐个查询后合并。

## 安全约定

* Token 只存在于 `~/.notionsync/workspaces.json`（或环境变量），**不写入本仓库**。
* 也支持 `NOTIONSYNC_TOKEN_<KEY>`（如 `NOTIONSYNC_TOKEN_A`）覆盖配置文件里的值，
  以及 `"token": "env:VAR_NAME"` 的间接引用。
* 所有错误信息与 `--json` 输出都经过脱敏，不会回显 Token。
* `temp/` 已在仓库根 `.gitignore` 中忽略，测试产物一律落在这里。
* `NOTIONSYNC_API_BASE` 可覆盖 API 基址，**仅供测试**指向本地 mock；日常使用不要设置它。

## 目录结构

```
tools/notionsync/
├── notionctl.py            CLI 启动器
├── mcp_server.py           stdio MCP 服务器启动器（DSH / Trae 指向它）
├── setup_tokens.py         交互式写入凭据
├── notionsync/
│   ├── config.py           多空间凭据发现与解析
│   ├── client.py           Notion REST 客户端（纯标准库）
│   ├── markdown.py         blocks ⇄ Markdown
│   ├── service.py          CLI / MCP 共用的业务层
│   ├── mcp.py              MCP 工具定义与 JSON-RPC 循环
│   └── cli.py              命令行
├── tests/
│   ├── run_all.py          总入口：一次跑完全部套件
│   ├── test_unit.py        离线单测（45 项，含转换器往返一致性）
│   ├── test_mcp_stdio.py   进程级 MCP 握手自检（16 项）
│   ├── test_e2e_mock.py    对本地 mock Notion API 的完整读写（28 项）
│   ├── test_client_configs.py  按 Trae / DSH 已落盘配置拉起并握手（17 项）
│   └── test_live.py        实链路读写自检（需真实 Token）
├── temp/                   ← gitignore：测试产物、探针配置
├── verify_link.py          早期链路验证脚本（MCP-over-HTTP，历史遗留）
└── round2_migrate.py       早期批量改写脚本（历史遗留）
```

## 已支持的 Markdown 子集

**读**：段落、一~三级标题、无序/有序列表、待办、折叠块、引用、callout、代码块、
分割线、图片/视频/文件/PDF/书签、子页面、表格、分栏（拍平）、同步块、公式。

**写**：`#/##/###`、`-`/`*`/`+`、`1.`、`- [ ]`/`- [x]`、`>`、``` 围栏代码、
`---`、普通段落，以及两空格缩进的嵌套列表。不认识的语法按段落原样保留，不静默丢弃。
