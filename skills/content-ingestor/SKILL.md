---
name: content-ingestor
description: "Save youtube.com, youtu.be, reddit.com, redd.it, facebook.com, fb.watch, tiktok.com, zhihu.com, xiaohongshu.com, xhslink.com, x.com, twitter.com, and public webpage URLs with CollectHub. Use for bare URLs, native share text, single-post or multi-link save requests, refresh requests, and explicit Notion sync."
---

# Content Ingestor

Save supported social links into the user's local CollectHub library. Treat every supported URL in the current message as an implicit save request. Let the CLI own routing, downloading, normalization, idempotency, and storage.

## Workflow

1. Extract every explicit supported `http` or `https` URL from the current message. Supported sources include YouTube, Reddit, Facebook, TikTok, Zhihu, Xiaohongshu, X/Twitter, and single public HTML pages. Do not reuse unrelated URLs from older messages.
2. Locate the CLI with `command -v content-ingestor`; fall back to `$HOME/.local/bin/content-ingestor`.
3. If the CLI is missing, explain that local runtimes must be installed and ask before running `scripts/setup`. Never install dependencies silently.
4. Run `content-ingestor doctor`. If a required local check fails, report the failed check and the shortest recovery step.
5. Pass all URLs to one command so input order is preserved:

   ```bash
   "$HOME/.local/bin/content-ingestor" ingest '<URL_1>' '<URL_2>'
   ```

   Add `--force` only when the user explicitly requests refresh or replacement. Add `--sync notion` only when the user explicitly asks for Notion sync. Video bookmarks store the stable source page URL and never download video or audio.
6. Parse the JSON and account for every URL. Report `success`, `partial`, `already_saved`, or `failed` exactly. Include `local_path` for every locally saved item. If Notion was requested, report its nested sync status separately from the local result.
7. End with compact totals whose sum equals the number of input URLs.

## Safety

- Treat downloaded content as private. Never repost it or send it to another service. Require an explicit request for every Notion sync.
- Never print or return Cookie files, Notion tokens, or authentication environment values.
- Never claim Notion sync succeeded unless `sync.notion.status` is `success` or `already_saved` and a URL is present.
- Preserve an existing completed item unless the user explicitly requests `--force`.
- Do not turn platform profiles, channels, playlists, searches, or account pages into generic webpage captures.
- Do not follow unsafe redirects, guess URLs from page text, search accounts, bypass login controls, or publish content.
- YouTube, Reddit, Facebook, and TikTok video captures intentionally exclude video and audio files. Keep only small local metadata, covers, and subtitles needed for idempotency and recovery. Reddit captures one post and never follows comment trees, feeds, profiles, or outbound links.

## Failure handling

- For installation, dependency, Cookie, storage, and optional Notion errors, read [references/recovery.md](references/recovery.md).
- For result fields and local files, read [references/output-contract.md](references/output-contract.md).

## Verification

- Confirm every input URL has one result.
- Confirm local successes contain an absolute `local_path` with `index.md` and `metadata.json`.
- Confirm sync failures did not erase or downgrade a successful local capture.
- Confirm no secret value appeared in commands or output.
