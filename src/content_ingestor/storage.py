from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import ContentItem, MediaAsset


def save_local_item(item: ContentItem, output: Path, *, force: bool = False) -> dict:
    month_dir = output / item.captured_at[:4] / item.captured_at[5:7] / item.platform
    target = month_dir / f"{safe_name(item.title)}--{safe_name(item.source_id)}"
    if target.exists() and not force and _existing_local_complete(target, item.content_type):
        return _result("already_saved", item, target)

    month_dir.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=month_dir))
    try:
        failures: list[str] = []
        persistent_media = [
            asset for asset in item.media
            if asset.local_path
        ]
        assets_dir = temp / "assets"
        if persistent_media:
            assets_dir.mkdir()
        for index, asset in enumerate(persistent_media, start=1):
            try:
                source = Path(asset.local_path)
                if not asset.local_path or not source.is_file():
                    raise FileNotFoundError("downloaded media file is missing")
                extension = source.suffix.lower() or (".mp4" if asset.kind in {"video", "live"} else ".bin")
                if asset.kind == "video":
                    prefix = "video"
                elif asset.kind == "audio":
                    prefix = "audio"
                elif asset.kind == "live":
                    prefix = f"live-{index:03d}"
                else:
                    prefix = f"{index:03d}"
                filename = f"{prefix}{extension}"
                shutil.copy2(source, assets_dir / filename)
                asset.filename = f"assets/{filename}"
                asset.status = "saved"
            except Exception as exc:  # Preserve metadata even when a CDN asset fails.
                asset.status = "failed"
                asset.error = str(exc)
                failures.append(f"{asset.kind}: {exc}")

        status = "partial" if item.capture_status in {"media_partial", "metadata_only"} else "success"
        required_kinds = _required_kinds(item.platform, item.content_type, item.media)
        if failures or any(not any(m.kind == kind for m in persistent_media) for kind in required_kinds):
            status = "partial"
            for kind in required_kinds:
                if not any(m.kind == kind for m in persistent_media):
                    failures.append(f"required {kind} media was not present in the parser response")
        if status == "partial" and item.capture_status and not failures:
            failures.append(f"capture status: {item.capture_status}")

        metadata = item.to_dict()
        for media in metadata["media"]:
            media.pop("local_path", None)
        metadata["ingest_status"] = status
        metadata["errors"] = failures
        (temp / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (temp / "index.md").write_text(_markdown(item, status, failures), encoding="utf-8")

        if target.exists():
            shutil.rmtree(target)
        os.replace(temp, target)
        return _result(status, item, target, failures)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


# Compatibility alias for callers that still use the v0.1 storage API.
save_item = save_local_item


def find_local_item(output: Path, source_url: str) -> ContentItem | None:
    if not output.is_dir():
        return None
    url_id = _source_id_from_url(source_url)
    for metadata_path in output.rglob("metadata.json"):
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            media_payload = payload.get("media") if isinstance(payload.get("media"), list) else []
            if payload.get("ingest_status") != "success":
                continue
            if source_url not in {payload.get("source_url"), payload.get("input_url")} and (
                not url_id or url_id != payload.get("source_id")
            ):
                continue
            target = metadata_path.parent
            if not _existing_local_complete(target, str(payload["content_type"])):
                continue
            item = ContentItem.from_dict(payload)
            item.published_at = _cached_iso_date(item.published_at)
            for asset in item.media:
                if asset.filename:
                    saved_path = _saved_asset_path(target, asset.filename)
                    asset.local_path = str(saved_path) if saved_path is not None else ""
            _restore_media_urls(item)
            return item
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return None


def safe_name(value: str, limit: int = 80) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "-", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" .-")
    return (normalized or "untitled")[:limit].rstrip(" .-")


def _markdown(item: ContentItem, status: str, errors: list[str]) -> str:
    lines = [
        f"# {item.title}",
        "",
        f"- 作者：{item.author}",
        f"- 平台：{_platform_label(item.platform)}",
        f"- 类型：{_type_label(item.content_type)}",
        f"- 发布时间：{item.published_at or '未知'}",
        f"- 来源：[{item.source_url}]({item.source_url})",
        f"- 采集状态：{status}",
        "",
        item.body,
    ]
    saved_images = [asset for asset in item.media if asset.kind == "image" and asset.filename]
    if saved_images:
        lines.extend(["", "## 图片", ""])
        lines.extend(f"![{index}]({asset.filename})" for index, asset in enumerate(saved_images, 1))
    saved_videos = [asset for asset in item.media if asset.kind == "video" and asset.filename]
    if saved_videos:
        lines.extend(["", "## 视频", "", f"[本地视频文件]({saved_videos[0].filename})"])
    saved_lives = [asset for asset in item.media if asset.kind == "live" and asset.filename]
    if saved_lives:
        lines.extend(["", "## Live Photo", ""])
        lines.extend(f"[动态文件 {index}]({asset.filename})" for index, asset in enumerate(saved_lives, 1))
    saved_audio = [asset for asset in item.media if asset.kind == "audio" and asset.filename]
    if saved_audio:
        lines.extend(["", "## 音频", ""])
        lines.extend(f"[本地音频文件 {index}]({asset.filename})" for index, asset in enumerate(saved_audio, 1))
    if item.tags:
        lines.extend(["", "## 标签", "", " ".join(f"#{tag}" for tag in item.tags)])
    if errors:
        lines.extend(["", "## 采集提示", ""])
        lines.extend(f"- {error}" for error in errors)
    return "\n".join(lines).rstrip() + "\n"


