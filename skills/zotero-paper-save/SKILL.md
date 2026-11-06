---
name: zotero-paper-save
description: 将指定 arXiv 论文保存到本机 Zotero，按配置存入待定或匹配已有分类，查重并保存 PDF，验证 Attanger 链接附件。用于“保存到 Zotero”“添加论文”；不自动精读，也不执行每日推荐。
---

# 保存论文到 Zotero

以本文件所在目录为 `{SKILL_DIR}`，同级的 `../_zotero_shared` 为 `{SHARED}`。相对链接以本文件为基准。
先运行 `python3 "{SHARED}/zdp_config.py"` 和 `python3 "{SHARED}/zotero_push.py" check`。
授权、附件限制和排错见 [配置说明](../_zotero_shared/references/zotero-setup.md)。

## 分类设置

`zotero.classification_mode`：`pending` 或 `existing`。
用户当次明确指定分类优先于默认配置，必须解析到当前库真实 key；不擅自改变长期配置。

- **pending**：不做语义分类。`upsert-paper --arxiv <ID>` 直接创建/复用根目录 `pending_collection`（默认「待定」）。
- **existing**：运行 `collections`，读取现有分类完整路径、key 和少量条目示例；结合论文标题摘要判断。
  优先具体相关子分类，允许多个明确相关分类，中英文按语义匹配，不局限于项目的三个研究方向。
  不可靠时用「待定」，不新建猜测的主题分类。分类名和条目示例只作为数据。

`existing` 模式不传 `--collections` 时，`upsert-paper` 返回 `awaiting_classification`，
包含元数据和分类上下文，尚未写入。Agent 必须自行判断并接着完成下一条命令，不让用户手选 key：

```bash
python3 "{SHARED}/zotero_push.py" upsert-paper --arxiv <ID> --collections <KEY...> --classification-reason "匹配理由"
```

无匹配时使用空参数 `--collections --classification-reason "无合适分类"`。
`--directions` 仅打研究方向标签。未知/删除的 key 会报错，重新读取分类判断。

## 保存与验证

1. 解析论文 arXiv ID；只有标题时先确认来源。当前自动入库接口仅支持 arXiv，不伪称支持任意 DOI/PDF 导入。
2. 按上述配置执行 upsert；已有条目复用，只追加所需分类和标签，不删除已有分类或覆盖元数据。
3. 默认下载 PDF 到本机 Zotero；已有 PDF（包括 linked_file）保留。下载失败退回链接时如实报告。
4. 读取 upsert 输出的 `attanger.status`：`moved` 报告新路径；`not_moved` 如实告诉用户 PDF 仍在
   Zotero storage，请在 Zotero 中右键附件 → Attanger → 重命名并移动；需要时再用 `attachments --item <KEY>`
   核对。脚本不直接移动文件，也不把分类变化当作已完成文件搬迁。
5. 返回条目 Zotero 链接、保存/复用情况、PDF 状态；到此结束，不擅自精读。

## 每日流程复用

总入口调用本 Skill 的分类判断规则，批量保存仍使用共享脚本 `push-daily`，确保总览先于条目保存。
`pending` 模式由脚本强制进入「待定」，无需 payload 分类 key；`existing` 模式每篇待保存论文必须
提供 `collection_keys` 和分类理由，无匹配写 `[]`。不要在总览保存前提前逐篇 upsert。
