from __future__ import annotations

import html
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


def reddit_identity(url: str) -> tuple[str, str]:
    normalized = normalize_public_url(url)
    parsed = urlsplit(normalized)
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    if host in {"redd.it", "www.redd.it"} and len(parts) == 1 and re.fullmatch(r"[A-Za-z0-9]+", parts[0]):
        source_id = parts[0].lower()
        return source_id, f"https://redd.it/{source_id}"
    match = re.fullmatch(
        r"/(?:r/[^/]+/|user/[^/]+/)?comments/([A-Za-z0-9]+)(?:/[^/]+)?/?",
        parsed.path,
    )
    if not match:
        raise AdapterError("unsupported_url", "Reddit URL must identify one post; communities, users, and feeds are not supported")
    source_id = match.group(1).lower()
    return source_id, f"https://www.reddit.com{parsed.path.rstrip('/')}"


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
    try:
        return read_media(canonical, "youtube")
    except AdapterError as media_error:
        try:
            return _read_oembed(
                "https://www.youtube.com/oembed?" + urlencode({"url": canonical, "format": "json"}),
                canonical,
                "youtube",
            )
        except AdapterError:
            raise media_error


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


def read_reddit(url: str) -> dict[str, Any]:
    _, canonical = reddit_identity(url)
    try:
        payload = _read_reddit_json(canonical)
    except AdapterError as json_error:
        if json_error.code == "invalid_parser_output":
            raise
        try:
            return read_media(canonical, "reddit")
        except AdapterError as media_error:
            try:
                return _read_oembed(
                    "https://www.reddit.com/oembed?" + urlencode({"url": canonical}),
                    canonical,
                    "reddit",
                )
            except AdapterError:
                try:
                    page = read_web(canonical, platform="reddit")
                    page["_reddit_error"] = json_error.code
                    return page
                except AdapterError:
                    raise media_error
    if payload.get("content_type") == "video":
        try:
            media = read_media(canonical, "reddit")
            payload["files"] = list(payload.get("files") or []) + list(media.get("files") or [])
            payload["info"] = {**(media.get("info") or {}), **(payload.get("info") or {})}
            payload["_staging_dirs"] = [payload.get("_staging_dir"), media.get("_staging_dir")]
        except AdapterError:
            pass
    return payload


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
        path = urlsplit(canonical).path
        item.content_type = "video" if any(token in path for token in ("/reel/", "/videos/", "/video.php", "/watch")) else "post"
        return item
    path = urlsplit(canonical).path
    content_type = "video" if any(token in path for token in ("/reel/", "/videos/", "/video.php", "/watch")) else "post"
    return _normalize_media(payload, input_url, "facebook", source_id, canonical, content_type)


def normalize_reddit(payload: dict[str, Any], input_url: str) -> ContentItem:
    source_id, canonical = reddit_identity(input_url)
    if "html" in payload:
        item = normalize_web(payload, input_url)
        item.platform, item.source_id, item.source_url, item.canonical_url = "reddit", source_id, canonical, canonical
        item.content_type = "post"
        return item
    content_type = str(payload.get("content_type") or "video")
    item = _normalize_media(payload, input_url, "reddit", source_id, canonical, content_type)
    item.source_url = item.canonical_url = canonical
    reddit = payload.get("reddit") if isinstance(payload.get("reddit"), dict) else {}
    community = str(reddit.get("community") or "")
    item.tags = [community] if community else []
    item.metrics = {
        key: value for key, value in {
            "score": reddit.get("score"),
            "upvote_ratio": reddit.get("upvote_ratio"),
            "comment_count": reddit.get("comment_count"),
        }.items() if value is not None
    }
    item.raw.update({
        "community": community,
        "external_url": str(reddit.get("external_url") or ""),
        "is_self": bool(reddit.get("is_self")),
    })
    if content_type != "video":
        item.capture_policy = "public_json"
        if content_type == "image" and not any(asset.kind == "image" for asset in item.media):
            item.capture_status = "media_partial"
    return item


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
    webpage = canonical
    extracted_webpage = str(info.get("webpage_url") or "")
    if extracted_webpage and platform in {"tiktok", "facebook"}:
        try:
            _, webpage = tiktok_identity(extracted_webpage) if platform == "tiktok" else facebook_identity(extracted_webpage)
        except AdapterError:
            pass
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
    embed_id = str(data["embed_product_id"])
    if not re.fullmatch(r"\d+", embed_id):
        raise AdapterError("invalid_parser_output", "TikTok oEmbed returned an invalid post ID")
    staging = Path(tempfile.mkdtemp(prefix="collecthub-tiktok-oembed-"))
    files: list[dict[str, str]] = []
    thumbnail_url = str(data.get("thumbnail_url") or "")
    if thumbnail_url:
        try:
            thumbnail = fetch_image(thumbnail_url)
            suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(thumbnail.content_type, ".img")
            path = staging / f"{embed_id}{suffix}"
            path.write_bytes(thumbnail.content)
            files.append({"path": str(path), "kind": "image"})
        except AdapterError:
            pass
    payload = {
        "info": {
            "id": embed_id, "title": str(data.get("title") or ""),
            "description": str(data.get("title") or ""), "uploader": str(data.get("author_name") or ""),
            "webpage_url": url,
        },
        "gallery": [], "files": files, "_staging_dir": str(staging), "oembed": True,
    }
    return payload


