from __future__ import annotations

import pytest

from yugang.config import Config
from yugang.gateway.service import GatewayService
from yugang.memory.store import MemoryStore
from yugang.runtime.mock import MockRuntime
from yugang.selfmodel.self_md import bootstrap_self


@pytest.fixture
def cfg(tmp_path):
    c = Config.default(str(tmp_path / "ayu"))
    c.model = "mock"
    c.ensure_dirs()
    return c


@pytest.fixture
def store(cfg):
    s = MemoryStore(cfg.db_path)
    bootstrap_self(s, cfg)
    yield s
    s.close()


def make_service(cfg, store=None, runtime=None, **kwargs):
    s = store or MemoryStore(cfg.db_path)
    runtime = runtime or MockRuntime(responder=lambda messages, purpose: "我在。")
    service = GatewayService(cfg, s, runtime, **kwargs)
    return service, s
