from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import content_ingestor.xhs_adapter as xhs_adapter
from content_ingestor.doctor import run_doctor, validate_cookie_file
from content_ingestor.config import notion_data_source_id, notion_token
from content_ingestor.notion import NotionError
from content_ingestor.service import ingest_urls
from content_ingestor.storage import find_local_item, safe_name, save_local_item
from content_ingestor.xhs_adapter import AdapterError, normalize_xhs, read_xhs


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def staged_payload(name: str) -> dict:
    payload = copy.deepcopy(fixture(name))
    staging = Path(tempfile.mkdtemp(prefix="content-os-test-"))
    for entry in payload.get("files", []):
        path = staging / entry["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    payload["_staging_dir"] = str(staging)
    return payload


class FakeNotion:
    def __init__(self):
        self.records = {}

    def save_item(self, item, *, local_path="", force=False):
        exists = item.source_id in self.records
        self.records[item.source_id] = True
        result = {
            "status": "success" if force or not exists else "already_saved",
            "platform": item.platform,
            "source_id": item.source_id,
            "source_url": item.source_url,
            "content_type": item.content_type,
            "notion_url": f"https://notion.test/{item.source_id}",
            "errors": [],
        }
        if local_path:
            result["local_path"] = local_path
            result["target_dir"] = local_path
        return result


class FailOnceNotion(FakeNotion):
    def __init__(self):
        super().__init__()
        self.failed = False

    def save_item(self, item, *, local_path="", force=False):
        if not self.failed:
            self.failed = True
            raise NotionError("notion_write", "temporary_failure", "temporary")
        return super().save_item(item, local_path=local_path, force=force)


class IngestorTests(unittest.TestCase):
    def test_xhs_reader_uses_bridge_packaged_next_to_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            python = runtime / ".venv/bin/python"
            python.parent.mkdir(parents=True)
            python.write_text("")
            response = subprocess.CompletedProcess(
                [],
                0,
                stdout=json.dumps({"ok": True, "upstream_version": "2.7"}),
                stderr="",
            )
            with patch("content_ingestor.xhs_adapter.xhs_home", return_value=runtime), patch(
                "content_ingestor.xhs_adapter.xhs_python", return_value=python
            ), patch(
                "content_ingestor.xhs_adapter.cookie_file", return_value=Path(directory) / "missing-cookie"
            ), patch("content_ingestor.xhs_adapter.subprocess.run", return_value=response) as run:
                payload = read_xhs("https://xhslink.com/demo")

            bridge = Path(run.call_args.args[0][1])
            self.assertEqual(
                bridge,
                Path(xhs_adapter.__file__).with_name("xhs_downloader_bridge.py"),
            )
            self.assertTrue(bridge.is_file())
            self.assertEqual(payload["upstream_version"], "2.7")

    def test_normalizes_xhs_downloader_image_note(self):
        payload = staged_payload("xhs_image.json")
        item = normalize_xhs(payload, "https://www.xiaohongshu.com/explore/abc123")
        self.assertEqual(item.source_id, "abc123")
        self.assertEqual(item.content_type, "image")
        self.assertEqual(item.author, "测试作者")
        self.assertEqual(item.published_at, "2024-03-09T16:00:00+08:00")
        self.assertEqual(len(item.media), 2)

    def test_safe_name_removes_path_characters(self):
        self.assertEqual(safe_name(' a/b:c*?"<>| '), "a-b-c")

    def test_image_ingest_writes_markdown_metadata_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            notion = FakeNotion()
            first = ingest_urls(
                ["https://www.xiaohongshu.com/explore/abc123"],
                output,
                reader=lambda _: staged_payload("xhs_image.json"),
                notion=notion,
            )[0]
            second = ingest_urls(
                ["https://www.xiaohongshu.com/explore/abc123"],
                output,
                reader=lambda _: staged_payload("xhs_image.json"),
                notion=notion,
            )[0]

            self.assertEqual(first["status"], "success")
            self.assertEqual(second["status"], "already_saved")
            self.assertNotIn("notion_url", first)
            self.assertTrue(Path(first["local_path"]).is_dir())
            self.assertEqual(len(list(output.rglob("metadata.json"))), 1)
            assets = list((Path(first["local_path"]) / "assets").iterdir())
            self.assertEqual(len(assets), 2)

    def test_force_replaces_existing_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            first = ingest_urls(
                ["https://www.xiaohongshu.com/explore/abc123"], output,
                reader=lambda _: staged_payload("xhs_image.json"),
                notion=FakeNotion(),
            )[0]
            refreshed = ingest_urls(
                ["https://www.xiaohongshu.com/explore/abc123"], output, force=True,
                reader=lambda _: staged_payload("xhs_image.json"),
                notion=FakeNotion(),
            )[0]
            self.assertEqual(refreshed["status"], "success")
            self.assertEqual(refreshed["local_path"], first["local_path"])

    def test_video_without_downloaded_file_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            result = ingest_urls(
                ["https://www.xiaohongshu.com/explore/video123"], Path(directory),
                reader=lambda _: staged_payload("xhs_video_without_url.json"),
                notion=FakeNotion(),
            )[0]
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error"]["code"], "local_media_missing")

    def test_live_photo_preserves_image_and_motion_file(self):
        with tempfile.TemporaryDirectory() as directory:
            result = ingest_urls(
                ["https://www.xiaohongshu.com/explore/live123"], Path(directory),
                reader=lambda _: staged_payload("xhs_live.json"),
                notion=FakeNotion(),
            )[0]
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["content_type"], "live_photo")
            assets = {path.name for path in (Path(result["target_dir"]) / "assets").iterdir()}
            self.assertTrue(any(name.startswith("live-") for name in assets))

    def test_video_retry_reuses_complete_local_media_without_redownload(self):
        calls = []

        def reader(_):
            calls.append("read")
            return staged_payload("xhs_video.json")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            notion = FailOnceNotion()
            first = ingest_urls(
                ["https://www.xiaohongshu.com/explore/video-ok"], output, reader=reader, notion=notion,
                sync="notion",
            )[0]
            second = ingest_urls(
                ["https://www.xiaohongshu.com/explore/video-ok"], output, reader=reader, notion=notion,
                sync="notion",
            )[0]
            self.assertEqual(first["status"], "success")
            self.assertEqual(first["sync"]["notion"]["status"], "failed")
            self.assertTrue(Path(first["local_path"]).is_dir())
            self.assertEqual(second["status"], "already_saved")
            self.assertEqual(second["sync"]["notion"]["status"], "success")
            self.assertEqual(calls, ["read"])

    def test_notion_sync_is_explicit_and_preserves_local_success(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            notion = FakeNotion()
            local_only = ingest_urls(
                ["https://www.xiaohongshu.com/explore/abc123"], output,
                reader=lambda _: staged_payload("xhs_image.json"), notion=notion,
            )[0]
            synced = ingest_urls(
                ["https://www.xiaohongshu.com/explore/abc123"], output,
                reader=lambda _: staged_payload("xhs_image.json"), notion=notion,
                sync="notion",
            )[0]
            self.assertNotIn("sync", local_only)
            self.assertEqual(synced["status"], "already_saved")
            self.assertEqual(synced["sync"]["notion"]["status"], "success")
            self.assertEqual(synced["notion_url"], "https://notion.test/abc123")

    def test_old_local_metadata_restores_video_url_from_raw_content(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            original = normalize_xhs(
                staged_payload("xhs_video.json"),
                "https://www.xiaohongshu.com/explore/video-ok",
            )
            original.media[0].url = ""
            saved = save_local_item(original, output)
            metadata_path = Path(saved["target_dir"]) / "metadata.json"
            payload = json.loads(metadata_path.read_text())
            payload["media"][0]["url"] = ""
            payload["published_at"] = "2026-06-10_17:41:47"
            metadata_path.write_text(json.dumps(payload, ensure_ascii=False))
            restored = find_local_item(
                output, "https://www.xiaohongshu.com/explore/video-ok"
            )
            self.assertIsNotNone(restored)
            self.assertEqual(restored.media[0].url, "https://example.invalid/video.mp4")
            self.assertEqual(restored.published_at, "2026-06-10T17:41:47+08:00")

    def test_local_metadata_cannot_reference_assets_outside_item_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "library"
            outside = Path(directory) / "secret.txt"
            outside.write_text("private", encoding="utf-8")
            original = normalize_xhs(
                staged_payload("xhs_image.json"),
                "https://www.xiaohongshu.com/explore/abc123",
            )
            saved = save_local_item(original, output)
            metadata_path = Path(saved["target_dir"]) / "metadata.json"
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            payload["media"][0]["filename"] = "../../../../secret.txt"
            metadata_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            restored = find_local_item(output, original.source_url)
            self.assertIsNotNone(restored)
            self.assertEqual(restored.media[0].local_path, "")

    def test_batch_failure_does_not_block_other_links(self):
        def reader(url: str):
            if "bad" in url:
                raise AdapterError("content_not_found", "missing")
            return staged_payload("xhs_image.json")

        with tempfile.TemporaryDirectory() as directory:
            results = ingest_urls(
                ["https://www.xiaohongshu.com/explore/bad", "https://www.xiaohongshu.com/explore/abc123"],
                Path(directory), reader=reader, notion=FakeNotion(),
            )
        self.assertEqual([result["status"] for result in results], ["failed", "success"])

    def test_five_link_batch_runs_serially_in_input_order(self):
        urls = [
            f"https://www.xiaohongshu.com/explore/note-{index}"
            for index in range(1, 6)
        ]
        calls: list[str] = []

        def reader(url: str):
            calls.append(url)
            if url.endswith("note-3"):
                raise AdapterError("content_not_found", "missing")
            note_id = url.rsplit("/", 1)[-1]
            payload = staged_payload("xhs_image.json")
            payload["content"]["作品ID"] = note_id
            payload["content"]["作品链接"] = url
            return payload

        with tempfile.TemporaryDirectory() as directory:
            results = ingest_urls(
                urls,
                Path(directory),
                reader=reader,
                notion=FakeNotion(),
            )

        self.assertEqual(calls, urls)
        self.assertEqual(
            [result["status"] for result in results],
            ["success", "success", "failed", "success", "success"],
        )
        self.assertEqual(
            [result["source_url"] for result in results],
            urls,
        )

    def test_rejects_non_http_and_local_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            results = [
                ingest_urls(["file:///tmp/private"], Path(directory))[0],
                ingest_urls(["http://localhost/private"], Path(directory))[0],
            ]
        self.assertTrue(all(result["error"]["code"] == "unsupported_url" for result in results))

    def test_cookie_file_must_be_private(self):
        with tempfile.TemporaryDirectory() as directory:
            cookie = Path(directory) / "cookie.txt"
            cookie.write_text("a=secret")
            cookie.chmod(0o644)
            self.assertFalse(validate_cookie_file(cookie)[0])
            cookie.chmod(0o600)
            self.assertTrue(validate_cookie_file(cookie)[0])

    def test_notion_token_reuses_private_hermes_env_only(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "NOTION_TOKEN=ntn_test\nCONTENT_OS_NOTION_DATA_SOURCE_ID=source_test\n"
            )
            env_file.chmod(0o600)
            with patch.dict(
                os.environ,
                {"CONTENT_OS_HERMES_ENV": str(env_file)},
                clear=False,
            ):
                os.environ.pop("CONTENT_OS_NOTION_TOKEN", None)
                os.environ.pop("NOTION_TOKEN", None)
                os.environ.pop("CONTENT_OS_NOTION_DATA_SOURCE_ID", None)
                self.assertEqual(notion_token(), "ntn_test")
                self.assertEqual(notion_data_source_id(), "source_test")
                env_file.chmod(0o644)
                self.assertEqual(notion_token(), "")
                self.assertEqual(notion_data_source_id(), "")

    def test_doctor_accepts_pinned_fake_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "runtime"
            (home / ".venv/bin").mkdir(parents=True)
            python = home / ".venv/bin/python"
            python.write_text("")
            python.chmod(0o700)
            with patch.dict(os.environ, {"CONTENT_OS_XHS_HOME": str(home)}), patch(
                "content_ingestor.doctor._git_commit",
                return_value="afaf2fb459980fccef9eec74e304a39af2c49cab",
            ), patch("content_ingestor.doctor.shutil.which", return_value="/usr/bin/uv"), patch(
                "content_ingestor.doctor.media_runtime_home", return_value=home,
            ), patch(
                "content_ingestor.doctor.x_python", return_value=python,
            ), patch(
                "content_ingestor.doctor._module_version",
                side_effect=lambda _, module: "1.32.1" if module == "gallery_dl" else "2026.07.04",
            ), patch(
                "content_ingestor.doctor._package_version", return_value="0.8.0",
            ), patch(
                "content_ingestor.doctor._binary_version", return_value="2.8.1",
            ), patch(
                "content_ingestor.doctor.NotionClient"
            ) as notion_client:
                notion_client.return_value.schema_report.return_value = {"ok": True, "missing": [], "mismatched": [], "missing_options": {}}
                self.assertTrue(run_doctor()["ok"])
                notion_client.assert_not_called()

    def test_installer_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "xhs"
            proc = subprocess.run(
                [str(ROOT / "scripts/install-xhs-downloader")],
                text=True, capture_output=True,
                env={**os.environ, "CONTENT_OS_XHS_HOME": str(target)},
                check=False,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertIn("Dry run only", proc.stdout)
            self.assertFalse(target.exists())

    def test_x_runtime_installer_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "x-runtime"
            proc = subprocess.run(
                [str(ROOT / "scripts/install-x-runtime")],
                text=True, capture_output=True,
                env={**os.environ, "CONTENT_OS_X_RUNTIME_HOME": str(target)},
                check=False,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertIn("Dry run only", proc.stdout)
            self.assertFalse(target.exists())

    @unittest.skipUnless(os.uname().sysname == "Darwin", "macOS installer")
    def test_collecthub_installer_smoke_in_clean_home(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            env = {**os.environ, "HOME": str(home)}
            legacy_skill = home / ".hermes/skills/social-media/content-ingestor"
            legacy_skill.mkdir(parents=True)
            (legacy_skill / "SKILL.md").write_text("legacy skill", encoding="utf-8")
            install = [
                str(ROOT / "install.sh"), "--yes", "--client", "all",
                "--source", str(ROOT), "--skip-runtimes",
            ]
            first = subprocess.run(install, text=True, capture_output=True, env=env, check=False)
            second = subprocess.run(install, text=True, capture_output=True, env=env, check=False)
            self.assertEqual((first.returncode, second.returncode), (0, 0))
            self.assertTrue((home / ".codex/skills/content-ingestor/SKILL.md").is_file())
            self.assertTrue((home / ".hermes/skills/content-ingestor/SKILL.md").is_file())
            self.assertFalse(legacy_skill.exists())
            legacy_backups = list(
                (home / ".local/share/collecthub/backups/skills/hermes").glob(
                    "content-ingestor-legacy-*/SKILL.md"
                )
            )
            self.assertEqual(len(legacy_backups), 1)
            self.assertEqual(legacy_backups[0].read_text(encoding="utf-8"), "legacy skill")
            self.assertTrue((home / ".local/bin/content-ingestor").is_file())
            remove = subprocess.run(
                [str(ROOT / "install.sh"), "--yes", "--client", "all", "--uninstall"],
                text=True, capture_output=True, env=env, check=False,
            )
            self.assertEqual(remove.returncode, 0)
            self.assertFalse((home / ".codex/skills/content-ingestor").exists())
            self.assertFalse((home / ".hermes/skills/content-ingestor").exists())


if __name__ == "__main__":
    unittest.main()
