"""Search the web and come back with a handful of readable hits.

Search is the other half of the window: instead of guessing a URL, she asks a
question and gets titles, links and snippets. It stays read-only -- no POST, no
forms -- but it still has to be honest about failure. "The query had no
results" and "the page layout changed and I parsed nothing" look identical to a
caller that only sees [], and confusing the two would teach her to believe a
lie about the world. So an unparsable page raises SearchError; [] is reserved
for a page that parsed cleanly and held no hits.

Backend note: html.duckduckgo.com and lite.duckduckgo.com are unreachable from
the cloud host, and baidu answers this client with a captcha wall, so the
backend is the plain HTML endpoint of cn.bing.com, whose result blocks are
<li class="b_algo">.
"""

from __future__ import annotations

import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser

__all__ = ["BACKEND", "SearchError", "SearchHit", "search"]

BACKEND = "https://cn.bing.com/search"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8"
_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5"

# One search page is a few hundred KB. Cap it so a hostile or broken endpoint
# cannot stream forever; a clipped page still parses its leading results.
_MAX_HTML_BYTES = 4_000_000

_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([A-Za-z0-9_.:-]+)", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")

_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "template"})
_VOID_TAGS = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }
)


class SearchError(RuntimeError):
    """A search that failed to happen, as opposed to one that found nothing."""


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str


def _clean(value: str) -> str:
    return _WS_RE.sub(" ", value).strip()


class _BingResults(HTMLParser):
    """Collect the three fields of each <li class="b_algo"> result block."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict[str, str]] = []
        self._block_count = 0

        self._stack: list[str] = []
        self._skip_depth = 0

        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []
        self._href: str | None = None
        self._in_title = False
        self._in_snippet = False
        self._seen_title_tag = False
        self._seen_snippet_tag = False

    # -- block lifecycle ---------------------------------------------------
    def _start_block(self) -> None:
        self._block_count += 1
        self._stack = ["li"]
        self._title_parts = []
        self._snippet_parts = []
        self._href = None
        self._in_title = False
        self._in_snippet = False
        self._seen_title_tag = False
        self._seen_snippet_tag = False

    def _finish_block(self) -> None:
        title = _clean("".join(self._title_parts))
        snippet = _clean("".join(self._snippet_parts))
        href = self._href
        if href:
            self.blocks.append({"title": title, "url": href, "snippet": snippet})
        self._stack = []
        self._in_title = False
        self._in_snippet = False

    # -- parser hooks ------------------------------------------------------
    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        if not self._stack:
            if tag == "li" and _class_contains(attrs, "b_algo"):
                self._start_block()
            return

        if tag not in _VOID_TAGS:
            self._stack.append(tag)

        if tag == "h2" and not self._seen_title_tag:
            self._in_title = True
            self._seen_title_tag = True
            return
        if tag == "a" and self._in_title and self._href is None:
            self._href = _attr(attrs, "href")
            return
        if tag == "p" and self._seen_title_tag and not self._seen_snippet_tag:
            self._in_snippet = True
            self._seen_snippet_tag = True

    def handle_startendtag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if self._skip_depth or tag in _SKIP_TAGS:
            return
        if self._stack and tag == "a" and self._in_title and self._href is None:
            self._href = _attr(attrs, "href")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if not self._stack:
            return

        if tag == "h2" and self._in_title:
            self._in_title = False
        if tag == "p" and self._in_snippet:
            self._in_snippet = False

        if tag in _VOID_TAGS:
            return
        # Tolerate slightly broken markup: unwind to the matching tag if it is
        # still on the stack, otherwise ignore the stray closer.
        if tag in self._stack:
            while self._stack and self._stack[-1] != tag:
                self._stack.pop()
            self._stack.pop()
        if not self._stack:
            self._finish_block()

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not self._stack or not data:
            return
        if self._in_title:
            self._title_parts.append(data)
        elif self._in_snippet:
            self._snippet_parts.append(data)

    def close(self) -> None:
        super().close()
        if self._stack:
            self._finish_block()


def _attr(attrs, name: str) -> str | None:
    for key, value in attrs:
        if key.lower() == name and value:
            return value
    return None


def _class_contains(attrs, needle: str) -> bool:
    classes = (_attr(attrs, "class") or "").lower()
    return needle in classes.split()


def _looks_internal(url: str) -> bool:
    """True for links that are not a page she could read: search-engine
    furniture, ad wrappers, javascript: handlers, relative paths."""
    lowered = url.strip().lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        return True
    try:
        host = urllib.parse.urlsplit(lowered).hostname or ""
    except ValueError:
        return True
    host = host.rstrip(".")
    if host == "bing.com" or host.endswith(".bing.com"):
        return True
    return False


def _parse_hits(page: str, limit: int) -> tuple[list[SearchHit], int]:
    parser = _BingResults()
    try:
        parser.feed(page)
        parser.close()
    except Exception:
        pass  # Keep whatever blocks were already completed.

    hits: list[SearchHit] = []
    seen: set[str] = set()
    for block in parser.blocks:
        if len(hits) >= limit:
            break
        url = block["url"].strip()
        if _looks_internal(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        hits.append(
            SearchHit(
                title=block["title"] or url,
                url=url,
                snippet=block["snippet"],
            )
        )
    return hits, parser._block_count


def _decode(raw: bytes, content_type: str) -> str:
    match = _CHARSET_RE.search(content_type or "")
    encodings = []
    if match:
        encodings.append(match.group(1).lower())
    encodings.append("utf-8")
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _http_get(url: str, *, timeout: float) -> str:
    """Fetch one search page as text. Kept separate so tests can patch it."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept-Language": _ACCEPT_LANGUAGE,
            "Accept": _ACCEPT,
            "Connection": "close",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(_MAX_HTML_BYTES + 1)
            content_type = response.headers.get("Content-Type") or ""
    except urllib.error.HTTPError as exc:
        try:
            exc.close()
        except Exception:
            pass
        raise SearchError(f"搜索失败：HTTP {exc.code}") from None
    except (socket.timeout, TimeoutError):
        raise SearchError("搜索超时") from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, socket.gaierror):
            raise SearchError("搜索失败：解析不到域名") from None
        raise SearchError("搜索失败：网络连不上") from None
    except OSError:
        raise SearchError("搜索失败：网络连不上") from None
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return _decode(raw[: _MAX_HTML_BYTES], content_type)


def search(query: str, *, limit: int = 5, timeout: float = 15.0) -> list[SearchHit]:
    """Return up to limit hits for a query, or raise SearchError.

    Failure to reach the engine raises; so does a page that carries no result
    blocks at all (that is a broken parser, not an empty world). [] means the
    engine answered and this query genuinely had nothing.
    """
    if not isinstance(query, str) or not query.strip():
        raise SearchError("检索词不能为空")
    wanted = max(0, int(limit))
    if wanted == 0:
        return []

    params = urllib.parse.urlencode(
        {"q": query.strip(), "setlang": "zh-CN", "count": wanted + 2}
    )
    page = _http_get(f"{BACKEND}?{params}", timeout=timeout)

    hits, blocks = _parse_hits(page, wanted)
    if blocks == 0:
        raise SearchError("搜索页面结构变了，解析不出结果")
    return hits
