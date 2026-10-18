"""A repeated read is served from memory, and any write drops the lot."""
import time

from src.readcache import ReadCache, default_ttl


def test_a_repeat_is_served_and_then_forgotten(monkeypatch):
    monkeypatch.setenv("ARGOS_READ_CACHE_S", "30")  # the suite turns it off; this test wants it on
    c = ReadCache(0.2)
    c.put("/api/settings", b'{"ok":true}', "application/json")
    assert c.get("/api/settings") == (b'{"ok":true}', "application/json")
    assert c.stats()["hits"] == 1
    time.sleep(0.25)
    assert c.get("/api/settings") is None, "past the ttl it is not a hit"
    assert c.stats()["entries"] == 0


def test_a_write_drops_every_remembered_read(monkeypatch):
    """A write makes every cached read wrong, so all of them go."""
    monkeypatch.setenv("ARGOS_READ_CACHE_S", "30")
    c = ReadCache(30)
    c.put("/api/settings", b"{}", "application/json")
    c.put("/api/profiles", b"[]", "application/json")
    assert c.stats()["entries"] == 2
    c.clear()
    assert c.stats()["entries"] == 0
    assert c.get("/api/settings") is None


def test_the_environment_turns_it_off(monkeypatch):
    """The suite sets this to 0: a read may be one poll old, and a test asserting state
    right after a write cannot have that."""
    monkeypatch.setenv("ARGOS_READ_CACHE_S", "0")
    assert default_ttl() == 0
    c = ReadCache(30)
    c.put("/api/settings", b"{}", "application/json")
    assert c.get("/api/settings") is None
    assert c.stats()["entries"] == 0


def test_the_installed_cache_is_off_under_test(client, app):
    assert app.extensions["read_cache"].enabled() is False


def test_only_the_repeated_reads_are_eligible():
    """An allowlist: a job's progress and the live pool view must stay live."""
    from src.readcache import CACHE_PATHS, SKIP_PREFIXES

    assert "/api/settings" in CACHE_PATHS and "/api/gpu/series" in CACHE_PATHS
    assert "/api/schedule/live" not in CACHE_PATHS, "live by name"
    assert "/api/assistant/chat" in SKIP_PREFIXES, "a stream is never remembered"
