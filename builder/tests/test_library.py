import os

from src.command import parse_command
from src.library import prefab_command, scan_media, scan_models
from src.settings import env_defaults

PREFAB = env_defaults({})["prefab"]


def touch(path, size=10):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)


def test_scan_like_modelscan(tmp_path):
    m = tmp_path / "models"
    touch(m / "Qwen3-8B-Q4_K_M.gguf", 100)
    touch(m / "big" / "Mistral-UD-Q3_K_XL-00001-of-00003.gguf", 50)
    touch(m / "big" / "Mistral-UD-Q3_K_XL-00002-of-00003.gguf", 50)
    touch(m / "big" / "Mistral-UD-Q3_K_XL-00003-of-00003.gguf", 40)
    touch(m / "half" / "Part-Q4_0-00001-of-00002.gguf", 5)
    touch(m / "gemma" / "gemma-3-12b-it-Q4_K_M.gguf", 30)
    touch(m / "gemma" / "mmproj-gemma-3-12b-Q8_0.gguf", 3)
    touch(m / "gemma" / "mmproj-gemma-3-12b-F16.gguf", 6)
    touch(m / "nomic-embed-text-v1.5.Q8_0.gguf", 2)
    touch(m / "mtp-gemma-4-12b-it-q8_0.gguf", 20)
    touch(m / ".hidden" / "skip.gguf")
    touch(m / "notes.txt")
    models = {x["id"]: x for x in scan_models(str(m))}
    assert set(models) == {"qwen3-8b-q4_k_m", "mistral-ud-q3_k_xl-00001-of-00003", "part-q4_0-00001-of-00002",
                           "gemma-3-12b-it-q4_k_m", "nomic-embed-text-v1.5.q8_0", "mtp-gemma-4-12b-it-q8_0"}
    big = models["mistral-ud-q3_k_xl-00001-of-00003"]
    assert (big["size"], big["shards"], big["quant"]) == (140, 3, "UD-Q3_K_XL")
    assert models["part-q4_0-00001-of-00002"]["shards"] == -2  # second shard missing
    gemma = models["gemma-3-12b-it-q4_k_m"]
    assert gemma["kind"] == "vision" and os.path.basename(gemma["mmproj"][0]) == "mmproj-gemma-3-12b-F16.gguf"
    assert models["nomic-embed-text-v1.5.q8_0"]["kind"] == "embedding"
    assert models["mtp-gemma-4-12b-it-q8_0"]["kind"] == "mtp"
    assert scan_models(str(tmp_path / "missing")) == []


def test_audio_and_image_models_are_listed_but_not_launchable(tmp_path):
    m = tmp_path / "models"
    touch(m / "qwen" / "Qwen3-8B-Q4_K_M.gguf", 100)
    touch(m / "whisper" / "ggml-large-v3-turbo.bin", 40)
    touch(m / "tts" / "kokoro-v1.0.onnx", 8)
    touch(m / "tts" / "OuteTTS-0.3-1B-Q8_0.gguf", 9)
    touch(m / "image" / "flux1-dev-Q8_0.gguf", 60)
    touch(m / "image" / "ae.safetensors", 3)
    touch(m / "image" / "t5xxl_fp16.safetensors", 7)
    touch(m / "sdxl-turbo" / "model_index.json", 1)
    touch(m / "sdxl-turbo" / "unet" / "diffusion_pytorch_model.safetensors", 20)
    touch(m / "llm" / "model-00001-of-00002.safetensors", 5)
    touch(m / "llm" / "voice-assistant-llama-8b-Q4_K_M.gguf", 5)
    assert sorted(x["name"] for x in scan_models(str(m))) == ["Qwen3-8B-Q4_K_M", "voice-assistant-llama-8b-Q4_K_M"],         "audio and image GGUFs are not offered to llama-server"
    media = {x["name"]: x for x in scan_media(str(m))}
    assert set(media) == {"ggml-large-v3-turbo", "kokoro-v1.0", "OuteTTS-0.3-1B-Q8_0", "flux1-dev-Q8_0", "ae", "t5xxl_fp16", "sdxl-turbo"}
    assert (media["ggml-large-v3-turbo"]["kind"], media["ggml-large-v3-turbo"]["task"], media["ggml-large-v3-turbo"]["format"]) == ("audio", "speech-to-text", "ggml")
    assert media["kokoro-v1.0"]["task"] == "text-to-speech" and media["OuteTTS-0.3-1B-Q8_0"]["quant"] == "Q8_0"
    assert (media["flux1-dev-Q8_0"]["kind"], media["ae"]["task"], media["t5xxl_fp16"]["task"]) == ("image", "vae", "text encoder")
    assert media["sdxl-turbo"]["format"] == "diffusers" and media["sdxl-turbo"]["size"] == 21, "a diffusers folder is one model"
    assert all(x["listed_only"] for x in media.values())


