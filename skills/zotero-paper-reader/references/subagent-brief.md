# 精读子 Agent 任务卡

完整精读（full-note）放到独立子 Agent 中执行：每篇论文一个全新上下文，
不受当天候选列表、评分、锐评、其它论文或之前对话的影响。

## 主 Agent 如何派发

- 每篇论文启动一个**全新**子 Agent（Claude Code 用 Agent 工具的通用子 Agent，不用 fork；
  Codex 等宿主用各自的子 Agent 能力）。不要继承或转述当前对话。
- prompt 只放下面填好的任务卡；不附带候选列表、评分理由、锐评、其它论文笔记。
  所有路径写成绝对路径，因为子 Agent 不知道 `{SKILL_DIR}`、`{SHARED}`、`TEMP_DIR`。
- 同时最多 3 个子 Agent；每个只写自己的条目和临时文件，可并行。
- 子 Agent 运行时，主 Agent 不读这篇论文的全文，也不改写它的笔记。
- 子 Agent 结束后，主 Agent 用 `notes --item <KEY>` 读回确认 note_key 存在，再记录进度；
  不能只凭子 Agent 的回复标记完成。失败项可以用同一任务卡重新派发。
- 宿主不支持子 Agent 时，在当前会话逐篇执行，每篇开始前重新读取本任务卡和精读模板，
  并忽略之前论文的内容。

## 任务卡

````text
请精读 arXiv {ARXIV_ID}（阅读版本：{版本号或“最新”}），写成 Zotero 子笔记。

- Zotero item_key：{ITEM_KEY；新论文写“无”}
- 已有同类笔记：{无 / note_key 及需要保留的用户手写内容}
- 语言：{zh 或 en}
- 用户额外要求：{无 / 用户原话}
- 临时笔记路径：{TEMP_DIR 绝对路径}/notes/{ARXIV_ID}.md
- 共享脚本目录：{SHARED 绝对路径}
- 流程见 {SKILL_DIR 绝对路径}/SKILL.md 的“获取全文与阅读”“保存与自检”两节，
  笔记结构见 {SKILL_DIR 绝对路径}/references/paper-note-template.md。
  没有 item_key 的新论文，读完后按 {SKILL_DIR 绝对路径}/../zotero-paper-save/SKILL.md 入库。

写作重点：
- 通读全文后再写。笔记的重心是问题与动机、方法。
- 问题与动机主要来自 Introduction：作者要解决什么问题，现有方法具体差在哪，为什么这个思路能奏效。
- 方法写到可以复现：输入输出、各模块的作用、训练目标或损失、关键公式（标 Eq. 编号）、算法流程、重要超参。
- 实验写简要：设置，以及主表的关键数字和与最强基线的差距。
- 关键判断就近标出处（§、Table、Figure、Eq.），区分作者主张、论文证据和你的推断。
- 全文读不到的部分，写明实际阅读范围。

完成后用 add-note 保存，并用 notes --item 读回。用户要求不保存时，只写临时笔记，note_key 留空。
最后回复一行 JSON，方便主 Agent 记录进度：
{"arxiv_id": "...", "item_key": "...", "note_key": "...", "status": "done|failed", "scope": "full-text|partial|abstract-only", "reason": "失败或部分阅读的原因，没有则留空", "verdict": "一句话锐评"}
````
