"""Offline tests for the Bing HTML search backend.

The fixture below is a trimmed copy of the real cn.bing.com markup (result
blocks are <li class="b_algo">), so parser regressions show up without touching
the network. YUGANG_LIVE=1 enables one live smoke test.
"""

from __future__ import annotations

import os
import socket
import urllib.error

import pytest

import yugang.world.search as search_mod
from yugang.world.search import BACKEND, SearchError, SearchHit, search

BING_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>数字生命 是什么 - 搜索</title></head>
<body><ol id="b_results">
<li class="b_algo" data-id="1">
  <h2><a target="_blank" href="https://example.org/a"><strong>数字生命</strong>是什么？</a></h2>
  <div class="b_caption"><p class="b_lineclamp2">一段&nbsp;简介 &amp; 说明</p></div>
</li>
<li class="b_algo">
  <h2><a href="https://www.bing.com/ck/a?u=abc">广告 &amp; 跳转</a></h2>
  <div class="b_caption"><p>广告文案</p></div>
</li>
<li class="b_algo">
  <h2><a href="https://example.org/a">重复结果</a></h2>
  <p>重复</p>
</li>
<li class="b_algo">
  <h2><a href="https://example.net/b">第二个结果</a></h2>
  <p>第二段<strong>加粗</strong>说明</p>
</li>
<li class="b_algo">
  <h2><a href="/relative/path">相对链接</a></h2>
  <p>相对</p>
</li>
</ol></body></html>"""


def _patch_page(monkeypatch, page: str, seen: list[str] | None = None) -> None:
    def fake_get(url, *, timeout):
        if seen is not None:
            seen.append(url)
        return page

    monkeypatch.setattr(search_mod, "_http_get", fake_get)


def test_backend_is_cn_bing_html():
    assert BACKEND == "https://cn.bing.com/search"


def test_search_parses_hits(monkeypatch):
    _patch_page(monkeypatch, BING_PAGE)
    hits = search("数字生命 是什么", limit=5)
    assert len(hits) == 2
    assert all(isinstance(hit, SearchHit) for hit in hits)
    first, second = hits
    assert first.title == "数字生命是什么？"
    assert first.url == "https://example.org/a"
    assert first.snippet == "一段 简介 & 说明"
    assert second.title == "第二个结果"
    assert second.url == "https://example.net/b"
    assert second.snippet == "第二段加粗说明"


def test_search_skips_engine_wrappers_relative_and_duplicates(monkeypatch):
    _patch_page(monkeypatch, BING_PAGE)
    urls = [hit.url for hit in search("数字生命", limit=10)]
    assert urls == ["https://example.org/a", "https://example.net/b"]
    assert not any("bing.com" in url for url in urls)


def test_search_builds_the_expected_request(monkeypatch):
    seen: list[str] = []
    _patch_page(monkeypatch, BING_PAGE, seen)
    search("数字 生命", limit=3)
    assert len(seen) == 1
    url = seen[0]
    assert url.startswith(BACKEND + "?")
    assert "q=%E6%95%B0%E5%AD%97+%E7%94%9F%E5%91%BD" in url
    assert "setlang=zh-CN" in url
    assert "count=5" in url


def test_search_respects_limit(monkeypatch):
    _patch_page(monkeypatch, BING_PAGE)
    assert len(search("数字生命", limit=1)) == 1
    assert len(search("数字生命", limit=2)) == 2


def test_search_returns_empty_list_only_when_page_parsed_but_has_no_hits(monkeypatch):
    page = """<html><body><ol>
    <li class="b_algo"><h2><a href="https://www.bing.com/ck/a?x=1">engine</a></h2><p>ad</p></li>
    </ol></body></html>"""
    _patch_page(monkeypatch, page)
    assert search("没有外部结果", limit=5) == []


def test_search_raises_when_structure_changes(monkeypatch):
    _patch_page(monkeypatch, "<html><body><p>no result blocks here</p></body></html>")
    with pytest.raises(SearchError) as excinfo:
        search("数字生命")
    assert "结构变了" in str(excinfo.value)


@pytest.mark.parametrize("query", ["", "   ", "\n\t "])
def test_search_rejects_empty_query(monkeypatch, query):
    def fake_get(url, *, timeout):  # pragma: no cover - must not be reached
        raise AssertionError("network must not be touched")

    monkeypatch.setattr(search_mod, "_http_get", fake_get)
    with pytest.raises(SearchError) as excinfo:
        search(query)
    assert "检索词不能为空" in str(excinfo.value)


def test_search_raises_on_http_error(monkeypatch):
    def raising_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", raising_urlopen)
    with pytest.raises(SearchError) as excinfo:
        search("数字生命")
    assert "HTTP 503" in str(excinfo.value)


def test_search_raises_on_network_failure(monkeypatch):
    def raising_urlopen(request, timeout=None):
        raise urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))

    monkeypatch.setattr(urllib.request, "urlopen", raising_urlopen)
    with pytest.raises(SearchError) as excinfo:
        search("数字生命")
    assert "解析不到域名" in str(excinfo.value)


def test_search_limit_zero_makes_no_request(monkeypatch):
    def fake_get(url, *, timeout):  # pragma: no cover - must not be reached
        raise AssertionError("network must not be touched")

    monkeypatch.setattr(search_mod, "_http_get", fake_get)
    assert search("数字生命", limit=0) == []


@pytest.mark.skipif(os.environ.get("YUGANG_LIVE") != "1", reason="live network")
def test_live_search_cn_bing():
    hits = search("数字生命 是什么", limit=3)
    assert hits
    assert all(hit.url.startswith(("http://", "https://")) for hit in hits)
