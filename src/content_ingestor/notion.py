from __future__ import annotations

import io
import html
import mimetypes
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from .config import NOTION_API_VERSION, notion_data_source_id, notion_token
from .models import ContentItem, MediaAsset


SCHEMA: dict[str, dict[str, Any]] = {
    "平台": {"select": {"options": [
        {"name": "小红书", "color": "red"},
        {"name": "X", "color": "blue"},
    ]}},
    "内容类型": {
        "select": {
            "options": [
                {"name": "图文", "color": "blue"},
                {"name": "视频", "color": "red"},
                {"name": "Live Photo", "color": "purple"},
                {"name": "普通帖子", "color": "gray"},
                {"name": "Long Post", "color": "blue"},
                {"name": "Article", "color": "green"},
            ]
        }
    },
    "内容 ID": {"rich_text": {}},
    "原文链接": {"url": {}},
    "作者": {"rich_text": {}},
    "发布时间": {"date": {}},
    "收藏时间": {"date": {}},
    "标签": {"multi_select": {"options": []}},
    "本地媒体路径": {"rich_text": {}},
    "保存状态": {
        "select": {
            "options": [
                {"name": "写入中", "color": "yellow"},
                {"name": "已完成", "color": "green"},
                {"name": "失败", "color": "red"},
            ]
        }
    },
    "失败信息": {"rich_text": {}},
    "抓取状态": {
        "select": {
            "options": [
                {"name": "complete", "color": "green"},
                {"name": "media_partial", "color": "yellow"},
                {"name": "metadata_only", "color": "yellow"},
                {"name": "authentication_required", "color": "orange"},
                {"name": "rate_limited", "color": "orange"},
                {"name": "deleted_or_private", "color": "red"},
            ]
        }
    },
}

EXPECTED_PROPERTY_TYPES = {
    "名称": "title",
    "视频链接": "url",
    **{name: next(iter(spec)) for name, spec in SCHEMA.items()},
}


class NotionError(RuntimeError):
    def __init__(self, stage: str, code: str, message: str):
        super().__init__(message)
        self.stage = stage
        self.code = code


