---
name: content-ingestor
description: "Save youtube.com, youtu.be, tiktok.com, facebook.com, fb.watch, zhihu.com, xiaohongshu.com, xhslink.com, x.com, twitter.com, and public webpage URLs with CollectHub. Use for bare URLs, native share text, multi-link save requests, refresh requests, automatic YouTube/TikTok Notion bookmarks, and explicit Notion sync for other sources."
---

# Content Ingestor

Save supported social links into the user's local CollectHub library. Treat every supported URL in the current message as an implicit save request. Let the CLI own routing, downloading, normalization, idempotency, and storage.

## Workflow

1. Extract every explicit supported `http` or `https` URL from the current message. Supported sources include YouTube, TikTok, Facebook, Zhihu, Xiaohongshu, X/Twitter, and single public HTML pages. Do not reuse unrelated URLs from older messages.
2. Locate the CLI with `command -v content-ingestor`; fall back to `$HOME/.local/bin/content-ingestor`.
3. If the CLI is missing, explain that local runtimes must be installed and ask before running `scripts/setup`. Never install dependencies silently.
4. Run `content-ingestor doctor`. If a required local check fails, report the failed check and the shortest recovery step.
5. Pass all URLs to one command so input order is preserved:

   ```bash
   "$HOME/.local/bin/content-ingestor" ingest '<URL_1>' '<URL_2>'
   ```

   Add `--force` only when the user explicitly requests refresh or replacement. The CLI automatically creates Notion bookmarks for YouTube and TikTok videos; it stores their source URL as the video link and never downloads video or audio. Add `--sync notion` only when the user explicitly asks to sync another source.
6. Parse the JSON and account for every URL. Report `success`, `partial`, `already_saved`, or `failed` exactly. Include `local_path` for every locally saved item. If Notion was requested, report its nested sync status separately from the local result.
7. End with compact totals whose sum equals the number of input URLs.

## Safety

- Treat downloaded content as private. Never repost it or send it to another service. The only implicit external write is the configured Notion bookmark for YouTube and TikTok videos; require an explicit request for every other Notion sync.
- Never print or return Cookie files, Notion tokens, or authentication environment values.
- Never claim Notion sync succeeded unless `sync.notion.status` is `success` or `already_saved` and a URL is present.
- Preserve an existing completed item unless the user explicitly requests `--force`.
- Do not turn platform profiles, channels, playlists, searches, or account pages into generic webpage captures.
- Do not follow unsafe redirects, guess URLs from page text, search accounts, bypass login controls, or publish content.
- YouTube and TikTok captures intentionally exclude video and audio files. Keep only small local metadata needed for idempotency and recovery, and use Notion as the user-facing bookmark with the original video URL. Facebook also excludes video and audio but remains local-first unless sync is requested.

## Failure handling

- For installation, dependency, Cookie, storage, and optional Notion errors, read [references/recovery.md](references/recovery.md).
- For result fields and local files, read [references/output-contract.md](references/output-contract.md).

## Verification

- Confirm every input URL has one result.
- Confirm local successes contain an absolute `local_path` with `index.md` and `metadata.json`.
- Confirm sync failures did not erase or downgrade a successful local capture.
- Confirm no secret value appeared in commands or output.
