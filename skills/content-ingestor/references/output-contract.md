# Output contract

Every captured item is stored under:

`~/CollectHub/YYYY/MM/<platform>/<safe-title>--<content-id>/`

The root can be changed with `--output` or `COLLECTHUB_LIBRARY`.

Each item contains:

- `index.md`: readable source attribution, body, tags, and relative media links.
- `metadata.json`: normalized model, extractor versions, ingest status, and non-secret errors.
- `assets/`: downloaded images, covers, subtitle sidecars, and source-allowed media when present.

Schema v3 adds `capture_policy` and structured `subtitles`. Video platforms use `metadata_subtitles`: missing video/audio files are intentional, and a source with no available subtitles can still be complete.

Top-level statuses:

- `success`: local content and required media were saved.
- `partial`: readable local content was saved, but some media or metadata is incomplete.
- `already_saved`: a completed local item already existed and was preserved.
- `failed`: no usable local capture was completed; inspect `error.stage`, `error.code`, and `error.message`.

Successful, partial, and already-saved results include `local_path` and the compatibility alias `target_dir`.

When `--sync notion` is present, the result also contains `sync.notion.status` plus `url` or `error`. A Notion failure does not change the local status. `notion_url` remains as a compatibility alias when a Notion URL exists.
