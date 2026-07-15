from __future__ import annotations

import re
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from .media_adapter import cleanup_media, extractor_versions, read_media, subtitle_text
from .models import ContentItem, MediaAsset
from .web_adapter import cleanup_payload as cleanup_web, normalize_web, read_web
from .web_fetch import fetch_image, fetch_json, normalize_public_url
from .xhs_adapter import AdapterError


def youtube_identity(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    parts = [p for p in parsed.path.split("/") if p]
    video_id = ""
    if host in {"youtu.be", "www.youtu.be"} and len(parts) == 1:
        video_id = parts[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif len(parts) == 2 and parts[0] in {"shorts", "live", "embed"}:
            video_id = parts[1]
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
        raise AdapterError("unsupported_url", "YouTube URL must identify one video, Short, or live replay")
    return video_id, f"https://www.youtube.com/watch?v={video_id}"


def tiktok_identity(url: str) -> tuple[str, str]:
    normalized = normalize_public_url(url)
    parsed = urlsplit(normalized)
    host = (parsed.hostname or "").lower()
    if host in {"vm.tiktok.com", "vt.tiktok.com"}:
        return _hash_id(normalized), normalized
    match = re.search(r"/@[^/]+/(?:video|photo)/(\d+)", parsed.path)
    if not match:
        raise AdapterError("unsupported_url", "TikTok URL must identify one video or photo post")
    return match.group(1), normalized


def facebook_identity(url: str) -> tuple[str, str]:
    normalized = normalize_public_url(url)
    parsed = urlsplit(normalized)
    host = (parsed.hostname or "").lower()
    if host == "fb.watch":
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) == 1:
            return _hash_id(normalized), normalized
    query = parse_qs(parsed.query)
    patterns = [r"/(?:reel|videos|posts|story\.php|permalink\.php)/?([^/?]+)?", r"/watch/?$"]
    if not any(re.search(pattern, parsed.path) for pattern in patterns) and not query.get("story_fbid") and not (parsed.path == "/video.php" and query.get("v")):
        raise AdapterError("unsupported_url", "Facebook URL must identify one post, video, Reel, Story, or permalink")
    candidate = query.get("story_fbid", [""])[0] or query.get("v", [""])[0] or next((p for p in reversed(parsed.path.split("/")) if p and p not in {"reel", "videos", "posts"}), "")
    return candidate or _hash_id(normalized), normalized


def zhihu_identity(url: str) -> tuple[str, str]:
    normalized = normalize_public_url(url)
    path = urlsplit(normalized).path
    patterns = [
        (r"/question/\d+/answer/(\d+)", "answer"),
        (r"/p/(\d+)", "article"),
        (r"/pin/(\d+)", "post"),
        (r"/zvideo/(\d+)", "video"),
    ]
    for pattern, _ in patterns:
        match = re.fullmatch(pattern + r"/?", path)
        if match:
            return match.group(1), normalized
    raise AdapterError("unsupported_url", "Zhihu URL must identify one answer, article, pin, or zvideo")


def read_youtube(url: str) -> dict[str, Any]:
    _, canonical = youtube_identity(url)
    return read_media(canonical, "youtube")


def read_tiktok(url: str) -> dict[str, Any]:
    tiktok_identity(url)
    if "/photo/" not in urlsplit(url).path:
        try:
            return _read_tiktok_oembed(url)
        except AdapterError:
            pass
    try:
        return read_media(url, "tiktok", gallery_first=True)
    except AdapterError as media_error:
        try:
            return _read_tiktok_oembed(url)
        except AdapterError:
            raise media_error


def read_facebook(url: str) -> dict[str, Any]:
    facebook_identity(url)
    path = urlsplit(url).path
    gallery_first = not any(token in path for token in ("/reel/", "/videos/", "/video.php", "/watch"))
    try:
        return read_media(url, "facebook", gallery_first=gallery_first)
    except AdapterError as media_error:
        try:
            page = read_web(url, platform="facebook")
            page["_media_error"] = media_error.code
            return page
        except AdapterError as web_error:
            if web_error.code == "authentication_required":
                raise web_error
            raise media_error


def read_zhihu(url: str) -> dict[str, Any]:
    _, canonical = zhihu_identity(url)
    if "/zvideo/" in urlsplit(canonical).path:
        return read_media(canonical, "zhihu")
    return read_web(canonical, platform="zhihu")


def normalize_youtube(payload: dict[str, Any], input_url: str) -> ContentItem:
    source_id, canonical = youtube_identity(input_url)
    return _normalize_media(payload, input_url, "youtube", source_id, canonical, "video")


def normalize_tiktok(payload: dict[str, Any], input_url: str) -> ContentItem:
    source_id, canonical = tiktok_identity(input_url)
    content_type = "image" if "/photo/" in urlsplit(canonical).path else "video"
    return _normalize_media(payload, input_url, "tiktok", source_id, canonical, content_type)


def normalize_facebook(payload: dict[str, Any], input_url: str) -> ContentItem:
    source_id, canonical = facebook_identity(input_url)
    if "html" in payload:
        item = normalize_web(payload, input_url)
        item.platform, item.source_id, item.source_url, item.canonical_url = "facebook", source_id, canonical, canonical
        item.content_type = "post"
        return item
    path = urlsplit(canonical).path
    content_type = "video" if any(token in path for token in ("/reel/", "/videos/", "/video.php", "/watch")) else "post"
    return _normalize_media(payload, input_url, "facebook", source_id, canonical, content_type)


def normalize_zhihu(payload: dict[str, Any], input_url: str) -> ContentItem:
    source_id, canonical = zhihu_identity(input_url)
    path = urlsplit(canonical).path
    if "/zvideo/" in path:
        return _normalize_media(payload, input_url, "zhihu", source_id, canonical, "video")
    item = normalize_web(payload, input_url)
    item.platform, item.source_id, item.source_url, item.canonical_url = "zhihu", source_id, canonical, canonical
    item.content_type = "answer" if "/answer/" in path else "article" if "/p/" in path else "post"
    return item


def cleanup_hybrid(payload: dict[str, Any]) -> None:
    cleanup_web(payload) if "html" in payload else cleanup_media(payload)


def _normalize_media(payload: dict[str, Any], input_url: str, platform: str, source_id: str, canonical: str, fallback_type: str) -> ContentItem:
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    actual_id = str(info.get("id") or source_id)
    webpage = str(info.get("webpage_url") or canonical)
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    media: list[MediaAsset] = []
    subtitles: list[dict[str, Any]] = []
    transcripts: list[str] = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        path = Path(str(entry.get("path") or ""))
        if not path.is_file():
            continue
        kind = str(entry.get("kind") or "")
        media.append(MediaAsset(kind=kind, local_path=str(path), status="complete"))
        if kind == "subtitle":
            language = _subtitle_language(path.name, actual_id)
            text = subtitle_text(path)
            subtitles.append({"language": language, "format": path.suffix.lstrip("."), "automatic": language in (info.get("automatic_captions") or {})})
            if text:
                transcripts.append(text)
    description = str(info.get("description") or info.get("fulltitle") or "").strip()
    body = description
    if transcripts:
        body += ("\n\n## 字幕\n\n" if body else "") + "\n\n".join(transcripts)
    title = str(info.get("title") or info.get("fulltitle") or "").strip() or f"{platform}-{actual_id}"
    content_type = fallback_type
    published = str(info.get("timestamp") or info.get("release_timestamp") or "")
    published_at = datetime.fromtimestamp(int(published), tz=timezone.utc).isoformat() if published.isdigit() else str(info.get("upload_date") or "") or None
    return ContentItem(
        schema_version="3", platform=platform, content_type=content_type, source_id=actual_id,
        source_url=webpage, canonical_url=webpage, input_url=input_url, title=title, body=body,
        author=str(info.get("uploader") or info.get("channel") or info.get("creator") or ""),
        published_at=published_at, captured_at=datetime.now(timezone.utc).isoformat(),
        tags=[str(tag) for tag in info.get("tags", []) if str(tag).strip()] if isinstance(info.get("tags"), list) else [],
        media=media, raw={"duration": info.get("duration"), "chapters": info.get("chapters") or [], "live_status": info.get("live_status")},
        capture_policy="metadata_subtitles", subtitles=subtitles, extractor_versions=extractor_versions(),
    )


def _subtitle_language(name: str, source_id: str) -> str:
    stem = Path(name).stem
    prefix = source_id + "."
    return stem[len(prefix):] if stem.startswith(prefix) else stem.rsplit(".", 1)[-1]


def _hash_id(value: str) -> str:
    import hashlib
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def _read_tiktok_oembed(url: str) -> dict[str, Any]:
    endpoint = "https://www.tiktok.com/oembed?" + urlencode({"url": url})
    response = fetch_json(endpoint)
    try:
        data = json.loads(response.content)
    except (ValueError, TypeError) as exc:
        raise AdapterError("invalid_parser_output", "TikTok oEmbed returned invalid JSON") from exc
    if not isinstance(data, dict) or not data.get("embed_product_id"):
        raise AdapterError("content_not_found", "TikTok oEmbed returned no public post metadata")
    staging = Path(tempfile.mkdtemp(prefix="collecthub-tiktok-oembed-"))
    files: list[dict[str, str]] = []
    thumbnail_url = str(data.get("thumbnail_url") or "")
    if thumbnail_url:
        try:
            thumbnail = fetch_image(thumbnail_url)
            suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(thumbnail.content_type, ".img")
            path = staging / f"{data['embed_product_id']}{suffix}"
            path.write_bytes(thumbnail.content)
            files.append({"path": str(path), "kind": "image"})
        except AdapterError:
            pass
    return {
        "info": {
            "id": str(data["embed_product_id"]), "title": str(data.get("title") or ""),
            "description": str(data.get("title") or ""), "uploader": str(data.get("author_name") or ""),
            "webpage_url": url,
        },
        "gallery": [], "files": files, "_staging_dir": str(staging), "oembed": True,
    }
