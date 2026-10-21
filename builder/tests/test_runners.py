import os

from src import audio_convert
from src.runners import Runners, detect, scan


class _Store:
    def __init__(self):
        self.s = {}

    def get_settings(self):
        return self.s

    def set_setting(self, k, v):
        self.s[k] = v


class _Settings:
    def __init__(self, root):
        self.root = root

    def get(self, group):
        return {"paths": {"runners_dir": self.root, "models_dir": ""}}.get(group, {})


def _touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def test_whisper_serves_ggml_files_and_the_smallest_answers_to_whisper_1(tmp_path):
    exe = "whisper-server.exe" if os.name == "nt" else "whisper-server"
    _touch(str(tmp_path / "whisper" / "bin" / exe))
    _touch(str(tmp_path / "whisper" / "models" / "ggml-base.en.bin"), b"x" * 100)
    _touch(str(tmp_path / "whisper" / "models" / "ggml-large-v3.bin"), b"x" * 900)
    media = [{"id": "piper-voice", "name": "piper-voice", "path": "/m/piper.onnx", "task": "text-to-speech", "format": "onnx"}]
    r = Runners(_Store(), _Settings(str(tmp_path)), lambda *a, **k: None, lambda: "", lambda d: media)
    assert detect("whisper", str(tmp_path / "whisper"))["installed"]
    e = r.entries()
    assert set(e) == {"ggml-base.en", "ggml-large-v3"}, "a TTS file is not given to whisper, and Kokoro is not installed"
    assert e["ggml-base.en"]["aliases"] == ["whisper-1"] and "aliases" not in e["ggml-large-v3"]
    assert "--inference-path /v1/audio/transcriptions" in e["ggml-base.en"]["cmd"] and e["ggml-base.en"]["cwd"]
    assert e["ggml-base.en"]["metadata"]["argos"]["task"] == "speech-to-text"


def test_an_override_stops_a_model_being_served(tmp_path):
    _touch(str(tmp_path / "whisper" / "bin" / ("whisper-server.exe" if os.name == "nt" else "whisper-server")))
    _touch(str(tmp_path / "whisper" / "models" / "ggml-base.en.bin"))
    store = _Store()
    store.s["_runner_overrides"] = {"ggml-base.en": "none"}
    assert Runners(store, _Settings(str(tmp_path)), lambda *a, **k: None, lambda: "", lambda d: []).entries() == {}


def test_scan_reads_inline_build_lines_and_build_files(tmp_path):
    (tmp_path / "README.md").write_text("# fork\n> Build: `cmake -B build -DGGML_HIP=1 && cmake --build build --target whisper-server`\n"
                                        "Speech to text server.\n```bash\n./build/bin/whisper-server --port 8080 -m m.bin\n```\n")
    (tmp_path / "CMakeLists.txt").write_text("project(x)")
    found = scan(str(tmp_path))
    assert found["install"][0]["cmd"].startswith("cmake -B build -DGGML_HIP=1") and found["install"][0]["source"] == "README line 2"
    assert any("whisper-server --port" in s["cmd"] for s in found["start"])
    assert found["task"] == "speech-to-text"


def test_uploads_are_rebuilt_with_every_field_and_wav_is_left_alone():
    wav = (b"RIFF" + b"\0" * 4 + b"WAVEfmt " + (16).to_bytes(4, "little") + (1).to_bytes(2, "little") + (1).to_bytes(2, "little")
           + (16000).to_bytes(4, "little") + b"\0" * 6 + (16).to_bytes(2, "little") + b"data")
    assert audio_convert.is_whisper_wav(wav)
    assert audio_convert.convert_form({"model": ["whisper-1"]}, {"file": [("a.wav", "audio/wav", wav)]}, "ffmpeg") is None
    body, ctype, _ = audio_convert.rebuild({"model": ["whisper-1"]}, {"file": [("a.m4a", "audio/mp4", b"abc")]})
    assert ctype.startswith("multipart/form-data; boundary=") and b'name="model"' in body and b"whisper-1" in body and b"abc" in body


