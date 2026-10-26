"""Offline tests for the read-only web fetcher.

Every test here serves a fake response through the module's opener seam, so the
suite passes on a machine with no network at all. The one live check is opt-in
through YUGANG_LIVE=1.
"""

from __future__ import annotations

import os
import socket
import urllib.error

import pytest

import yugang.world.fetch as fetch_mod
from yugang.world.fetch import FetchResult, blocked_reason, fetch, html_to_text


class _FakeResponse:
    """Minimal stand-in for http.client.HTTPResponse."""

    def __init__(self, body: bytes, *, status: int = 200, content_type: str = "text/html; charset=utf-8"):
        self._body = body
        self.status = status
        self.headers = {"Content-Type": content_type}

    def read(self, size: int | None = -1) -> bytes:
        if size is None or size < 0:
            chunk, self._body = self._body, b""
        else:
            chunk, self._body = self._body[:size], self._body[size:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    def close(self) -> None:
        self._body = b""


class _FakeOpener:
    def __init__(self, handler):
        self._handler = handler

    def open(self, request, timeout=None):
        return self._handler(request)


def _install(monkeypatch, handler) -> None:
    monkeypatch.setattr(fetch_mod, "_build_opener", lambda: _FakeOpener(handler))


def _serving(body: bytes, **kwargs):
    def handler(request):
        return _FakeResponse(body, **kwargs)

    return handler


def _raising(exc):
    def handler(request):
        raise exc

    return handler


# -- html_to_text ---------------------------------------------------------


def test_html_to_text_reads_title_and_drops_unreadable_markup():
    page = """
    <html><head>
      <title>标题 &amp; 副标题</title>
      <style>body { color: red; }</style>
      <script>var secret = 1;</script>
    </head><body>
      <noscript>请开启脚本</noscript>
      <svg><text>矢量</text></svg>
      <p>第一段 &lt;tag&gt; &amp; &#20013;</p>
      <div>第二段</div>
      <ul><li>条目一</li><li>条目二</li></ul>
    </body></html>
    """
    title, text = html_to_text(page)
    assert title == "标题 & 副标题"
    assert "第一段 <tag> & 中" in text
    assert "第二段" in text
    assert "条目一" in text and "条目二" in text
    assert "color: red" not in text
    assert "secret" not in text
    assert "请开启脚本" not in text
    assert "矢量" not in text
    # Escaped entities are page text; real markup must be gone.
    assert "<p>" not in text and "</p>" not in text
    assert "<div>" not in text and "<strong>" not in text


def test_html_to_text_turns_block_tags_into_line_breaks():
    _, text = html_to_text("<p>一行</p><p>二行</p><div>三行</div>")
    assert "一行" in text and "二行" in text and "三行" in text
    assert "\n\n\n" not in text
    assert text.count("\n") >= 2


def test_html_to_text_clips_to_max_chars():
    title, text = html_to_text("<title>t</title><p>" + "字" * 500 + "</p>", max_chars=50)
    assert title == "t"
    assert len(text) == 50
    assert text == "字" * 50


def test_html_to_text_never_leaves_attributes_or_tags():
    _, text = html_to_text('<a href="https://x.test" onclick="evil()">链接</a>')
    assert text == "链接"


# -- blocked_reason -------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.0.0.1:8080/admin",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::1]:8000/",
        "http://100.64.0.1/",
        "http://0.0.0.0/",
        "file:///etc/passwd",
        "ftp://x.test/file",
        "javascript:alert(1)",
        "http://user:pw@example.com/",
        "not a url",
        "",
    ],
)
def test_blocked_reason_rejects_dangerous_urls(url):
    assert blocked_reason(url) is not None


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "https://example.com/a?b=c#d",
        "https://93.184.216.34/",
    ],
)
def test_blocked_reason_allows_public_urls(url):
    # Hostname lookups that cannot resolve return None here on purpose: an
    # unresolvable name cannot reach anything, so the fetch reports it later.
    assert blocked_reason(url) is None


# -- fetch ----------------------------------------------------------------


def test_fetch_returns_readable_page(monkeypatch):
    body = "<html><head><title>hello</title></head><body><p>world 世界</p></body></html>".encode()
    _install(monkeypatch, _serving(body))
    result = fetch("https://example.com/")
    assert isinstance(result, FetchResult)
    assert result.ok is True
    assert result.status == 200
    assert result.url == "https://example.com/"
    assert result.content_type == "text/html"
    assert result.title == "hello"
    assert "world 世界" in result.text
    assert result.chars == len(result.text)
    assert result.bytes == len(body)
    assert result.truncated is False
    assert result.error is None


def test_fetch_rejects_unsupported_content_type(monkeypatch):
    _install(monkeypatch, _serving(b"\x89PNG\r\n", content_type="image/png"))
    result = fetch("https://example.com/pic.png")
    assert result.ok is False
    assert result.status == 200
    assert result.error == "不支持的内容类型：image/png"
    assert result.text == ""
    assert blocked_reason("https://example.com/pic.png") is None


