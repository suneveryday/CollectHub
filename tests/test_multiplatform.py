from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from content_ingestor.media_adapter import _run_ytdlp, read_media, subtitle_text
from content_ingestor.platform_adapters import (
    facebook_identity, normalize_tiktok, normalize_youtube, read_tiktok, tiktok_identity, youtube_identity, zhihu_identity,
)
from content_ingestor.router import adapter_for
from content_ingestor.service import ingest_urls
from content_ingestor.storage import find_local_item
from content_ingestor.web_adapter import _cookie_headers, canonicalize_web_url, normalize_web, read_web
from content_ingestor.web_fetch import fetch_html, normalize_public_url, validate_public_destination
from content_ingestor.xhs_adapter import AdapterError


class MultiPlatformTests(unittest.TestCase):
    def test_known_domains_route_before_generic_web(self):
        expected = {
            "https://youtu.be/abcdefghijk": "youtube",
            "https://www.tiktok.com/@u/video/123456789": "tiktok",
            "https://www.facebook.com/u/posts/123": "facebook",
            "https://www.zhihu.com/question/1/answer/2": "zhihu",
            "https://example.com/article": "web",
        }
        for url, platform in expected.items():
            self.assertEqual(adapter_for(url).name, platform)

    def test_known_platform_collection_urls_are_rejected(self):
        bad = [
            "https://www.youtube.com/@creator",
            "https://www.youtube.com/playlist?list=PL123",
            "https://www.tiktok.com/@creator",
            "https://www.facebook.com/creator",
            "https://www.zhihu.com/search?q=test",
        ]
        for url in bad:
            with self.assertRaises(AdapterError, msg=url):
                adapter_for(url).canonicalizer(url)

    def test_platform_canonical_ids(self):
        self.assertEqual(youtube_identity("https://youtu.be/abcdefghijk?t=2")[0], "abcdefghijk")
        self.assertEqual(tiktok_identity("https://www.tiktok.com/@u/photo/123456")[0], "123456")
        self.assertEqual(facebook_identity("https://www.facebook.com/u/posts/123")[0], "123")
        self.assertEqual(facebook_identity("https://www.facebook.com/video.php?v=456")[0], "456")
        self.assertEqual(zhihu_identity("https://www.zhihu.com/question/1/answer/2")[0], "2")

    def test_tracking_parameters_and_fragments_do_not_change_web_id(self):
        first = canonicalize_web_url("https://example.com/a?utm_source=x&lang=zh#top")
        second = canonicalize_web_url("https://example.com/a?lang=zh&utm_medium=y")
        self.assertEqual(first, second)

    def test_non_http_and_private_destinations_are_rejected(self):
        for url in ("file:///etc/passwd", "http://localhost/a", "http://127.0.0.1/a", "http://[::1]/a"):
            with self.assertRaises(AdapterError, msg=url):
                validate_public_destination(url)

    def test_dns_resolution_to_private_address_is_rejected(self):
        with patch("content_ingestor.web_fetch.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 0))]):
            with self.assertRaises(AdapterError) as caught:
                validate_public_destination("https://example.test/a")
        self.assertEqual(caught.exception.code, "unsafe_url")

    def test_fetch_connects_to_the_address_that_passed_ssrf_validation(self):
        class Response:
            status = 200
            def getheader(self, name, default=""):
                return {"content-type": "text/html", "content-length": "13"}.get(name.lower(), default)
            def read(self, _size):
                if getattr(self, "done", False):
                    return b""
                self.done = True
                return b"<p>public</p>"
        class Connection:
            def request(self, *_args, **_kwargs): pass
            def getresponse(self): return Response()
            def close(self): pass
        with patch("content_ingestor.web_fetch.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]), patch(
            "content_ingestor.web_fetch._connection_for", return_value=Connection()
        ) as connection:
            result = fetch_html("https://example.com/a")
        self.assertEqual(result.content, b"<p>public</p>")
        connection.assert_called_once_with("https://example.com/a", "93.184.216.34")

    def test_redirect_is_revalidated_and_blocks_local_target(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": "http://127.0.0.1/private"}, request=request))
        with httpx.Client(transport=transport, follow_redirects=False) as client, patch(
            "content_ingestor.web_fetch.validate_public_destination", side_effect=lambda url: validate_public_destination(url) if "127.0.0.1" in url else None
        ):
            with self.assertRaises(AdapterError) as caught:
                fetch_html("https://public.test/start", client=client)
        self.assertEqual(caught.exception.code, "unsafe_url")

    def test_cross_host_redirect_does_not_forward_cookie(self):
        seen = []
        def handler(request):
            seen.append((request.url.host, request.headers.get("cookie")))
            if request.url.host == "source.test":
                return httpx.Response(302, headers={"location": "https://target.test/page"}, request=request)
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>ok</p>", request=request)
        with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client, patch("content_ingestor.web_fetch.validate_public_destination"):
            fetch_html("https://source.test/start", client=client, headers={"Cookie": "secret=value"})
        self.assertEqual(seen, [("source.test", "secret=value"), ("target.test", None)])

    def test_html_size_and_content_type_limits(self):
        def handler(request):
            return httpx.Response(200, headers={"content-type": "application/pdf", "content-length": "5"}, content=b"%PDF-", request=request)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, patch("content_ingestor.web_fetch.validate_public_destination"):
            with self.assertRaises(AdapterError) as caught:
                fetch_html("https://public.test/a", client=client)
        self.assertEqual(caught.exception.code, "unsupported_content_type")

    def test_declared_oversize_html_is_rejected_before_body_read(self):
        def handler(request):
            return httpx.Response(200, headers={"content-type": "text/html", "content-length": str(11 * 1024 * 1024)}, content=b"x", request=request)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, patch("content_ingestor.web_fetch.validate_public_destination"):
            with self.assertRaises(AdapterError) as caught:
                fetch_html("https://public.test/a", client=client)
        self.assertEqual(caught.exception.code, "response_too_large")

    def test_static_web_normalization_uses_schema_v3(self):
        payload = {
            "url": "https://example.com/article",
            "html": "<html><head><title>Useful Page</title></head><body><article><h1>Useful Page</h1><p>A useful public article with enough readable words for static extraction.</p><p>Second paragraph for the reader.</p><script>alert(1)</script></article></body></html>",
            "files": [],
        }
        item = normalize_web(payload, payload["url"])
        self.assertEqual((item.schema_version, item.platform, item.content_type), ("3", "web", "webpage"))
        self.assertNotIn("alert(1)", item.body)

    def test_tracking_variants_dedupe_to_same_local_web_item(self):
        html = "<html><head><title>Article</title></head><body><article><p>A readable article body long enough for extraction and local persistence.</p><p>More useful content.</p></article></body></html>"
        def reader(url):
            return {"url": normalize_public_url(url), "html": html, "files": []}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            first = ingest_urls(["https://example.com/a?utm_source=x"], output, reader=reader)[0]
            second = ingest_urls(["https://example.com/a?utm_medium=y"], output, reader=reader)[0]
        self.assertEqual((first["status"], second["status"]), ("success", "already_saved"))

    def test_failed_page_image_does_not_destroy_readable_capture(self):
        html_result = type("Result", (), {"url": "https://example.com/a", "content": b"<html><body><article><p>A readable article with enough useful content for extraction.</p></article><img src='https://cdn.example.com/a.jpg'></body></html>", "content_type": "text/html"})()
        with patch("content_ingestor.web_adapter.fetch_html", return_value=html_result), patch(
            "content_ingestor.web_adapter.fetch_image", side_effect=AdapterError("upstream_failed", "failed")
        ):
            payload = read_web("https://example.com/a")
        self.assertEqual(payload["files"], [])

    def test_private_netscape_cookie_is_domain_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            cookie = Path(directory) / "zhihu.txt"
            cookie.write_text(".zhihu.com\tTRUE\t/\tTRUE\t0\tsession\tsecret-value\n")
            cookie.chmod(0o600)
            with patch("content_ingestor.web_adapter.platform_cookie_file", return_value=cookie):
                headers = _cookie_headers("https://www.zhihu.com/question/1", "zhihu")
                unrelated = _cookie_headers("https://example.com/", "zhihu")
        self.assertEqual(headers, {"Cookie": "session=secret-value"})
        self.assertIsNone(unrelated)

    def test_youtube_metadata_subtitle_capture_is_complete_without_video(self):
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "staging"
            staging.mkdir()
            subtitle = staging / "abcdefghijk.en.vtt"
            subtitle.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello world\n", encoding="utf-8")
            cover = staging / "abcdefghijk.webp"
            cover.write_bytes(b"image")
            payload = {
                "info": {"id": "abcdefghijk", "title": "Video", "description": "Description", "uploader": "Creator"},
                "files": [{"path": str(subtitle), "kind": "subtitle"}, {"path": str(cover), "kind": "image"}],
                "_staging_dir": str(staging),
            }
            result = ingest_urls(
                ["https://youtu.be/abcdefghijk"], Path(directory) / "library", reader=lambda _: payload
            )[0]
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["capture_status"], "complete")
            target = Path(result["local_path"])
            metadata = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["capture_policy"], "metadata_subtitles")
            self.assertTrue(metadata["subtitles"][0]["filename"].startswith("assets/subtitle-"))
            self.assertIn("Hello world", (target / "index.md").read_text(encoding="utf-8"))
            self.assertFalse(any(path.suffix == ".mp4" for path in (target / "assets").iterdir()))
            restored = find_local_item(Path(directory) / "library", "https://www.youtube.com/watch?v=abcdefghijk")
            self.assertIsNotNone(restored)

    def test_youtube_video_defaults_to_notion_bookmark_without_local_media_path(self):
        class BookmarkNotion:
            def __init__(self):
                self.local_paths = []

            def save_item(self, item, *, local_path="", force=False):
                self.local_paths.append(local_path)
                return {"status": "success", "notion_url": f"https://notion.test/{item.source_id}"}

        with tempfile.TemporaryDirectory() as directory:
            notion = BookmarkNotion()
            payload = {
                "info": {"id": "abcdefghijk", "title": "Video"},
                "files": [],
                "_staging_dir": directory,
            }
            result = ingest_urls(
                ["https://youtu.be/abcdefghijk"],
                Path(directory) / "library",
                reader=lambda _: payload,
                notion=notion,
                auto_video_notion=True,
            )[0]
        self.assertEqual(result["sync"]["notion"]["status"], "success")
        self.assertEqual(result["notion_url"], "https://notion.test/abcdefghijk")
        self.assertEqual(notion.local_paths, [""])

    def test_missing_subtitles_is_still_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = {"info": {"id": "abcdefghijk", "title": "No captions"}, "files": [], "_staging_dir": directory}
            result = ingest_urls(["https://youtu.be/abcdefghijk"], Path(directory) / "out", reader=lambda _: payload)[0]
            self.assertEqual(result["status"], "success")

    def test_tiktok_official_oembed_is_used_when_extractors_are_blocked(self):
        payload = {"info": {"id": "123456", "title": "Post", "webpage_url": "https://www.tiktok.com/@u/video/123456"}, "files": [], "_staging_dir": tempfile.mkdtemp(), "oembed": True}
        with patch("content_ingestor.platform_adapters.read_media", side_effect=AdapterError("upstream_failed", "blocked")), patch(
            "content_ingestor.platform_adapters._read_tiktok_oembed", return_value=payload
        ):
            actual = read_tiktok("https://www.tiktok.com/@u/video/123456")
        item = normalize_tiktok(actual, "https://www.tiktok.com/@u/video/123456")
        self.assertTrue(actual["oembed"])
        self.assertEqual((item.content_type, item.capture_policy), ("video", "metadata_subtitles"))

    def test_subtitle_cleaner_removes_timing_and_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.en.vtt"
            path.write_text("WEBVTT\n00:00:00.000 --> 00:00:01.000\nHi\nHi\n00:00:01.000 --> 00:00:02.000\nThere\n")
            self.assertEqual(subtitle_text(path), "Hi\nThere")

    def test_cookie_file_permissions_are_checked_before_subprocess(self):
        with tempfile.TemporaryDirectory() as directory:
            cookie = Path(directory) / "youtube.txt"
            cookie.write_text("# Netscape HTTP Cookie File\n")
            cookie.chmod(0o644)
            with patch("content_ingestor.media_adapter.media_python", return_value=Path("/bin/sh")), patch(
                "content_ingestor.media_adapter.platform_cookie_file", return_value=cookie
            ), patch("content_ingestor.media_adapter.subprocess.run") as run:
                with self.assertRaises(AdapterError) as caught:
                    read_media("https://youtu.be/abcdefghijk", "youtube")
            self.assertEqual(caught.exception.code, "insecure_cookie_file")
            run.assert_not_called()

    def test_url_is_passed_as_one_argument_after_option_terminator(self):
        captured = {}
        def fake_run(command, **kwargs):
            captured["command"] = command
            return type("Result", (), {"returncode": 1, "stdout": "", "stderr": "failed"})()
        with tempfile.TemporaryDirectory() as directory, patch("content_ingestor.media_adapter.subprocess.run", side_effect=fake_run):
            _run_ytdlp(Path("/python"), Path(directory), "https://example.com/v;touch /tmp/pwned", None)
        self.assertEqual(captured["command"][-2], "--")
        self.assertEqual(captured["command"][-1], "https://example.com/v;touch /tmp/pwned")

    def test_force_refresh_and_batch_failure_are_isolated_for_new_platform(self):
        calls = []
        def reader(url):
            calls.append(url)
            if "badbad" in url:
                raise AdapterError("content_not_found", "missing")
            return {"info": {"id": "abcdefghijk", "title": "Video"}, "files": [], "_staging_dir": tempfile.mkdtemp()}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            urls = ["https://youtu.be/badbad1", "https://youtu.be/abcdefghijk"]
            result = ingest_urls(urls, output, reader=reader)
            refreshed = ingest_urls([urls[1]], output, reader=reader, force=True)[0]
        self.assertEqual([item["status"] for item in result], ["failed", "success"])
        self.assertEqual(refreshed["status"], "success")
        self.assertEqual(calls, urls + [urls[1]])


if __name__ == "__main__":
    unittest.main()
