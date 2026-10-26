"""The Odysseus hub against a stand-in Odysseus speaking its /api/codex API."""
import json
from http.server import BaseHTTPRequestHandler

from .fakes import serve

TOKEN = "ody_" + "x" * 43


def make_odysseus(scopes, updated=True, calls=None, capabilities=True):
    calls = calls if calls is not None else []
    state = {"notes": [], "memories": [{"id": 1, "text": "user prefers row split for two chats", "category": "preference",
                                        "created_at": "2026-10-01T10:00:00"}], "events": [], "docs": [{"id": "d1", "title": "Old report"}],
             "presets": [{"name": "Qwen fast", "cmd": "llama-server -m q.gguf"}]}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _auth(self, need):
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                self._send({"error": "Invalid API token"}, 401)
                return False
            if need and not (set(need) & set(scopes)):
                self._send({"detail": f"API token missing required scope: {' or '.join(need)}"}, 403)
                return False
            return True

        def do_GET(self):
            path = self.path.split("?")[0]
            calls.append(("GET", self.path))
            routes = {
                "/api/codex/capabilities": (None, lambda: {"token_scopes": scopes, "tools": {
                    "todos": {"read": "todos:read" in scopes, "write": "todos:write" in scopes},
                    "memory": {"read": "memory:read" in scopes, "write": "memory:write" in scopes},
                    "calendar": {"read": "calendar:read" in scopes, "write": "calendar:write" in scopes},
                    "documents": {"read": "documents:read" in scopes, "write": "documents:write" in scopes},
                    "cookbook": {"read": "cookbook:read" in scopes},
                    **({"gallery": {"read": "gallery:read" in scopes}, "tasks": {"read": "tasks:read" in scopes, "run": "tasks:run" in scopes},
                        "skills": {"read": "skills:read" in scopes}} if updated else {})}}),
                "/api/codex/todos": (["todos:read"], lambda: {"results": "\n".join(f"- {n}" for n in state["notes"]) or "", "exit_code": 0}
                                     if state["notes"] else {"response": "No notes found.", "exit_code": 0}),
                "/api/codex/memory": (["memory:read"], lambda: {"memories": state["memories"]}),
                "/api/codex/calendar/events": (["calendar:read"], lambda: {"events": state["events"]}),
                "/api/codex/documents": (["documents:read"], lambda: {"documents": state["docs"]}),
                "/api/codex/cookbook/presets": (["cookbook:read"], lambda: {"presets": list(state["presets"])}),
                "/api/codex/cookbook/servers": (["cookbook:read"], lambda: {"servers": [{"host": "local"}]}),
            }
            if updated:
                routes.update({
                    "/api/codex/gallery": (["gallery:read"], lambda: {"images": [{"id": "img1", "filename": "cat.png"}]}),
                    "/api/codex/tasks": (["tasks:read", "tasks:run"], lambda: {"tasks": [{"id": "t1", "name": "nightly digest"}]}),
                    "/api/codex/skills": (["skills:read"], lambda: {"skills": [{"name": "web_search"}], "count": 1}),
                })
            if not capabilities:
                routes.pop("/api/codex/capabilities")
            if path not in routes:
                return self._send({"detail": "Not Found"}, 404)
            need, fn = routes[path]
            if self._auth(need):
                self._send(fn())

        def do_POST(self):
            path = self.path.split("?")[0]
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            calls.append(("POST", path, body))
            if path == "/api/codex/todos" and self._auth(["todos:write"]):
                state["notes"].append(body["title"])
                self._send({"response": "Note created", "exit_code": 0})
            elif path == "/api/codex/memory" and self._auth(["memory:write"]):
                state["memories"].append({"id": len(state["memories"]) + 1, "text": body["text"]})
                self._send({"success": True})
            elif path == "/api/codex/calendar/events" and self._auth(["calendar:write"]):
                state["events"].append(body)
                self._send({"uid": "e1"})
            elif path == "/api/codex/documents" and self._auth(["documents:write"]):
                state["docs"].append({"id": "d2", "title": body["title"]})
                self._send({"id": "d2"})
            elif updated and path == "/api/codex/cookbook/presets/delete" and self._auth(["cookbook:launch"]):
                before = len(state["presets"])
                state["presets"] = [p for p in state["presets"] if " ".join(p["cmd"].split()) != body["cmd"]]
                self._send({"ok": True, "deleted": before - len(state["presets"])})
            elif updated and path == "/api/codex/tasks/t1/run" and self._auth(["tasks:run"]):
                self._send({"ok": True, "message": "Task triggered"})
            elif not self.headers.get("Authorization"):
                self._send({}, 401)
            else:
                self._send({"detail": "Not Found"}, 404)

    H.state = state
    return H