def test_prefabs_per_kind():
    chat = prefab_command({"path": "/m/Qwen3-8B-Q4_K_M.gguf", "kind": "chat", "mmproj": []}, PREFAB)
    assert chat.to_string(style="odysseus") == (
        'llama-server --model "/m/Qwen3-8B-Q4_K_M.gguf" --host 0.0.0.0 --port ${PORT} -ngl 99 -c 32768 '
        '--flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 --jinja')

    tuned = {**PREFAB, "load_mode": "dio", "split_mode": "layer", "env": "HIP_VISIBLE_DEVICES=0\n# note",
             "extra": "--n-cpu-moe 4", "flash_attn": "", "cache_type": ""}
    s = prefab_command({"path": "/m/A.gguf", "kind": "chat", "mmproj": []}, tuned, port="${PORT}")
    assert s.env == {"HIP_VISIBLE_DEVICES": "0"}
    assert s.get("load-mode") == "dio" and s.get("split-mode") == "layer" and s.get("n-cpu-moe") == "4"
    assert s.get("port") == "${PORT}" and s.get("flash-attn") is None

    emb = prefab_command({"path": "/m/nomic-embed.gguf", "kind": "embedding", "mmproj": []}, PREFAB)
    assert emb.get("embedding") == "" and emb.get("pooling") == "mean" and emb.get("ctx-size") == "8192"
    assert emb.get("jinja") is None and emb.get("cache-type-k") is None

    mtp = prefab_command({"path": "/m/mtp-x.gguf", "kind": "mtp", "mmproj": []}, PREFAB)
    assert mtp.env["LLAMA_ENABLE_MTP_OPT"] == "1"
    assert mtp.get("spec-type") == "draft-mtp" and mtp.get("spec-draft-n-max") == "3"

    vis = prefab_command({"path": "/m/g.gguf", "kind": "vision", "mmproj": ["/m/mm proj.gguf"]}, PREFAB)
    assert vis.get("mmproj") == "/m/mm proj.gguf" and vis.get("image-max-tokens") == "1024"
    assert parse_command(vis.to_string(style="odysseus")).get("mmproj") == "/m/mm proj.gguf"


def test_prefab_launch_program_can_point_at_a_remote_launcher():
    """A build that holds no llama.cpp launches through whatever it does have.

    The program belongs to the prefab because the prefab is what gets run: an
    empty setting stays llama-server, and a wrapper keeps its backend word with
    the llama-server flags after it, which is exactly what the wrapper passes on.
    """
    model = {"path": "/m/Qwen3-8B-Q4_K_M.gguf", "kind": "chat", "mmproj": []}
    assert prefab_command(model, PREFAB).program == "llama-server", "empty program means the usual binary"

    remote = prefab_command(model, {**PREFAB, "program": " /app/bin/remote-llama rocm "})
    assert remote.program == "/app/bin/remote-llama"
    text = remote.to_string(style="odysseus")
    assert text.startswith("/app/bin/remote-llama rocm --model "), text
    # the flags the wrapper forwards still have to be there, the port included
    assert "--host 0.0.0.0" in text and "--port ${PORT}" in text and "-ngl 99" in text


def test_library_routes_and_profile_counts(client, app):
    models_dir = app.extensions["pb"].settings.get("paths")["models_dir"]
    touch(os.path.join(models_dir, "q", "Qwen3-0.6B-Q8_0.gguf"))
    lib = client.get("/api/library").get_json()
    assert lib["exists"] and [m["id"] for m in lib["models"]] == ["qwen3-0.6b-q8_0"]
    path = lib["models"][0]["path"]
    pre = client.post("/api/library/prefab", json={"path": path}).get_json()
    assert pre["command"].startswith(f'llama-server --model "{path}" --host 0.0.0.0 --port ${{PORT}}')
    assert client.post("/api/library/prefab", json={"path": "/etc/passwd"}).status_code == 404
    for i in range(3):
        client.post("/api/profiles", json={"name": f"p{i}", "launch_command": pre["command"]})
    assert client.get("/api/library").get_json()["models"][0]["profiles"] == 3
    only = client.get("/api/profiles?model_id=qwen3-0.6b-q8_0").get_json()["profiles"]
    assert len(only) == 3 and all(p["model_id"] == "qwen3-0.6b-q8_0" for p in only)


