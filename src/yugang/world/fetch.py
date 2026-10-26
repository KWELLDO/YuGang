"""Read one public web page as plain text -- her first window onto the world.

Reading is the cheap half of having a world: one GET is undone by doing nothing,
so fetching needs no operator approval. Two things still need a guard, and both
protect the operator rather than her:

- She runs on a cloud box. Its loopback interface, its private network and its
  metadata endpoint (169.254.169.254) belong to the infrastructure. A page she
  just read could talk her into fetching "http://169.254.169.254/..." and
  handing back a credential, so every URL -- and every redirect hop -- is
  resolved first and refused if *any* address is private, loopback, link-local,
  multicast, reserved, unspecified, CGNAT (100.64.0.0/10) or 169.254.0.0/16.
- A page can stream 200 MB. The reader stops at max_bytes + 1 and says so.

Nothing here raises: a bad scheme, embedded credentials, an unsupported content
type, a timeout, a broken TLS chain all come back as
FetchResult(ok=False, error="..."). The tool layer prints that error to her
verbatim, so she learns what happened instead of seeing a traceback.
"""

from __future__ import annotations

import codecs
import html as html_lib
import http.client
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser

__all__ = ["FetchResult", "blocked_reason", "fetch", "html_to_text"]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8"
_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "text/plain;q=0.8,*/*;q=0.5"
)

_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_APPLICATION_TEXT_TYPES = frozenset(
    {"application/json", "application/xml", "application/xhtml+xml"}
)
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_METADATA = ipaddress.ip_network("169.254.0.0/16")

# Charset hints. The Content-Type header wins; otherwise an early <meta> or an
# XML declaration is the best guess, which is how a legacy GB2312 page is read
# without mojibake.
_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([A-Za-z0-9_.:-]+)", re.IGNORECASE)
_META_CHARSET_RE = re.compile(
    rb"<meta[^>]{0,300}?charset\s*=\s*[\"']?\s*([A-Za-z0-9_.:-]+)", re.IGNORECASE
)
_XML_ENCODING_RE = re.compile(
    rb"<\?xml[^>]{0,200}?encoding\s*=\s*[\"']\s*([A-Za-z0-9_.:-]+)", re.IGNORECASE
)
_ENCODING_FIXUPS = {
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "gb_2312-80": "gb18030",
    "x-gbk": "gb18030",
    "cp936": "gb18030",
    "latin1": "iso-8859-1",
}

_INLINE_WS = re.compile(r"\s+")
_BLANK_RUN = re.compile(r"\n{3,}")

# Tags whose text is never page text. <head> is deliberately not here because
# <title> lives inside it and is extracted separately.
_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "template"})
# Tags that start a new visual line. Everything else is inline.
_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl",
        "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p",
        "pre", "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr",
        "ul",
    }
)


@dataclass(frozen=True)
class FetchResult:
    """What one GET produced. The error field is Chinese, set only when not ok."""

    ok: bool
    url: str
    status: int | None
    content_type: str
    title: str
    text: str
    chars: int
    bytes: int
    truncated: bool
    error: str | None = None


class _Redirected(Exception):
    """A 3xx hop, surfaced so fetch can inspect the next address before opening it."""

    def __init__(self, location: str, status: int):
        super().__init__(location)
        self.location = location
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects inside urllib; fetch walks the hops itself."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        # Close the hop's body before unwinding: a redirect page can still be big.
        try:
            fp.close()
        except Exception:
            pass
        raise _Redirected(str(newurl), int(code))


def _is_internal_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return True
    if address.version == 4:
        return address in _CGNAT or address in _METADATA
    return False