def test_custom_pytorch_takes_an_index_txt_a_pip_index_or_wheels():
    from src.runners import install_steps, torch_source
    t = torch_source("https://example.org/py-gfx906/rocm10.1/torch-v2.14.1+gfx906/index.txt")
    assert t["kind"] == "index.txt" and t["python"] == "3.12" and "wheels_index" in t["step"]
    w = torch_source("/wheels/torch-2.14.1-cp312-cp312-linux_x86_64.whl, /wheels/torchaudio-2.14.1-cp312-cp312-linux_x86_64.whl")
    assert w["kind"] == "wheels" and w["python"] == "3.12" and w["step"]["run"].count(".whl") == 2
    assert torch_source("https://download.pytorch.org/whl/rocm6.4")["kind"] == "index"
    assert torch_source("") is None
    steps = install_steps("kokoro", "/data/runners/kokoro", "rocm", {"torch_index": "https://example.org/x/index.txt"})
    runs = [s.get("run", "") for s in steps]
    # the custom build goes in before Kokoro, and Kokoro is installed without its own ROCm PyTorch extra
    assert "uv venv --python 3.12 .venv" in runs and 'uv pip install --python .venv -e .' in runs
    assert not any("[rocm]" in r for r in runs)
    # on CPU the field is ignored
    assert any('".[cpu]"' in r for r in (s.get("run", "") for s in install_steps("kokoro", "/k", "cpu", {"torch_index": "https://x/index.txt"})))


def test_whisper_rocm_build_uses_the_detected_architecture(monkeypatch):
    from src import runners
    runners.rocm_archs.cache_clear()
    monkeypatch.setattr(runners, "rocm_archs", lambda: ("gfx906",))
    steps = runners.install_steps("whisper", "/data/runners/whisper", "rocm", {"repo": "https://github.com/someone/whisper.cpp-fork"})
    assert '"-DAMDGPU_TARGETS=gfx906"' in steps[2]["run"]
    steps = runners.install_steps("whisper", "/w", "rocm", {"repo": "https://x/y", "gpu_target": "gfx906, gfx90a"})
    assert '"-DAMDGPU_TARGETS=gfx906;gfx90a"' in steps[2]["run"]


def test_sdcpp_serves_single_file_image_models_with_their_extra_args(tmp_path):
    exe = "sd-server.exe" if os.name == "nt" else "sd-server"
    _touch(str(tmp_path / "sdcpp" / "bin" / exe))
    media = [{"id": "sd-v1-5", "name": "sd-v1-5", "path": "/m/sd-v1-5.gguf", "task": "image", "format": "gguf", "size": 2 * 2 ** 30},
             {"id": "flux-dev", "name": "flux-dev", "path": "/m/flux-dev.safetensors", "task": "image", "format": "safetensors", "size": 2 ** 30},
             {"id": "ae", "name": "ae", "path": "/m/vae/ae.safetensors", "task": "vae", "format": "safetensors", "size": 1},
             {"id": "sdxl-folder", "name": "sdxl", "path": "/m/sdxl", "task": "image", "format": "diffusers", "size": 1}]
    store = _Store()
    r = Runners(store, _Settings(str(tmp_path)), lambda *a, **k: None, lambda: "", lambda d: media)
    rec = r.records()
    rec["sdcpp"]["device"] = "cuda"
    rec["sdcpp"]["opts"] = {"model_args": {"flux-dev": "--vae /m/vae/ae.safetensors"}}
    r.save(rec)
    e = r.entries()
    assert set(e) == {"sd-v1-5", "flux-dev"}, "a VAE part and a diffusers folder are not sd-server models"
    assert "--listen-port ${PORT}" in e["sd-v1-5"]["cmd"] and e["sd-v1-5"]["capabilities"]["out"] == ["image"]
    assert e["flux-dev"]["cmd"].endswith("--vae /m/vae/ae.safetensors")
    assert sum(1 for x in e.values() if x.get("aliases") == ["dall-e-3"]) == 1
    assert e["sd-v1-5"]["vram"] == 3.5  # 2 GiB * 1.25 + 1


