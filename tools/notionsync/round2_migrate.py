"""notionsync / round2_migrate.py — 第二轮迁移。

任务 1（--pages）：改写 Technology Gallery 每个子页的「现状」，删除优先级复述。
  做法：读取原页面正文 → 只替换 `## 现状` 段 → 写回。关注点/更新记录原样保留。
任务 2（--spec）：改写《每日资讯 整理要求》，把"推送内容清单"换成"执行指引"。
  做法：定位 `## 三、推送内容` 到 `## 四、过滤规则` 之间，整段替换；其余章节不动。

用法：
  python round2_migrate.py --pages --dry-run      # 只打印将要写入的内容
  python round2_migrate.py --pages                # 实际写入
  python round2_migrate.py --spec  --dry-run
  python round2_migrate.py --spec

安全：写入前把原文+新文备份到同目录 _backup_round2.json。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

from verify_link import NotionMCP, load_token

DS = "collection://3ede47bb-dac5-8036-a5d7-000ba0673afb"
SPEC_PAGE = "https://app.notion.com/p/3ede47bbdac58013a76cc4f66887500c"

# 管理方案页正文是结构文档，不是标准子页骨架，跳过
SKIP_IDS = {"3ede47bbdac580088ad9c86d702f43e1"}

BACKUP_DIR = pathlib.Path(__file__).with_name("_backup")
# 两个任务各写各的，绝不共用文件名（曾因此覆盖掉 122 页的备份）
BACKUP_PAGES = BACKUP_DIR / "round2_pages.json"
BACKUP_SPEC = BACKUP_DIR / "round2_spec.json"

# ---------------------------------------------------------------------------
# 新的「现状」文案（全部不含优先级）
# ---------------------------------------------------------------------------
STATUS: dict[str, str] = {
    # Overall
    "Efficiency": "办公与知识工具的主战场：Notion 用于采集与沉淀，TeamTalk + 石墨文档用于日常协作。",
    "AI": "推送主题一（AI 与工具发展，08:00）的总入口。",
    "Technology": "推送主题二（12:00）与主题三（16:00）的总入口。",
    # Efficiency
    "Notion": "采集与沉淀的主力工具；长期方向是与本地 md（AtriumNote）打通。",
    "飞书生态": "不常用；办公主力为 TeamTalk + 石墨文档。",
    # AI 模型/产品
    "DeepSeek": "工作主力模型，日常与生产环境的默认选择。",
    "OpenAI": "行业前沿，能力与生态的基准线。",
    "Google Gemini": "行业前沿，多模态与生成能力的主要竞争者。",
    "Anthropic Claude": "行业前沿，编码 Agent 与 MCP 生态的定义者之一。",
    "Midjourney": "图像生成领域的头部产品，用户指定关注。",
    "Kimi": "国产长上下文方向的代表，常用备选。",
    "Qwen": "国产开源权重的主力；与通义为同一主体。",
    "MiniMax": "国产多模态（语音 / 视频）方向的重要玩家。",
    "Meta & Llama": "开源权重生态的源头；Meta 与 Llama 为同一主体。",
    "Doubao": "字节系主力模型，端侧与云侧并行。",
    "Grok/xAI": "xAI 的旗舰模型；属行业前沿，但当前关注度一般。",
    "GLM/智谱": "国产开源与 Agent 产品并行的代表。",
    "Mistral": "欧洲开源模型的代表，不常用但优秀。",
    "Cohere": "企业级 RAG 与检索方向。",
    "Perplexity": "搜索增强方向的产品代表。",
    "阶跃星辰": "多模态方向的潜力模型。",
    "零一万物": "国产模型，处于业务转型期。",
    "百川": "国产模型，偏行业落地。",
    "文心": "百度系主力模型。",
    "混元": "腾讯系主力模型，3D / 视频生成有特色。",
    "星火": "科大讯飞系，语音能力见长。",
    "盘古": "华为云行业大模型。",
    "Runway": "生成式视频方向的代表产品。",
    "Suno": "生成式音乐方向的代表产品。",
    "Luma": "生成式视频与 3D 方向。",
    "Flux": "开源图像生成模型的代表。",
    # AI Agent/平台
    "Qoder": "日常办公主要平台之一。",
    "Trae Code / Work": "个人主要使用；同时是每日资讯的执行载体。",
    "Cursor": "原办公主力 IDE。",
    "Claude Code": "行业前沿的编码 Agent CLI。",
    "Codex": "行业前沿的编码 Agent。",
    "OpenCode": "开源编码 Agent CLI。",
    "GitHub Copilot": "老牌编码助手，当前不常用。",
    "Windsurf": "编码 Agent IDE，Cascade 为其核心。",
    "Cline": "开源 VS Code Agent。",
    "Roo Code": "Cline 分支，模式化能力更强。",
    "Continue": "开源 IDE 助手，可接本地模型。",
    "Aider": "开源 CLI Agent，repo map 机制有特色。",
    "Devin": "自主编码 Agent 的早期代表。",
    "Replit": "云端 IDE 与 Agent。",
    "Lovable": "全栈生成平台。",
    "v0": "Vercel 系前端生成。",
    "Bolt.new": "浏览器内全栈生成。",
    "Amazon Q Developer": "AWS 生态编码助手。",
    "JetBrains AI": "JetBrains 系助手（Junie）。",
    # 色彩管理
    "ICC": "色彩管理的基础规范组织，独立发布规范版本。",
    "ISO TC 130": "图形技术领域的国际标准委员会。",
    "Apple Developer Color": "苹果色彩管线（ColorSync / EDR）的权威文档来源。",
    "Fogra": "印刷标准化与色彩表征的研究机构。",
    "PDF Association": "PDF 规范组织，涉及输出意图与色彩条款。",
    "X-Rite": "分光 / 色度测量硬件与软件的主要厂商。",
    "Datacolor": "Spyder 系列校准硬件厂商。",
    "CalMAN": "专业显示校准软件。",
    "Portrait Displays": "CalMAN 母公司，显示校准软硬件产品线。",
    "sRGB": "最通用的标准色域，规范已冻结。",
    "Display P3": "苹果生态的广色域标准。",
    "Adobe RGB": "印刷与摄影侧的广色域工作空间。",
    "BT.2020": "UHDTV 广色域标准。",
    "DCI-P3": "数字电影色域标准。",
    "HDR": "HDR 传输函数与元数据生态的总称。",
    "杜比视界": "动态元数据 HDR 的商业实现。",
    "HLG": "广播侧 HDR 传输函数，兼容 SDR。",
    "PQ": "HDR 感知量化曲线（ST 2084）。",
    "ACES": "学院色彩编码体系，覆盖采集到交付全流程。",
    "色彩配置文件": "ICC 特性文件格式与解释规则。",
    "软打样": "在显示器上模拟印刷效果的工作流。",
    "显示器校准": "保证显示一致性的基础环节。",
    "AI 超分": "成像 / 后期的分辨率增强，区别于渲染侧的实时重建。",
    "AI 降噪": "成像 / 后期的噪点去除。",
    "AI 调色": "自动调色、色彩匹配与 LUT 生成。",
    # 编解码
    "AV1": "免专利费的开放编码标准，已被广泛采纳。",
    "AV2": "AOMedia 的下一代编码标准，正在推进。",
    "H.266/VVC": "MPEG 系最新编码标准。",
    "HEVC": "当前主流的广播与平台编码标准，专利池复杂。",
    "H.264": "兼容性最好的老标准，已稳定。",
    "VP9": "Web 侧开放编码标准，正被 AV1 取代。",
    "ProRes": "Apple 的后期中间格式。",
    "DNxHR": "Avid 的后期中间格式。",
    "RAW": "相机与摄影机原始格式族，含开放与私有。",
    "FFmpeg": "核心开源工具链。",
    "HandBrake": "开源转码前端。",
    "DaVinci Resolve": "后期调色与剪辑主力软件。",
    "Premiere": "Adobe 系剪辑软件。",
    "OBS": "开源推流与录制软件。",
    "NVIDIA Video Codec SDK": "GPU 硬件编解码（NVENC / NVDEC）的官方 SDK。",
    "OpenAPV": "面向专业视频的开源编码标准。",
    # 渲染
    "实时渲染": "按时间预算划分的渲染领域，与算法轴正交。",
    "路径追踪": "光线追踪家族中的蒙特卡洛算法，无偏且物理正确。",
    "光线追踪": "光传输求解的方法族，路径追踪为其子集。",
    "DLSS": "NVIDIA 的实时超分 / 帧生成 / 光线重建技术。",
    "FSR": "AMD 的跨厂商超分与帧生成。",
    "XeSS": "Intel 的超分方案。",
    "Gaussian Splatting": "基于可微光栅化的辐射场表示，并非神经网络方法。",
    "NeRF": "神经辐射场，隐式表示与视图合成。",
    "神经渲染": "涵盖 NeRF 等方法的上位概念。",
    "GPU 渲染": "GPU 上的光栅化与计算着色，实时渲染的执行基础。",
    "Shader": "着色器语言与编译链。",
    "Vulkan": "Khronos 的显式跨平台图形 API。",
    "WebGPU": "W3C 的浏览器图形与计算 API，WebGL 的替代方案。",
    "DirectX": "微软图形 API，D3D12 为当前主力。",
    "Metal": "Apple 平台图形 API。",
    "WebGL": "基于 OpenGL ES 的浏览器图形 API，已进入维护模式。",
    # 开发
    "Unreal Engine 4/5/6": "商业引擎主力，Nanite / Lumen 为渲染标杆。",
    "Unity": "跨平台引擎，URP / HDRP 双管线。",
    "Godot": "开源引擎。",
    "O3DE": "Amazon 发起的开源引擎。",
    "Blender": "开源 DCC，Cycles / EEVEE 双渲染器。",
    "Maya": "Autodesk 主力 DCC，影视管线核心。",
    "3ds Max": "Autodesk DCC，建筑与游戏资产方向。",
    "Cinema 4D": "Maxon 动效与 MoGraph 方向。",
    "Houdini": "SideFX 程序化 VFX 标准工具，Karma / Solaris 已接 USD。",
    "ZBrush": "数字雕刻标准工具。",
    "Substance 3D": "Adobe 材质制作套件。",
    "MetaHuman": "Epic 的数字人方案。",
    "USD": "场景描述与互操作标准（OpenUSD）。",
    "OpenColorIO": "色彩管理配置框架，ACES 生态的落地层。",
    "AI 绑定": "自动蒙皮与骨骼生成的 AI 方法。",
}

NEW_SECTION_3 = """## 三、推送内容从何而来