class NotionClient:
    def __init__(
        self,
        token: str | None = None,
        data_source_id: str | None = None,
        *,
        base_url: str = "https://api.notion.com/v1",
        timeout: float = 30.0,
    ) -> None:
        self.token = token if token is not None else notion_token()
        self.data_source_id = data_source_id or notion_data_source_id()
        if not self.token:
            raise NotionError("notion_auth", "notion_token_missing", "CONTENT_OS_NOTION_TOKEN is not configured")
        if not self.data_source_id:
            raise NotionError(
                "notion_auth",
                "notion_data_source_missing",
                "CONTENT_OS_NOTION_DATA_SOURCE_ID is not configured",
            )
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise NotionError("notion_auth", "dependency_missing", "httpx is required for Notion sync") from exc
        self._httpx = httpx
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            # X downloaders may need the user's desktop proxy, but routing the
            # Notion API through the same shared exit can trigger Cloudflare.
            # Notion is independently reachable on this runtime, so keep its
            # transport isolated from HTTP(S)_PROXY and ALL_PROXY.
            trust_env=False,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": NOTION_API_VERSION,
                # Cloudflare currently blocks the generic python-httpx
                # fingerprint on some Notion POST requests. Identify this
                # client explicitly instead of impersonating a browser.
                "User-Agent": "CollectHub/1.0 (+https://github.com/suneveryday/CollectHub)",
            },
        )

    def close(self) -> None:
        self._client.close()

    def get_schema(self) -> dict[str, Any]:
        return self._request("GET", f"/data_sources/{self.data_source_id}")

    def schema_report(self) -> dict[str, Any]:
        payload = self.get_schema()
        actual = payload.get("properties") if isinstance(payload.get("properties"), dict) else {}
        missing = [name for name in EXPECTED_PROPERTY_TYPES if name not in actual]
        mismatched = [
            {"name": name, "expected": expected, "actual": actual[name].get("type")}
            for name, expected in EXPECTED_PROPERTY_TYPES.items()
            if name in actual and actual[name].get("type") != expected
        ]
        missing_options: dict[str, list[str]] = {}
        for name in ("平台", "内容类型", "抓取状态"):
            if name not in actual or actual[name].get("type") != "select":
                continue
            expected_names = {option["name"] for option in SCHEMA[name]["select"]["options"]}
            actual_names = {option.get("name") for option in actual[name].get("select", {}).get("options", [])}
            absent = sorted(expected_names - actual_names)
            if absent:
                missing_options[name] = absent
        return {
            "ok": not missing and not mismatched and not missing_options,
            "missing": missing,
            "mismatched": mismatched,
            "missing_options": missing_options,
        }

    def apply_schema(self) -> dict[str, Any]:
        report = self.schema_report()
        if report["mismatched"]:
            mismatch = ", ".join(item["name"] for item in report["mismatched"])
            raise NotionError("notion_schema", "schema_mismatch", f"existing properties have incompatible types: {mismatch}")
        additions = {name: SCHEMA[name] for name in report["missing"] if name in SCHEMA}
        payload = self.get_schema()
        actual = payload.get("properties") if isinstance(payload.get("properties"), dict) else {}
        for name in report.get("missing_options", {}):
            existing = actual.get(name, {}).get("select", {}).get("options", [])
            by_name = {option.get("name"): option for option in existing if option.get("name")}
            for option in SCHEMA[name]["select"]["options"]:
                by_name.setdefault(option["name"], option)
            additions[name] = {"select": {"options": list(by_name.values())}}
        if additions:
            self._request("PATCH", f"/data_sources/{self.data_source_id}", json={"properties": additions})
        return self.schema_report()

    def find_record(self, item: ContentItem) -> dict[str, Any] | None:
        payload = self._request(
            "POST",
            f"/data_sources/{self.data_source_id}/query",
            json={
                "filter": {
                    "and": [
                        {"property": "平台", "select": {"equals": _platform_label(item.platform)}},
                        {"property": "内容 ID", "rich_text": {"equals": item.source_id}},
                    ]
                },
                "page_size": 2,
            },
        )
        results = payload.get("results") if isinstance(payload.get("results"), list) else []
        return results[0] if results else None

    def save_item(
        self, item: ContentItem, *, local_path: str = "", force: bool = False
    ) -> dict[str, Any]:
        existing = self.find_record(item)
        if (
            existing
            and _select_value(existing, "保存状态") == "已完成"
            and _select_value(existing, "抓取状态") == "complete"
            and not force
        ):
            return self._result("already_saved", item, existing, local_path)

        page = existing or self._create_page(item, local_path)
        page_id = str(page["id"])
        try:
            blocks = self._content_blocks(item, local_path)
            if existing:
                self._update_page(page_id, item, local_path, "写入中", "")
                self._clear_children(page_id)
            for chunk in _chunks(blocks, 100):
                self._request(
                    "PATCH", f"/blocks/{page_id}/children", json={"children": chunk}, retry_safe=False
                )
            self._update_page(page_id, item, local_path, "已完成", "")
        except Exception as exc:
            message = str(exc)
            try:
                self._update_page(page_id, item, local_path, "失败", message[:1900])
            except Exception:
                pass
            if isinstance(exc, NotionError):
                raise
            raise NotionError("notion_write", "notion_write_failed", message) from exc
        page["url"] = page.get("url") or _page_url(page_id)
        result_status = "success" if item.capture_status == "complete" else "partial"
        return self._result(result_status, item, page, local_path)

    def save_failure(self, item: ContentItem, error: str) -> dict[str, Any]:
        existing = self.find_record(item)
        page = existing or self._create_page(item, "")
        page_id = str(page["id"])
        if existing:
            self._clear_children(page_id)
        self._update_page(page_id, item, "", "失败", error[:1900])
        self._request(
            "PATCH",
            f"/blocks/{page_id}/children",
            retry_safe=False,
            json={"children": [_paragraph(f"抓取失败：{error[:1900]}")]},
        )
        page["url"] = page.get("url") or _page_url(page_id)
        return self._result("failed", item, page, "")

    def _create_page(self, item: ContentItem, local_path: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/pages",
            retry_safe=False,
            json={
                "parent": {"type": "data_source_id", "data_source_id": self.data_source_id},
                "properties": self._properties(item, local_path, "写入中", ""),
            },
        )

    def _update_page(self, page_id: str, item: ContentItem, local_path: str, status: str, error: str) -> None:
        self._request(
            "PATCH",
            f"/pages/{page_id}",
            json={"properties": self._properties(item, local_path, status, error)},
        )

    def _properties(
        self, item: ContentItem, local_path: str, status: str, error: str
    ) -> dict[str, Any]:
        video_url = next((asset.url for asset in item.media if asset.kind in {"video", "live"} and asset.url), "")
        properties: dict[str, Any] = {
            "名称": _title(item.title),
            "平台": {"select": {"name": _platform_label(item.platform)}},
            "内容类型": {"select": {"name": _type_label(item.content_type)}},
            "内容 ID": _rich_text(item.source_id),
            "原文链接": {"url": item.source_url or None},
            "作者": _rich_text(item.author),
            "发布时间": (
                {"date": {"start": item.published_at}} if item.published_at else {"date": None}
            ),
            "收藏时间": {"date": {"start": item.captured_at}},
            "标签": {"multi_select": [{"name": tag[:100]} for tag in item.tags[:100]]},
            "视频链接": {"url": video_url or None},
            "本地媒体路径": _rich_text(local_path),
            "保存状态": {"select": {"name": status}},
            "失败信息": _rich_text(error),
            "抓取状态": {"select": {"name": item.capture_status}},
        }
        return properties

    def _content_blocks(self, item: ContentItem, local_path: str) -> list[dict[str, Any]]:
        blocks = self._source_blocks(item)
        if item.reply_to:
            blocks.append(_heading("回复关系"))
            blocks.append(_paragraph(str(item.reply_to.get("url") or item.reply_to.get("content_id") or ""), link=item.reply_to.get("url") or None))
        if item.quoted_post:
            blocks.append(_heading("引用帖子"))
            blocks.append(_paragraph(str(item.quoted_post.get("url") or item.quoted_post.get("content_id") or ""), link=item.quoted_post.get("url") or None))
        if item.metrics:
            blocks.append(_heading("互动数据"))
            blocks.append(_paragraph(" · ".join(f"{key}: {value}" for key, value in item.metrics.items())))
        if item.content_type == "live_photo":
            motion_urls = [asset.url for asset in item.media if asset.kind == "live" and asset.url]
            if motion_urls:
                blocks.append(_heading("动态内容"))
                blocks.extend(_paragraph(url, link=url) for url in motion_urls)
        if local_path:
            blocks.append(_heading("本地媒体"))
            blocks.append(_paragraph(local_path))
        if item.extractor_versions:
            blocks.append(_heading("抓取信息"))
            blocks.append(_paragraph(" · ".join(f"{key} {value}" for key, value in item.extractor_versions.items())))
        return blocks

    def _source_blocks(self, item: ContentItem) -> list[dict[str, Any]]:
        if item.platform == "x" and item.content_type == "article":
            article_blocks = _x_article_blocks(str(item.article.get("html") or ""))
            if article_blocks:
                if not any(block["type"] == "image" for block in article_blocks):
                    article_blocks.extend(self._uploaded_image_blocks(item.media))
                return article_blocks

        text_blocks = [_paragraph(part) for part in _split_text(item.body, 2000)] if item.body else []
        image_blocks = self._uploaded_image_blocks(item.media)
        if item.platform == "xiaohongshu" and item.content_type == "image":
            return image_blocks + text_blocks
        return text_blocks + image_blocks

    def _uploaded_image_blocks(self, media: list[MediaAsset]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        images = [asset for asset in media if asset.kind == "image" and asset.local_path]
        for index, asset in enumerate(images, 1):
            upload_id = self._upload(Path(asset.local_path))
            blocks.append(_file_upload_image(upload_id, f"图片 {index}"))
        return blocks

    def _upload(self, path: Path) -> str:
        if not path.is_file():
            raise NotionError("notion_upload", "media_missing", f"media file is missing: {path}")
        size = path.stat().st_size
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if size <= 20 * 1024 * 1024:
            upload = self._request(
                "POST",
                "/file_uploads",
                retry_safe=False,
                json={"mode": "single_part", "filename": path.name, "content_type": content_type},
            )
            with path.open("rb") as handle:
                self._request(
                    "POST",
                    f"/file_uploads/{upload['id']}/send",
                    retry_safe=False,
                    files={"file": (path.name, handle, content_type)},
                )
            return str(upload["id"])

        part_size = 20 * 1024 * 1024
        number_of_parts = (size + part_size - 1) // part_size
        upload = self._request(
            "POST",
            "/file_uploads",
            retry_safe=False,
            json={
                "mode": "multi_part",
                "filename": path.name,
                "content_type": content_type,
                "number_of_parts": number_of_parts,
            },
        )
        with path.open("rb") as handle:
            for part_number in range(1, number_of_parts + 1):
                data = handle.read(part_size)
                self._request(
                    "POST",
                    f"/file_uploads/{upload['id']}/send",
                    retry_safe=False,
                    data={"part_number": str(part_number)},
                    files={"file": (path.name, io.BytesIO(data), content_type)},
                )
        self._request("POST", f"/file_uploads/{upload['id']}/complete", retry_safe=False)
        return str(upload["id"])

    def _clear_children(self, page_id: str) -> None:
        cursor: str | None = None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            payload = self._request("GET", f"/blocks/{page_id}/children", params=params)
            for block in payload.get("results", []):
                self._request("PATCH", f"/blocks/{block['id']}", json={"in_trash": True})
            if not payload.get("has_more"):
                break
            cursor = payload.get("next_cursor")

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        retry_safe = bool(kwargs.pop("retry_safe", True))
        for attempt in range(4):
            try:
                response = self._client.request(method, path, **kwargs)
            except self._httpx.HTTPError as exc:
                if attempt == 3 or not retry_safe:
                    raise NotionError("notion_request", "network_error", str(exc)) from exc
                time.sleep(2**attempt)
                continue
            if response.status_code == 429 and attempt < 3:
                time.sleep(float(response.headers.get("Retry-After", "1")))
                continue
            if response.status_code >= 500 and attempt < 3 and retry_safe:
                time.sleep(2**attempt)
                continue
            if response.is_error:
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                if response.status_code == 403 and _is_cloudflare_block(response):
                    ray_id = _cloudflare_ray_id(response)
                    suffix = f" (Ray ID: {ray_id})" if ray_id else ""
                    raise NotionError(
                        "notion_request",
                        "cloudflare_blocked",
                        f"Notion API {method} {path} was blocked by Cloudflare{suffix}; retry once later",
                    )
                code = str(payload.get("code") or f"http_{response.status_code}")
                message = str(payload.get("message") or response.text or "Notion API request failed")
                stage = "notion_auth" if response.status_code in {401, 403, 404} else "notion_request"
                raise NotionError(stage, code, message)
            if not response.content:
                return {}
            return response.json()
        raise NotionError("notion_request", "retry_exhausted", "Notion request retries exhausted")

    @staticmethod
    def _result(status: str, item: ContentItem, page: dict[str, Any], local_path: str) -> dict[str, Any]:
        result = {
            "status": status,
            "platform": item.platform,
            "source_id": item.source_id,
            "source_url": item.source_url,
            "content_type": item.content_type,
            "notion_url": page.get("url") or _page_url(str(page["id"])),
            "errors": [],
            "capture_status": item.capture_status,
        }
        if local_path:
            result["local_path"] = local_path
            result["target_dir"] = local_path
        return result


def _is_cloudflare_block(response: Any) -> bool:
    content_type = str(response.headers.get("content-type") or "").lower()
    body = response.text[:4096].lower()
    return "text/html" in content_type and "cloudflare" in body and "blocked" in body


def _cloudflare_ray_id(response: Any) -> str:
    header = str(response.headers.get("cf-ray") or "").strip()
    if header:
        return header
    match = re.search(r"cloudflare ray id:\s*(?:<[^>]+>)*\s*([a-z0-9-]+)", response.text, re.IGNORECASE)
    return match.group(1) if match else ""


class _ArticleBlockParser(HTMLParser):
    BLOCK_TAGS = {
        "p": "paragraph",
        "h1": "heading_1",
        "h2": "heading_2",
        "h3": "heading_3",
        "pre": "code",
    }
    CONTAINER_TAGS = {"article", "div", "section", "figure", "figcaption"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict[str, Any]] = []
        self._block_type: str | None = None
        self._rich_text: list[dict[str, Any]] = []
        self._lists: list[str] = []
        self._semantic_blocks: list[tuple[str, str]] = []
        self._links: list[str | None] = []
        self._bold = 0
        self._italic = 0
        self._code = 0
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {name.lower(): value for name, value in attrs}
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in self.BLOCK_TAGS:
            self._flush()
            default_type = self.BLOCK_TAGS[tag]
            self._block_type = self._active_semantic_type() if tag == "p" else default_type
        elif tag in self.CONTAINER_TAGS:
            self._flush()
        elif tag in {"ul", "ol"}:
            self._lists.append(tag)
        elif tag == "blockquote":
            self._flush()
            self._semantic_blocks.append((tag, "quote"))
            self._block_type = "quote"
        elif tag == "li":
            self._flush()
            block_type = "numbered_list_item" if self._lists and self._lists[-1] == "ol" else "bulleted_list_item"
            self._semantic_blocks.append((tag, block_type))
            self._block_type = block_type
        elif tag == "img":
            self._flush()
            url = _safe_article_image_url(attributes.get("src"))
            if url:
                self.blocks.append(_external_image(url, str(attributes.get("alt") or "")[:2000]))
        elif tag == "hr":
            self._flush()
            self.blocks.append({"object": "block", "type": "divider", "divider": {}})
        elif tag == "br":
            self._append_text("\n")
        elif tag == "a":
            self._links.append(_safe_http_url(attributes.get("href")))
        elif tag in {"strong", "b"}:
            self._bold += 1
        elif tag in {"em", "i"}:
            self._italic += 1
        elif tag == "code" and self._block_type != "code":
            self._code += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in self.BLOCK_TAGS or tag in self.CONTAINER_TAGS:
            self._flush()
        elif tag in {"blockquote", "li"}:
            self._flush()
            self._pop_semantic_block(tag)
        elif tag in {"ul", "ol"}:
            if self._lists:
                self._lists.pop()
        elif tag == "a":
            if self._links:
                self._links.pop()
        elif tag in {"strong", "b"}:
            self._bold = max(0, self._bold - 1)
        elif tag in {"em", "i"}:
            self._italic = max(0, self._italic - 1)
        elif tag == "code" and self._block_type != "code":
            self._code = max(0, self._code - 1)

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._append_text(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _append_text(self, value: str) -> None:
        if not value:
            return
        if self._block_type is None:
            self._block_type = self._active_semantic_type()
        annotations = {
            "bold": self._bold > 0,
            "italic": self._italic > 0,
            "strikethrough": False,
            "underline": False,
            "code": self._code > 0 or self._block_type == "code",
            "color": "default",
        }
        text: dict[str, Any] = {"content": value}
        link = self._links[-1] if self._links else None
        if link:
            text["link"] = {"url": link}
        self._rich_text.append({"type": "text", "text": text, "annotations": annotations})

    def _flush(self) -> None:
        if not self._block_type:
            return
        if any(str(part.get("text", {}).get("content") or "").strip() for part in self._rich_text):
            for rich_text in _split_rich_text(self._rich_text, 2000):
                self.blocks.append(_rich_text_block(self._block_type, rich_text))
        self._block_type = None
        self._rich_text = []

    def _active_semantic_type(self) -> str:
        return self._semantic_blocks[-1][1] if self._semantic_blocks else "paragraph"

    def _pop_semantic_block(self, tag: str) -> None:
        for index in range(len(self._semantic_blocks) - 1, -1, -1):
            if self._semantic_blocks[index][0] == tag:
                del self._semantic_blocks[index]
                return


def _x_article_blocks(value: str) -> list[dict[str, Any]]:
    if not value:
        return []
    parser = _ArticleBlockParser()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, AssertionError):
        return []
    return parser.blocks


def _safe_article_image_url(value: str | None) -> str | None:
    url = html.unescape(str(value or "")).strip()
    parsed = urlparse(url)
    if parsed.scheme == "https" and (parsed.hostname or "").lower().endswith("twimg.com"):
        return url
    return None


def _safe_http_url(value: str | None) -> str | None:
    url = html.unescape(str(value or "")).strip()
    return url if urlparse(url).scheme in {"http", "https"} else None


def _select_value(page: dict[str, Any], name: str) -> str:
    prop = page.get("properties", {}).get(name, {})
    selected = prop.get("select") if isinstance(prop, dict) else None
    return str(selected.get("name") or "") if isinstance(selected, dict) else ""


def _title(value: str) -> dict[str, Any]:
    return {"title": [{"type": "text", "text": {"content": value[:2000]}}]}


def _rich_text(value: str) -> dict[str, Any]:
    return (
        {"rich_text": [{"type": "text", "text": {"content": value[:2000]}}]}
        if value
        else {"rich_text": []}
    )


def _paragraph(value: str, *, link: str | None = None) -> dict[str, Any]:
    text: dict[str, Any] = {"content": value[:2000]}
    if link:
        text["link"] = {"url": link}
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": text}]},
    }


