from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

from .config import (
    GALLERY_DL_VERSION,
    XHS_COMMIT,
    XHS_VERSION,
    YT_DLP_VERSION,
    cookie_file,
    x_runtime_home,
    x_python,
    xhs_home,
    xhs_python,
)
from .notion import NotionClient, NotionError


def run_doctor(*, sync: str | None = None) -> dict:
    checks: list[dict] = []
    uv = shutil.which("uv")
    checks.append(_check("uv", bool(uv), uv or "uv is not on PATH"))

    home = xhs_home()
    python = xhs_python()
    checks.append(_check("xhs_downloader_home", home.is_dir(), str(home)))
    checks.append(_check("xhs_downloader_python", python.is_file() and os.access(python, os.X_OK), str(python)))

    actual_commit = _git_commit(home)
    checks.append(
        _check(
            "xhs_downloader_version",
            actual_commit == XHS_COMMIT,
            f"expected {XHS_VERSION} ({XHS_COMMIT[:12]}), found {actual_commit or 'not installed'}",
        )
    )

    runtime = x_runtime_home()
    xpy = x_python()
    checks.append(_check("x_runtime_home", runtime.is_dir(), str(runtime)))
    checks.append(_check("x_runtime_python", xpy.is_file() and os.access(xpy, os.X_OK), str(xpy)))
    gallery_version = _module_version(xpy, "gallery_dl") if xpy.is_file() else ""
    ytdlp_version = _module_version(xpy, "yt_dlp") if xpy.is_file() else ""
    checks.append(_check("gallery_dl_version", gallery_version == GALLERY_DL_VERSION, f"expected {GALLERY_DL_VERSION}, found {gallery_version or 'not installed'}"))
    checks.append(_check("yt_dlp_version", ytdlp_version == YT_DLP_VERSION, f"expected {YT_DLP_VERSION}, found {ytdlp_version or 'not installed'}"))

    cookie = cookie_file()
    if cookie.exists():
        cookie_ok, detail = validate_cookie_file(cookie)
        checks.append(_check("cookie_file", cookie_ok, detail))
    else:
        checks.append({"name": "cookie_file", "ok": True, "optional": True, "detail": f"not configured: {cookie}"})

    if sync == "notion":
        client: NotionClient | None = None
        try:
            client = NotionClient()
            report = client.schema_report()
            detail = "schema ready" if report["ok"] else f"missing={report['missing']}, mismatched={report['mismatched']}"
            checks.append(_check("notion", bool(report["ok"]), detail))
        except NotionError as exc:
            checks.append(_check("notion", False, f"{exc.code}: {exc}"))
        finally:
            if client is not None:
                client.close()
    else:
        checks.append({
            "name": "notion",
            "ok": True,
            "optional": True,
            "detail": "not checked; pass --sync notion to validate",
        })

    return {
        "ok": all(check["ok"] for check in checks),
        "xhs_version": XHS_VERSION,
        "x_runtime": {"gallery-dl": GALLERY_DL_VERSION, "yt-dlp": YT_DLP_VERSION},
        "checks": checks,
    }


def validate_cookie_file(path: Path) -> tuple[bool, str]:
    try:
        info = path.lstat()
    except OSError as exc:
        return False, f"cannot stat cookie file: {exc}"
    if not stat.S_ISREG(info.st_mode):
        return False, "cookie path must be a regular file"
    if info.st_uid != os.getuid():
        return False, "cookie file must be owned by the current user"
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o077:
        return False, f"cookie permissions must be 0600 or stricter, found {mode:04o}"
    return True, f"configured securely: {path}"


def _git_commit(home: Path) -> str:
    if not (home / ".git").is_dir():
        return ""
    proc = subprocess.run(
        ["git", "-C", str(home), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _module_version(python: Path, module: str) -> str:
    try:
        proc = subprocess.run(
            [str(python), "-m", module, "--version"],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except OSError:
        return ""
    return proc.stdout.strip().splitlines()[0] if proc.returncode == 0 and proc.stdout.strip() else ""


def _check(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "ok": ok, "detail": detail}
