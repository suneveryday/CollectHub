# Recovery guide

- `dependency_missing` or `version_mismatch`: rerun the pinned CollectHub installer with `--repair` after user approval.
- `insecure_cookie_file`: require a regular current-user-owned file with mode `0600` or stricter. Never inspect or print its contents.
- `unsupported_url`: accept only direct supported domains and X `/status/<id>` paths.
- `content_not_found`, `deleted_or_private`, or `authentication_required`: explain that the source may be deleted, private, restricted, or unavailable to anonymous access. Do not propose bypasses.
- `parser_timeout` or `upstream_failed`: suggest checking network access and retrying once. Do not loop automatically.
- `storage_failed`: report the filesystem error and target path. Do not silently change the library root.
- `notion_token_missing` or `notion_data_source_missing`: configure the matching environment value locally; never request it in chat.
- `unauthorized`, `restricted_resource`, or `object_not_found`: share the selected data source with the configured Notion integration.
- `rate_limited` or `cloudflare_blocked`: preserve the local result and retry sync later.