def _resolved_addresses(host: str, port: int) -> list[ipaddress._BaseAddress] | None:
    """Resolve a host once. None means "could not resolve", not "empty"."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        return None
    addresses: list[ipaddress._BaseAddress] = []
    for info in infos:
        raw = info[4][0]
        if not isinstance(raw, str):
            continue
        try:
            addresses.append(ipaddress.ip_address(raw.split("%", 1)[0]))
        except ValueError:
            continue
    return addresses


def blocked_reason(url: str) -> str | None:
    """Return a Chinese reason when this URL must not be fetched, else None.

    The check is deliberately conservative at the address level: if *any*
    address a hostname resolves to is internal, the whole URL is refused. A
    resolution failure returns None here (the fetch itself reports
    "解析不到主机") because an unresolvable hostname cannot reach anything.
    """
    if not isinstance(url, str) or not url.strip():
        return "没有网址可读"
    candidate = url.strip()
    try:
        parts = urllib.parse.urlsplit(candidate)
    except ValueError:
        return "网址格式不对"
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return "只读 http/https 链接，别的链接不读"
    if parts.username is not None or parts.password is not None:
        return "网址里带着账号密码，不读"
    host = parts.hostname
    if not host:
        return "网址里没有主机名，不读"
    default_port = 443 if scheme == "https" else 80
    try:
        port = parts.port or default_port
    except ValueError:
        return "网址里的端口不对，不读"

    bare = host.split("%", 1)[0]
    try:
        literal = ipaddress.ip_address(bare)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_internal_address(literal):
            return "目标是内网/本机地址，不读"
        return None

    addresses = _resolved_addresses(host, port)
    if addresses is None:
        # Cannot confirm where it points; fetch will fail on connect and name it.
        return None
    if any(_is_internal_address(address) for address in addresses):
        return "目标是内网/本机地址，不读"
    return None


class _TextExtractor(HTMLParser):
    """Collect visible text, remembering where block tags end lines."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._title_parts: list[str] = []
        self._skip_depth = 0
        self._title_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._title_depth += 1
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if self._skip_depth or tag == "title":
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            if self._title_depth:
                self._title_depth -= 1
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data:
            return
        if self._title_depth:
            self._title_parts.append(data)
            return
        self._parts.append(_INLINE_WS.sub(" ", data))

    def title(self) -> str:
        return _INLINE_WS.sub(" ", "".join(self._title_parts)).strip()

    def text(self) -> str:
        raw = "".join(self._parts).replace("\r\n", "\n").replace("\r", "\n")
        lines = [_INLINE_WS.sub(" ", line).strip() for line in raw.split("\n")]
        joined = "\n".join(lines)
        return _BLANK_RUN.sub("\n\n", joined).strip()


def _html_to_text_all(html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # A broken page still usually yielded text; keep whatever was collected.
        pass
    return parser.title(), parser.text()


def html_to_text(html: str, *, max_chars: int = 20000) -> tuple[str, str]:
    """Return (title, text) for an HTML string, clipped to max_chars.

    Script/style/noscript/svg content is dropped, block tags become newlines,
    entities are unescaped by the parser, runs of blank lines collapse to one
    blank line, and no tag or attribute survives into the text.
    """
    if not isinstance(html, str):
        html = str(html)
    title, text = _html_to_text_all(html)
    limit = max(0, int(max_chars))
    if limit == 0:
        return title, ""
    if len(text) > limit:
        text = text[:limit]
    return title, text


def _media_type(content_type: str | None) -> str:
    if not content_type:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def _content_type_allowed(media: str) -> bool:
    if not media:
        # No header at all: try to read it as text rather than refusing a page
        # that plainly came back. Binary garbage decodes to replacement chars.
        return True
    if media.startswith("text/"):
        return True
    if media in _APPLICATION_TEXT_TYPES:
        return True
    return media.startswith("application/") and (
        media.endswith("+json") or media.endswith("+xml")
    )


def _charset_from_content_type(content_type: str | None) -> str | None:
    match = _CHARSET_RE.search(content_type or "")
    return match.group(1).lower() if match else None


def _sniff_charset(head: bytes) -> str | None:
    for pattern in (_META_CHARSET_RE, _XML_ENCODING_RE):
        match = pattern.search(head)
        if match:
            return match.group(1).decode("ascii", "ignore").lower() or None
    return None


def _candidate_encodings(declared: str | None) -> list[str]:
    candidates: list[str] = []
    if declared:
        key = declared.strip().lower()
        mapped = _ENCODING_FIXUPS.get(key, key)
        candidates.extend([mapped, key])
    candidates.append("utf-8")
    unique: list[str] = []
    for encoding in candidates:
        if not encoding or encoding in unique:
            continue
        try:
            codecs.lookup(encoding)
        except LookupError:
            continue
        unique.append(encoding)
    return unique or ["utf-8"]


def _decode_body(raw: bytes, content_type: str | None) -> str:
    """Decode bytes without ever raising, trusting header over meta over utf-8."""
    if raw.startswith(codecs.BOM_UTF8):
        raw = raw[len(codecs.BOM_UTF8) :]
    declared = _charset_from_content_type(content_type) or _sniff_charset(raw[:4096])
    for encoding in _candidate_encodings(declared):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _looks_like_html(media: str, raw: bytes) -> bool:
    if media:
        return (
            media == "text/html"
            or media == "application/xhtml+xml"
            or media.endswith("+html")
        )
    head = raw[:256].lstrip(b" \t\r\n").lower()
    return (
        head.startswith(b"<!doctype html")
        or head.startswith(b"<html")
        or b"<html" in head
    )


def _request_headers() -> dict[str, str]:
    return {
        "User-Agent": _USER_AGENT,
        "Accept-Language": _ACCEPT_LANGUAGE,
        "Accept": _ACCEPT,
        "Connection": "close",
    }


def _build_opener() -> urllib.request.OpenerDirector:
    """Kept as a module-level seam so tests can serve fake responses."""
    return urllib.request.build_opener(_NoRedirect())


def _failure(
    url: str, error: str, *, status: int | None = None, content_type: str = ""
) -> FetchResult:
    return FetchResult(
        ok=False,
        url=url,
        status=status,
        content_type=content_type,
        title="",
        text="",
        chars=0,
        bytes=0,
        truncated=False,
        error=error,
    )


def _network_error_reason(exc: BaseException) -> str:
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, socket.gaierror):
        return "解析不到主机"
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "连接超时"
    if isinstance(reason, ConnectionRefusedError):
        return "连接被拒绝"
    if isinstance(reason, http.client.HTTPException):
        return "连接失败：HTTP 协议错误"
    text = str(reason).strip() or type(reason).__name__
    return f"连接失败：{text[:80]}"