ALL = ["todos:read", "todos:write", "memory:read", "memory:write", "calendar:read", "calendar:write",
       "documents:read", "documents:write", "cookbook:read", "gallery:read", "tasks:read", "tasks:run", "skills:read"]


def connect(client, url, token=TOKEN):
    client.put("/api/settings/odysseus", json={"url": url, "token": token})


def test_not_configured(client):
    j = client.get("/api/ody/status").get_json()
    assert j["configured"] is False and not any(j["areas"].values())
    r = client.get("/api/ody/notes")
    assert r.status_code == 502 and "Odysseus URL" in r.get_json()["error"]


def test_status_shows_what_the_token_may_do(client):
    with serve(make_odysseus(["todos:read", "memory:read", "memory:write"])) as url:
        connect(client, url)
        j = client.get("/api/ody/status").get_json()
    assert j["connected"] and j["areas"]["notes"] and j["areas"]["brain"] and not j["areas"]["calendar"]
    assert j["writable"]["brain"] and not j["writable"]["notes"] and j["updated"] is True


def test_an_older_odysseus_has_no_gallery_tasks_or_tools(client):
    with serve(make_odysseus(ALL, updated=False)) as url:
        connect(client, url)
        j = client.get("/api/ody/status").get_json()
        assert j["updated"] is False and not j["areas"]["gallery"] and j["areas"]["library"]
        r = client.get("/api/ody/gallery")
    assert r.status_code == 200 and r.get_json()["unavailable"] is True, "an area this Odysseus lacks is just empty"


def test_every_area_lists(client):
    with serve(make_odysseus(ALL)) as url:
        connect(client, url)
        got = {a: client.get(f"/api/ody/{a}").get_json() for a in ("notes", "brain", "calendar", "library", "cookbook", "gallery", "tasks", "tools")}
    assert got["notes"]["text"] == "No notes found."
    assert got["brain"]["items"][0]["text"].startswith("user prefers")
    assert got["library"]["items"][0]["title"] == "Old report"
    assert got["cookbook"]["items"][0]["name"] == "Qwen fast" and got["cookbook"]["servers"] == [{"host": "local"}]
    assert got["gallery"]["items"][0]["filename"] == "cat.png"
    assert got["tasks"]["items"][0]["name"] == "nightly digest" and got["tools"]["items"][0]["name"] == "web_search"
    assert client.get("/api/ody/nope").status_code == 404


def test_adding_and_running(client):
    calls = []
    with serve(make_odysseus(ALL, calls=calls)) as url:
        connect(client, url)
        assert client.post("/api/ody/notes", json={}).status_code == 400
        assert client.post("/api/ody/notes", json={"title": "Try np 3", "content": "next week"}).get_json()["ok"]
        assert "Try np 3" in client.get("/api/ody/notes").get_json()["text"]
        assert client.post("/api/ody/brain", json={"text": "MI50 cards run at 170 W"}).get_json()["ok"]
        assert client.post("/api/ody/calendar", json={"summary": "bench", "start": "2026-10-12T10:00"}).get_json()["ok"]
        assert client.post("/api/ody/calendar", json={"summary": "no start"}).status_code == 400
        assert client.post("/api/ody/library", json={"title": "Report", "content": "# hi"}).get_json()["ok"]
        assert client.post("/api/ody/gallery", json={}).status_code == 400
        assert client.post("/api/ody/tasks/t1/run").get_json()["result"]["ok"]
    sent = [c for c in calls if c[0] == "POST"]
    assert sent[0][2] == {"action": "add", "title": "Try np 3", "content": "next week"}
    assert any(c[1] == "/api/codex/calendar/events" and c[2]["dtstart"] == "2026-10-12T10:00" for c in sent)