def _read_reddit_json(canonical: str) -> dict[str, Any]:
    response = fetch_json(
        canonical + ".json?raw_json=1",
        headers={"Accept": "application/json", "User-Agent": "CollectHub/1.2 (+https://github.com/suneveryday/CollectHub)"},
    )
    try:
        data = json.loads(response.content)
        post = data[0]["data"]["children"][0]["data"]
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise AdapterError("invalid_parser_output", "Reddit returned invalid public post JSON") from exc
    if not isinstance(post, dict) or not post.get("id"):
        raise AdapterError("content_not_found", "Reddit returned no public post")
    expected_id = reddit_identity(canonical)[0]
    source_id = str(post["id"]).lower()
    if not re.fullmatch(r"[a-z0-9]+", source_id) or source_id != expected_id:
        raise AdapterError("invalid_parser_output", "Reddit returned a mismatched post ID")
    title = str(post.get("title") or f"Reddit post {source_id}").strip()
    body = str(post.get("selftext") or "").strip()
    author = str(post.get("author") or "")
    if author == "[deleted]" and not body:
        raise AdapterError("deleted_or_private", "the Reddit post is deleted or unavailable")
    staging = Path(tempfile.mkdtemp(prefix="collecthub-reddit-"))
    image_urls = _reddit_image_urls(post)
    files: list[dict[str, str]] = []
    total = 0
    for index, image_url in enumerate(image_urls[:20], 1):
        try:
            image = fetch_image(image_url)
            if total + len(image.content) > 100 * 1024 * 1024:
                break
            total += len(image.content)
            suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp", "image/avif": ".avif"}.get(image.content_type)
            if not suffix:
                continue
            path = staging / f"{source_id}-{index:03d}{suffix}"
            path.write_bytes(image.content)
            files.append({"path": str(path), "kind": "image"})
        except AdapterError:
            continue
    reddit_video = post.get("secure_media", {}).get("reddit_video", {}) if isinstance(post.get("secure_media"), dict) else {}
    is_video = bool(post.get("is_video") or reddit_video)
    content_type = "video" if is_video else "image" if image_urls or post.get("is_gallery") or post.get("post_hint") == "image" else "post"
    created = post.get("created_utc")
    external_url = str(post.get("url_overridden_by_dest") or post.get("url") or "")
    if external_url.startswith(("https://www.reddit.com/", "https://reddit.com/")):
        external_url = ""
    payload: dict[str, Any] = {
        "info": {
            "id": source_id,
            "title": title,
            "description": body,
            "uploader": author,
            "timestamp": created,
            "webpage_url": canonical,
            "duration": reddit_video.get("duration") if isinstance(reddit_video, dict) else None,
        },
        "reddit": {
            "community": str(post.get("subreddit_name_prefixed") or ""),
            "score": post.get("score"),
            "upvote_ratio": post.get("upvote_ratio"),
            "comment_count": post.get("num_comments"),
            "external_url": external_url,
            "is_self": bool(post.get("is_self")),
        },
        "content_type": content_type,
        "files": files,
        "_staging_dir": str(staging),
    }
    return payload


def _reddit_image_urls(post: dict[str, Any]) -> list[str]:
    result: list[str] = []
    metadata = post.get("media_metadata") if isinstance(post.get("media_metadata"), dict) else {}
    gallery = post.get("gallery_data") if isinstance(post.get("gallery_data"), dict) else {}
    for entry in gallery.get("items", []) if isinstance(gallery.get("items"), list) else []:
        media_id = str(entry.get("media_id") or "") if isinstance(entry, dict) else ""
        source = metadata.get(media_id, {}).get("s", {}) if isinstance(metadata.get(media_id), dict) else {}
        url = source.get("u") if isinstance(source, dict) else ""
        if url:
            result.append(html.unescape(str(url)))
    if not result:
        destination = str(post.get("url_overridden_by_dest") or "")
        if re.search(r"\.(?:jpe?g|png|gif|webp|avif)(?:\?|$)", destination, re.IGNORECASE):
            result.append(destination)
        preview = post.get("preview") if isinstance(post.get("preview"), dict) else {}
        images = preview.get("images") if isinstance(preview.get("images"), list) else []
        if images:
            source = images[0].get("source", {}) if isinstance(images[0], dict) else {}
            url = source.get("url") if isinstance(source, dict) else ""
            if url and url not in result:
                result.append(html.unescape(str(url)))
    return result


def _read_oembed(endpoint: str, source_url: str, platform: str) -> dict[str, Any]:
    response = fetch_json(endpoint)
    try:
        data = json.loads(response.content)
    except (ValueError, TypeError) as exc:
        raise AdapterError("invalid_parser_output", f"{platform} oEmbed returned invalid JSON") from exc
    if not isinstance(data, dict) or not data.get("title"):
        raise AdapterError("content_not_found", f"{platform} oEmbed returned no public metadata")
    if platform == "youtube":
        source_id = youtube_identity(source_url)[0]
    elif platform == "reddit":
        source_id = reddit_identity(source_url)[0]
    else:
        source_id = _hash_id(source_url)
    staging = Path(tempfile.mkdtemp(prefix=f"collecthub-{platform}-oembed-"))
    files: list[dict[str, str]] = []
    thumbnail_url = str(data.get("thumbnail_url") or "")
    if thumbnail_url:
        try:
            thumbnail = fetch_image(thumbnail_url)
            suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(thumbnail.content_type)
            if suffix:
                path = staging / f"{source_id}{suffix}"
                path.write_bytes(thumbnail.content)
                files.append({"path": str(path), "kind": "image"})
        except AdapterError:
            pass
    payload: dict[str, Any] = {
        "info": {
            "id": source_id,
            "title": str(data.get("title") or ""),
            "uploader": str(data.get("author_name") or ""),
            "webpage_url": source_url,
        },
        "files": files,
        "_staging_dir": str(staging),
        "oembed": True,
    }
    if platform == "reddit":
        payload["content_type"] = "post"
    return payload
