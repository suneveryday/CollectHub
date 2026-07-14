# CollectHub

CollectHub 是一个本地优先的 Agent Skill：把小红书和 X/Twitter 单条内容保存为可长期阅读、迁移和检索的 Markdown、JSON 与媒体文件。首版正式支持 macOS 上的 Codex 和 Hermes。

## 一行安装

安装固定版本 `v1.0.0`：

```bash
curl --proto '=https' --tlsv1.2 -fsSL \
  https://raw.githubusercontent.com/suneveryday/CollectHub/v1.0.0/install.sh | sh
```

安装器会先显示写入位置、依赖和许可证，确认后在用户目录安装：

- CollectHub：`~/.local/share/collecthub/releases/1.0.0`
- CLI：`~/.local/bin/content-ingestor`
- Skill：自动检测 `~/.codex/skills/`、`~/.hermes/skills/` 或两者
- 本地内容库：`~/CollectHub`

同时安装固定版本的隔离 Python 3.12、XHS-Downloader 2.7、gallery-dl 1.32.1 和 yt-dlp 2026.06.09，不修改系统 Python。仅支持 macOS arm64 和 x86_64。

非交互安装、预览、修复与卸载：

```bash
curl -fsSL https://raw.githubusercontent.com/suneveryday/CollectHub/v1.0.0/install.sh | sh -s -- --yes
curl -fsSL https://raw.githubusercontent.com/suneveryday/CollectHub/v1.0.0/install.sh | sh -s -- --dry-run
curl -fsSL https://raw.githubusercontent.com/suneveryday/CollectHub/v1.0.0/install.sh | sh -s -- --yes --repair
curl -fsSL https://raw.githubusercontent.com/suneveryday/CollectHub/v1.0.0/install.sh | sh -s -- --yes --uninstall
```

卸载不会删除 `~/CollectHub`。现有 Skill 和旧版本会先移到非扫描备份目录。

## 使用

重启 Codex 或开启新的 Hermes 会话，然后直接发送支持的链接，例如：

```text
帮我保存 https://www.xiaohongshu.com/explore/NOTE_ID
收藏 https://x.com/username/status/TWEET_ID
```

裸链接和原生分享文本都会触发保存。一次消息可包含多个链接，按出现顺序串行处理；单条失败不会阻断后续内容。

也可以直接使用 CLI：

```bash
~/.local/bin/content-ingestor doctor
~/.local/bin/content-ingestor ingest \
  'https://www.xiaohongshu.com/explore/NOTE_ID' \
  'https://x.com/username/status/TWEET_ID'
```

默认输出可通过参数或环境变量修改：

```bash
content-ingestor ingest '<URL>' --output "$HOME/MyLibrary"
export COLLECTHUB_LIBRARY="$HOME/MyLibrary"
```

只有明确需要刷新已有内容时才使用 `--force`。

## 本地文件

每条内容保存在：

```text
~/CollectHub/YYYY/MM/<platform>/<title>--<content-id>/
├── index.md
├── metadata.json
└── assets/            # 有媒体时创建
```

状态包括 `success`、`partial`、`already_saved` 和 `failed`。成功、部分成功及已存在结果都返回绝对 `local_path`。

## 可选 Notion 同步

Notion 默认关闭，且仓库不包含任何个人 data source ID。先在本地安全配置：

```bash
export CONTENT_OS_NOTION_TOKEN='ntn_...'
export CONTENT_OS_NOTION_DATA_SOURCE_ID='your_data_source_id'
content-ingestor notion-schema
content-ingestor notion-schema --apply
content-ingestor doctor --sync notion
```

只有显式添加 `--sync notion` 才会同步：

```bash
content-ingestor ingest '<URL>' --sync notion
```

Notion 同步失败不会删除本地内容，也不会把本地成功改成失败。不要把 Token 或 Cookie 发到聊天、命令参数或仓库中。

## 小红书 Cookie

公开内容通常无需 Cookie。确有需要时，用本地编辑器写入受限文件：

```bash
mkdir -p ~/.config/content-os
chmod 700 ~/.config/content-os
# 使用本地编辑器写入 ~/.config/content-os/xhs-cookie.txt
chmod 600 ~/.config/content-os/xhs-cookie.txt
```

程序只在一次性下载进程中读取该文件，不会把内容写入日志、Markdown 或 metadata。CollectHub 不提供登录绕过、账号搜索、批量监控、评论或发布能力。

## 从源码开发

```bash
uv sync --locked --python 3.12
uv run python -m unittest discover -s tests -v
./install.sh --dry-run --source "$PWD"
```

Skill 位于 `skills/content-ingestor`，遵循 [Agent Skills](https://agentskills.io) 目录规范。Codex 用户也可通过 Skill Installer 安装该 GitHub 目录；Hermes 用户可运行 `hermes skills install suneveryday/CollectHub/skills/content-ingestor`，随后首次使用时按提示运行 Skill 内的 setup 脚本安装本地运行时。

## 许可证与第三方组件

CollectHub 使用 [MIT License](LICENSE)。下载器和运行时作为独立程序安装并通过子进程调用，不复制进本仓库；其许可证与固定版本见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。用户应遵守来源平台条款及适用法律，仅保存自己有权访问和使用的内容。
