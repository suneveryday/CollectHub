from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import GALLERY_DL_VERSION, YT_DLP_VERSION, x_python
from .models import ContentItem, MediaAsset
from .xhs_adapter import AdapterError


_STATUS_RE = re.compile(r"/(?:i/web/|[^/]+/)?status/(\d+)(?:/|$)")
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
_AUDIO_EXTENSIONS = {".m4a", ".mp3", ".aac", ".opus", ".ogg", ".wav"}


def canonicalize_x_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed = {"x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}
    if parsed.scheme not in {"http", "https"} or host not in allowed:
        raise AdapterError("unsupported_url", "only direct x.com or twitter.com status URLs are supported")
    match = _STATUS_RE.search(parsed.path.rstrip("/") + "/")
    if not match:
        raise AdapterError("unsupported_url", "X URL must contain /status/<numeric tweet id>")
    tweet_id = match.group(1)
    return tweet_id, f"https://x.com/i/web/status/{tweet_id}"


def read_x(url: str) -> dict[str, Any]:
    tweet_id, canonical_url = canonicalize_x_url(url)
    python = x_python()
    if not python.is_file():
        raise AdapterError(
            "dependency_missing",
            "X capture runtime is not installed; run scripts/install-x-runtime --apply",
        )
    staging = Path(tempfile.mkdtemp(prefix="content-os-x-"))
    config = staging / "gallery-dl.json"
    config.write_text(json.dumps(_gallery_config(staging), ensure_ascii=False), encoding="utf-8")
    command = [str(python), "-m", "gallery_dl", "--config", str(config), "--quiet", canonical_url]
    try:
        proc = subprocess.run(command, text=True, capture_output=True, check=False, timeout=180)
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise AdapterError("parser_timeout", "gallery-dl timed out after 180 seconds") from exc

    metadata = _json_lines(proc.stdout)
    if proc.returncode and not metadata:
        code = _error_code(proc.stderr)
        shutil.rmtree(staging, ignore_errors=True)
        raise AdapterError(code, _safe_error(proc.stderr, "gallery-dl failed"))

    files = _manifest(staging, exclude={config.name})
    fallback: dict[str, Any] = {}
    if _expects_video(metadata) and not any(entry["kind"] == "video" for entry in files):
        fallback = _read_ytdlp(python, staging, canonical_url)
        files = _manifest(staging, exclude={config.name})

    return {
        "ok": True,
        "tweet_id": tweet_id,
        "canonical_url": canonical_url,
        "metadata": metadata,
        "fallback": fallback,
        "files": files,
        "gallery_error": _safe_error(proc.stderr, "") if proc.returncode else "",
        "_staging_dir": str(staging),
    }


def normalize_x(payload: dict[str, Any], input_url: str) -> ContentItem:
    tweet_id, canonical_url = canonicalize_x_url(payload.get("canonical_url") or input_url)
    records = payload.get("metadata") if isinstance(payload.get("metadata"), list) else []
    record = next((item for item in records if _id(item) == tweet_id), records[0] if records else {})
    note = _mapping(record.get("note_tweet") or record.get("note"))
    article = _mapping(record.get("article"))
    article_html = _article_html(payload, article)
    article_plain = _text(article.get("plain_text") or article.get("text")) or _html_text(article_html)
    body = article_plain or _text(note.get("text") or note.get("content")) or _text(
        record.get("content") or record.get("full_text") or record.get("text") or record.get("description")
    )
    content_type = "article" if article or article_html else "long_post" if note else "post"
    title = _text(article.get("title")) or _fallback_title(body, tweet_id)
    media = _media(payload)
    expected = _expected_media_count(record)
    failed_media = [asset for asset in media if asset.status == "failed"]
    if payload.get("gallery_error") or failed_media or (expected and len([a for a in media if a.local_path]) < expected):
        capture_status = "media_partial" if body or article_plain else "metadata_only"
    elif not body and not article_plain:
        capture_status = "metadata_only"
    else:
        capture_status = "complete"
    user = _mapping(record.get("user") or record.get("author"))
    author = _text(user.get("name") or user.get("screen_name") or record.get("username") or record.get("author"))
    raw_article = {
        "title": _text(article.get("title")),
        "html": article_html,
        "plain_text": article_plain,
        "cover": _text(article.get("cover") or article.get("cover_url")),
        "published_at": _iso_date(article.get("date") or record.get("date")),
        "updated_at": _iso_date(article.get("date_updated")),
    }
    return ContentItem(
        schema_version="2",
        platform="x",
        content_type=content_type,
        source_id=tweet_id,
        source_url=canonical_url,
        canonical_url=canonical_url,
        input_url=input_url,
        title=title,
        body=body,
        author=author,
        published_at=_iso_date(record.get("date") or record.get("created_at")),
        captured_at=datetime.now(timezone.utc).isoformat(),
        tags=[str(tag) for tag in record.get("tags", []) if str(tag).strip()] if isinstance(record.get("tags"), list) else [],
        media=media,
        raw={"gallery": record, "fallback": payload.get("fallback", {})},
        capture_status=capture_status,
        reply_to=_relation(record, "reply"),
        quoted_post=_relation(record, "quote"),
        metrics=_metrics(record),
        article=raw_article if content_type == "article" else {},
        extractor_versions={"gallery-dl": GALLERY_DL_VERSION, "yt-dlp": YT_DLP_VERSION},
    )


def cleanup_payload(payload: dict[str, Any]) -> None:
    staging = payload.get("_staging_dir")
    if staging:
        shutil.rmtree(str(staging), ignore_errors=True)


def _gallery_config(staging: Path) -> dict[str, Any]:
    return {
        "extractor": {
            "base-directory": str(staging),
            "directory": [],
            "twitter": {
                "text-tweets": True,
                "articles": ["document", "meta", "cover", "media", "html"],
                "conversations": False,
                "quoted": True,
                "retweets": False,
                "ratelimit": "abort",
                "retries-api": 2,
                "postprocessors": [
                    {"name": "metadata/jsonl@post", "filename": "-"},
                ],
            },
        }
    }


def _read_ytdlp(python: Path, staging: Path, url: str) -> dict[str, Any]:
    output = str(staging / "%(id)s-video.%(ext)s")
    command = [str(python), "-m", "yt_dlp", "--no-playlist", "--no-progress", "--print-json", "-o", output, url]
    try:
        proc = subprocess.run(command, text=True, capture_output=True, check=False, timeout=300)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "yt-dlp timed out"}
    values = _json_lines(proc.stdout)
    return {"ok": proc.returncode == 0, "entries": values, "error": _safe_error(proc.stderr, "")}


