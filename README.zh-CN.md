# zotero-daily-papers

中文 | [English](README.md)

让 AI Agent（如 Claude Code、Codex）每天帮你从 arXiv 挑选论文、精读，并把结果整理进本机 Zotero：
每日推荐总览、论文条目与 PDF、点评笔记、必读论文的精读笔记，全部保存在 Zotero 里。

只需在 Agent 对话中说一句「今日论文推荐」，剩下的步骤自动完成。

## 功能

- **每日推荐**：抓取 arXiv 新论文和 Hugging Face 热门论文 → 按你的研究方向筛选评分 → 与 Zotero 已有条目查重 → 逐篇锐评。
- **自动入库**：先保存当天的推荐总览，再把入选论文存入 Zotero（含 PDF 和点评子笔记），最后回填总览中的条目链接。
- **精读笔记**：对必读论文读取全文，生成包含方法、公式、主结果表和关键图的精读笔记，保存为条目子笔记。
- **单独使用**：也可以只保存某篇论文，或只精读某篇论文。
- **安全可重跑**：重复运行会先查重，复用已有条目和笔记，不会重复创建；保留你手动整理的分类和附件。
- **本地优先**：通过 Zotero 10 的本机 API 写入，不需要云端 API key，不直接读写 `zotero.sqlite`。
- **兼容 Attanger**：与 Attanger 插件配合，自动核对 PDF 是否已被移动到你的文件目录。

## 工作流程

1. **抓取**：arXiv 最新一个公告批次，以及 Hugging Face Daily / Trending。
2. **筛选评分**：先按关键词初筛，再由 Agent 读摘要、按研究方向打分。
3. **查重**：对照 Zotero 中已有的条目。
4. **锐评**：Agent 逐篇点评，分为必读、值得看、可跳过。
5. **保存每日总览**：写入 `Daily Papers` 分类。
6. **论文入库**：存入「待定」或匹配的已有分类。
7. **精读**：为必读论文生成精读笔记，保存为条目子笔记。

评分、锐评和精读都由当前对话中的 Agent 完成，脚本不会额外调用任何付费模型 API。

## 环境要求

