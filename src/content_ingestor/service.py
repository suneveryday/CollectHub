from __future__ import annotations

from pathlib import Path
from typing import Callable
from datetime import datetime, timezone
from .models import ContentItem
from .notion import NotionClient, NotionError
from .router import adapter_for
from .storage import find_local_item, save_local_item
from .xhs_adapter import AdapterError


def ingest_urls(
    urls: list[str],
    output: Path,
    *,
    force: bool = False,
    reader: Callable[[str], dict] | None = None,
    notion: NotionClient | None = None,
    sync: str | None = None,
    auto_video_notion: bool = False,
) -> list[dict]:
    results: list[dict] = []
    owned_notion: NotionClient | None = None
    for url in urls:
        payload: dict | None = None
        local_path = ""
        cleanup: Callable[[dict], None] | None = None
        adapter = None
        source_id = ""
        canonical_url = url
        try:
            try:
                adapter = adapter_for(url)
            except ValueError as exc:
                raise AdapterError("unsupported_url", "no content adapter supports this URL") from exc
            cleanup = adapter.cleanup
            source_id, canonical_url = adapter.canonicalizer(url)
            item = None if force else (find_local_item(output, url) or find_local_item(output, canonical_url))
            if item is None:
                payload = (reader or adapter.reader)(url)
                item = adapter.normalizer(payload, url)
            _validate_media(item)
            local_result = save_local_item(item, output, force=force)
            local_path = local_result["local_path"]
            if local_result["status"] == "partial":
                item.capture_status = "media_partial"
                local_result["capture_status"] = item.capture_status
            should_sync_notion = sync == "notion" or (
                auto_video_notion
                and item.platform in {"youtube", "tiktok"}
                and item.content_type == "video"
            )
            if should_sync_notion:
                try:
                    client = notion
                    if client is None:
                        if owned_notion is None:
                            owned_notion = NotionClient()
                        client = owned_notion
                    notion_local_path = (
                        ""
                        if item.platform in {"youtube", "tiktok"}
                        and item.capture_policy == "metadata_subtitles"
                        else local_path
                    )
                    synced = client.save_item(item, local_path=notion_local_path, force=force)
                    notion_result = {
                        "status": synced["status"],
                        "url": synced.get("notion_url", ""),
                    }
                    local_result["sync"] = {"notion": notion_result}
                    if notion_result["url"]:
                        local_result["notion_url"] = notion_result["url"]
                except NotionError as exc:
                    local_result["sync"] = {
                        "notion": {
                            "status": "failed",
                            "error": {"stage": exc.stage, "code": exc.code, "message": str(exc)},
                        }
                    }
            results.append(local_result)
        except AdapterError as exc:
            failure = _failure(url, "capture", exc.code, str(exc), local_path)
            if sync == "notion" and adapter is not None and adapter.name == "x" and source_id and exc.code in {
                "authentication_required", "rate_limited", "deleted_or_private"
            }:
                client = notion
                try:
                    if client is None:
                        if owned_notion is None:
                            owned_notion = NotionClient()
                        client = owned_notion
                    if hasattr(client, "save_failure"):
                        saved = client.save_failure(_failed_x_item(source_id, canonical_url, url, exc.code), str(exc))
                        failure["notion_url"] = saved.get("notion_url", "")
                except NotionError:
                    pass
            results.append(failure)
        except SaveError as exc:
            results.append(_failure(url, exc.stage, exc.code, str(exc), local_path))
        except NotionError as exc:
            results.append(_failure(url, exc.stage, exc.code, str(exc), local_path))
        except OSError as exc:
            results.append(_failure(url, "local_media", "storage_failed", str(exc), local_path))
        finally:
            if payload is not None and cleanup is not None:
                cleanup(payload)
    if owned_notion is not None:
        owned_notion.close()
    return results


class SaveError(RuntimeError):
    def __init__(self, stage: str, code: str, message: str):
        super().__init__(message)
        self.stage = stage
        self.code = code


def _validate_media(item: ContentItem) -> None:
    supported_types = {
        "xiaohongshu": {"image", "video", "live_photo"},
        "x": {"post", "long_post", "article"},
        "zhihu": {"answer", "article", "post", "video"},
        "youtube": {"video"},
        "tiktok": {"post", "image", "video"},
        "facebook": {"post", "image", "video"},
        "web": {"webpage", "article"},
    }.get(item.platform, set())
    if item.content_type not in supported_types:
        raise SaveError("type_detection", "content_type_unknown", "content type could not be determined")
    if item.capture_policy == "metadata_subtitles":
        # Video/audio absence is intentional. Images and subtitle sidecars are best effort.
        return
    if item.platform in {"x", "web", "zhihu", "facebook"}:
        if item.capture_status == "metadata_only" and not item.body and not item.article.get("plain_text"):
            return
        missing = [asset for asset in item.media if asset.status == "failed"]
        if missing:
            item.capture_status = "media_partial"
        return
    required_kind = "video" if item.content_type == "video" else "live" if item.content_type == "live_photo" else ""
    if required_kind:
        candidates = [asset for asset in item.media if asset.kind == required_kind]
        if not candidates or not any(asset.url for asset in candidates):
            code = "video_url_missing" if required_kind == "video" else "live_url_missing"
            raise SaveError("type_detection", code, f"no {required_kind} media URL was returned")
        if not any(
            asset.local_path
            and Path(asset.local_path).is_file()
            and Path(asset.local_path).stat().st_size > 0
            for asset in candidates
        ):
            raise SaveError("local_media", "local_media_missing", f"downloaded {required_kind} media is missing or empty")
    missing_images = [
        asset for asset in item.media
        if asset.kind == "image" and (not asset.local_path or not Path(asset.local_path).is_file())
    ]
    if missing_images:
        raise SaveError("capture", "image_media_missing", f"{len(missing_images)} image file(s) are missing")


def _failure(source_url: str, stage: str, code: str, message: str, local_path: str = "") -> dict:
    result = {
        "status": "failed",
        "source_url": source_url,
        "error": {"stage": stage, "code": code, "message": message},
    }
    if local_path:
        result["local_path"] = local_path
        result["target_dir"] = local_path
    return result


def _failed_x_item(source_id: str, canonical_url: str, input_url: str, capture_status: str) -> ContentItem:
    return ContentItem(
        schema_version="3",
        platform="x",
        content_type="post",
        source_id=source_id,
        source_url=canonical_url,
        canonical_url=canonical_url,
        input_url=input_url,
        title=f"X 帖子 {source_id}",
        body="",
        author="",
        published_at=None,
        captured_at=datetime.now(timezone.utc).isoformat(),
        capture_status=capture_status,
    )
