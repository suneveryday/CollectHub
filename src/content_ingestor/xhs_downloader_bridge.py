#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from pathlib import Path

EXPECTED_VERSION = "2.7"
MEDIA_SUFFIXES = {
    ".jpeg", ".jpg", ".png", ".webp", ".avif", ".heic",
    ".mp4", ".mov", ".m4v", ".mkv", ".mpg", ".flv", ".avi",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream-root", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--cookie-file", type=Path)
    return parser.parse_args()


def manifest(root: Path) -> list[dict]:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES:
            files.append({"path": path.relative_to(root).as_posix(), "size": path.stat().st_size})
    return files


async def run(args: argparse.Namespace) -> dict:
    args.staging.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(args.upstream_root))
    cookie = ""
    if args.cookie_file:
        cookie = args.cookie_file.read_text(encoding="utf-8").strip()

    with contextlib.redirect_stdout(sys.stderr):
        from source import XHS
        from source.module import VERSION_MAJOR, VERSION_MINOR

        version = f"{VERSION_MAJOR}.{VERSION_MINOR}"
        if version != EXPECTED_VERSION:
            return {
                "ok": False,
                "upstream_version": version,
                "error": {"code": "version_mismatch", "message": "expected XHS-Downloader 2.7"},
            }
        async with XHS(
            work_path=str(args.staging),
            folder_name="payload",
            name_format="作品ID",
            cookie=cookie,
            record_data=False,
            image_format="AUTO",
            folder_mode=False,
            image_download=True,
            video_download=True,
            live_download=True,
            video_preference="resolution",
            download_record=False,
            author_archive=False,
            write_mtime=True,
            language="zh_CN",
            script_server=False,
        ) as xhs:
            result = await xhs.extract(args.url, download=True)

    works = [item for item in result if isinstance(item, dict) and item]
    warnings = []
    if not cookie:
        warnings.append("cookie_not_configured")
    if not works:
        return {
            "ok": False,
            "upstream_version": EXPECTED_VERSION,
            "error": {"code": "content_not_found", "message": "XHS-Downloader returned no work data"},
            "warnings": warnings,
        }
    return {
        "ok": True,
        "upstream_version": EXPECTED_VERSION,
        "content": works[0],
        "files": manifest(args.staging),
        "warnings": warnings,
    }


def main() -> int:
    args = parse_args()
    cookie_value = ""
    try:
        if args.cookie_file and args.cookie_file.is_file():
            cookie_value = args.cookie_file.read_text(encoding="utf-8").strip()
        payload = asyncio.run(run(args))
    except Exception as exc:
        message = str(exc)
        if cookie_value:
            message = message.replace(cookie_value, "[REDACTED]")
        print(f"XHS-Downloader bridge error: {type(exc).__name__}: {message}", file=sys.stderr)
        payload = {
            "ok": False,
            "upstream_version": EXPECTED_VERSION,
            "error": {"code": "upstream_failed", "message": "XHS-Downloader failed; see local stderr log"},
        }
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