| 依赖 | 说明 |
|---|---|
| Python 3.10+ | 仅用标准库，无需 `pip install` |
| `curl` | 富化脚本获取 arXiv HTML、元数据页及 PDF 所需 |
| Zotero 10+ | 需要开启本机 API（见下文） |
| Claude Code 或 Codex | 或其他能运行本机命令、支持 Skill 的 AI Agent |
| [Attanger](https://github.com/MuiseDestiny/zotero-attanger)（可选） | 自动重命名并移动 PDF |
| `pdftotext`（可选） | 从 PDF 首页提取作者机构，macOS 可通过 `brew install poppler` 安装 |

## 快速开始

### 1. 下载项目

```bash
git clone https://github.com/qingtongqt/zotero-daily-papers.git
cd zotero-daily-papers
```

项目目录需要长期保留：Skill 安装后通过链接指向这里，共享脚本也从这里运行。

### 2. 开启 Zotero 本机 API

打开 Zotero → **设置 → 高级**，勾选「允许此计算机上的其他应用程序与 Zotero 通信」。

然后检查连接并申请写入授权：

```bash
python3 skills/_zotero_shared/zotero_push.py check       # 检查能否连上本机 Zotero
python3 skills/_zotero_shared/zotero_push.py authorize   # Zotero 会弹出授权窗口
```

在弹窗中选择 **「始终允许」**，之后运行就不会再弹窗。

### 3. 安装 Skill

安装器把三个 Skill 和共享目录以符号链接的形式放进 Agent 的技能目录，不复制代码，重复运行是安全的。

用 `--lang` 选择语言：`en`（英文，默认）或 `zh`（中文）。语言决定 Skill 说明、Agent 写的点评/笔记、
笔记标题标识（`【精读】` 或 `[Deep Read]`）和默认的「待定」分类名（`待定` 或 `Pending`），并写入个人配置。

**Claude Code**

```bash
python3 install.py --target ~/.claude/skills --lang zh    # 中文
python3 install.py --target ~/.claude/skills              # English（默认）
```

安装后在当前会话中即可使用。可以直接用自然语言触发，也可以输入 `/zotero-daily-papers` 手动调用。

**Codex**

```bash
python3 install.py --target ~/.agents/skills --lang zh    # 不加 --lang 则安装英文版
```

安装后如果 Skill 没有出现，重启 Codex。可以直接用自然语言触发，也可以输入 `$zotero-daily-papers`
或通过 `/skills` 选择调用。较旧版本的 Codex 使用 `~/.codex/skills` 目录。

> 两个都用的话，两条命令各运行一次即可。它们链接到同一个项目目录，更新代码只需改一处。
> 如需复制安装，把 `skills/`（英文版为 `skills-en/`，需 `cp -rL` 展开其中的链接）下的三个 Skill 和 `_zotero_shared/` 一起复制到宿主技能目录，保持四个目录同级；英文版再运行 `python3 skills/_zotero_shared/zdp_config.py --set-language en`。
>
> 切换已安装的语言：`python3 install.py --target <目录> --lang zh --update`。
>
> 从旧版本升级：配置默认语言现为英文，中文用户请运行 `python3 install.py --target <目录> --lang zh` 或 `python3 skills/_zotero_shared/zdp_config.py --set-language zh`，否则新论文会进入 `Pending` 分类、笔记标题变为英文。

如果你的 Agent 不支持 Skill 目录，也可以直接在对话里让它读取 `skills/zotero-daily-papers/SKILL.md`。

### 4. 设置研究方向

默认配置面向三个方向：多模态大模型幻觉、大模型后训练、大模型架构。要换成你自己的方向，先创建个人配置：

```bash
python3 skills/_zotero_shared/zdp_config.py --init
```

然后编辑 `~/.config/zotero-daily-papers/config.json`，见下文[研究方向](#研究方向)。

### 5. 开始使用

在 Agent 对话中直接说：

| 你说 | 执行的操作 |
|---|---|
| 「今日论文推荐」 | 完整流程：抓取、筛选、锐评、入库、精读必读论文 |
| 「推荐过去三天的论文」 | 同上，时间范围为最近 3 天（也支持「过去一周」） |
| 「继续上次的推荐」 | 核对 Zotero 实际状态，只重试未完成的部分 |
| 「把 2311.16922 保存到 Zotero」 | 只保存论文和 PDF，不精读 |
| 「精读一下 2311.16922」/「快速看一下这篇」 | 读论文并保存笔记到对应条目下 |

## Zotero 中的结果

| 内容 | 保存位置 |
|---|---|
| 每日推荐总览 | 顶层分类 `Daily Papers`（不存在时自动创建） |
| 入选论文 | 默认放入顶层分类 `待定`；可改为自动匹配已有分类 |
| PDF | 论文条目的附件；Attanger 是否移动取决于插件设置及事件处理，脚本会检查结果 |
| 每日锐评、精读笔记 | 论文条目的子笔记 |

笔记通过标题中的标识识别类型，如 `【每日推荐】`、`【每日锐评】`、`【精读】`、`【速读】`，每日笔记的标题还带日期。
请不要删除这些标识，否则重跑时无法识别已有笔记。

## 配置

默认配置在 [`config.default.json`](skills/_zotero_shared/config.default.json)，不要直接修改它。
个人设置写在 `~/.config/zotero-daily-papers/config.json`，只需写要覆盖的键：

```json
{
  "zotero": {
    "classification_mode": "pending",
    "attach_pdf": "upload"
  },
  "daily_papers": {
    "top_n": 10
  }
}
```

合并规则：对象逐层合并；列表（如 `directions`）整体替换。
查看实际生效的配置：

```bash
python3 skills/_zotero_shared/zdp_config.py
```

### 研究方向

研究方向决定哪些论文会被选中，也会作为标签写入条目，但**不会**决定论文进入哪个分类。

```json
{
  "daily_papers": {
    "directions": [
      {
        "id": "rag",
        "name": "检索增强生成",
        "keywords": [["retrieval-augmented", "rag"], "dense retrieval", ["reranker", "reranking"]]
      },
      {
        "id": "llm-agent",
        "name": "大模型智能体",
        "keywords": [["llm agent", "llm agents", "language agent"], ["tool use", "tool learning", "function calling"], "agentic search"]
      }
    ],
    "negative_keywords": ["medical imaging", "protein"],
    "quality_boost": {
      "venues": ["NeurIPS", "ICLR", "ACL", "EMNLP"],
      "venue_multiplier": 1.5,
      "accepted_multiplier": 1.2
    },
    "arxiv_categories": ["cs.CL", "cs.IR"],
    "top_n": 10
  }
}
```

- `keywords`：研究方向词。论文至少命中一个方向词或关注词，才进入初筛计分。每一项可以是单个词，也可以是一组同义词（如 `["rlvr", "verifiable reward"]`），同一组最多计一次分：命中标题加 3 分，仅命中摘要加 2 分。各方向分别计分，论文取得分最高的方向，不同方向的分数不累加。之后由 Agent 读摘要评分。
- `focus_keywords`：特别关注的主题词，写法同 `keywords`（可写同义词组）。每组命中一次，总分加 `focus_bonus`（默认 3）；只命中关注词也能进入初筛。
- `quality_boost`：读取 arXiv comment 字段。写明 `venues` 中的会议（如 `Accepted to NeurIPS 2026`）时总分乘 `venue_multiplier`（默认 1.5）；只写 Accepted / To appear 或是会议 workshop 时乘 `accepted_multiplier`（默认 1.2）；含 Under review、Submitted to 等字样不加权。
- `negative_keywords`：标题或摘要命中即直接排除，用于过滤不关心的应用领域。
- `arxiv_categories`：抓取哪些 arXiv 分类。

### 分类模式

论文进入哪个 Zotero 分类有两种模式：

| 模式 | 行为 |
|---|---|
| `pending`（默认） | 新论文统一进入「待定」，由你稍后手动整理 |
| `existing` | Agent 读取你已有的分类结构，按论文内容匹配最合适的分类；找不到合适的才放入「待定」 |

```bash
python3 skills/_zotero_shared/zdp_config.py --set-classification existing
```

两种模式下，已经在某个分类里的论文都会保留原分类，不会被额外加进「待定」。

### 常用配置项

| 键（顶层） | 默认值 | 说明 |
|---|---|---|
| `language` | `en` | `en` 或 `zh`，由 `install.py --lang` 设置，也可用 `zdp_config.py --set-language` 修改 |

| 键（`zotero` 节） | 默认值 | 说明 |
|---|---|---|
| `root_collection` | `Daily Papers` | 每日推荐总览所在的顶层分类 |
| `pending_collection` | `待定`（英文为 `Pending`） | 未分类论文的去处 |
| `classification_mode` | `pending` | `pending` 或 `existing` |
| `attach_pdf` | `upload` | `upload` 下载 PDF；`link` 仅添加链接；`none` 不添加 |
| `attanger_wait_seconds` | `10` | 上传 PDF 后等待 Attanger 处理的秒数，`0` 关闭检查 |
| `write_tiers` | `["must", "worth"]` | 哪些等级的推荐论文会入库 |

| 键（`daily_papers` 节） | 默认值 | 说明 |
|---|---|---|
| `top_n` | `10` | 每天最终入选的论文数上限 |
| `candidate_pool_size` | `30` | 关键词初筛后交给 Agent 打分的候选数 |
| `recall_min_score` | `3` | 关键词初筛的最低分 |
| `ranking.min_score` | `2.0` | Agent 相关性评分（0–4）的入选门槛 |
| `focus_bonus` | `3` | 每命中一组关注词加的分数 |

全部配置项、Web API 模式和排错说明见 [Zotero 配置](skills/_zotero_shared/references/zotero-setup.md)。

## 关于 Attanger

装了 Attanger 时，脚本上传 PDF 后会等待几秒，并在结果中报告 PDF 的状态：

- `moved`：已被 Attanger 移到你的文件目录。
- `not_moved`：仍在 Zotero 存储目录。请在 Zotero 中右键该附件 → Attanger → 重命名并移动。

之后如果在 Zotero 中调整了论文分类，调整完成后再对 PDF 执行一次 Attanger 的「重命名并移动」。

## 命令行参考

日常使用只需和 Agent 对话。以下命令供调试或手动操作：

```bash
# 检查连接、申请授权
python3 skills/_zotero_shared/zotero_push.py check
python3 skills/_zotero_shared/zotero_push.py authorize

# 保存单篇论文（pending 模式）
python3 skills/_zotero_shared/zotero_push.py upsert-paper --arxiv 2311.16922

# 保存到指定分类（先列出分类及其 key）
python3 skills/_zotero_shared/zotero_push.py collections
python3 skills/_zotero_shared/zotero_push.py upsert-paper --arxiv 2311.16922 --collections <KEY> --classification-reason "理由"

# 查找条目、查看附件真实路径和已有笔记
python3 skills/_zotero_shared/zotero_push.py find --arxiv 2311.16922
python3 skills/_zotero_shared/zotero_push.py attachments --arxiv 2311.16922
python3 skills/_zotero_shared/zotero_push.py notes --arxiv 2311.16922

# 只预览，不修改文献库
python3 skills/_zotero_shared/zotero_push.py --dry-run upsert-paper --arxiv 2311.16922
```

## 项目结构

```text
zotero-daily-papers/
├── skills/
│   ├── zotero-daily-papers/           # 总入口、推荐脚本和锐评指南
│   ├── zotero-paper-save/             # 单篇入库与分类
│   ├── zotero-paper-reader/           # 精读流程和笔记模板
│   └── _zotero_shared/                # Zotero 脚本、默认配置和配置说明
├── skills-en/                         # 英文版 Skill 说明（脚本链接到 skills/，共用一份代码）
├── tests/
└── install.py                         # Skill 安装器
```

## 数据与隐私

- 个人配置、本地授权和缓存保存在 `~/.config/zotero-daily-papers/`（授权文件权限为 600）。
- 中间文件写入系统临时目录，可用环境变量 `ZDP_TEMP_DIR` 指定。
- 抓取访问 arXiv 和 Hugging Face Daily/Trending API；富化访问 arXiv 页面与 PDF，本机入库访问 localhost。
- 使用 Web API 模式时还会访问 Zotero 云端及其返回的文件上传地址；该模式嵌入图片时也会访问笔记中的图片 URL。
- Agent 获取论文全文、项目主页或相关资料时，可能另行访问对应站点。