def _result(status: str, item: ContentItem, target: Path, errors: list[str] | None = None) -> dict:
    return {
        "status": status,
        "platform": item.platform,
        "source_id": item.source_id,
        "source_url": item.source_url,
        "content_type": item.content_type,
        "capture_status": item.capture_status,
        "local_path": str(target.resolve()),
        "target_dir": str(target.resolve()),
        "errors": errors or [],
    }


def _type_label(content_type: str) -> str:
    return {
        "image": "图文", "video": "视频", "live_photo": "Live Photo",
        "post": "普通帖子", "long_post": "Long Post", "article": "Article",
    }.get(content_type, content_type)


def _platform_label(platform: str) -> str:
    return {"xiaohongshu": "小红书", "x": "X"}.get(platform, platform)


def _existing_local_complete(target: Path, content_type: str) -> bool:
    metadata_path = target / "metadata.json"
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if payload.get("ingest_status") != "success":
        return False
    media = payload.get("media") if isinstance(payload.get("media"), list) else []
    required_kinds = _required_kinds(str(payload.get("platform", "")), content_type, media)
    candidates = [asset for asset in media if isinstance(asset, dict) and asset.get("kind") in required_kinds]
    if not required_kinds:
        return True
    if not candidates:
        return False
    return all(
        any(
            asset.get("kind") == kind
            and asset.get("status") == "saved"
            and asset.get("filename")
            and (saved_path := _saved_asset_path(target, str(asset["filename"]))) is not None
            and saved_path.stat().st_size > 0
            for asset in candidates
        )
        for kind in required_kinds
    )


def _source_id_from_url(value: str) -> str:
    from urllib.parse import urlparse

    parts = [part for part in urlparse(value).path.split("/") if part]
    hostname = (urlparse(value).hostname or "").lower()
    if not parts:
        return ""
    if hostname == "xhslink.com" or hostname.endswith(".xhslink.com"):
        return parts[-1]
    if hostname == "xiaohongshu.com" or hostname.endswith(".xiaohongshu.com"):
        return parts[-1]
    if hostname in {"x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}:
        try:
            index = parts.index("status")
            return parts[index + 1] if parts[index + 1].isdigit() else ""
        except (ValueError, IndexError):
            return ""
    return ""


def _required_kinds(platform: str, content_type: str, media: list[MediaAsset] | list[dict]) -> set[str]:
    if platform == "xiaohongshu":
        return {"video"} if content_type == "video" else {"live"} if content_type == "live_photo" else set()
    kinds = {
        str(asset.kind if isinstance(asset, MediaAsset) else asset.get("kind", ""))
        for asset in media
        if isinstance(asset, (MediaAsset, dict))
    }
    return kinds & {"video", "audio", "live"}


def _restore_media_urls(item: ContentItem) -> None:
    content = item.raw.get("content") if isinstance(item.raw, dict) else None
    if not isinstance(content, dict):
        return
    downloads = iter(_metadata_urls(content.get("下载地址")))
    motions = iter(_metadata_urls(content.get("动图地址")))
    for asset in item.media:
        if asset.url:
            continue
        if asset.kind in {"image", "video"}:
            asset.url = next(downloads, "")
        elif asset.kind == "live":
            asset.url = next(motions, "")


def _saved_asset_path(target: Path, filename: str) -> Path | None:
    try:
        assets = (target / "assets").resolve()
        candidate = (target / filename).resolve()
        candidate.relative_to(assets)
        return candidate if candidate.is_file() else None
    except (OSError, ValueError):
        return None


def _metadata_urls(value: object) -> list[str]:
    values = value if isinstance(value, list) else str(value or "").split()
    return [str(entry).strip() for entry in values if entry and str(entry).strip() != "NaN"]


def _cached_iso_date(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.replace("_", "T", 1).replace("/", "-")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
    return parsed.isoformat()
