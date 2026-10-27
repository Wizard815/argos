"""Profiles: a star that stays put, and when each one was last used, for the Favorites and Recently used groups."""


def save(client, **kw):
    d = {"name": "p", "base_model": "a", "launch_command": "llama-server --model /m/a.gguf --port 8080", **kw}
    return client.post("/api/profiles", json=d).get_json()["profile"]


def test_ProfileFavorites_star_is_not_an_edit_and_a_save_keeps_it(app, client):
    p = save(client)
    j = client.put(f"/api/profiles/{p['profile_id']}/favorite", json={"on": True}).get_json()
    assert j["profile"]["favorite"] == 1 and j["profile"]["updated_at"] == p["updated_at"], "starring does not move Updated"
    again = save(client, profile_id=p["profile_id"], name="renamed")
    assert again["favorite"] == 1, "a save from the Builder does not send the star; it must not drop it"
    assert client.put(f"/api/profiles/{p['profile_id']}/favorite", json={"on": False}).get_json()["profile"]["favorite"] == 0
    assert client.put("/api/profiles/prof_missing/favorite", json={"on": True}).status_code == 404


def test_ProfileFavorites_launching_marks_it_used(app, client):
    from .conftest import fake_command
    p = save(client, launch_command=fake_command())
    assert p.get("last_used_at") is None
    r = client.post("/api/launches", json={"command": fake_command(), "profile_id": p["profile_id"]}).get_json()
    client.post(f"/api/launches/{r['launch']['launch_id']}/stop")
    used = app.extensions["pb"].store.get_profile(p["profile_id"])["last_used_at"]
    assert used and used >= p["created_at"]