def test_delete_a_model_removes_all_its_parts_and_nothing_else(client, app):
    m = app.config["MODELS_DIR"]
    for part in (1, 2):
        touch(os.path.join(m, "big", f"Big-Q4_K_M-0000{part}-of-00002.gguf"))
    touch(os.path.join(m, "big", "mmproj-big-F16.gguf"))
    touch(os.path.join(m, "keep", "Keep-Q8_0.gguf"))
    touch(os.path.join(m, "sdxl", "model_index.json"))
    touch(os.path.join(m, "sdxl", "unet", "x.safetensors"))
    lib = client.get("/api/library").get_json()
    big = next(x for x in lib["models"] if x["name"].startswith("Big"))
    check = client.post("/api/library/delete/check", json={"path": big["path"]}).get_json()
    assert len(check["files"]) == 2 and check["mmproj"] == [os.path.join(m, "big", "mmproj-big-F16.gguf")]
    r = client.post("/api/library/delete", json={"path": big["path"], "with_mmproj": True}).get_json()
    assert len(r["removed"]) == 3 and not r["problems"]
    assert sorted(os.listdir(os.path.join(m, "big"))) == []
    assert os.path.exists(os.path.join(m, "keep", "Keep-Q8_0.gguf"))

    sdxl = next(x for x in client.get("/api/library").get_json()["media"] if x["name"] == "sdxl")
    assert client.post("/api/library/delete", json={"path": sdxl["path"]}).get_json()["ok"]
    assert not os.path.exists(os.path.join(m, "sdxl")), "a diffusers model is its whole folder"
    assert client.post("/api/library/delete", json={"path": os.path.join(m, "..", "x.gguf")}).status_code == 404
    assert client.post("/api/library/delete", json={"path": m}).status_code == 404


def test_model_groups_round_trip(client):
    assert client.get("/api/library/groups").get_json()["groups"] == []
    r = client.put("/api/library/groups", json={"groups": [{"name": "Qwen", "models": ["a", "b"]}, {"name": "qwen", "models": ["c"]},
                                                          {"name": "Gemma", "models": ["b", "d"]}, {"name": " "}]}).get_json()
    assert r["groups"] == [{"name": "Qwen", "models": ["a", "b"]}, {"name": "Gemma", "models": ["d"]}],         "names are unique, a model sits in one group, blank names are dropped"
    assert client.get("/api/library/groups").get_json()["groups"] == r["groups"]
    assert client.put("/api/library/groups", json={"groups": "x"}).status_code == 400


def test_scan_models_walks_the_folder_once_and_forgets_on_request(tmp_path, monkeypatch):
    import src.library as lib
    lib.forget_scans()
    (tmp_path / "A-Q4_K_M.gguf").write_bytes(b"x")
    walks = []
    real = os.walk
    monkeypatch.setattr(lib.os, "walk", lambda *a, **k: (walks.append(1), real(*a, **k))[1])
    assert [m["name"] for m in lib.scan_models(str(tmp_path))] == ["A-Q4_K_M"]
    (tmp_path / "B-Q4_K_M.gguf").write_bytes(b"x")
    assert len(lib.scan_models(str(tmp_path))) == 1 and len(walks) == 1  # served from the cache
    lib.forget_scans()
    assert len(lib.scan_models(str(tmp_path))) == 2 and len(walks) == 2


def test_scan_models_hands_out_copies(tmp_path):
    import src.library as lib
    lib.forget_scans()
    (tmp_path / "A-Q4_K_M.gguf").write_bytes(b"x")
    lib.scan_models(str(tmp_path))[0]["name"] = "mutated"
    assert lib.scan_models(str(tmp_path))[0]["name"] == "A-Q4_K_M"


def test_hugging_face_cache_files_are_named_by_their_repo(tmp_path):
    from src.library import _scan_media, display_name
    snap = tmp_path / "HFCache" / "hub" / "models--unsloth--whisper-large-v3" / "snapshots" / "1c3aae815bd9d41a"
    snap.mkdir(parents=True)
    (snap / "model.safetensors").write_bytes(b"x")
    (m,) = _scan_media(str(tmp_path))
    assert m["name"] == "whisper-large-v3" and m["task"] == "speech-to-text"
    assert display_name("/m/Qwen3-0.6B-Q8_0.gguf", "Qwen3-0.6B-Q8_0") == "Qwen3-0.6B-Q8_0"
    assert display_name("/m/flux-schnell/diffusion_pytorch_model.safetensors", "diffusion_pytorch_model") == "flux-schnell"


def test_a_renamed_model_keeps_its_id_and_file_name():
    from src.library import with_names
    (m,) = with_names([{"id": "model", "name": "model", "path": "/x"}], {"model": "Whisper large v3"})
    assert (m["id"], m["name"], m["file_name"], m["renamed"]) == ("model", "Whisper large v3", "model", True)
