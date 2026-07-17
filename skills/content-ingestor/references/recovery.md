# Recovery guide

- `dependency_missing` or `version_mismatch`: rerun the pinned CollectHub installer with `--repair` after user approval.
- `insecure_cookie_file`: require a regular current-user-owned file with mode `0600` or stricter. Never inspect or print its contents.
- `unsupported_url`: require one direct content URL. Reject profiles, channels, playlists, searches, account pages, unsupported protocols, and PDF links.
- `unsafe_url`, `dns_failed`, or `invalid_redirect`: do not retry with a bypass. Generic web capture blocks local, private, link-local, reserved, and non-global destinations at every redirect.
- `response_too_large` or `unsupported_content_type`: the static webpage exceeded limits or was not HTML. Do not add browser rendering or disable the limit.
- `content_not_found`, `deleted_or_private`, or `authentication_required`: explain that the source may be deleted, private, restricted, or unavailable to anonymous access. Do not propose bypasses.
- `parser_timeout` or `upstream_failed`: suggest checking network access and retrying once. Do not loop automatically.
- `storage_failed`: report the filesystem error and target path. Do not silently change the library root.
- `notion_token_missing` or `notion_data_source_missing`: configure the matching environment value locally; never request it in chat.
- `unauthorized`, `restricted_resource`, or `object_not_found`: share the selected data source with the configured Notion integration.
- `rate_limited` or `cloudflare_blocked`: preserve the local result and retry sync later.

Optional Netscape Cookie files live at `~/.config/collecthub/cookies/{youtube,reddit,facebook,tiktok,zhihu}.txt`. They must be current-user-owned regular files with mode `0600` or stricter. Anonymous public capture is attempted without credentials when no file exists. Never inspect or echo Cookie contents.
