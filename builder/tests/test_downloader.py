import os
import time

import pytest

from src.downloader import DownloadError, HubClient, fit, group_quants

from .fakes import make_hub, serve

REPO = "Qwen/Qwen3-0.6B-GGUF"
FILES = {
    "Qwen3-0.6B-Q8_0.gguf": b"8" * 3000,
    "Qwen3-0.6B-Q4_K_M.gguf": b"4" * 1800,
    "big/Qwen3-0.6B-F16-00001-of-00002.gguf": b"a" * 2500,
    "big/Qwen3-0.6B-F16-00002-of-00002.gguf": b"b" * 2400,
    "mmproj-Qwen3-F16.gguf": b"m" * 100,
}


def test_group_quants_and_fit():
    groups = group_quants([{"name": n, "size": len(b)} for n, b in FILES.items()])
    assert [(g["quant"], g["shards"], g["size"]) for g in groups] == [
        ("Q4_K_M", 1, 1800), ("Q8_0", 1, 3000), ("F16", 2, 4900)]
    assert fit(int(4e9), 24) == "fits" and fit(int(19e9), 24) == "tight" and fit(int(30e9), 24) == "too big"
    assert fit(100, 0) == "unknown"


def test_hub_client_search_and_repo():
    with serve(make_hub(FILES)) as url:
        hub = HubClient(url)
        assert hub.search("qwen3")[0]["id"] == REPO
        info = hub.repo(REPO)
        assert [q["quant"] for q in info["quants"]] == ["Q4_K_M", "Q8_0", "F16"]
        assert [f["name"] for f in info["mmproj"]] == ["mmproj-Qwen3-F16.gguf"]
        with pytest.raises(DownloadError, match="not found"):
            hub.repo("nobody/nothing")
        with pytest.raises(DownloadError, match="org/name"):
            hub.repo("../etc")
    with serve(make_hub(FILES, gated=True)) as url:
        with pytest.raises(DownloadError, match="token"):
            HubClient(url).repo(REPO)
        assert HubClient(url, token="t").repo(REPO)["id"] == REPO


def wait_done(client, job_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = next(d for d in client.get("/api/downloads").get_json()["downloads"] if d["id"] == job_id)
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError("download never finished")


def test_download_flow_with_resume(client, app):
    hub_cls = make_hub(FILES)
    models = app.extensions["pb"].settings.get("paths")["models_dir"]
    with serve(hub_cls) as url:
        client.put("/api/settings/downloads", json={"hf_endpoint": url, "vram_gb": 0.000004})
        assert client.get("/api/hf/search?q=q").status_code == 400
        assert client.get("/api/hf/search?q=qwen").get_json()["results"][0]["id"] == REPO
        repo = client.get(f"/api/hf/repo?id={REPO}").get_json()
        assert [q["fit"] for q in repo["quants"]] == ["too big"] * 3
        assert repo["dest"] == os.path.join(models, "Qwen3-0.6B-GGUF")

        # a .part left by an interrupted download is resumed with a Range request
        os.makedirs(repo["dest"], exist_ok=True)
        with open(os.path.join(repo["dest"], "Qwen3-0.6B-Q8_0.gguf.part"), "wb") as f:
            f.write(FILES["Qwen3-0.6B-Q8_0.gguf"][:1000])
        r = client.post("/api/downloads", json={"repo": REPO, "files": ["Qwen3-0.6B-Q8_0.gguf",
                                                                          "big/Qwen3-0.6B-F16-00001-of-00002.gguf"]})
        job = wait_done(client, r.get_json()["download"]["id"])
        assert job["status"] == "done", job
        assert job["bytes_done"] == job["bytes_total"] == 3000 + 2500
        assert hub_cls.seen_ranges == ["bytes=1000-"]
        with open(os.path.join(repo["dest"], "Qwen3-0.6B-Q8_0.gguf"), "rb") as f:
            assert f.read() == FILES["Qwen3-0.6B-Q8_0.gguf"]
        assert os.path.exists(os.path.join(repo["dest"], "big", "Qwen3-0.6B-F16-00001-of-00002.gguf"))
        assert client.get(f"/api/hf/repo?id={REPO}").get_json()["quants"][1]["downloaded"] is True

        # retry of a finished job re-checks sizes and skips what is already complete
        again = client.post(f"/api/downloads/{job['id']}/retry").get_json()["download"]
        assert wait_done(client, again["id"])["status"] == "done"

        bad = client.post("/api/downloads", json={"repo": REPO, "files": ["../../etc/passwd"]})
        assert bad.status_code == 400 and "not in that repo" in bad.get_json()["error"]
        assert client.post("/api/downloads", json={"repo": REPO, "files": "x"}).status_code == 400
        assert client.post("/api/downloads/dl-nope/cancel").status_code == 404
    events = [e["message"] for e in client.get("/api/events?kind=download").get_json()["events"]]
    assert any(m.startswith("finished Qwen/Qwen3-0.6B-GGUF") for m in events)


def test_unreachable_hub_is_a_clear_error(client):
    r = client.get("/api/hf/search?q=qwen")  # conftest points HF_ENDPOINT at a closed port
    assert r.status_code == 502 and "cannot reach" in r.get_json()["error"]
