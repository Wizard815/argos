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
    assert '"-DAMDGPU_TARGETS=gfx906"' in steps[1]["run"]
    steps = runners.install_steps("whisper", "/w", "rocm", {"repo": "https://x/y", "gpu_target": "gfx906, gfx90a"})
    assert '"-DAMDGPU_TARGETS=gfx906;gfx90a"' in steps[1]["run"]