def _json_lines(value: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line in value.splitlines():
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            result.append(parsed)
    return result


def _manifest(staging: Path, *, exclude: set[str]) -> list[dict[str, str]]:
    result = []
    for path in sorted(staging.rglob("*")):
        if not path.is_file() or path.name in exclude or path.suffix in {".part", ".ytdl"}:
            continue
        suffix = path.suffix.lower()
        kind = "image" if suffix in _IMAGE_EXTENSIONS else "video" if suffix in _VIDEO_EXTENSIONS else "audio" if suffix in _AUDIO_EXTENSIONS else "document"
        result.append({"path": str(path.relative_to(staging)), "kind": kind})
    return result


def _media(payload: dict[str, Any]) -> list[MediaAsset]:
    staging = Path(str(payload.get("_staging_dir") or ""))
    assets: list[MediaAsset] = []
    remote_urls = _remote_urls(payload)
    for entry in payload.get("files", []):
        if not isinstance(entry, dict) or entry.get("kind") == "document":
            continue
        candidate = (staging / str(entry.get("path", ""))).resolve()
        try:
            candidate.relative_to(staging.resolve())
        except ValueError:
            continue
        kind = str(entry.get("kind"))
        assets.append(MediaAsset(
            kind=kind,
            url=next(iter(remote_urls.get(kind, [])), ""),
            local_path=str(candidate),
            status="complete",
        ))
    return assets


def _remote_urls(payload: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {"image": [], "video": [], "audio": []}
    records = payload.get("metadata") if isinstance(payload.get("metadata"), list) else []
    fallback = payload.get("fallback") if isinstance(payload.get("fallback"), dict) else {}
    for entry in fallback.get("entries", []):
        if not isinstance(entry, dict):
            continue
        candidate = _text(entry.get("url") or entry.get("webpage_url"))
        if candidate:
            result["video"].append(candidate)
    for record in records:
        for candidate in _walk_urls(record):
            lower = candidate.lower()
            if any(token in lower for token in (".mp4", ".m3u8", "video")):
                result["video"].append(candidate)
            elif any(token in lower for token in (".m4a", ".mp3", "audio")):
                result["audio"].append(candidate)
            elif any(token in lower for token in (".jpg", ".jpeg", ".png", ".webp", "image")):
                result["image"].append(candidate)
    return result


def _walk_urls(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [item for nested in value.values() for item in _walk_urls(nested)]
    if isinstance(value, list):
        return [item for nested in value for item in _walk_urls(nested)]
    text = _text(value)
    return [text] if text.startswith(("http://", "https://")) else []


def _article_html(payload: dict[str, Any], article: dict[str, Any]) -> str:
    direct = _text(article.get("html"))
    if direct:
        return direct
    staging = Path(str(payload.get("_staging_dir") or ""))
    for entry in payload.get("files", []):
        if isinstance(entry, dict) and str(entry.get("path", "")).lower().endswith(('.html', '.htm')):
            path = staging / str(entry["path"])
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                return ""
    return ""


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(html.unescape(data.strip()))


def _html_text(value: str) -> str:
    if not value:
        return ""
    parser = _HTMLText()
    parser.feed(value)
    return "\n\n".join(parser.parts)


def _expects_video(records: list[dict[str, Any]]) -> bool:
    text = json.dumps(records, ensure_ascii=False).lower()
    return any(token in text for token in ('"video"', 'video_info', 'video_url', 'amplify_video'))


def _expected_media_count(record: dict[str, Any]) -> int:
    value = record.get("media") or record.get("entities")
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        media = value.get("media")
        return len(media) if isinstance(media, list) else 0
    return 0


def _metrics(record: dict[str, Any]) -> dict[str, Any]:
    aliases = {
        "likes": ("favorite_count", "like_count", "likes"),
        "reposts": ("retweet_count", "repost_count", "retweets"),
        "replies": ("reply_count", "replies"),
        "quotes": ("quote_count", "quotes"),
        "views": ("view_count", "views"),
        "bookmarks": ("bookmark_count", "bookmarks"),
    }
    return {name: record[key] for name, keys in aliases.items() for key in keys if record.get(key) is not None}


def _relation(record: dict[str, Any], kind: str) -> dict[str, Any] | None:
    if kind == "reply":
        identifier = record.get("reply_id") or record.get("in_reply_to_status_id_str")
        url = record.get("reply_url")
    else:
        nested = _mapping(record.get("quoted_tweet") or record.get("quote"))
        identifier = record.get("quote_id") or nested.get("tweet_id") or nested.get("id")
        url = record.get("quote_url") or nested.get("url")
    if not identifier and not url:
        return None
    return {"content_id": _text(identifier), "url": _text(url) or (f"https://x.com/i/web/status/{identifier}" if identifier else "")}


def _id(record: dict[str, Any]) -> str:
    return _text(record.get("tweet_id") or record.get("id") or record.get("post_id"))


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return "" if value is None or isinstance(value, (dict, list)) else str(value).strip()


def _iso_date(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    if text.isdigit():
        return datetime.fromtimestamp(int(text), tz=timezone.utc).isoformat()
    return text


def _fallback_title(body: str, tweet_id: str) -> str:
    first = next((line.strip() for line in body.splitlines() if line.strip()), "")
    return first[:120] if first else f"X 帖子 {tweet_id}"


def _safe_error(value: str, fallback: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return (lines[-1] if lines else fallback)[:1000]


def _error_code(stderr: str) -> str:
    text = stderr.lower()
    if "429" in text or "rate limit" in text:
        return "rate_limited"
    if "login" in text or "authentication" in text or "cookie" in text:
        return "authentication_required"
    if "not found" in text or "private" in text or "deleted" in text or "unavailable" in text:
        return "deleted_or_private"
    return "upstream_failed"