def _rich_text_block(block_type: str, rich_text: list[dict[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {"rich_text": rich_text}
    if block_type == "code":
        payload["language"] = "plain text"
    return {"object": "block", "type": block_type, block_type: payload}


def _split_rich_text(parts: list[dict[str, Any]], limit: int) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    remaining = limit
    for part in parts:
        content = str(part.get("text", {}).get("content") or "")
        offset = 0
        while offset < len(content):
            if remaining == 0 or len(current) == 100:
                chunks.append(current)
                current = []
                remaining = limit
            piece = content[offset : offset + remaining]
            copied = {
                **part,
                "text": {**part.get("text", {}), "content": piece},
            }
            current.append(copied)
            offset += len(piece)
            remaining -= len(piece)
    if current:
        chunks.append(current)
    return chunks


def _external_image(url: str, caption: str = "") -> dict[str, Any]:
    return {
        "object": "block",
        "type": "image",
        "image": {
            "type": "external",
            "external": {"url": url},
            "caption": _caption(caption),
        },
    }


def _file_upload_image(upload_id: str, caption: str = "") -> dict[str, Any]:
    return {
        "object": "block",
        "type": "image",
        "image": {
            "type": "file_upload",
            "file_upload": {"id": upload_id},
            "caption": _caption(caption),
        },
    }


def _caption(value: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": value[:2000]}}] if value else []


def _heading(value: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": value}}]},
    }


def _split_text(value: str, limit: int) -> list[str]:
    return [value[index : index + limit] for index in range(0, len(value), limit)] or [""]


def _chunks(values: list[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _type_label(content_type: str) -> str:
    return {
        "image": "图文", "video": "视频", "live_photo": "Live Photo",
        "post": "普通帖子", "long_post": "Long Post", "article": "Article",
    }.get(content_type, content_type)


def _platform_label(platform: str) -> str:
    return {"xiaohongshu": "小红书", "x": "X"}.get(platform, platform)


def _page_url(page_id: str) -> str:
    return f"https://www.notion.so/{page_id.replace('-', '')}"
