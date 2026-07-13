from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class MediaAsset:
    kind: str
    url: str = ""
    local_path: str = ""
    filename: str = ""
    status: str = "pending"
    error: str = ""


@dataclass
class ContentItem:
    schema_version: str
    platform: str
    content_type: str
    source_id: str
    source_url: str
    title: str
    body: str
    author: str
    published_at: str | None
    captured_at: str
    tags: list[str] = field(default_factory=list)
    media: list[MediaAsset] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    input_url: str = ""
    capture_status: str = "complete"
    canonical_url: str = ""
    reply_to: dict[str, Any] | None = None
    quoted_post: dict[str, Any] | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    article: dict[str, Any] = field(default_factory=dict)
    extractor_versions: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ContentItem":
        payload = dict(value)
        payload["media"] = [MediaAsset(**asset) for asset in payload.get("media", [])]
        for key in ("ingest_status", "errors"):
            payload.pop(key, None)
        return cls(**payload)
