# Zotero 配置

下文的 `{SHARED}` 表示本文件所在 `references/` 的上一级目录。

## 默认：本机 Zotero 10+

1. 打开 Zotero，在设置 → 高级中启用“允许本机其他应用程序与 Zotero 通信”。
2. `python3 "{SHARED}/zotero_push.py" check` 检查连接与本地库。
3. `python3 "{SHARED}/zotero_push.py" authorize` 请求写入授权。在 Zotero 弹窗中选择“始终允许”，
   可在后续运行复用授权；“允许”仅对一次写请求有效。拒绝会停止操作。
4. 单篇入库无需 setup。每日总览直接保存在 Daily Papers 分类中（不存在时创建），论文默认进入「待定」，可配置为匹配已有分类。

本地模式不需要云端 API key 或用户 ID；个人库用 `/api/users/0`。
群组库需 `library_type: "group"` 和群组 `library_id`，且必须有编辑权限。
程序不读写 `zotero.sqlite`，不改动 Attanger 设置，也不直接移动附件。

本地授权按 Zotero Server ID 隔离，以权限 600 保存在个人配置目录的 `local-auth-*.json`。
可在 Zotero 高级设置清除写授权。缓存与云端分开，避免混用本地/云端版本号。
`--dry-run` 不连接 Zotero、不弹授权、不下载 PDF、不改库；单篇入库预览仍会获取 arXiv 元数据。

## 附件与 Attanger

`attach_pdf: "upload"`（默认）下载 PDF 后保存到本机 Zotero。这里的 upload 在本地模式中
只向 localhost 传文件；Zotero 是否随后同步文件，由用户的同步设置决定。
已有 PDF 不重复导入，包括 Attanger 管理的 linked file。
运行 `attachments --arxiv <ID>` 可获取附件的当前路径及文件是否存在；阅读时优先用存在的本地 PDF。
Attanger 只处理“添加事件后约 1 秒时文件已存在”的附件；本地 API 是先建附件条目再上传文件，
存在赶不上的可能。因此脚本上传后会轮询最多 `attanger_wait_seconds` 秒，并在结果里给出 `attanger.status`：
`moved`（已成为 linked file，含新路径）/ `not_moved`（仍在 `~/Zotero/storage`，需在 Zotero 中右键
附件用 Attanger 的“重命名并移动”）/ `unknown` / `skipped`。不要把 `not_moved` 报告成已整理。
Attanger 移动存储附件时会新建 linked file 附件并删除原附件，附件 key 因此改变；`moved` 时输出的
`pdf_key` 已是新附件。附件 key 不是稳定标识：之后需要 PDF 时用条目 key 运行 `attachments --item <KEY>` 实时查询。
下载失败会提示并退回 PDF 链接，不能把链接当作已经下载的文件；upload 模式下次运行会重新尝试下载，
成功后保存 PDF，不会重复叠加链接附件。

新条目元数据与 Zotero“按标识符添加”（arXiv 转换器）一致：日期取最新版本、`extra` 为
`arXiv:ID [主分类]`、`libraryCatalog: arXiv.org`、arXiv 学科自动标签、PDF 标题 `Preprint PDF`，
因此 Attanger 生成的文件名（作者 - 年份 - 标题）与手动添加一致。

pending 模式只把**未归类**的论文放进「待定」；已在任意分类中的已有条目保持原分类，不再额外加入「待定」。

## 可调配置

个人覆盖：`~/.config/zotero-daily-papers/config.json`（`ZDP_CONFIG` 可指定其他文件）。

| 键 | 默认 | 说明 |
|---|---|---|
| language | zh | `zh` / `en`：生成的笔记标题和本地化默认值所用语言；环境变量 ZDP_LANGUAGE 可覆盖 |

| 键（zotero 节） | 默认 | 说明 |
|---|---|---|
| backend | local | local / web；环境变量 ZOTERO_BACKEND 可覆盖 |
| local_url | http://127.0.0.1:23119 | 仅允许本机回环 HTTP 地址 |
| attach_pdf | upload | upload 保存 PDF；link 仅链接；none 不添加 |
| attanger_wait_seconds | 10 | 上传后等待并核对 Attanger 移动结果的秒数；0 关闭核对 |
| root_collection | Daily Papers | 每日推荐总览所在的顶层分类（不存在时自动创建），总览直接放在其中 |
| classification_mode | pending | pending：统一待定；existing：读取已有分类，由 Agent 语义判断 |
| pending_collection | 待定（zh）/ Pending（en） | pending 默认目标，以及 existing 无匹配时的根目录分类 |
| write_tiers | must, worth | 每日推荐入库等级 |
| max_embedded_images_per_note | 4 | 仅 Web 模式生效；本地模式插图为链接 |

## 可选：Web API 模式

设置 `ZOTERO_BACKEND=web`，并设置 `ZOTERO_API_KEY`、`ZOTERO_LIBRARY_ID`。
在 https://www.zotero.org/settings/keys 创建 key，授权 library、notes、write；上传附件需文件权限。
Web 模式上传文件使用 Zotero 云端存储，无法读取本地 linked file 路径。

## 排错

- 连接失败：保持 Zotero 打开，确认端口和本地 API 设置。
- 缺少 Server ID：需升级到 Zotero 10+ 才能使用此本地写入后端。
- 401：脚本重新申请授权；403：检查授权拒绝或库权限。
- 412：Zotero 实例切换或版本冲突，重新执行命令读取新版本。
- 文件不存在：在 Zotero 中修复 linked file 的路径后重试。
- 不自动重试结果不明的写请求；重新运行会先查重。

官方协议：https://www.zotero.org/support/dev/web_api/v3/local_api

## 标签与笔记识别

新条目仅使用普通研究方向名称和主题标签，不再生成 `dp` 或 `dp/...` 标签。
笔记类型通过正文首个一级标题识别：`【精读】`、`【速读】`、`【每日锐评】`、`【每日推荐】`
（英文安装写作 `[Deep Read]`、`[Quick Read]`、`[Daily Review]`、`[Daily Digest]`，两种写法始终都能识别）。
每日锐评和总览标题同时包含日期，用于区分不同天的记录。

同一父条目下的同类型笔记可被查重；每日总览只在总览分类内查找，按类型和日期更新。
请保留标题中的类型和日期；手动删除这些标识会影响后续识别。
旧版标签仅作为读取兼容：更新旧笔记时转成上述标题并移除其旧 dp 标签，保留其他标签。
不会自动批量清理库内历史条目或笔记的标签。

## 切换语言或分类模式

`python3 "{SHARED}/zdp_config.py" --set-language zh`（或 `en`）设置新笔记和本地化默认值的语言；
`python3 "{SHARED}/zdp_config.py" --set-classification pending` 或 `--set-classification existing` 设置分类模式。
命令只修改个人配置中的对应键，保留其余设置。通过 `zdp_config.py` 确认实际生效值；
如使用 ZDP_CONFIG，需同时检查该文件覆盖值。非法模式会报错，不静默退回默认模式。
