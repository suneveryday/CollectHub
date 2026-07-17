from __future__ import annotations

import ipaddress
import http.client
import socket
import ssl
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from .xhs_adapter import AdapterError


MAX_REDIRECTS = 5
MAX_HTML_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_MEDIA_BYTES = 100 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
TRACKING_PARAMETERS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid",
    "ref", "ref_src", "spm", "_r", "_t",
}


@dataclass(frozen=True)
class FetchResult:
    url: str
    content: bytes
    content_type: str


def normalize_public_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise AdapterError("unsupported_url", "only public http/https URLs are supported")
    if parsed.username or parsed.password:
        raise AdapterError("unsafe_url", "URLs containing credentials are not supported")
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        raise AdapterError("unsafe_url", "local network URLs are not supported")
    try:
        port = parsed.port
    except ValueError as exc:
        raise AdapterError("unsafe_url", "the URL contains an invalid port") from exc
    netloc = f"[{host}]" if ":" in host else host
    if port and not ((parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)):
        netloc += f":{port}"
    query = []
    for key, val in parse_qsl(parsed.query, keep_blank_values=True):
        lower = key.lower()
        if lower.startswith("utm_") or lower in TRACKING_PARAMETERS:
            continue
        query.append((key, val))
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", urlencode(query, doseq=True), ""))


