"""Per-app rules, dollar budgets, MCP tool rules, retries and cooldown (gateway.py, providers.py)."""
import pytest


def test_Gateway_app_rules_are_cleaned_and_bounded():
    from src.gateway import clean_rules, app_rules
    r = clean_rules({"apps": {"codex": {"rpm": "5", "models": "qwen*\ngemma*", "daily_usd": "1.5", "junk": 1},
                              "": {"rpm": 1}, "x": "not a dict"}})
    assert r["apps"] == {"codex": {"models": ["qwen*", "gemma*"], "rpm": 5, "tpm": 0, "parallel": 0, "daily_tokens": 0,
                                   "monthly_tokens": 0, "daily_usd": 1.5, "monthly_usd": 0.0, "mcp_tools_deny": [],
                                   "blocked": False, "note": ""}}
    assert app_rules(r, "codex")["rpm"] == 5 and app_rules(r, "hermes") is None and app_rules(r, "") is None
    with pytest.raises(ValueError, match="app codex: rpm cannot be negative"):
        clean_rules({"apps": {"codex": {"rpm": -1}}})
    with pytest.raises(ValueError):
        clean_rules({"apps": ["codex"]})
    many = clean_rules({"apps": {f"a{i}": {"rpm": 1} for i in range(500)}})
    assert len(many["apps"]) == 100


@pytest.mark.parametrize("v", ["-1", "nan", "1e400", "abc", 1e8])
def test_Gateway_bad_budgets_are_refused(v):
    from src.gateway import clean_rules
    with pytest.raises(ValueError):
        clean_rules({"daily_usd": v})


def test_Gateway_budget_problem_says_why():
    from src.gateway import budget_problem
    assert budget_problem(0.5, 3, {"daily_usd": 1, "monthly_usd": 10}) == ""
    assert "a day" in budget_problem(1.0, 3, {"daily_usd": 1})
    assert "the app codex" in budget_problem(0, 10.01, {"monthly_usd": 10}, "the app codex")
    assert budget_problem(99, 99, {}) == "", "no budget: never refused"


def test_Gateway_mcp_tool_rules():
    from src.gateway import tool_allowed
    assert tool_allowed("files__rm", None)
    r = {"mcp_tools": ["files__read_*", "github__*"], "mcp_tools_deny": ["github__delete*"],
         "apps": {"codex": {"mcp_tools_deny": ["github__create_issue"]}}}
    assert tool_allowed("files__read_file", r)
    assert not tool_allowed("files__write_file", r), "not in the allow list"
    assert not tool_allowed("github__delete_repo", r), "denied"
    assert tool_allowed("github__create_issue", r) and not tool_allowed("github__create_issue", r, "codex"), "the app's own deny"
    assert tool_allowed("anything__x", {"mcp_tools_deny": ["files__*"]})


def test_Gateway_retryable_only_what_may_pass():
    from src.gateway import retryable
    assert retryable(503, b'{"error":"loading"}', True)
    assert not retryable(429, b"loop guard", True), "a local 429 is Argos's own limit"
    assert retryable(429, b"rate limited", False), "a provider's 429 is worth another try"
    assert not retryable(502, b"stopped by the loop guard", True)
    assert not retryable(502, b"x: the request was cancelled in Argos", True)
    assert not retryable(503, b"m crashed 3 times in the last 10 minutes and is not started again", True)
    assert not retryable(400, b"bad", True) and not retryable(404, b"", False)


def test_Gateway_health_cools_down_and_recovers():
    from src.gateway import Health
    h, st = Health(), {"retries": 1, "fails": 3, "window_s": 60, "cooldown_s": 30}
    assert not h.fail("model:m", "503", st, now=100) and not h.fail("model:m", "503", st, now=101)
    assert h.fail("model:m", "503 again", st, now=102), "the third failure starts the rest"
    assert 27 < h.cooling("model:m", now=103) <= 30 and h.cooling("model:other", now=103) == 0
    assert h.view(now=103)[0]["why"] == "503 again"
    assert h.cooling("model:m", now=133) == 0, "over"
    h.fail("model:m", "x", st, now=200)
    h.ok("model:m")
    assert not h.fail("model:m", "x", st, now=201) and not h.fail("model:m", "x", st, now=202), "a success starts the count again"
    old = Health()
    for t in (0, 100, 200):
        assert not old.fail("p", "x", st, now=t), "failures far apart never add up"
    off = Health()
    for t in range(10):
        assert not off.fail("p", "x", {**st, "cooldown_s": 0}, now=t)
    assert off.cooling("p", now=10) == 0


def test_Gateway_retry_settings_bounds():
    from src.gateway import clean_retry
    assert clean_retry({"retries": 2, "fails": 3, "window_s": 60, "cooldown_s": 0})["retries"] == 2
    for bad in ({"retries": 9}, {"fails": 0}, {"window_s": 1}, {"cooldown_s": -1}, {"retries": "x"}):
        with pytest.raises(ValueError):
            clean_retry({"retries": 1, "fails": 3, "window_s": 60, "cooldown_s": 60, **bad})


def test_Providers_prices_by_hand_and_cost():
    from src import providers
    p = providers.clean({"name": "or", "url": "https://x.example/v1", "prices": "a/b: 0.15, 0.60\nc: 2"})
    assert p["prices"] == {"a/b": [0.15, 0.6], "c": [2.0, 0.0]}
    assert providers.price(p, "a/b") == (0.15, 0.6)
    assert providers.cost((0.15, 0.6), 1_000_000, 500_000) == pytest.approx(0.45)
    assert providers.cost(None, 10, 10) == 0.0
    for bad in ("a: x, y", "a: -1, 0", "a: 1e9, 0"):
        with pytest.raises(ValueError):
            providers.clean({"name": "or", "url": "https://x.example/v1", "prices": bad})


def test_Providers_price_never_waits_on_the_network(monkeypatch):
    import time
    from src import providers
    calls = []
    monkeypatch.setattr(providers, "catalog", lambda p, fresh=False: (calls.append(1), time.sleep(2))[0])
    p = providers.clean({"name": "slow", "url": "https://slow.example/v1"})
    t0 = time.time()
    assert providers.price(p, "m") is None
    assert providers.price(p, "m") is None
    assert time.time() - t0 < 0.5
    time.sleep(0.1)
    assert len(calls) == 1, "the list is fetched once in the background"


def test_Fallback_price_field():
    from src.gateway import clean_fallback
    base = {"url": "https://x.example/v1", "match": "q*"}
    assert clean_fallback({**base, "price": "0.1, 0.4"})["price"] == [0.1, 0.4]
    assert clean_fallback(base)["price"] == [] and clean_fallback({**base, "price": "0, 0"})["price"] == []
    with pytest.raises(ValueError):
        clean_fallback({**base, "price": "free"})
