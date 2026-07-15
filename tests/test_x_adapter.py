from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from content_ingestor.router import adapter_for
from content_ingestor.service import ingest_urls
from content_ingestor.x_adapter import canonicalize_x_url, cleanup_payload, normalize_x, read_x
from content_ingestor.xhs_adapter import AdapterError


FIXTURES = Path(__file__).parent / "fixtures"


def staged(name: str) -> dict:
    payload = copy.deepcopy(json.loads((FIXTURES / name).read_text(encoding="utf-8")))
    root = Path(tempfile.mkdtemp(prefix="content-os-x-test-"))
    for entry in payload.get("files", []):
        path = root / entry["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    payload["_staging_dir"] = str(root)
    return payload


class FakeNotion:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], str] = {}

    def save_item(self, item, *, local_path="", force=False):
        key = (item.platform, item.source_id)
        previous = self.records.get(key)
        self.records[key] = item.capture_status
        status = "already_saved" if previous == "complete" and not force else (
            "success" if item.capture_status == "complete" else "partial"
        )
        result = {
            "status": status,
            "platform": item.platform,
            "source_id": item.source_id,
            "source_url": item.source_url,
            "content_type": item.content_type,
            "capture_status": item.capture_status,
            "notion_url": f"https://notion.test/{item.platform}/{item.source_id}",
            "errors": [],
        }
        if local_path:
            result["local_path"] = local_path
            result["target_dir"] = local_path
        return result

    def save_failure(self, item, error):
        self.records[(item.platform, item.source_id)] = item.capture_status
        return {
            "status": "failed",
            "notion_url": f"https://notion.test/{item.platform}/{item.source_id}",
            "error": error,
        }


