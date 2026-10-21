import time

from flask import Flask, jsonify

from src.readcache import ReadCache, install


def test_a_read_polled_faster_than_the_ttl_still_expires():
    app = Flask(__name__)
    state = {"v": "starting"}

    @app.get("/api/launches")
    def launches():
        return jsonify(state=state["v"])

    install(app, ReadCache(ttl=0.3))
    c = app.test_client()
    assert c.get("/api/launches").get_json()["state"] == "starting"
    state["v"] = "ready"
    seen = []
    for _ in range(8):  # poll every 0.1 s: each hit used to restart the entry's clock
        seen.append(c.get("/api/launches").get_json()["state"])
        time.sleep(0.1)
    assert "ready" in seen, seen
