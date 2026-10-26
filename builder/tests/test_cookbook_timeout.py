"""The cookbook route must answer well inside the engine's timeout.

The engine polls /api/cookbook/state every five minutes and gives the whole call
odysseus.timeoutSeconds - 20 by default. The builder used to reach Odysseus with
its own 20-second timeout, so the inner call could never win that race: the
engine's deadline always fired first, it learned nothing, and it logged a
failure. A slow Odysseus also emptied the engine's model list.
"""
import pytest

import src.routes.odysseus_hub as hub
from src.integrations.odysseus_api import Odysseus, OdysseusError


class FakeStore:
    def get_settings(self):
        return {}  # no Odysseus presets deleted here


class FakeCtx:
    def __init__(self, settings):
        self.settings = settings
        self.export_state = {}
        self.store = FakeStore()


def _settings(**odysseus):
    base = {"url": "http://ody:7005", "token": "tok", "use_presets": True}
    base.update(odysseus)
    return {"odysseus": base}


def _patch_ctx(monkeypatch, ctx):
    monkeypatch.setattr(hub, "ctx", lambda: ctx)


def test_the_client_is_asked_for_a_timeout_under_the_engines_own():
    # The engine's default is 20s. This side has to give up well before that or
    # it has nothing to say when the engine's deadline fires.
    assert hub.PRESETS_TIMEOUT_S < 20.0


def test_a_hanging_odysseus_does_not_hang_the_route(monkeypatch):
    ctx = FakeCtx(_settings())
    _patch_ctx(monkeypatch, ctx)

    asked = {}

    def slow(self, *a, **k):
        asked["timeout"] = self.timeout
        raise OdysseusError("timed out")

    monkeypatch.setattr(Odysseus, "cookbook", slow)
    out = hub.cookbook_presets()

    assert out["presets"] == [] and out["error"], "it should report, not raise"
    assert asked["timeout"] == hub.PRESETS_TIMEOUT_S, f"used {asked}"


def test_the_last_good_presets_survive_a_failure(monkeypatch):
    ctx = FakeCtx(_settings())
    _patch_ctx(monkeypatch, ctx)

    monkeypatch.setattr(Odysseus, "cookbook",
                        lambda self, *a, **k: {"presets": [{"label": "qwen3 fast", "cmd": "llama-server"}]})
    first = hub.cookbook_presets()
    assert [p["label"] for p in first["presets"]] == ["qwen3 fast"]

    # ...and now Odysseus goes away, past the cache's own lifetime.
    monkeypatch.setattr(Odysseus, "cookbook",
                        lambda self, *a, **k: (_ for _ in ()).throw(OdysseusError("unreachable")))
    ctx.export_state["_ody_presets"]["at"] -= hub.PRESETS_TTL_S + 1

    second = hub.cookbook_presets()
    assert [p["label"] for p in second["presets"]] == ["qwen3 fast"], \
        "a slow Odysseus emptied the engine's model list"
    assert "unreachable" in second["error"], "the problem should still be reported"


def test_nothing_is_called_when_presets_are_off(monkeypatch):
    ctx = FakeCtx(_settings(use_presets=False))
    _patch_ctx(monkeypatch, ctx)

    def boom(self, *a, **k):
        raise AssertionError("Odysseus was called with use_presets off")

    monkeypatch.setattr(Odysseus, "cookbook", boom)
    assert hub.cookbook_presets() == {"presets": [], "error": ""}