**本页不再维护推送内容清单。** 所有跟踪对象及其检索规格统一存放在 **Technology Gallery** 数据库；本页只规定「去哪里取、怎么排、怎么滤、怎么发」。

### 3.1 去哪里取

数据源：**Technology Gallery**（数据库，位于 AI Work Dashboard 下）。

用结构化查询获取，**不要解析本页或页面正文**。按 `Topic` 属性分组即为每封邮件的内容范围：

| Topic 取值 | 对应邮件 | 推送时间 |
| --- | --- | --- |
| `AI与工具 08:00` | 生产效率：AI 与工具发展 | 每日 08:00 |
| `色彩与编解码 12:00` | 专业领域：色彩管理与媒体编解码 | 每日 12:00 |
| `渲染与开发 16:00` | 专业领域：渲染与开发 | 每日 16:00 |
| `不参与` | 总览页，不进入推送 | — |

参考查询：

```sql
SELECT "名称", "Category", "Kind", "Priority", "Sources", "Query Keys"
FROM "collection://3ede47bb-dac5-8036-a5d7-000ba0673afb"
WHERE "Topic" = 'AI与工具 08:00'
ORDER BY "Priority";
```

### 3.2 每个属性怎么用

| 属性 | 用途 |
| --- | --- |
| `名称` | 检索主体；同时是去重与归档的键 |
| `Category` | 归入邮件的哪个栏目（模型与产品 / Agent 与平台 / 色彩管理 / 编解码 / 渲染 / 开发 / 工具更新） |
| `Kind` | 事物的性质，仅用于理解与分组，**不影响检索** |
| `Priority` | `P0` 进「今日必看」；`P00` 仅总览；`P1`–`P3` 进分类栏目 |
| `Sources` | **一手来源**，优先查这里列出的官方渠道 |
| `Query Keys` | 检索关键词与别名（含旧名 / 中文名 / 版本号），作为检索基线 |

