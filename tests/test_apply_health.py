"""体检探针必须问服务真正在听的那个端口。

端口在 her config.json 的 http.port（默认 3198）。合入器写死 3198 时，只要操作员哪天
把端口改了，每次合入都会被读成「服务起来了，但体检没过」然后回滚——一个配置改动，
变成她再也改不动自己。
"""

from __future__ import annotations

import json

import pytest

from yugang import apply as apply_mod


def test_health_probe_follows_the_port_in_her_config(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"http": {"port": 3301}}), encoding="utf-8")
    assert apply_mod.health_url_for(tmp_path) == "http://127.0.0.1:3301/health"


@pytest.mark.parametrize(
    "content",
    [
        None,
        "{not json",
        json.dumps({}),
        json.dumps({"http": {"port": "abc"}}),
        json.dumps({"http": {"port": 0}}),
        json.dumps({"http": {"port": True}}),
    ],
)
def test_a_config_without_a_usable_port_falls_back_to_the_default(tmp_path, content):
    if content is not None:
        (tmp_path / "config.json").write_text(content, encoding="utf-8")
    assert apply_mod.health_url_for(tmp_path) == apply_mod.HEALTH_URL


def test_a_port_given_as_a_string_still_works(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"http": {"port": "3302"}}), encoding="utf-8")
    assert apply_mod.health_url_for(tmp_path) == "http://127.0.0.1:3302/health"
