from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from .models import ContentItem
from .xhs_adapter import cleanup_payload as cleanup_xhs, normalize_xhs, read_xhs


Reader = Callable[[str], dict[str, Any]]
Normalizer = Callable[[dict[str, Any], str], ContentItem]
Cleanup = Callable[[dict[str, Any]], None]
Canonicalizer = Callable[[str], tuple[str, str]]


@dataclass(frozen=True)
class PlatformAdapter:
    name: str
    reader: Reader
    normalizer: Normalizer
    cleanup: Cleanup
    canonicalizer: Canonicalizer


def adapter_for(url: str, *, xhs_reader: Reader | None = None) -> PlatformAdapter:
    if _host_is(url, {"xiaohongshu.com", "xhslink.com"}):
        return PlatformAdapter("xiaohongshu", xhs_reader or read_xhs, normalize_xhs, cleanup_xhs, _xhs_identity)
    if _host_is(url, {"x.com", "twitter.com"}):
        from .x_adapter import canonicalize_x_url, cleanup_payload, normalize_x, read_x

        return PlatformAdapter("x", read_x, normalize_x, cleanup_payload, canonicalize_x_url)
    from .platform_adapters import (
        cleanup_hybrid, cleanup_media, facebook_identity, normalize_facebook,
        normalize_reddit, normalize_tiktok, normalize_youtube, normalize_zhihu, read_facebook,
        read_reddit, read_tiktok, read_youtube, read_zhihu, reddit_identity, tiktok_identity, youtube_identity,
        zhihu_identity,
    )
    if _host_is(url, {"youtube.com", "youtu.be"}):
        return PlatformAdapter("youtube", read_youtube, normalize_youtube, cleanup_media, youtube_identity)
    if _host_is(url, {"tiktok.com"}):
        return PlatformAdapter("tiktok", read_tiktok, normalize_tiktok, cleanup_media, tiktok_identity)
    if _host_is(url, {"facebook.com", "fb.watch"}):
        return PlatformAdapter("facebook", read_facebook, normalize_facebook, cleanup_hybrid, facebook_identity)
    if _host_is(url, {"reddit.com", "redd.it"}):
        return PlatformAdapter("reddit", read_reddit, normalize_reddit, cleanup_hybrid, reddit_identity)
    if _host_is(url, {"zhihu.com"}):
        return PlatformAdapter("zhihu", read_zhihu, normalize_zhihu, cleanup_hybrid, zhihu_identity)
    from .web_adapter import canonicalize_web_url, cleanup_payload, normalize_web, read_web
    try:
        canonicalize_web_url(url)
    except Exception as exc:
        raise ValueError("unsupported_url") from exc
    return PlatformAdapter("web", read_web, normalize_web, cleanup_payload, canonicalize_web_url)


def _host_is(url: str, domains: set[str]) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme in {"http", "https"} and any(
        hostname == domain or hostname.endswith(f".{domain}") for domain in domains
    )


def _xhs_identity(url: str) -> tuple[str, str]:
    parts = [part for part in urlparse(url).path.split("/") if part]
    return (parts[-1] if parts else "", url)
