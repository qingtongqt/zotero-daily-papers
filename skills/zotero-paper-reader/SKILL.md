---
name: zotero-paper-reader
description: 精读或快速阅读指定论文、arXiv 链接或已有 Zotero 条目，读取本机 PDF（含 Attanger 附件），将研究笔记保存为 Zotero 子笔记。用于“读一下”“精读”“快速看一下”；仅保存请求交给 zotero-paper-save。
---

# 阅读论文并保存子笔记

以本文件所在目录为 `{SKILL_DIR}`，同级的 `../_zotero_shared` 为 `{SHARED}`。相对链接以本文件为基准。
运行 `python3 "{SHARED}/zdp_config.py"` 获取配置和临时目录。
默认中文；遵循用户语言及“不保存”要求。

## 定位与已有笔记

解析 arXiv ID（阅读时保留请求版本）、来源链接或已有 item_key。无法确认论文身份时先澄清。
每日流程传入 item_key 时直接复用，不再次入库或重新分类。
单篇先运行 `find --arxiv <ID>`；已存在则 `notes --item <KEY>` 检查已有笔记。
笔记字数和公式数量只是线索，不是完整性判据；必要时通过本地 API 的 item GET 读取 note HTML，
核对方法、实验、主要公式、主表格、关键图及来源覆盖。完整且符合请求的笔记可复用；
更新须保留用户手写内容，快速摘要不能覆盖 full-note。不能只读笔记摘要统计就声称保留了原文。

## 获取全文与阅读

1. 已有条目用 `attachments --item <KEY>` 获取真实路径，优先读取 exists=true 的本机 PDF。
2. 无可用附件时读取 arXiv HTML，失败回退 PDF；阅读来源不偷偷换版本。
3. 按 [精读模板](references/paper-note-template.md) 写临时 Markdown。
4. “与我研究方向的关系”栏目按有效配置 daily_papers.directions 的 name 逐项生成。
5. 全文不可得时明确阅读范围，不把摘要笔记当作完整精读；不凑字数、公式或图表。

“快速看一下”写 300–600 字要点，kind=quick；默认完整阅读用 full-note。
用户要求批判分析或公式提取时按要求组织内容，不强行扩展成完整精读。

## 保存与自检

明确“不保存”时只在聊天回答，跳过以下写入。
新论文读完并写好笔记后执行 [保存 Skill](../zotero-paper-save/SKILL.md)；已有条目跳过入库。

```bash
python3 "{SHARED}/zotero_push.py" add-note --item <KEY> --markdown <临时笔记路径> --kind full-note
python3 "{SHARED}/zotero_push.py" notes --item <KEY>
```

已有同类笔记需保留用户内容后才用 `--replace`。确认实际子笔记存在，内容与来源相符。
需要引用图像时附上 Zotero Deep Link (zotero://open-pdf/library/...)，精确到页。
每日调用时返回 arXiv ID、item_key、note_key、完成/失败状态，供总入口更新阅读进度。
最后向用户给简短锐评、Zotero 链接及实际未完成项。
