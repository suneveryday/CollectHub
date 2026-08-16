from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import XHS_VERSION, cookie_file, xhs_home, xhs_python
from .doctor import validate_cookie_file
from .models import ContentItem, MediaAsset


class AdapterError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def read_xhs(url: str) -> dict[str, Any]:
    home = xhs_home()
    python = xhs_python()
    bridge = Path(__file__).resolve().with_name("xhs_downloader_bridge.py")
    if not python.is_file() or not home.is_dir() or not bridge.is_file():
        raise AdapterError(
            "dependency_missing",
            "CollectHub's XHS runtime is incomplete; rerun the CollectHub installer with --repair",
        )

    staging = Path(tempfile.mkdtemp(prefix="content-os-xhs-"))
    command = [
        str(python), str(bridge), "--upstream-root", str(home), "--url", url,
        "--staging", str(staging),
    ]
    cookie = cookie_file()
    if cookie.exists():
        valid, detail = validate_cookie_file(cookie)
        if not valid:
            shutil.rmtree(staging, ignore_errors=True)
            raise AdapterError("insecure_cookie_file", detail)
        command.extend(["--cookie-file", str(cookie)])

    try:
        proc = subprocess.run(command, text=True, capture_output=True, timeout=180, check=False)
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise AdapterError("parser_timeout", "XHS-Downloader did not finish within 180 seconds") from exc
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise AdapterError("dependency_failed", f"could not start XHS-Downloader: {exc}") from exc

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        detail = "XHS-Downloader bridge returned invalid JSON"
        if proc.returncode and proc.stderr.strip():
            detail = "XHS-Downloader bridge failed before returning JSON"
        raise AdapterError("invalid_parser_output", detail) from exc

    if not payload.get("ok"):
        shutil.rmtree(staging, ignore_errors=True)
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        raise AdapterError(
            str(error.get("code") or "parser_failed"),
            str(error.get("message") or "XHS-Downloader could not process the note"),
        )
    if payload.get("upstream_version") != XHS_VERSION:
        shutil.rmtree(staging, ignore_errors=True)
        raise AdapterError("version_mismatch", "XHS-Downloader bridge did not report pinned version 2.7")
    payload["_staging_dir"] = str(staging)
    return payload


def normalize_xhs(payload: dict[str, Any], source_url: str) -> ContentItem:
    content = payload.get("content")
    if not isinstance(content, dict) or not content:
        raise AdapterError("content_not_found", "XHS-Downloader returned no work data")

    source_id = _text(content.get("作品ID"))
    if not source_id:
        raise AdapterError("invalid_parser_output", "XHS-Downloader response is missing 作品ID")
    download_urls = _clean_urls(content.get("下载地址"))
    live_urls = _clean_urls(content.get("动图地址"))
    if _text(content.get("作品类型")) == "视频":
        content_type = "video"
    elif live_urls:
        content_type = "live_photo"
    else:
        content_type = "image"
    staging = Path(str(payload.get("_staging_dir") or ""))
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    media = _media_from_manifest(files, staging, content_type)
    if content_type == "video":
        for _ in range(max(0, len(download_urls) - sum(asset.kind == "video" for asset in media))):
            media.append(MediaAsset(kind="video"))
    else:
        for _ in range(max(0, len(download_urls) - sum(asset.kind == "image" for asset in media))):
            media.append(MediaAsset(kind="image"))
        for _ in range(max(0, len(live_urls) - sum(asset.kind == "live" for asset in media))):
            media.append(MediaAsset(kind="live"))
    _attach_media_urls(media, download_urls, live_urls, content_type)

    raw = {key: value for key, value in payload.items() if not key.startswith("_")}
    return ContentItem(
        schema_version="3",
        platform="xiaohongshu",
        content_type=content_type,
        source_id=source_id,
        source_url=_text(content.get("作品链接")) or source_url,
        title=_text(content.get("作品标题")) or f"小红书-{source_id}",
        body=_text(content.get("作品描述")),
        author=_text(content.get("作者昵称")) or "未知作者",
        published_at=_published_at(content.get("发布时间")),
        captured_at=datetime.now(timezone.utc).isoformat(),
        tags=_tags(content.get("作品标签")),
        media=media,
        raw=raw,
        input_url=source_url,
    )


def cleanup_payload(payload: dict[str, Any]) -> None:
    staging = payload.get("_staging_dir")
    if staging:
        shutil.rmtree(str(staging), ignore_errors=True)


def _media_from_manifest(files: list[Any], staging: Path, content_type: str) -> list[MediaAsset]:
    media: list[MediaAsset] = []
    for entry in files:
        if not isinstance(entry, dict) or not entry.get("path"):
            continue
        relative = Path(str(entry["path"]))
        candidate = (staging / relative).resolve()
        try:
            candidate.relative_to(staging.resolve())
        except ValueError:
            raise AdapterError("invalid_parser_output", "bridge file manifest escaped staging directory")
        if candidate.suffix.lower() in {".mp4", ".mov", ".m4v"}:
            kind = "video" if content_type == "video" else "live"
        else:
            kind = "image"
        media.append(MediaAsset(kind=kind, local_path=str(candidate)))
    return media


def _attach_media_urls(
    media: list[MediaAsset], download_urls: list[str], live_urls: list[str], content_type: str
) -> None:
    image_urls = iter(download_urls if content_type != "video" else [])
    video_urls = iter(download_urls if content_type == "video" else [])
    motion_urls = iter(live_urls)
    for asset in media:
        if asset.kind == "image":
            asset.url = next(image_urls, "")
        elif asset.kind == "video":
            asset.url = next(video_urls, "")
        elif asset.kind == "live":
            asset.url = next(motion_urls, "")


def _clean_urls(value: Any) -> list[str]:
    return [text for item in _as_list(value) if (text := _text(item)) and text != "NaN"]


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return value.split() if value else []
    return []


def _published_at(value: Any) -> str | None:
    text = _text(value)
    if not text or text in {"未知", "Unknown"}:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("/", "-"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
    return parsed.isoformat()


def _tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    return [item for item in _text(value).split() if item]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()
