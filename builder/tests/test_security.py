"""Settings -> Security: the web page password and API keys, checked for requests from outside the container."""
import pytest

OUT = {"REMOTE_ADDR": "192.168.20.7"}


def _from(app, environ):
    c = app.test_client()
    c.environ_base.update(environ)
    return c


@pytest.fixture
def outside(app):
    return _from(app, OUT)


def test_Security_off_by_default_everything_opens(outside):
    assert outside.get("/").status_code == 200
    assert outside.get("/api/settings").status_code == 200


def test_Security_a_password_guards_the_page_and_api_until_login(app, client, outside):
    assert client.put("/api/security/password", json={"new": "short"}).status_code == 400, "8 characters at least"
    assert client.put("/api/security/password", json={"new": "hunter22!"}).get_json()["password_set"]
    page = outside.get("/")
    assert page.status_code == 302 and page.headers["Location"].startswith("/login?next=")
    assert outside.get("/running").status_code == 401, "the engine's own paths need the login too"
    assert outside.post("/v1/chat/completions", json={"model": "m"}).status_code != 401, "no keys required: the model API stays open"
    api = outside.get("/api/settings")
    assert api.status_code == 401 and api.get_json()["login"] is True
    assert outside.get("/health").status_code != 401 and outside.get("/login").status_code == 200
    assert outside.post("/login", data={"password": "wrong"}).status_code == 401
    r = outside.post("/login", data={"password": "hunter22!", "next": "https://evil.example/"})
    assert r.status_code == 302 and r.headers["Location"] == "/", "never sent on to another site"
    assert outside.get("/api/settings").status_code == 200, "logged in"
    # a new password signs every browser out
    assert client.put("/api/security/password", json={"current": "hunter22!", "new": "correct horse"}).status_code == 200
    assert outside.get("/api/settings").status_code == 401
    assert client.put("/api/security/password", json={"current": "nope", "new": "x" * 9}).status_code == 403


def test_Security_api_keys_for_the_model_api(app, client, outside):
    assert client.put("/api/security/require-key", json={"on": True}).status_code == 400, "no key yet: would lock every app out"
    made = client.post("/api/security/keys", json={"name": "Hermes"}).get_json()["key"]
    assert made["key"].startswith("argos-") and made["name"] == "Hermes"
    listed = client.get("/api/security").get_json()["keys"]
    assert listed[0]["prefix"] == made["key"][:12] and "key" not in listed[0], "the key itself is shown once, never again"
    assert client.put("/api/security/require-key", json={"on": True}).get_json()["require_key"]

    none = outside.post("/v1/chat/completions", json={"model": "m"})
    assert none.status_code == 401 and none.get_json()["error"]["code"] == "invalid_api_key"
    bad = outside.post("/v1/chat/completions", json={"model": "m"}, headers={"Authorization": "Bearer argos-nope"})
    assert bad.status_code == 401 and "not valid" in bad.get_json()["error"]["message"]
    good = outside.post("/v1/chat/completions", json={"model": "m"}, headers={"Authorization": f"Bearer {made['key']}"})
    assert good.status_code != 401, "past the gate (the engine is not running here, so it fails further on)"
    assert outside.get("/v1/models", headers={"x-api-key": made["key"]}).status_code != 401, "x-api-key works too"
    assert outside.get("/api/settings").status_code == 200, "no password: the page stays open"

    assert client.delete(f"/api/security/keys/{made['id']}").status_code == 200
    assert outside.post("/v1/chat/completions", json={"model": "m"}, headers={"Authorization": f"Bearer {made['key']}"}).status_code == 401


def test_Security_a_key_opens_the_api_but_not_security_itself(app, client, outside):
    key = client.post("/api/security/keys", json={"name": "dashboard"}).get_json()["key"]["key"]
    client.put("/api/security/password", json={"new": "hunter22!"})
    h = {"Authorization": f"Bearer {key}"}
    assert outside.get("/api/settings", headers=h).status_code == 200
    assert outside.get("/api/security", headers=h).status_code == 401
    assert outside.post("/api/security/keys", json={"name": "x"}, headers=h).status_code == 401


def test_Security_a_reverse_proxy_on_the_same_host_is_not_inside(app, client):
    client.put("/api/security/password", json={"new": "hunter22!"})
    proxy = _from(app, {"REMOTE_ADDR": "127.0.0.1"})
    assert proxy.get("/api/settings", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 401
    assert proxy.get("/api/settings").status_code == 200, "the container itself"


def test_Security_argos_password_clear(app, client, monkeypatch, tmp_path):
    from src import security
    client.put("/api/security/password", json={"new": "hunter22!"})
    sec = app.extensions["security"]
    assert sec.password_set()
    monkeypatch.setattr(security, "Security", lambda store: sec)
    assert security.main(["clear"]) == 0
    sec._cache = (0.0, {})
    assert not sec.password_set()
