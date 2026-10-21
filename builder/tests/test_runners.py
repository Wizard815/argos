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