def test_a_custom_runner_with_a_file_pattern_serves_one_model_per_file(tmp_path):
    models = tmp_path / "models"
    _touch(str(models / "voices" / "en_us-amy.onnx"))
    _touch(str(models / "voices" / "en_gb-alan.onnx"))
    store = _Store()

    class S(_Settings):
        def get(self, group):
            return {"paths": {"runners_dir": str(tmp_path / "runners"), "models_dir": str(models)}}.get(group, {})

    r = Runners(store, S(""), lambda *a, **k: None, lambda: str(models), lambda d: [])
    rec = r.records()
    rec["piper"] = {"id": "piper", "kind": "custom", "device": "cpu", "enabled": True, "opts": {
        "start": "piper-server --port ${PORT} --model ${MODEL_PATH}", "models_glob": "**/*.onnx", "task": "text-to-speech"}}
    r.save(rec)
    e = r.entries()
    assert set(e) == {"piper-en_gb-alan", "piper-en_us-amy"}
    assert all("${MODEL_PATH}" not in x["cmd"] and ".onnx" in x["cmd"] for x in e.values())


def test_rocm_architectures_are_lowercased_and_the_hip_compiler_is_set(monkeypatch):
    from src import runners
    monkeypatch.setattr(runners, "WINDOWS", False)
    steps = runners.install_steps("whisper", "/data/runners/whisper", "rocm",
                                  {"repo": "https://github.com/Wizard815/whisper.cpp-rocm10-gfx906", "gpu_target": "GFX906"})
    assert steps[0] == {"needs": ["git", "cmake", "c++", "hipconfig"]}
    assert steps[1]["run"].startswith('git clone --depth 1 "https://github.com/Wizard815/whisper.cpp-rocm10-gfx906"')
    assert '"-DAMDGPU_TARGETS=gfx906"' in steps[2]["run"] and "HIPCXX=" in steps[2]["run"]
    assert steps[3]["run"].endswith("--target whisper-server")


def test_gpu_tools_put_a_custom_rocm_in_front_for_builds_and_runs(tmp_path):
    from src import gputools
    rocm = tmp_path / "rocm-gfx906"
    for d in ("bin", "llvm/bin", "lib"):
        (rocm / d).mkdir(parents=True)
    try:
        gputools.configure({"rocm_path": str(rocm)})
        e = gputools.env({"PATH": "/usr/bin"}, "rocm")
        assert e["PATH"].split(os.pathsep if os.name != "nt" else ";")[0] == str(rocm / "bin") and e["ROCM_PATH"] == str(rocm)
        lines = gputools.run_env("rocm")
        assert any(x.startswith("ROCM_PATH=") for x in lines) and not any(x.startswith("PATH=") for x in lines)
        assert gputools.run_env("cuda") == [] and gputools.run_env("cpu") == []
    finally:
        gputools.configure({})


def test_a_source_build_checks_its_tools_first(monkeypatch):
    from src import runners
    monkeypatch.setattr(runners, "WINDOWS", False)
    steps = runners.install_steps("whisper", "/data/runners/whisper", "rocm", {"repo": "https://x/y", "gpu_target": "gfx906"})
    assert steps[0] == {"needs": ["git", "cmake", "c++", "hipconfig"]}
    monkeypatch.setattr(runners.shutil, "which", lambda n, path=None: None if n in ("cmake", "hipconfig") else f"/usr/bin/{n}")
    assert runners.missing_tools(["git", "cmake", "c++", "hipconfig"]) == ["cmake", "hipconfig"]


def test_a_whisper_server_you_built_is_used_without_installing(tmp_path):
    exe = tmp_path / "mine" / "whisper-server"
    _touch(str(exe))
    _touch(str(tmp_path / "runners" / "whisper" / "models" / "ggml-base.en.bin"))
    r = Runners(_Store(), _Settings(str(tmp_path / "runners")), lambda *a, **k: None, lambda: "", lambda d: [])
    rec = r.records()
    rec["whisper"]["opts"] = {"program": str(exe)}
    r.save(rec)
    e = r.entries()
    assert list(e) == ["ggml-base.en"] and e["ggml-base.en"]["cmd"].startswith(str(exe).replace("\\", "/") if os.name == "nt" else str(exe))
    assert r.found(rec["whisper"])["detail"] == "your own build"
