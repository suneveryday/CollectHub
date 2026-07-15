from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .config import DENO_VERSION, GALLERY_DL_VERSION, YT_DLP_VERSION, deno_binary, media_python, platform_cookie_file
from .doctor import validate_cookie_file
from .xhs_adapter import AdapterError


_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
_SUBTITLE_EXTENSIONS = {".vtt", ".srt", ".ass", ".lrc", ".json3"}


def read_media(url: str, platform: str, *, gallery_first: bool = False) -> dict[str, Any]:
    python = media_python()
    if not python.is_file():
        raise AdapterError("dependency_missing", "media runtime is not installed; rerun the CollectHub installer with --repair")
    staging = Path(tempfile.mkdtemp(prefix=f"collecthub-{platform}-"))
    cookie = _secure_cookie(platform)
    gallery: list[dict[str, Any]] = []
    gallery_error = ""
    if gallery_first:
        gallery, gallery_error = _run_gallery(python, staging, url, cookie)
    info, ytdlp_error = _run_ytdlp(python, staging, url, cookie)
    files = _manifest(staging)
    if not info and not files:
        shutil.rmtree(staging, ignore_errors=True)
        message = ytdlp_error or gallery_error or "no content metadata was returned"
        raise AdapterError(_error_code(message), _safe_error(message))
    return {
        "info": info, "gallery": gallery, "files": files,
        "gallery_error": gallery_error, "ytdlp_error": ytdlp_error,
        "_staging_dir": str(staging),
    }


def cleanup_media(payload: dict[str, Any]) -> None:
    if payload.get("_staging_dir"):
        shutil.rmtree(str(payload["_staging_dir"]), ignore_errors=True)


def subtitle_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if path.suffix.lower() == ".json3":
        try:
            data = json.loads(text)
            return "\n".join(
                "".join(str(segment.get("utf8") or "") for segment in event.get("segs", []))
                for event in data.get("events", []) if isinstance(event, dict)
            ).strip()
        except (ValueError, TypeError):
            return ""
    lines: list[str] = []
    last = ""
    for raw in text.splitlines():
        line = re.sub(r"<[^>]+>", "", raw).strip()
        if not line or line.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")) or "-->" in line or line.isdigit():
            continue
        line = re.sub(r"^\[[^]]+\]\s*", "", line)
        if line and line != last:
            lines.append(line)
            last = line
    return "\n".join(lines)


def extractor_versions() -> dict[str, str]:
    return {"gallery-dl": GALLERY_DL_VERSION, "yt-dlp": YT_DLP_VERSION, "deno": DENO_VERSION}


def _run_ytdlp(python: Path, staging: Path, url: str, cookie: Path | None) -> tuple[dict[str, Any], str]:
    command = [
        str(python), "-m", "yt_dlp", "--no-playlist", "--skip-download", "--no-simulate", "--write-thumbnail",
        "--ignore-errors", "--write-subs", "--write-auto-subs", "--sub-langs", "en,zh,zh-Hans,zh-Hant", "--sub-format", "vtt",
        "--no-progress", "--no-warnings", "--print-json", "--paths", str(staging),
        "-o", "%(id)s.%(ext)s",
    ]
    deno = deno_binary()
    if deno.is_file():
        command.extend(["--js-runtimes", f"deno:{deno}"])
    if cookie:
        command.extend(["--cookies", str(cookie)])
    command.extend(["--", url])
    try:
        proc = subprocess.run(command, text=True, capture_output=True, check=False, timeout=120)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {}, f"yt-dlp failed: {type(exc).__name__}"
    values = _json_lines(proc.stdout)
    return (values[-1] if values else {}), (proc.stderr.strip() if proc.returncode else "")


def _run_gallery(python: Path, staging: Path, url: str, cookie: Path | None) -> tuple[list[dict[str, Any]], str]:
    config = staging / ".gallery-dl.json"
    config.write_text(json.dumps({"extractor": {"base-directory": str(staging), "directory": [], "postprocessors": [{"name": "metadata", "mode": "json", "filename": "{id}.metadata.json"}]}}), encoding="utf-8")
    command = [
        str(python), "-m", "gallery_dl", "--config", str(config), "--quiet", "--range", "1",
        "--filter", "extension not in ('mp4','webm','m4v','mov','mkv','mp3','m4a','aac','opus','ogg')",
    ]
    if cookie:
        command.extend(["--cookies", str(cookie)])
    command.extend(["--", url])
    try:
        proc = subprocess.run(command, text=True, capture_output=True, check=False, timeout=120)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [], f"gallery-dl failed: {type(exc).__name__}"
    records: list[dict[str, Any]] = []
    for path in staging.glob("*.metadata.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                records.append(value)
        except (OSError, ValueError):
            continue
    return records, (proc.stderr.strip() if proc.returncode else "")


def _manifest(staging: Path) -> list[dict[str, str]]:
    result = []
    for path in sorted(staging.rglob("*")):
        if not path.is_file() or path.name.startswith(".gallery") or path.name.endswith(".metadata.json") or path.suffix in {".part", ".ytdl"}:
            continue
        suffix = path.suffix.lower()
        if suffix in _IMAGE_EXTENSIONS:
            kind = "image"
        elif suffix in _SUBTITLE_EXTENSIONS:
            kind = "subtitle"
        else:
            continue
        result.append({"path": str(path), "kind": kind})
    return result


def _secure_cookie(platform: str) -> Path | None:
    path = platform_cookie_file(platform)
    if not path.exists():
        return None
    valid, detail = validate_cookie_file(path)
    if not valid:
        raise AdapterError("insecure_cookie_file", detail)
    return path


def _json_lines(value: str) -> list[dict[str, Any]]:
    result = []
    for line in value.splitlines():
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            result.append(parsed)
    return result


def _error_code(value: str) -> str:
    lower = value.lower()
    if any(token in lower for token in ("login", "cookie", "authentication", "sign in", "not logged")):
        return "authentication_required"
    if "429" in lower or "rate limit" in lower:
        return "rate_limited"
    if any(token in lower for token in ("private", "unavailable", "deleted", "not found")):
        return "deleted_or_private"
    return "upstream_failed"


def _safe_error(value: str) -> str:
    # Tool stderr can contain request headers or cookie paths. Return a stable classified message only.
    code = _error_code(value)
    return {
        "authentication_required": "the platform requires authentication; configure a private Netscape cookie file",
        "rate_limited": "the platform rate-limited the request",
        "deleted_or_private": "the content is unavailable, private, or deleted",
        "upstream_failed": "the platform extractor could not read this public URL",
    }[code]
