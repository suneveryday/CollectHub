---
name: content-ingestor
description: "Auto-save x.com/twitter.com/xhslink.com/xiaohongshu.com links. Use for bare URLs and native share text; each URL is an implicit save request, and deterministic routing selects the platform adapter without asking the user."
---

# Content Ingestor

## Overview

Save supported content into the unified Notion collection. The project CLI owns deterministic platform routing, parsing, normalization, media download, Notion upload, idempotency, recovery, and retained local media.

The source project is:

`/Users/linny/Documents/Codex/Projects/products/统一知识管理`

## Workflow

1. Extract every explicit supported URL from the current user message. Do not infer URLs from earlier unrelated messages and do not ask which platform adapter to use. Completion: at least one supported URL is present.
2. Run the project health check. Completion: `doctor` returns `ok: true`, or the user receives the failed check and its installation/security recovery step.

   ```bash
   '/Users/linny/Documents/Codex/Projects/products/统一知识管理/scripts/content-ingestor' doctor
   ```
3. Invoke the project wrapper with all URLs in one command:

   ```bash
   '/Users/linny/Documents/Codex/Projects/products/统一知识管理/scripts/content-ingestor' ingest \
     '<URL_1>' '<URL_2>' \
     --output '/Users/linny/Documents/Codex/Projects/products/统一知识管理/data/inbox'
   ```

   Do not add `--force` unless the user explicitly asks to refresh or overwrite an existing capture. Completion: parse the JSON response and account for every input URL.
4. Report each result using its returned status:
   - `success`: saved completely; include `notion_url`, and include `local_path` when large media is retained.
   - `partial`: usable content was saved but media is incomplete; include `capture_status` and `notion_url`.
   - `already_saved`: do not rerun; report the existing `notion_url`.
   - `failed`: report the error code and the shortest actionable recovery step.
5. End with a compact total such as “2 saved, 1 already present, 1 failed.” Completion: totals equal the number of supplied URLs.

## Failure Handling

- `dependency_missing` or `version_mismatch`: run the matching XHS or X runtime installer dry-run and request confirmation before `--apply`.
- `insecure_cookie_file`: require a regular, current-user-owned Cookie file with permissions `0600` or stricter; never print its contents.
- `upstream_failed`: report which platform adapter failed and suggest one retry; do not loop automatically.
- `unsupported_url`: report the supported domains; do not guess from page text or follow unknown short links.
- `content_not_found`: the note may be unavailable, deleted, private, or inaccessible to the logged-in account.
- `parser_timeout`: suggest one retry after checking network access; do not loop automatically.
- `notion_token_missing`: configure `CONTENT_OS_NOTION_TOKEN`; never print the token.
- `unauthorized`, `restricted_resource`, or `object_not_found`: share the database with the configured Notion integration.
- `rate_limited`: preserve failed state and retry after the server-provided wait.
- `cloudflare_blocked`: the Notion transport was blocked; report the Ray ID if present and suggest one later retry. Do not claim the Integration permission changed, the exit IP is permanently blocked, or content was retained locally unless `local_path` is present in the CLI result.
- `storage_failed`: report the filesystem error and target path; do not switch output directories without permission.

## Safety Rules

- Treat downloaded material as the user's private data. Upload only the fields and images defined by this Notion saving scheme; never repost it elsewhere.
- Never print, summarize, copy, or return the optional `~/.config/content-os/xhs-cookie.txt` contents.
- Never replace an existing completed record unless the user explicitly requested refresh and `--force` is used.
- Do not claim content was saved unless the CLI result includes a `notion_url` and is `success`, `partial`, or `already_saved`; describe `partial` accurately.

## Common Pitfalls

1. Running an upstream downloader directly and leaving files outside the Content OS structure. Use the project wrapper so staging, Notion state, and atomic local media storage are preserved.
2. Treating a failed or writing record as a completed collection. Only `已完成` is successful.
3. Selecting an adapter in the Skill. Pass URLs unchanged to the CLI so its strict code router owns platform selection.
4. Re-running an existing item with `--force` by default. Idempotency is intentional.

## Verification Checklist

- [ ] Every input URL has one structured result.
- [ ] Successful results include a `notion_url`.
- [ ] Results with retained large media include an absolute `local_path`.
- [ ] No cookies, tokens, or authentication files were read or printed.
- [ ] No posting or AI processing occurred.