def fetch(
    url: str,
    *,
    timeout: float = 20.0,
    max_bytes: int = 2_000_000,
    max_chars: int = 20000,
    max_redirects: int = 3,
) -> FetchResult:
    """GET one public http(s) URL and return its readable text.

    Redirects are walked by hand so each hop is re-checked against the SSRF
    guard before it is opened. At most max_bytes + 1 bytes are read.
    """
    current = (url or "").strip()
    byte_cap = max(1, int(max_bytes))
    char_cap = max(0, int(max_chars))
    hop_budget = max(0, int(max_redirects))
    redirects = 0

    opener = _build_opener()

    while True:
        reason = blocked_reason(current)
        if reason is not None:
            return _failure(current, reason)

        try:
            request = urllib.request.Request(
                current, headers=_request_headers(), method="GET"
            )
        except ValueError:
            return _failure(current, "网址格式不对")

        try:
            with opener.open(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 0) or 0)
                headers = response.headers
                raw_type = headers.get("Content-Type") or ""
                media = _media_type(raw_type)
                if not _content_type_allowed(media):
                    return _failure(
                        current,
                        f"不支持的内容类型：{media}",
                        status=status,
                        content_type=media,
                    )
                raw = response.read(byte_cap + 1)
        except _Redirected as exc:
            if redirects >= hop_budget:
                return _failure(
                    current,
                    f"跳转次数超过上限（{hop_budget} 次）",
                    status=exc.status,
                )
            redirects += 1
            current = urllib.parse.urljoin(current, exc.location)
            continue
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            try:
                exc.close()
            except Exception:
                pass
            if status in _REDIRECT_CODES:
                return _failure(current, "跳转地址缺失", status=status)
            return _failure(current, f"HTTP {status}", status=status)
        except (socket.timeout, TimeoutError):
            return _failure(current, "连接超时")
        except urllib.error.URLError as exc:
            return _failure(current, _network_error_reason(exc))
        except http.client.HTTPException:
            return _failure(current, "连接失败：HTTP 协议错误")
        except OSError as exc:
            return _failure(current, _network_error_reason(exc))
        except ValueError:
            return _failure(current, "网址格式不对")

        if not isinstance(raw, bytes):
            raw = bytes(raw)
        byte_truncated = len(raw) > byte_cap
        if byte_truncated:
            raw = raw[:byte_cap]

        content_type = media
        if _looks_like_html(media, raw):
            title, full_text = _html_to_text_all(_decode_body(raw, raw_type))
        else:
            title = ""
            full_text = _decode_body(raw, raw_type).strip()

        char_truncated = char_cap > 0 and len(full_text) > char_cap
        text = full_text[:char_cap] if char_cap > 0 else ""

        return FetchResult(
            ok=True,
            url=current,
            status=status,
            content_type=content_type,
            title=title,
            text=text,
            chars=len(text),
            bytes=len(raw),
            truncated=byte_truncated or char_truncated,
            error=None,
        )