def validate_public_destination(url: str) -> tuple[str, ...]:
    normalized = normalize_public_url(url)
    host = urlsplit(normalized).hostname or ""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    addresses: Iterable[str]
    if literal is not None:
        addresses = [str(literal)]
    else:
        try:
            addresses = {entry[4][0] for entry in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
        except socket.gaierror as exc:
            raise AdapterError("dns_failed", "the destination hostname could not be resolved") from exc
    if not addresses:
        raise AdapterError("dns_failed", "the destination hostname resolved to no addresses")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise AdapterError("unsafe_url", "the destination resolved to an invalid address") from exc
        if not ip.is_global:
            raise AdapterError("unsafe_url", "private, local, reserved, and non-global addresses are blocked")
    return tuple(sorted(addresses))


def fetch_html(url: str, *, client: httpx.Client | None = None, headers: dict[str, str] | None = None) -> FetchResult:
    return _fetch(url, {"text/html", "application/xhtml+xml"}, MAX_HTML_BYTES, client=client, headers=headers)


def fetch_image(url: str, *, client: httpx.Client | None = None) -> FetchResult:
    return _fetch(url, {"image/"}, MAX_IMAGE_BYTES, client=client)


def fetch_json(
    url: str,
    *,
    client: httpx.Client | None = None,
    headers: dict[str, str] | None = None,
) -> FetchResult:
    return _fetch(url, {"application/json"}, MAX_JSON_BYTES, client=client, headers=headers)


def _fetch(
    url: str,
    allowed_types: set[str],
    limit: int,
    *,
    client: httpx.Client | None,
    headers: dict[str, str] | None = None,
) -> FetchResult:
    current = normalize_public_url(url)
    credential_host = urlsplit(current).hostname or ""
    if client is None:
        return _fetch_pinned(current, allowed_types, limit, headers=headers, credential_host=credential_host)
    session = client
    try:
        for redirects in range(MAX_REDIRECTS + 1):
            validate_public_destination(current)
            try:
                with session.stream("GET", current, headers=_headers_for_host(headers, current, credential_host)) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if redirects == MAX_REDIRECTS:
                            raise AdapterError("too_many_redirects", "the page exceeded five redirects")
                        location = response.headers.get("location", "")
                        if not location:
                            raise AdapterError("invalid_redirect", "the server returned an empty redirect")
                        current = normalize_public_url(urljoin(current, location))
                        continue
                    if response.status_code in {401, 403}:
                        raise AdapterError("authentication_required", "the page requires authentication or denied access")
                    if response.status_code in {404, 410}:
                        raise AdapterError("content_not_found", "the page was not found")
                    if response.status_code == 429:
                        raise AdapterError("rate_limited", "the page rate-limited the request")
                    if response.status_code >= 400:
                        raise AdapterError("upstream_failed", f"the page returned HTTP {response.status_code}")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
                    if not any(content_type == allowed or (allowed.endswith("/") and content_type.startswith(allowed)) for allowed in allowed_types):
                        raise AdapterError("unsupported_content_type", f"unsupported content type: {content_type or 'unknown'}")
                    declared = response.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > limit:
                        raise AdapterError("response_too_large", f"response exceeds {limit} bytes")
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > limit:
                            raise AdapterError("response_too_large", f"response exceeds {limit} bytes")
                        chunks.append(chunk)
                    return FetchResult(str(response.url), b"".join(chunks), content_type)
            except httpx.TimeoutException as exc:
                raise AdapterError("parser_timeout", "the page request timed out") from exc
            except httpx.HTTPError as exc:
                raise AdapterError("upstream_failed", f"the page request failed: {type(exc).__name__}") from exc
        raise AdapterError("too_many_redirects", "the page exceeded five redirects")
    finally:
        pass


def _fetch_pinned(url: str, allowed_types: set[str], limit: int, *, headers: dict[str, str] | None, credential_host: str) -> FetchResult:
    current = url
    for redirects in range(MAX_REDIRECTS + 1):
        addresses = validate_public_destination(current)
        last_error: OSError | None = None
        response: http.client.HTTPResponse | None = None
        connection: http.client.HTTPConnection | None = None
        for address in addresses:
            try:
                connection = _connection_for(current, address)
                parsed = urlsplit(current)
                target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
                request_headers = {
                    "Host": parsed.netloc,
                    "User-Agent": "CollectHub/1.1 (+https://github.com/suneveryday/CollectHub)",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                    **_headers_for_host(headers, current, credential_host),
                }
                connection.request("GET", target, headers=request_headers)
                response = connection.getresponse()
                break
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                last_error = exc if isinstance(exc, OSError) else OSError(type(exc).__name__)
                if connection is not None:
                    connection.close()
                connection = None
        if response is None or connection is None:
            if isinstance(last_error, (TimeoutError, socket.timeout)):
                raise AdapterError("parser_timeout", "the page request timed out") from last_error
            raise AdapterError("upstream_failed", "the page request failed") from last_error
        try:
            if response.status in {301, 302, 303, 307, 308}:
                if redirects == MAX_REDIRECTS:
                    raise AdapterError("too_many_redirects", "the page exceeded five redirects")
                location = response.getheader("location", "")
                if not location:
                    raise AdapterError("invalid_redirect", "the server returned an empty redirect")
                current = normalize_public_url(urljoin(current, location))
                continue
            if response.status in {401, 403}:
                raise AdapterError("authentication_required", "the page requires authentication or denied access")
            if response.status in {404, 410}:
                raise AdapterError("content_not_found", "the page was not found")
            if response.status == 429:
                raise AdapterError("rate_limited", "the page rate-limited the request")
            if response.status >= 400:
                raise AdapterError("upstream_failed", f"the page returned HTTP {response.status}")
            content_type = (response.getheader("content-type", "").split(";", 1)[0].lower().strip())
            if not any(content_type == allowed or (allowed.endswith("/") and content_type.startswith(allowed)) for allowed in allowed_types):
                raise AdapterError("unsupported_content_type", f"unsupported content type: {content_type or 'unknown'}")
            declared = response.getheader("content-length", "")
            if declared.isdigit() and int(declared) > limit:
                raise AdapterError("response_too_large", f"response exceeds {limit} bytes")
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = response.read(min(64 * 1024, limit - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise AdapterError("response_too_large", f"response exceeds {limit} bytes")
                chunks.append(chunk)
            return FetchResult(current, b"".join(chunks), content_type)
        except (socket.timeout, TimeoutError) as exc:
            raise AdapterError("parser_timeout", "the page request timed out") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise AdapterError("upstream_failed", "the page request failed") from exc
        finally:
            connection.close()
    raise AdapterError("too_many_redirects", "the page exceeded five redirects")


def _connection_for(url: str, address: str) -> http.client.HTTPConnection:
    parsed = urlsplit(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if parsed.scheme == "https":
        return _PinnedHTTPSConnection(parsed.hostname or "", address, port, timeout=30)
    return _PinnedHTTPConnection(parsed.hostname or "", address, port, timeout=30)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, hostname: str, address: str, port: int, *, timeout: float) -> None:
        super().__init__(hostname, port=port, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _PinnedHTTPSConnection(_PinnedHTTPConnection):
    def connect(self) -> None:
        raw = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self.host)


def _headers_for_host(headers: dict[str, str] | None, url: str, credential_host: str) -> dict[str, str]:
    result = dict(headers or {})
    if (urlsplit(url).hostname or "") != credential_host:
        result.pop("Cookie", None)
        result.pop("cookie", None)
    return result
