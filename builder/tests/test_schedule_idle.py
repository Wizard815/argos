"""The schedule's own idle unload: the models it loaded go back when nothing uses them."""
import time

from src.integrations import engine_api
from src.scheduler import idle_unload


class _Plans:
    def __init__(self, plan):
        self.plan = plan

    def get(self):
        return self.plan


class _Store:
    """Only list_requests matters here: it is the evidence a model was ever used."""

    def __init__(self, ts):
        self.ts = ts

    def list_requests(self, model=None, limit=1):
        return [{"ts": self.ts}] if self.ts else []


def _pool(state="ready", **flags):
    return {"models": [{"id": "m1", "state": state, **flags}]}


def _run(monkeypatch, plan, ts, pool=None):
    unloaded, events = [], []
    monkeypatch.setattr(engine_api, "pool_state", lambda url, timeout=3.0: pool if pool is not None else _pool())
    monkeypatch.setattr(engine_api, "unload_model", lambda url, mid, timeout=60.0: unloaded.append(mid))
    done = idle_unload(_Plans(plan), _Store(ts), "http://engine", lambda k, m, **kw: events.append(m))
    return done, unloaded, events


def test_unloads_a_model_nothing_has_used_for_the_set_time(monkeypatch):
    done, unloaded, events = _run(monkeypatch, {"idle_unload_min": 30, "persistent": []}, time.time() - 40 * 60)
    assert unloaded == ["m1"] and done == ["m1"]
    assert "unused for 40 min" in events[0]


def test_leaves_a_model_used_inside_the_window(monkeypatch):
    _, unloaded, _ = _run(monkeypatch, {"idle_unload_min": 30, "persistent": []}, time.time() - 60)
    assert unloaded == []


def test_leaves_a_model_with_no_request_history_alone(monkeypatch):
    # No evidence it was used: unloading on a guess costs a reload of minutes.
    _, unloaded, _ = _run(monkeypatch, {"idle_unload_min": 30, "persistent": []}, None)
    assert unloaded == []


def test_never_unloads_a_model_kept_loaded(monkeypatch):
    _, unloaded, _ = _run(monkeypatch, {"idle_unload_min": 30, "persistent": ["m1"]}, time.time() - 40 * 60)
    assert unloaded == []


def test_respects_the_engines_own_flags(monkeypatch):
    _, unloaded, _ = _run(monkeypatch, {"idle_unload_min": 30, "persistent": []}, time.time() - 40 * 60,
                          pool=_pool(protected=True))
    assert unloaded == []
    _, unloaded, _ = _run(monkeypatch, {"idle_unload_min": 30, "persistent": []}, time.time() - 40 * 60,
                          pool=_pool(persistent=True))
    assert unloaded == []


def test_zero_minutes_or_a_stopped_model_never_unloads(monkeypatch):
    _, unloaded, _ = _run(monkeypatch, {"idle_unload_min": 0, "persistent": []}, time.time() - 40 * 60)
    assert unloaded == []
    _, unloaded, _ = _run(monkeypatch, {"idle_unload_min": 30, "persistent": []}, time.time() - 40 * 60,
                          pool=_pool(state="stopped"))
    assert unloaded == []


def test_a_dead_engine_is_not_an_error(monkeypatch):
    def boom(url, timeout=3.0):
        raise OSError("connection refused")

    monkeypatch.setattr(engine_api, "pool_state", boom)
    assert idle_unload(_Plans({"idle_unload_min": 30, "persistent": []}), _Store(time.time() - 9999),
                       "http://engine", lambda *a, **k: None) == []


def test_a_model_just_loaded_for_an_old_favourite_is_not_unloaded(monkeypatch):
    """Last request hours ago, but it was loaded a moment ago for a request still in flight."""
    old = time.time() - 5 * 3600
    plan = {"idle_unload_min": 30, "persistent": []}
    # still starting: never touched
    _, unloaded, _ = _run(monkeypatch, plan, old, pool=_pool("starting"))
    assert unloaded == []
    # ready, and this load began seconds ago
    monkeypatch.setattr(engine_api, "pool_state", lambda url, timeout=3.0: _pool())
    gone = []
    monkeypatch.setattr(engine_api, "unload_model", lambda url, mid, timeout=60.0: gone.append(mid))
    idle_unload(_Plans(plan), _Store(old), "http://engine", lambda *a, **k: None, loaded_at={"m1": time.time() - 5})
    assert gone == []
    # the engine says it served something a minute ago
    from datetime import datetime, timezone
    recent = datetime.fromtimestamp(time.time() - 60, timezone.utc).isoformat()
    _, unloaded, _ = _run(monkeypatch, plan, old, pool=_pool(last_used=recent))
    assert unloaded == []
