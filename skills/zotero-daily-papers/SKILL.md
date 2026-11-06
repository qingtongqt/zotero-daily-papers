---
name: zotero-daily-papers
description: 每日论文推荐完整流水线：抓取、筛选、查重、锐评，先保存每日总览到本机 Zotero，再按配置入库论文，最后精读必读论文并保存子笔记。用于今日论文推荐、过去几天论文推荐、继续上次推荐；仅保存或仅阅读指定论文时转交对应 Skill。
---

# Zotero 每日论文总入口

这是完整流水线的编排入口。用户只需说一次“今日论文推荐”。
顺序：**抓取 → 筛选 → 查重 → 锐评 → 保存总览 → 入库论文 → 精读必读论文**。
所有论文、总览和阅读笔记保存到 Zotero；Markdown/JSON 只是临时中间文件。

## 路由与共享资源

以本文件所在目录为 `{SKILL_DIR}`，同级的 `../_zotero_shared` 为 `{SHARED}`。

- 只保存论文、PDF 或管理入库分类：执行 [zotero-paper-save](../zotero-paper-save/SKILL.md)，不自动精读。
- 指定论文精读、快速阅读：执行 [zotero-paper-reader](../zotero-paper-reader/SKILL.md)，不跑每日推荐。
- 每日/过去 N 天推荐：按下文完成全部阶段。

## 1. 配置与检查

运行 `python3 "{SHARED}/zdp_config.py"` 解析 `runtime.temp_dir` 为 `TEMP_DIR`，
运行 `python3 "{SHARED}/zotero_push.py" check`。本机模式需要 Zotero 10+ 打开及本地 API 开启。
授权和排错见 [Zotero 配置](../_zotero_shared/references/zotero-setup.md)。不要打印凭据，不直接操作数据库。
同时跑多条流水线时使用不同 `ZDP_TEMP_DIR`。默认中文，用户明确用英文则全部点评/笔记用英文。

入库设置 `zotero.classification_mode` 仅有两个值：

- `pending`：推荐论文存入根目录「待定」，不存在就创建。无需 Agent 做分类判断。
- `existing`：按保存 Skill 读取现有分类、路径和条目示例，结合标题摘要做语义匹配；无匹配才进入「待定」。

研究方向配置只用于推荐筛选和标签，不决定真实分类。已有条目复用并保留用户原有分类和附件。

## 2. 抓取、筛选、查重、锐评

执行 [每日准备阶段](references/daily-workflow.md)，输出 checked 候选、enriched 元数据和 `zotero_payload.json`。

## 3. 先保存总览，再入库

```bash
python3 "{SHARED}/zotero_push.py" push-daily --payload "{TEMP_DIR}/zotero_payload.json" --enriched "{TEMP_DIR}/daily_papers_enriched.json"
```

脚本先保存当天总览（失败则自动停止），再按 `write_tiers`（默认 must/worth）保存论文、PDF 和点评子笔记，
并把本次论文写入推荐历史。同日重跑会复用已有条目和笔记，可直接重跑。
注意：`--days N` 多天模式不按历史去重，可能出现之前推荐过的论文。

读取 `zotero_push_result.json`：包含 `stage`、总览 key、每篇结果、错误和 `reading_queue`。
每篇结果含 `attanger.status`；汇报时列出 `not_moved` 的论文，提示用户在 Zotero 中手动重命名并移动。
退出码 2 是部分失败；检查原因后重跑。
错误不能静默忽略；成功部分的精读可以继续，失败项单独记录并汇报。

## 4. 精读

总览及论文入库阶段完成后，对 `reading_queue` 中每篇执行
[zotero-paper-reader](../zotero-paper-reader/SKILL.md)，传入 arXiv ID 和已有 item_key，跳过再次入库。
默认精读全部必读论文，不把短评当精读；用户要求精读全部入选论文时扩大到所有成功入库项。
每篇保存后，在 `{TEMP_DIR}/reading_progress.json` 记录 arXiv ID、item_key、note_key、状态及失败原因。
进度必须依据实际写入及读回结果；不能因为仅有条目或队列就标记精读完成。

## 5. 继续与汇报

用户说“继续”时，先读取当前任务的 push 结果和阅读进度，并查询 Zotero 实际笔记确认，
只重试未完成项。临时文件丢失时从当天总览和条目笔记恢复，无法确定任务时澄清日期。
汇报总览链接、推荐/已入库/已精读数量，以及失败或未完成项。保留已有成果，不因上下文长度降低阅读质量。