def test_fetch_marks_truncated_when_byte_cap_hit(monkeypatch):
    body = b"<p>" + b"x" * 5000 + b"</p>"
    _install(monkeypatch, _serving(body))
    result = fetch("https://example.com/big", max_bytes=1000, max_chars=100000)
    assert result.ok is True
    assert result.truncated is True
    assert result.bytes == 1000
    assert len(result.text) <= 100000


def test_fetch_marks_truncated_when_text_clipped(monkeypatch):
    body = b"<p>" + "字".encode() * 500 + b"</p>"
    _install(monkeypatch, _serving(body))
    result = fetch("https://example.com/long", max_chars=10)
    assert result.ok is True
    assert result.chars == 10
    assert result.truncated is True


def test_fetch_follows_one_redirect(monkeypatch):
    calls: list[str] = []

    def handler(request):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise fetch_mod._Redirected("https://93.184.216.34/final", 302)
        return _FakeResponse(b"<title>final</title><p>arrived</p>")

    _install(monkeypatch, handler)
    result = fetch("https://93.184.216.34/start")
    assert result.ok is True
    assert result.url == "https://93.184.216.34/final"
    assert result.title == "final"
    assert "arrived" in result.text
    assert len(calls) == 2


def test_fetch_blocks_redirect_into_private_network(monkeypatch):
    _install(monkeypatch, _raising(fetch_mod._Redirected("http://127.0.0.1/admin", 302)))
    result = fetch("https://93.184.216.34/start")
    assert result.ok is False
    assert "内网" in result.error
    assert result.url == "http://127.0.0.1/admin"


def test_fetch_blocks_redirect_to_metadata_address(monkeypatch):
    _install(
        monkeypatch,
        _raising(fetch_mod._Redirected("http://169.254.169.254/latest/meta-data/", 302)),
    )
    result = fetch("https://93.184.216.34/start")
    assert result.ok is False
    assert "内网" in result.error


def test_fetch_gives_up_after_redirect_budget(monkeypatch):
    _install(
        monkeypatch,
        _raising(fetch_mod._Redirected("https://93.184.216.34/again", 302)),
    )
    result = fetch("https://93.184.216.34/start", max_redirects=1)
    assert result.ok is False
    assert "跳转次数超过上限" in result.error
    assert "1" in result.error


def test_fetch_reports_http_404(monkeypatch):
    request = None

    def handler(req):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    _install(monkeypatch, handler)
    result = fetch("https://93.184.216.34/missing")
    assert result.ok is False
    assert result.status == 404
    assert result.error == "HTTP 404"


def test_fetch_reports_timeout(monkeypatch):
    _install(monkeypatch, _raising(socket.timeout("timed out")))
    result = fetch("https://93.184.216.34/slow")
    assert result.ok is False
    assert result.error == "连接超时"


def test_fetch_reports_dns_failure(monkeypatch):
    _install(
        monkeypatch,
        _raising(urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))),
    )
    result = fetch("https://no-such-host.invalid/")
    assert result.ok is False
    assert result.error == "解析不到主机"


def test_fetch_honours_meta_charset(monkeypatch):
    body = (
        b'<html><head><meta charset="gb2312"></head><body><p>'
        + "你好世界".encode("gb18030")
        + b"</p></body></html>"
    )
    _install(monkeypatch, _serving(body, content_type="text/html"))
    result = fetch("https://example.com/gbk")
    assert result.ok is True
    assert "你好世界" in result.text


def test_fetch_honours_header_charset(monkeypatch):
    body = b"<p>" + "中文".encode("gb18030") + b"</p>"
    _install(monkeypatch, _serving(body, content_type="text/html; charset=gb18030"))
    result = fetch("https://example.com/gbk2")
    assert result.ok is True
    assert "中文" in result.text


def test_fetch_refuses_file_url_without_opening(monkeypatch):
    def handler(request):  # pragma: no cover - must not be reached
        raise AssertionError("opener must not be called")

    _install(monkeypatch, handler)
    result = fetch("file:///etc/passwd")
    assert result.ok is False
    assert "http" in result.error


def test_fetch_refuses_url_with_credentials(monkeypatch):
    def handler(request):  # pragma: no cover - must not be reached
        raise AssertionError("opener must not be called")

    _install(monkeypatch, handler)
    result = fetch("http://user:pw@example.com/")
    assert result.ok is False
    assert "账号密码" in result.error


def test_fetch_plain_text_is_not_parsed_as_html(monkeypatch):
    _install(monkeypatch, _serving("纯文本 <不是标签>".encode(), content_type="text/plain; charset=utf-8"))
    result = fetch("https://example.com/notes.txt")
    assert result.ok is True
    assert result.title == ""
    assert result.text == "纯文本 <不是标签>"


@pytest.mark.skipif(os.environ.get("YUGANG_LIVE") != "1", reason="live network")
def test_live_fetch_example_com():
    result = fetch("https://example.com/")
    assert result.ok is True
    assert result.status == 200
    assert "Example Domain" in (result.title + result.text)
