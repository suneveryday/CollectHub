from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from content_ingestor.models import ContentItem, MediaAsset
from content_ingestor.notion import (
    NotionClient,
    NotionError,
    _split_text,
    _video_bookmark_needs_refresh,
)


def item(content_type="image", media=None, platform="xiaohongshu", capture_status="complete"):
    return ContentItem(
        schema_version="1",
        platform=platform,
        content_type=content_type,
        source_id="note-1",
        source_url="https://www.xiaohongshu.com/explore/note-1",
        title="标题",
        body="正文",
        author="作者",
        published_at="2025-01-01T08:00:00+08:00",
        captured_at="2026-07-13T04:00:00+00:00",
        tags=["测试"],
        media=media or [],
        capture_status=capture_status,
    )


class BlockClient(NotionClient):
    def __init__(self):
        self.uploaded = []

    def _upload(self, path):
        self.uploaded.append(path)
        return f"upload-{len(self.uploaded)}"


class NotionTests(unittest.TestCase):
    def test_notion_transport_ignores_download_proxy_environment(self):
        try:
            import httpx  # noqa: F401
        except ImportError:
            self.skipTest("httpx is not installed in the test interpreter")

        client = NotionClient(token="test", data_source_id="source-1")
        try:
            self.assertFalse(client._client._trust_env)
            self.assertEqual(
                client._client.headers["User-Agent"],
                "CollectHub/1.0 (+https://github.com/suneveryday/CollectHub)",
            )
        finally:
            client.close()

    def test_cloudflare_html_403_is_not_reported_as_auth_failure(self):
        try:
            import httpx
        except ImportError:
            self.skipTest("httpx is not installed in the test interpreter")

        def handler(_request):
            return httpx.Response(
                403,
                headers={"content-type": "text/html", "cf-ray": "ray-123"},
                text="<html><title>Cloudflare</title>Sorry, you have been blocked</html>",
            )

        client = NotionClient(token="test", data_source_id="source-1")
        client._client.close()
        client._client = httpx.Client(
            base_url="https://notion.test/v1",
            transport=httpx.MockTransport(handler),
            trust_env=False,
        )
        try:
            with self.assertRaises(NotionError) as raised:
                client.get_schema()
            self.assertEqual(raised.exception.stage, "notion_request")
            self.assertEqual(raised.exception.code, "cloudflare_blocked")
            self.assertIn("ray-123", str(raised.exception))
            self.assertNotIn("<html>", str(raised.exception))
        finally:
            client.close()

    def test_properties_map_normalized_fields(self):
        client = BlockClient()
        payload = client._properties(item(), "", "已完成", "")
        self.assertEqual(payload["内容 ID"]["rich_text"][0]["text"]["content"], "note-1")
        self.assertEqual(payload["内容类型"]["select"]["name"], "图文")
        self.assertEqual(payload["标签"]["multi_select"][0]["name"], "测试")

    def test_x_properties_include_platform_type_and_capture_status(self):
        client = BlockClient()
        payload = client._properties(item("long_post", platform="x", capture_status="media_partial"), "", "已完成", "")
        self.assertEqual(payload["平台"]["select"]["name"], "X")
        self.assertEqual(payload["内容类型"]["select"]["name"], "Long Post")
        self.assertEqual(payload["抓取状态"]["select"]["name"], "media_partial")

    def test_youtube_and_tiktok_bookmarks_use_source_as_video_url(self):
        client = BlockClient()
        for platform, source_url in (
            ("youtube", "https://www.youtube.com/watch?v=abcdefghijk"),
            ("tiktok", "https://www.tiktok.com/@creator/video/123456"),
        ):
            video = item("video", platform=platform)
            video.source_url = source_url
            payload = client._properties(video, "", "已完成", "")
            self.assertEqual(payload["原文链接"]["url"], source_url)
            self.assertEqual(payload["视频链接"]["url"], source_url)

    def test_legacy_video_bookmark_without_video_url_requires_refresh(self):
        video = item("video", platform="youtube")
        video.source_url = "https://www.youtube.com/watch?v=abcdefghijk"
        legacy = {
            "properties": {
                "原文链接": {"url": video.source_url},
                "视频链接": {"url": None},
                "本地媒体路径": {"rich_text": []},
            }
        }
        current = {
            "properties": {
                "原文链接": {"url": video.source_url},
                "视频链接": {"url": video.source_url},
                "本地媒体路径": {"rich_text": []},
            }
        }
        self.assertTrue(_video_bookmark_needs_refresh(video, legacy))
        self.assertFalse(_video_bookmark_needs_refresh(video, current))

    def test_xhs_images_keep_source_order_before_body(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "1.jpg"
            second = Path(directory) / "2.jpg"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            client = BlockClient()
            blocks = client._content_blocks(
                item(media=[MediaAsset("image", local_path=str(first)), MediaAsset("image", local_path=str(second))]),
                "",
            )
            self.assertEqual(client.uploaded, [first, second])
            self.assertEqual([block["type"] for block in blocks], ["image", "image", "paragraph"])

    def test_x_post_keeps_body_before_attachments(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "1.jpg"
            image.write_bytes(b"one")
            client = BlockClient()
            blocks = client._content_blocks(
                item("post", [MediaAsset("image", local_path=str(image))], platform="x"),
                "",
            )
            self.assertEqual([block["type"] for block in blocks], ["paragraph", "image"])

    def test_pure_text_body_needs_no_upload(self):
        client = BlockClient()
        blocks = client._content_blocks(item(media=[]), "")
        self.assertEqual(client.uploaded, [])
        self.assertEqual([block["type"] for block in blocks], ["paragraph"])

    def test_x_article_uses_ordered_twimg_urls_without_file_uploads(self):
        client = BlockClient()
        article = item(
            "article",
            [MediaAsset("image", local_path="/tmp/should-not-upload.jpg")],
            platform="x",
        )
        article.article = {
            "html": (
                '<p>正文</p><img src="https://pbs.twimg.com/media/first?format=jpg&amp;name=orig">'
                '<img src="https://pbs.twimg.com/media/second?format=jpg&amp;name=orig">'
                '<img src="https://example.com/not-x.jpg">'
            )
        }
        blocks = client._content_blocks(article, "")
        images = [block["image"] for block in blocks if block["type"] == "image"]
        self.assertEqual(client.uploaded, [])
        self.assertEqual(
            [image["external"]["url"] for image in images],
            [
                "https://pbs.twimg.com/media/first?format=jpg&name=orig",
                "https://pbs.twimg.com/media/second?format=jpg&name=orig",
            ],
        )

    def test_x_article_preserves_semantic_blocks_and_inline_formatting(self):
        client = BlockClient()
        article = item("article", platform="x")
        article.article = {
            "html": (
                '<h1>文章标题</h1><p>第一段 <strong>重点</strong> '
                '<a href="https://example.com/source">链接</a></p>'
                '<img src="https://pbs.twimg.com/media/first?format=jpg&amp;name=orig" alt="第一张">'
                '<h2>章节</h2><ul><li><p><em>列表项</em></p></li></ul>'
                '<ol><li>步骤一</li></ol><blockquote><p>引用</p></blockquote>'
                '<pre>print("ok")</pre>'
                '<img src="https://pbs.twimg.com/media/second?format=png&amp;name=orig">'
                '<p>最后一段</p>'
            )
        }
        blocks = client._content_blocks(article, "")
        self.assertEqual(
            [block["type"] for block in blocks],
            [
                "heading_1", "paragraph", "image", "heading_2",
                "bulleted_list_item", "numbered_list_item", "quote",
                "code", "image", "paragraph",
            ],
        )
        paragraph = blocks[1]["paragraph"]["rich_text"]
        self.assertTrue(paragraph[1]["annotations"]["bold"])
        self.assertEqual(paragraph[3]["text"]["link"]["url"], "https://example.com/source")
        self.assertTrue(blocks[4]["bulleted_list_item"]["rich_text"][0]["annotations"]["italic"])
        self.assertEqual(blocks[7]["code"]["language"], "plain text")
        self.assertEqual(blocks[2]["image"]["caption"][0]["text"]["content"], "第一张")

    def test_x_article_falls_back_to_body_and_downloaded_images_without_usable_html(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "fallback.jpg"
            image.write_bytes(b"image")
            client = BlockClient()
            article = item(
                "article",
                [MediaAsset("image", local_path=str(image))],
                platform="x",
            )
            article.article = {"html": "<script>ignored()</script>"}
            blocks = client._content_blocks(article, "")
            self.assertEqual([block["type"] for block in blocks], ["paragraph", "image"])
            self.assertEqual(client.uploaded, [image])

    def test_x_article_splits_long_semantic_text_at_notion_limit(self):
        client = BlockClient()
        article = item("article", platform="x")
        article.article = {"html": f"<p>{'x' * 4500}</p>"}
        blocks = client._content_blocks(article, "")
        lengths = [
            sum(len(part["text"]["content"]) for part in block["paragraph"]["rich_text"])
            for block in blocks
        ]
        self.assertEqual(lengths, [2000, 2000, 500])

    def test_video_uploads_cover_but_not_original_video(self):
        with tempfile.TemporaryDirectory() as directory:
            cover = Path(directory) / "cover.jpg"
            video = Path(directory) / "video.mp4"
            cover.write_bytes(b"cover")
            video.write_bytes(b"video")
            client = BlockClient()
            blocks = client._content_blocks(
                item(
                    "video",
                    [
                        MediaAsset("image", local_path=str(cover)),
                        MediaAsset("video", url="https://example.test/video.mp4", local_path=str(video)),
                    ],
                ),
                "/data/video",
            )
            self.assertEqual(client.uploaded, [cover])
            self.assertEqual(sum(block["type"] == "image" for block in blocks), 1)

    def test_live_photo_keeps_motion_links_and_local_path(self):
        client = BlockClient()
        blocks = client._content_blocks(
            item("live_photo", [MediaAsset("live", url="https://example.test/motion.mp4")]),
            "/data/live",
        )
        rendered = str(blocks)
        self.assertIn("https://example.test/motion.mp4", rendered)
        self.assertIn("/data/live", rendered)

    def test_long_body_is_split_to_notion_rich_text_limit(self):
        self.assertEqual([len(part) for part in _split_text("x" * 4500, 2000)], [2000, 2000, 500])

    def test_http_contract_creates_body_and_completes_record(self):
        try:
            import httpx
        except ImportError:
            self.skipTest("httpx is not installed in the test interpreter")

        calls = []

        def handler(request):
            calls.append((request.method, request.url.path, json.loads(request.content or b"{}")))
            if request.url.path.endswith("/query"):
                return httpx.Response(200, json={"results": []})
            if request.url.path.endswith("/pages") and request.method == "POST":
                return httpx.Response(200, json={"id": "page-1", "url": "https://notion.test/page-1"})
            return httpx.Response(200, json={"results": [], "has_more": False})

        client = NotionClient(token="test", data_source_id="source-1", base_url="https://notion.test/v1")
        client._client.close()
        client._client = httpx.Client(
            base_url="https://notion.test/v1",
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer test", "Notion-Version": "2026-03-11"},
        )
        result = client.save_item(item())
        client.close()
        self.assertEqual(result["status"], "success")
        self.assertEqual(
            [(method, path) for method, path, _ in calls],
            [
                ("POST", "/v1/data_sources/source-1/query"),
                ("POST", "/v1/pages"),
                ("PATCH", "/v1/blocks/page-1/children"),
                ("PATCH", "/v1/pages/page-1"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
