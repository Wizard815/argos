import os
import time

from src import runner_catalog as catalog
from src.runners import Runners

from .fakes import make_hub, serve
from .test_runners import _Settings, _Store, _touch


def test_runner_catalog_whisper_lists_every_size_offline_and_reads_the_hub_when_it_answers():
    keys = [c["key"] for c in catalog.whisper_choices()]
    for name in ("ggml-tiny.bin", "ggml-tiny.en-q5_1.bin", "ggml-small.en.bin", "ggml-large-v3-turbo.bin",
                 "ggml-large-v3-turbo-q5_0.bin", "ggml-large-v2-q8_0.bin"):
        assert name in keys
    assert "ggml-large-v3.en.bin" not in keys, "large has no English-only build"
    first = catalog.whisper_choices()[0]
    assert first["group"] == "Multilingual" and first["parts"] == [{"repo": catalog.WHISPER_REPO, "file": "ggml-tiny.bin"}]
    live = catalog.whisper_choices([{"name": "ggml-base.bin", "size": 7}, {"name": "ggml-base-encoder.mlmodelc.zip", "size": 1},
                                    {"name": "README.md", "size": 1}, {"name": "ggml-new-size.bin", "size": 3}])
    assert [c["key"] for c in live] == ["ggml-base.bin", "ggml-new-size.bin"], "a size it does not know is still listed"
    assert live[0]["approx"] == 7


def test_runner_catalog_kokoro_has_the_model_all_voices_and_each_voice():
    ch = catalog.kokoro_choices()
    assert ch[0]["key"] == "model" and ch[0]["dest"] == "kokoro-model"
    every = next(c for c in ch if c["key"] == "voices-all")
    assert len(every["parts"]) == len(catalog.KOKORO_VOICES)
    heart = next(c for c in ch if c["key"] == "voice-af_heart")
    assert heart["parts"] == [{"repo": catalog.KOKORO_REPO, "file": "voices/af_heart.pt", "save_as": "af_heart.pt"}]
    assert heart["note"] == "American English, female"


def test_runner_catalog_flux_bundle_loads_as_a_diffusion_model_beside_its_parts(tmp_path):
    flux = next(c for c in catalog.sdcpp_choices() if c["key"] == "flux1-schnell-q4")
    folder = catalog.folder_for(flux, "/models")
    assert folder == os.path.join("/models", "flux1-schnell")
    mid, args = catalog.model_args(flux, "/models/flux1-schnell")
    assert mid == "flux1-schnell-q4_0" and "--vae /models/flux1-schnell/ae.safetensors" in args
    assert len(catalog.missing(flux, str(tmp_path))) == 4

    _touch(str(tmp_path / "sdcpp" / "bin" / ("sd-server.exe" if os.name == "nt" else "sd-server")))
    media = [{"id": mid, "name": mid, "path": "/models/flux1-schnell/flux1-schnell-Q4_0.gguf", "task": "image", "format": "gguf", "size": 1}]
    store = _Store()
    r = Runners(store, _Settings(str(tmp_path)), lambda *a, **k: None, lambda: "", lambda d: media)
    rec = r.records()
    rec["sdcpp"]["opts"] = {"model_args": {mid: args}}
    r.save(rec)
    cmd = r.entries()[mid]["cmd"]
    assert "--diffusion-model /models/flux1-schnell/flux1-schnell-Q4_0.gguf" in cmd and " -m " not in cmd
    assert cmd.count("--diffusion-model") == 1 and "--clip_l /models/flux1-schnell/clip_l.safetensors" in cmd


def _wait(client, ids, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = [d for d in client.get("/api/downloads").get_json()["downloads"] if d["id"] in ids]
        if rows and all(d["status"] not in ("queued", "running") for d in rows):
            return rows
        time.sleep(0.05)
    raise AssertionError("download never finished")


def test_runner_catalog_download_gets_a_whisper_ggml_file_into_the_models_folder(client, app):
    files = {"ggml-tiny.bin": b"t" * 500, "ggml-tiny-encoder.mlmodelc.zip": b"z"}
    with serve(make_hub(files, repo=catalog.WHISPER_REPO)) as url:
        client.put("/api/settings/downloads", json={"hf_endpoint": url})
        cat = client.get("/api/runners/whisper/catalog").get_json()
        assert "Hugging Face" in cat["source"] and [c["key"] for c in cat["choices"]] == ["ggml-tiny.bin"]
        assert cat["choices"][0]["have"] is False
        assert client.post("/api/runners/whisper/catalog/download", json={"key": "nope"}).status_code == 400
        r = client.post("/api/runners/whisper/catalog/download", json={"key": "ggml-tiny.bin"}).get_json()
        assert [d["status"] for d in _wait(client, {j["id"] for j in r["jobs"]})] == ["done"]
        path = os.path.join(app.config["MODELS_DIR"], "whisper.cpp", "ggml-tiny.bin")
        assert os.path.getsize(path) == 500
        assert client.get("/api/runners/whisper/catalog").get_json()["choices"][0]["have"] is True
        assert client.post("/api/runners/whisper/catalog/download", json={"key": "ggml-tiny.bin"}).get_json()["have"] is True
        # the plain download API takes a file that is not a GGUF too
        assert client.post("/api/downloads", json={"repo": catalog.WHISPER_REPO, "files": ["ggml-tiny-encoder.mlmodelc.zip"]}).status_code == 200


def test_runner_catalog_kokoro_voice_lands_in_kokoros_own_folder(client):
    with serve(make_hub({"voices/af_heart.pt": b"v" * 40, "kokoro-v1_0.pth": b"k"}, repo=catalog.KOKORO_REPO)) as url:
        client.put("/api/settings/downloads", json={"hf_endpoint": url})
        cat = client.get("/api/runners/kokoro/catalog").get_json()
        heart = next(c for c in cat["choices"] if c["key"] == "voice-af_heart")
        assert [c["key"] for c in cat["choices"]] == ["model", "voices-all", "voice-af_heart"]
        r = client.post("/api/runners/kokoro/catalog/download", json={"key": "voice-af_heart"}).get_json()
        assert [d["status"] for d in _wait(client, {j["id"] for j in r["jobs"]})] == ["done"]
        assert heart["folder"].endswith(os.path.join("api", "src", "voices", "v1_0"))
        assert os.path.getsize(os.path.join(heart["folder"], "af_heart.pt")) == 40


def test_runner_catalog_falls_back_to_the_built_in_list_offline(client):
    cat = client.get("/api/runners/whisper/catalog").get_json()
    assert cat["source"].startswith("built in") and len(cat["choices"]) > 20
    sd = client.get("/api/runners/sdcpp/catalog").get_json()
    assert {c["key"] for c in sd["choices"]} >= {"sd-v1-5", "sdxl-turbo", "flux1-schnell-q4"}
