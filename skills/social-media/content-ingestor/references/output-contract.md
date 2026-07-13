# Output contract

Every completed capture creates or reuses one record in the configured Notion data source.

Pure text, images, and X Articles have no persistent local output after Notion succeeds. Retained large media is stored under:

`data/inbox/YYYY/MM/xiaohongshu/<safe-title>--<note-id>/`

or:

`data/inbox/YYYY/MM/x/<safe-title>--<tweet-id>/`

Local files for video and Live Photo:

- `index.md`: human-readable content with source attribution and relative asset links.
- `metadata.json`: normalized content model, media statuses, raw parser response, ingest status, and errors.
- `assets/`: downloaded images, video, and Live Photo motion files when available.

CLI response statuses:

- `success`: Notion is complete and any required local media is complete.
- `partial`: usable content is in Notion, but `capture_status` records incomplete media or metadata-only capture.
- `already_saved`: the completed Notion record already existed and was preserved.
- `failed`: includes `error.stage`, `error.code`, and `error.message`; a failed Notion record or local media may remain for retry.

Saved results include `content_type`, `capture_status`, and `notion_url`. Results with retained local media also include `local_path` and the compatibility alias `target_dir`.
