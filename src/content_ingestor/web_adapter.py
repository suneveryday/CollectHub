from __future__ import annotations

import hashlib
import time
import shutil
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from trafilatura import bare_extraction

from .config import TRAFILATURA_VERSION, platform_cookie_file
from .doctor import validate_cookie_file
from .models import ContentItem, MediaAsset
from .web_fetch import MAX_MEDIA_BYTES, fetch_html, fetch_image, normalize_public_url
from .xhs_adapter import AdapterError


class _Images(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta" and values.get("property") in {"og:image", "twitter:image"}:
            self.urls.append(values.get("content", ""))
        elif tag.lower() == "img":
            self.urls.append(values.get("src") or values.get("data-src") or "")


def canonicalize_web_url(url: str) -> tuple[str, str]:
    canonical = normalize_public_url(url)
    return hashlib.sha256(canonical.encode()).hexdigest()[:24], canonical


def read_web(url: str, *, platform: str = "") -> dict[str, Any]:
    headers = _cookie_headers(url, platform) if platform else None
    result = fetch_html(url, headers=headers)
    html = result.content.decode("utf-8", errors="replace")
    staging = Path(tempfile.mkdtemp(prefix="collecthub-web-"))
    parser = _Images()
    parser.feed(html)
    files: list[dict[str, str]] = []
    total = 0
    seen: set[str] = set()
    for index, raw_url in enumerate(parser.urls, 1):
        if len(files) >= 20:
            break
        try:
            image_url = normalize_public_url(urljoin(result.url, raw_url)) if raw_url else ""
            if not image_url or image_url in seen:
                continue
            seen.add(image_url)
            image = fetch_image(image_url)
            if total + len(image.content) > MAX_MEDIA_BYTES:
                break
            total += len(image.content)
            suffix = _image_suffix(image.content_type, urlsplit(image.url).path)
            path = staging / f"image-{index:03d}{suffix}"
            path.write_bytes(image.content)
            files.append({"path": str(path), "url": image.url, "kind": "image"})
        except AdapterError:
            continue
    return {"url": result.url, "html": html, "files": files, "_staging_dir": str(staging)}


def normalize_web(payload: dict[str, Any], input_url: str) -> ContentItem:
    canonical = normalize_public_url(str(payload.get("url") or input_url))
    source_id, _ = canonicalize_web_url(canonical)
    html = str(payload.get("html") or "")
    extracted = bare_extraction(
        html, url=canonical, output_format="markdown", with_metadata=True,
        include_links=True, include_images=False, favor_precision=True,
    )
    if extracted is None:
        raise AdapterError("content_not_found", "no readable page content was found")
    data = extracted.as_dict() if hasattr(extracted, "as_dict") else dict(extracted)
    body = str(data.get("text") or data.get("raw_text") or "").strip()
    title = str(data.get("title") or "").strip() or urlsplit(canonical).hostname or "网页"
    if not body:
        raise AdapterError("content_not_found", "no readable page body was found")
    media = [
        MediaAsset(kind="image", url=str(entry.get("url") or ""), local_path=str(entry["path"]), status="complete")
        for entry in payload.get("files", []) if isinstance(entry, dict) and entry.get("path")
    ]
    return ContentItem(
        schema_version="3", platform="web", content_type="webpage", source_id=source_id,
        source_url=canonical, canonical_url=canonical, input_url=input_url, title=title, body=body,
        author=str(data.get("author") or "").strip(), published_at=str(data.get("date") or "").strip() or None,
        captured_at=datetime.now(timezone.utc).isoformat(), media=media, capture_policy="static_html",
        raw={"hostname": urlsplit(canonical).hostname or ""},
        extractor_versions={"trafilatura": TRAFILATURA_VERSION},
    )


def cleanup_payload(payload: dict[str, Any]) -> None:
    staging = payload.get("_staging_dir")
    if staging:
        shutil.rmtree(str(staging), ignore_errors=True)


def _image_suffix(content_type: str, path: str) -> str:
    by_type = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "image/avif": ".avif"}
    suffix = Path(path).suffix.lower()
    return by_type.get(content_type, suffix if suffix in set(by_type.values()) else ".img")


def _cookie_headers(url: str, platform: str) -> dict[str, str] | None:
    path = platform_cookie_file(platform)
    if not path.exists():
        return None
    valid, detail = validate_cookie_file(path)
    if not valid:
        raise AdapterError("insecure_cookie_file", detail)
    host = (urlsplit(url).hostname or "").lower()
    secure_request = urlsplit(url).scheme == "https"
    values: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise AdapterError("cookie_unreadable", "the configured Cookie file could not be read") from exc
    for raw in lines:
        line = raw[10:] if raw.startswith("#HttpOnly_") else raw
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            continue
        domain, _, cookie_path, secure, expires, name, value = fields
        domain = domain.lstrip(".").lower()
        if not (host == domain or host.endswith("." + domain)):
            continue
        if secure.upper() == "TRUE" and not secure_request:
            continue
        if expires.isdigit() and int(expires) and int(expires) < int(time.time()):
            continue
        if not urlsplit(url).path.startswith(cookie_path or "/"):
            continue
        if name and "\r" not in value and "\n" not in value:
            values.append(f"{name}={value}")
    return {"Cookie": "; ".join(values)} if values else None
