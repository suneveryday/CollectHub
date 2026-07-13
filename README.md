# 统一知识管理 MVP

本项目实现个人使用的 Notion-first Content OS：Hermes 从 QQ Bot 会话中接收受支持平台的内容链接，统一 Content Ingestor 根据 URL 自动选择 Adapter，并将收藏写入 Notion「统一收藏管理」数据库。

## 当前范围

- 支持小红书单条笔记和公开 X 单条帖子链接；不需要用户指定平台。
- 图文正文和图片只保存到 Notion，不在 `data/inbox` 留副本。
- 视频的描述和封面图写入 Notion，原始媒体保留在现有本地目录。
- Live Photo 独立归类；静态内容进入 Notion，动态媒体保留本地。
- 不包含 AI 摘要、自动分类、内容搜索、评论或发布。
- X 暂不包含登录 Cookie、搜索、账号历史、完整 Thread 或批量监控。

## 使用前准备

本项目不使用 Docker或常驻服务。第三方下载器使用独立 Python 3.12 环境，每次采集完成后进程立即退出。

先预览依赖安装内容：

```bash
./scripts/install-xhs-downloader
./scripts/install-x-runtime
```

确认后安装固定的 XHS-Downloader 2.7：

```bash
./scripts/install-xhs-downloader --apply
./scripts/install-x-runtime --apply
./scripts/content-ingestor doctor
```

默认不需要 Cookie。需要更高画质或访问受限内容时，可由用户手动创建安全文件：

```bash
mkdir -p ~/.config/content-os
chmod 700 ~/.config/content-os
# 使用本地编辑器写入 ~/.config/content-os/xhs-cookie.txt
chmod 600 ~/.config/content-os/xhs-cookie.txt
```

不要把 Cookie 作为命令参数或聊天内容发送。程序只在一次性 bridge 进程中读取该文件，不写入日志、Markdown 或 metadata。

创建 Notion internal integration，将「统一收藏管理」数据库分享给它。若 Hermes 已通过 Notion MCP 完成授权，CLI 会安全复用 `~/.hermes/.env` 中的 `NOTION_TOKEN`；无需再配置第二份凭证。

非 Hermes 环境可以显式配置项目变量：

```bash
export CONTENT_OS_NOTION_TOKEN='ntn_...'
```

目标 data source 默认是本项目指定的「统一收藏管理」。需要切换环境时可以覆盖：

```bash
export CONTENT_OS_NOTION_DATA_SOURCE_ID='39c6977d-8940-8009-ba73-000b93ec9385'
```

不要把 Notion token 写入仓库、命令输出或聊天内容。数据库 schema 命令默认只预览：

```bash
./scripts/content-ingestor notion-schema
./scripts/content-ingestor notion-schema --apply
./scripts/content-ingestor doctor
```

## CLI 调试入口

```bash
./scripts/content-ingestor ingest \
  'https://www.xiaohongshu.com/explore/NOTE_ID' \
  'https://x.com/username/status/TWEET_ID' \
  --output ./data/inbox
```

可以一次传入不同平台的多个链接。CLI 按严格域名和路径选择 Adapter；默认使用「平台 + 内容 ID」在 Notion 中去重，已完成记录返回 `already_saved`。`partial` 记录会在下次提交时自动补抓；只有明确需要刷新完整记录时才添加 `--force`。

## Notion 图文排版

写入 Notion 时会按来源平台保留原始阅读顺序，而不是统一把图片追加到正文末尾：

- 小红书图文按轮播图顺序写入图片，再写正文描述；标题仍保存在 Notion 页面标题字段。
- 普通 X 帖子和 Long Post 先写正文，再写帖子附件图片。
- X Article 根据抓取到的 HTML 原位转换标题、段落、列表、引用、链接、基础行内格式和图片。

X Article 提供了图片与段落之间的明确锚点，因此可以精确还原。小红书当前抓取数据只提供有序图片列表和完整描述，无法判断某张图片属于描述中的第几段，所以采用平台原生的「轮播图在前、描述在后」结构。

如需更新已经保存的历史内容，传入原链接并显式重刷：

```bash
./scripts/content-ingestor ingest \
  'https://www.xiaohongshu.com/explore/NOTE_ID' \
  'https://x.com/username/status/TWEET_ID' \
  --output ./data/inbox \
  --force
```

`--force` 会重新抓取来源，并在同一条 Notion 记录中清空旧正文 blocks 后按新规则写回，不会创建重复记录。来源已删除、转为私密或当前账号无权访问时无法重刷。

## 保存结构

图文没有本地输出目录。视频和 Live Photo 继续使用：

```text
data/inbox/YYYY/MM/xiaohongshu/标题--note-id/
├── index.md
├── metadata.json
└── assets/
    ├── 001.jpg
    ├── video.mp4
    └── live-002.mp4
```

X 的纯文本、图片和 Article 在写入 Notion 后清理 staging；包含视频或音频时，仅保留大媒体及索引：

```text
data/inbox/YYYY/MM/x/标题--tweet-id/
├── index.md
├── metadata.json
└── assets/
    └── video.mp4
```

状态包括 `success`、`partial`、`already_saved` 和 `failed`。`success` 必须表示 Notion 已完整写入，且要求保留的本地大媒体已经保存；`partial` 表示正文可用并已入库，但媒体不完整。成功结果包含 `content_type`、`capture_status` 和 `notion_url`，保留本地媒体时还包含 `local_path`。失败结果返回 `stage`、`code` 和 `message`。

## Hermes Skill

源码位于 `skills/social-media/content-ingestor`。安装器默认只预览：

```bash
./scripts/install-hermes-skill
```

确认后安装：

```bash
./scripts/install-hermes-skill --apply
hermes skills list
```

如果目标 Skill 已存在，安装器会先将旧目录移动到 `~/.hermes/backups/skills/content-ingestor-YYYYMMDD-HHMMSS`。备份不会留在 `~/.hermes/skills` 扫描树中，避免产生同名 Skill 冲突。

## 验证

```bash
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python \
  -m unittest discover -s tests -v
```

自动化测试使用固定 fixture 和 mock Notion 客户端，不依赖实时平台、Notion 或用户登录态。

## 第三方边界

XHS-Downloader 固定为 2.7 / `afaf2fb459980fccef9eec74e304a39af2c49cab`。X runtime 固定为 gallery-dl 1.32.1 与 yt-dlp 2026.06.09。三者均安装到用户本地隔离环境并通过独立进程调用。详见 `THIRD_PARTY_NOTICES.md`。