def test_scope_and_token_errors_read_like_advice(client):
    with serve(make_odysseus(["todos:read"])) as url:
        connect(client, url)
        r = client.post("/api/ody/brain", json={"text": "x"})
        assert r.status_code == 403 and "not allowed" in r.get_json()["error"] and "memory:write" in r.get_json()["error"]
        connect(client, url, token="ody_wrong" + "y" * 40)
        r = client.get("/api/ody/notes")
        assert r.status_code == 401 and "did not accept the API token" in r.get_json()["error"]
    connect(client, "http://127.0.0.1:9")
    assert "not reachable" in client.get("/api/ody/notes").get_json()["error"]


def test_the_builder_sends_a_preset(app, client):
    p = client.post("/api/profiles", json={"name": "Turbo", "base_model": "m",
                                           "launch_command": "llama-server --model /m/m.gguf -c 8192"}).get_json()["profile"]
    calls = []
    with serve(make_odysseus(ALL, calls=calls)) as url:
        connect(client, url)
        assert client.post("/api/ody/send", json={"profile_id": "nope", "what": "note"}).status_code == 404
        assert client.post("/api/ody/send", json={"profile_id": p["profile_id"], "what": "bogus"}).status_code == 400
        for what in ("note", "brain", "document", "event"):
            assert client.post("/api/ody/send", json={"profile_id": p["profile_id"], "what": what}).get_json()["ok"], what
    posted = {c[1]: c[2] for c in calls if c[0] == "POST"}
    assert "llama-server --model /m/m.gguf -c 8192" in posted["/api/codex/todos"]["content"]
    assert posted["/api/codex/memory"]["text"].startswith("For model m, the preset “Turbo”")
    assert posted["/api/codex/documents"]["title"] == "Turbo (m)" and "| Launch config |" not in posted["/api/codex/documents"]["content"]
    assert posted["/api/codex/calendar/events"]["summary"] == "Benchmark Turbo"
    events = [e["message"] for e in client.get("/api/events?kind=odysseus").get_json()["events"]]
    assert "sent “Turbo” to Odysseus as a note" in events


def test_brain_joins_the_assistants_memory(app, client):
    with serve(make_odysseus(ALL)) as url:
        connect(client, url)
        app.extensions["pb"].memory.sync()
        hits = client.post("/api/assistant/memory/search", json={"q": "row split"}).get_json()["results"]
        assert hits[0]["source"] == "odysseus-brain"
        client.put("/api/settings/odysseus", json={"brain_in_memory": False})
        app.extensions["pb"].memory.sync()
        assert not [h for h in client.post("/api/assistant/memory/search", json={"q": "row split"}).get_json()["results"]
                    if h["source"] == "odysseus-brain"]


def test_the_token_is_never_shown(app, client):
    client.put("/api/settings/odysseus", json={"url": "http://x", "token": TOKEN})
    assert client.get("/api/settings").get_json()["settings"]["odysseus"]["token"] == "•" * 8
    client.put("/api/settings/odysseus", json={"token": "•" * 8, "url": "http://y"})
    saved = app.extensions["pb"].settings.get("odysseus")
    assert saved["token"] == TOKEN and saved["url"] == "http://y", "sending the mask back keeps the real token"


# ----- presets come through the API, so only the URL and token are needed ---------------------------------
def test_odysseus_presets_become_models_from_the_url_and_token_alone(client):
    with serve(make_odysseus(ALL)) as url:
        connect(client, url)
        j = client.get("/api/integrations").get_json()["odysseus"]
        assert j["ok"] and [c["name"] for c in j["commands"]] == ["Qwen fast"]
        state = client.get("/api/cookbook/state").get_json()
        assert [p["label"] for p in state["presets"]] == ["Qwen fast"] and state["presets"][0]["cmd"] == "llama-server -m q.gguf"
        client.put("/api/settings/odysseus", json={"use_presets": False})
        assert client.get("/api/cookbook/state").get_json()["presets"] == []