class XAdapterTests(unittest.TestCase):
    def test_canonicalizes_supported_aliases(self):
        expected = ("1900000000000000001", "https://x.com/i/web/status/1900000000000000001")
        self.assertEqual(canonicalize_x_url("https://x.com/user/status/1900000000000000001?s=20"), expected)
        self.assertEqual(canonicalize_x_url("https://twitter.com/i/web/status/1900000000000000001"), expected)

    def test_rejects_non_status_and_lookalike_domains(self):
        for url in ("https://x.com/example", "https://evil-x.com/user/status/1900000000000000001", "https://evil.x.com/user/status/1900000000000000001"):
            with self.assertRaises(AdapterError):
                canonicalize_x_url(url)

    def test_normalizes_text_long_post_and_article(self):
        text_payload = staged("x_text.json")
        long_payload = staged("x_long_post.json")
        article_payload = staged("x_article.json")
        try:
            text = normalize_x(text_payload, text_payload["canonical_url"])
            long_post = normalize_x(long_payload, long_payload["canonical_url"])
            article = normalize_x(article_payload, article_payload["canonical_url"])
            self.assertEqual((text.content_type, text.body), ("post", "A plain public post"))
            self.assertEqual((long_post.content_type, long_post.body), ("long_post", "This is the complete long post body."))
            self.assertEqual((article.content_type, article.title), ("article", "Article title"))
            self.assertIn("First paragraph.", article.body)
        finally:
            cleanup_payload(text_payload)
            cleanup_payload(long_payload)
            cleanup_payload(article_payload)

    def test_normalizer_ignores_unrelated_article_from_timeline(self):
        payload = staged("x_text.json")
        staging = Path(payload["_staging_dir"])
        unrelated_html = staging / "unrelated-article.html"
        unrelated_html.write_text(
            "<h1>Wrong historical article</h1><p>Wrong body.</p>",
            encoding="utf-8",
        )
        payload["metadata"].append(
            {
                "tweet_id": "1800000000000000000",
                "content": "https://x.com/i/article/old",
                "article": {"title": "Wrong historical article"},
            }
        )
        payload["files"].append(
            {"path": unrelated_html.name, "kind": "document"}
        )
        try:
            item = normalize_x(payload, payload["canonical_url"])
            self.assertEqual(item.content_type, "post")
            self.assertEqual(item.title, "A plain public post")
            self.assertEqual(item.body, "A plain public post")
        finally:
            cleanup_payload(payload)

    def test_normalizer_rejects_payload_without_target_tweet(self):
        payload = staged("x_text.json")
        payload["metadata"][0]["tweet_id"] = "1800000000000000000"
        try:
            with self.assertRaisesRegex(
                AdapterError,
                "did not contain target tweet 1900000000000000001",
            ):
                normalize_x(payload, payload["canonical_url"])
        finally:
            cleanup_payload(payload)

    def test_reader_limits_gallery_dl_to_first_post(self):
        with tempfile.TemporaryDirectory() as directory:
            python = Path(directory) / "python"
            python.write_text("", encoding="utf-8")
            completed = SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "tweet_id": "1900000000000000001",
                        "content": "A plain public post",
                    }
                ),
                stderr="",
            )
            with patch(
                "content_ingestor.x_adapter.x_python",
                return_value=python,
            ), patch(
                "content_ingestor.x_adapter.subprocess.run",
                return_value=completed,
            ) as run:
                payload = read_x(
                    "https://x.com/example/status/1900000000000000001"
                )
            try:
                command = run.call_args.args[0]
                option_index = command.index("--post-range")
                self.assertEqual(command[option_index + 1], "1")
            finally:
                cleanup_payload(payload)

    def test_routes_x_video_and_persists_all_downloaded_media(self):
        with tempfile.TemporaryDirectory() as directory:
            result = ingest_urls(
                ["https://x.com/example/status/1900000000000000004"],
                Path(directory),
                reader=lambda _: staged("x_video.json"),
                notion=FakeNotion(),
            )[0]
            self.assertEqual(result["status"], "success")
            assets = {path.name for path in (Path(result["local_path"]) / "assets").iterdir()}
            self.assertEqual(assets, {"video.mp4", "002.jpg"})

    def test_partial_media_is_saved_as_partial_without_local_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = ingest_urls(
                ["https://x.com/example/status/1900000000000000005"],
                output,
                reader=lambda _: staged("x_partial.json"),
                notion=FakeNotion(),
            )[0]
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["capture_status"], "media_partial")
            self.assertTrue(any(output.rglob("metadata.json")))
            self.assertEqual(len(list((Path(result["local_path"]) / "assets").iterdir())), 1)

    def test_router_supports_known_platforms_and_generic_web(self):
        self.assertEqual(adapter_for("https://www.xiaohongshu.com/explore/abc").name, "xiaohongshu")
        self.assertEqual(adapter_for("https://x.com/a/status/1900000000000000001").name, "x")
        self.assertEqual(adapter_for("https://example.com/post").name, "web")
        with self.assertRaises(ValueError):
            adapter_for("file:///tmp/private")

    def test_mixed_platform_batch_routes_each_url_independently(self):
        def reader(url: str):
            return staged("x_text.json") if "x.com" in url else staged("xhs_image.json")

        with tempfile.TemporaryDirectory() as directory:
            results = ingest_urls(
                [
                    "https://www.xiaohongshu.com/explore/abc123",
                    "https://x.com/example/status/1900000000000000001",
                ],
                Path(directory),
                reader=reader,
                notion=FakeNotion(),
            )
        self.assertEqual([result["platform"] for result in results], ["xiaohongshu", "x"])
        self.assertEqual([result["status"] for result in results], ["success", "success"])

    def test_known_x_access_failure_is_indexed_for_retry(self):
        def reader(_):
            raise AdapterError("deleted_or_private", "tweet is unavailable")

        notion = FakeNotion()
        with tempfile.TemporaryDirectory() as directory:
            result = ingest_urls(
                ["https://x.com/example/status/1900000000000000006"],
                Path(directory),
                reader=reader,
                notion=notion,
                sync="notion",
            )[0]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "deleted_or_private")
        self.assertIn("notion_url", result)
        self.assertEqual(notion.records[("x", "1900000000000000006")], "deleted_or_private")


if __name__ == "__main__":
    unittest.main()
