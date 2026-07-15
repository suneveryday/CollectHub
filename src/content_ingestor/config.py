from __future__ import annotations

import os
import stat
from pathlib import Path

XHS_VERSION = "2.7"
XHS_COMMIT = "afaf2fb459980fccef9eec74e304a39af2c49cab"
XHS_REPOSITORY = "https://github.com/JoeanAmier/XHS-Downloader.git"
GALLERY_DL_VERSION = "1.32.1"
YT_DLP_VERSION = "2026.06.09"
YT_DLP_EJS_VERSION = "0.8.0"
DENO_VERSION = "2.8.1"
TRAFILATURA_VERSION = "2.1.0"
NOTION_API_VERSION = "2026-03-11"


def xhs_home() -> Path:
    override = os.environ.get("CONTENT_OS_XHS_HOME")
    return Path(override).expanduser() if override else Path.home() / ".local/share/collecthub/runtimes/xhs-downloader/2.7"


def xhs_python() -> Path:
    return xhs_home() / ".venv/bin/python"


def media_runtime_home() -> Path:
    override = os.environ.get("COLLECTHUB_MEDIA_RUNTIME_HOME") or os.environ.get("CONTENT_OS_X_RUNTIME_HOME")
    version = f"gallery-dl-{GALLERY_DL_VERSION}_yt-dlp-{YT_DLP_VERSION}_deno-{DENO_VERSION}"
    return Path(override).expanduser() if override else Path.home() / ".local/share/collecthub/runtimes/media" / version


def x_runtime_home() -> Path:  # Backward-compatible API.
    return media_runtime_home()


def x_python() -> Path:
    return media_runtime_home() / ".venv/bin/python"


def media_python() -> Path:
    return x_python()


def deno_binary() -> Path:
    return media_runtime_home() / "bin/deno"


def cookie_file() -> Path:
    override = os.environ.get("CONTENT_OS_XHS_COOKIE_FILE")
    return Path(override).expanduser() if override else Path.home() / ".config/content-os/xhs-cookie.txt"


def platform_cookie_file(platform: str) -> Path:
    if platform == "xiaohongshu":
        return cookie_file()
    override = os.environ.get(f"COLLECTHUB_{platform.upper()}_COOKIE_FILE")
    return Path(override).expanduser() if override else Path.home() / ".config/collecthub/cookies" / f"{platform}.txt"


def notion_token() -> str:
    configured = os.environ.get("CONTENT_OS_NOTION_TOKEN", "").strip()
    if configured:
        return configured
    configured = os.environ.get("NOTION_TOKEN", "").strip()
    if configured:
        return configured
    hermes_env = Path(
        os.environ.get("CONTENT_OS_HERMES_ENV", Path.home() / ".hermes/.env")
    ).expanduser()
    return _private_env_value(hermes_env, "NOTION_TOKEN")


def notion_data_source_id() -> str:
    return os.environ.get("CONTENT_OS_NOTION_DATA_SOURCE_ID", "").strip()


def default_output() -> Path:
    configured = os.environ.get("COLLECTHUB_LIBRARY", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / "CollectHub"


def _private_env_value(path: Path, key: str) -> str:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            return ""
        if stat.S_IMODE(info.st_mode) & 0o077:
            return ""
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            name, separator, value = line.partition("=")
            if separator and name.strip() == key:
                return value.strip().strip("'\"")
    except OSError:
        return ""
    return ""