### 3.3 检索词怎么扩

`Query Keys` 是**基线**而非穷举。允许扩展，但边界如下：

| 允许自行发挥 | 必须由数据库固定 |
| --- | --- |
| 同义词、别名、产品名、版本号扩展 | **收录哪些主体**（清单完整性） |
| 按当日新闻动态调整检索词组合 | **每个主体的一手来源** |
| 排序、去重、摘要、中英翻译 | **优先级**（决定 P0 置顶） |

**理由**：若主体清单也由模型临时生成，就无法区分「今天没有新闻」与「模型漏掉了这个源」。而过滤规则第 6 条要求无更新时照常发送，二者在输出上无法分辨，会造成**沉默的漏报**。因此主体清单必须可审计。

### 3.4 优先级怎么判

| 判定 | 规则 |
| --- | --- |
| 升为 `P0` | 属于「常用工具 / 模型」或「行业前沿」，且必须来自官方或两个独立来源 |
| 降级 | 规范已冻结、产品进入维护模式，或用户明确不再常用 |
| 合并 | 同一主体只允许一行；别名写入 `Query Keys`，不新建行 |
| 新增 | 先入数据库（补齐 `Sources` 与 `Query Keys`），再纳入检索范围 |

"""


# ---------------------------------------------------------------------------
def split_sections(text: str):
    marks = [(m.start(), m.group(1)) for m in re.finditer(r"(?m)^##[ \t]+(.+?)[ \t]*$", text)]
    out: dict[str, str] = {}
    order: list[str] = []
    for i, (pos, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        out[name] = text[pos:end].rstrip("\n")
        order.append(name)
    return out, order


def extract_content(page_text: str) -> str | None:
    m = re.search(r"<content>\n?(.*?)\n?</content>", page_text, re.S)
    return m.group(1) if m else None


def page_id_of(url: str) -> str:
    return re.findall(r"[0-9a-fA-F]{32}", url.replace("-", ""))[0]


def list_pages(mcp: NotionMCP) -> list[dict]:
    """SQL 结果被截断在 100 行，按 Field 分批查以取全量。"""
    rows: list[dict] = []
    for field in ("Management", "AI", "Efficiency", "Technology"):
        text, _ = mcp.call("notion-query-data-sources", {
            "data": {
                "mode": "sql",
                "data_source_urls": [DS],
                "query": f'SELECT url, "名称" FROM "{DS}" WHERE "Field" = ?',
                "params": [field],
            }
        })
        batch = json.loads(text)["results"]
        print(f"  Field={field}: {len(batch)} 行")
        rows.extend(batch)
        time.sleep(0.3)
    return rows


def do_pages(mcp: NotionMCP, dry_run: bool) -> None:
    rows = list_pages(mcp)
    print(f"数据库共 {len(rows)} 行")
    backup = []
    planned, skipped, failed = [], [], []

    for row in rows:
        name = row.get("名称") or ""
        url = row.get("url") or ""
        if not url:
            failed.append((name, "no url")); continue
        pid = page_id_of(url)
        if pid in SKIP_IDS:
            skipped.append((name, "skip 管理方案")); continue
        if name not in STATUS:
            skipped.append((name, "无现状文案")); continue

        text, _ = mcp.call("notion-fetch", {"id": url})
        content = extract_content(text)
        if content is None:
            skipped.append((name, "无法解析 <content>")); continue
        secs, order = split_sections(content)
        if "现状" not in secs:
            skipped.append((name, "无 ## 现状 段")); continue

        new_parts = []
        for s in order:
            new_parts.append(f"## 现状\n{STATUS[name]}" if s == "现状" else secs[s])
        new_content = "\n\n".join(new_parts) + "\n"

        backup.append({"page_id": pid, "name": name, "old": content, "new": new_content})
        planned.append((name, pid, new_content))
        time.sleep(0.3)

    print(f"计划改写 {len(planned)}，跳过 {len(skipped)}，失败 {len(failed)}")
    for n, why in skipped:
        print(f"  SKIP {n}: {why}")
    for n, why in failed:
        print(f"  FAIL {n}: {why}")

    print("\n=== 样例（前 3 条新正文）===")
    for name, pid, nc in planned[:3]:
        print(f"--- {name} ---\n{nc}")

    if dry_run:
        print("\n[dry-run] 未写入。")
        return

    BACKUP_DIR.mkdir(exist_ok=True)
    BACKUP_PAGES.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已备份 {len(backup)} 条到 {BACKUP_PAGES.name}")

    ok = 0
    for i, (name, pid, nc) in enumerate(planned, 1):
        res, _ = mcp.call("notion-update-page", {
            "page_id": pid, "command": "replace_content", "new_str": nc,
        })
        if "error" in res.lower()[:200]:
            print(f"  FAIL {name}: {res[:160]}")
        else:
            ok += 1
        if i % 20 == 0:
            print(f"  ... {i}/{len(planned)}")
        time.sleep(0.4)
    print(f"改写完成：{ok}/{len(planned)}")


def do_spec(mcp: NotionMCP, dry_run: bool) -> None:
    text, _ = mcp.call("notion-fetch", {"id": SPEC_PAGE})
    content = extract_content(text)
    if content is None:
        print("无法解析页面 <content>"); return

    start = content.find("## 三、推送内容")
    end = content.find("## 四、过滤规则")
    if start < 0 or end < 0 or end <= start:
        print(f"定位失败 start={start} end={end}"); return

    old_block = content[start:end]
    new_content = content[:start] + NEW_SECTION_3 + content[end:]
    print(f"原第三章长度 {len(old_block)} 字符 → 新 {len(NEW_SECTION_3)} 字符")
    print(f"整页 {len(content)} → {len(new_content)} 字符")

    if dry_run:
        print("\n=== 新的第三章 ===")
        print(NEW_SECTION_3)
        print("\n[dry-run] 未写入。")
        return

    BACKUP_DIR.mkdir(exist_ok=True)
    BACKUP_SPEC.write_text(json.dumps(
        [{"page_id": page_id_of(SPEC_PAGE), "name": "每日资讯 整理要求",
          "old": content, "new": new_content}], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已备份到 {BACKUP_SPEC.name}")
    res, _ = mcp.call("notion-update-page", {
        "page_id": page_id_of(SPEC_PAGE), "command": "replace_content", "new_str": new_content,
    })
    print("写入结果:", res[:200])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", action="store_true", help="任务1：改写子页现状")
    ap.add_argument("--spec", action="store_true", help="任务2：改写整理要求第三章")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not (args.pages or args.spec):
        ap.error("至少指定 --pages 或 --spec")

    mcp = NotionMCP(load_token())
    if args.pages:
        do_pages(mcp, args.dry_run)
    if args.spec:
        do_spec(mcp, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