def test_a_bad_token_is_a_warning_not_a_broken_engine_feed(client):
    with serve(make_odysseus(ALL)) as url:
        connect(client, url, token="wrong")
        j = client.get("/api/integrations").get_json()["odysseus"]
        assert j["ok"] is False and "token" in j["error"]
        assert client.get("/api/cookbook/state").status_code == 200


def test_an_odysseus_without_capabilities_still_works(client):
    with serve(make_odysseus(ALL, updated=False, capabilities=False)) as url:
        connect(client, url)
        j = client.get("/api/ody/status").get_json()
        assert j["connected"] is True and j["capabilities"] is False
        assert j["areas"]["notes"] and j["areas"]["cookbook"]
        assert not j["areas"]["gallery"] and not j["areas"]["tasks"] and not j["areas"]["tools"]
        r = client.get("/api/ody/gallery").get_json()
        assert r["ok"] and r["items"] == [] and r["unavailable"] is True, "a missing area is empty, not an error"


def pulled(client):
    return [p for p in client.get("/api/profiles").get_json()["profiles"] if p["source"] == "odysseus"]


def test_OdysseusPresets_deleting_a_pulled_profile_deletes_the_preset_in_odysseus(client):
    ody = make_odysseus(ALL + ["cookbook:launch"])
    with serve(ody) as url:
        connect(client, url)
        assert len(client.post("/api/ody/pull").get_json()["imported"]) == 1
        prof = pulled(client)[0]
        j = client.delete(f"/api/profiles/{prof['profile_id']}").get_json()
        assert j["odysseus"]["deleted"] == 1 and "deleted in Odysseus" in j["odysseus"]["odysseus"]
        assert ody.state["presets"] == [], "gone from Odysseus itself"
        assert client.post("/api/ody/pull").get_json()["imported"] == [], "and a pull does not bring it back"
        assert pulled(client) == []


def test_OdysseusPresets_an_odysseus_that_cannot_delete_still_never_resyncs_it(client):
    ody = make_odysseus(ALL, updated=False)
    with serve(ody) as url:
        connect(client, url)
        client.post("/api/ody/pull")
        prof = pulled(client)[0]
        j = client.delete(f"/api/profiles/{prof['profile_id']}").get_json()
        assert "cannot delete presets yet" in j["odysseus"]["odysseus"]
        assert len(ody.state["presets"]) == 1
        assert client.post("/api/ody/pull").get_json()["imported"] == [], "kept out of the pull"
        cmds = [p["cmd"] for p in client.get("/api/cookbook/state").get_json()["presets"]]
        assert not any("q.gguf" in c for c in cmds), "and out of what the engine is served"
        assert client.get("/api/ody/cookbook").get_json()["items"] == []
        hidden = client.get("/api/ody/presets/hidden").get_json()["hidden"]
        assert [h["cmd"] for h in hidden] == ["llama-server -m q.gguf"]

        client.delete("/api/ody/presets/hidden", json={"cmd": hidden[0]["cmd"]})
        assert len(client.post("/api/ody/pull").get_json()["imported"]) == 1, "Show again lets the next pull bring it back"


def test_OdysseusPresets_delete_one_from_the_cookbook_list(client):
    ody = make_odysseus(ALL + ["cookbook:launch"])
    with serve(ody) as url:
        connect(client, url)
        j = client.post("/api/ody/presets/delete", json={"name": "Qwen fast", "cmd": "llama-server  -m q.gguf"}).get_json()
        assert j["hidden"] and j["deleted"] == 1 and ody.state["presets"] == []
        assert client.post("/api/ody/presets/delete", json={"name": "x", "cmd": ""}).status_code == 400
