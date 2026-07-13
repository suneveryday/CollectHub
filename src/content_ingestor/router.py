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
    raise ValueError("unsupported_url")


def _host_is(url: str, domains: set[str]) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme in {"http", "https"} and any(
        hostname == domain or hostname.endswith(f".{domain}") for domain in domains
    )


def _xhs_identity(url: str) -> tuple[str, str]:
    parts = [part for part in urlparse(url).path.split("/") if part]
    return (parts[-1] if parts else "", url)
