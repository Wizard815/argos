"""Helper files added onto a main model: a vision projector (mmproj) and a separate MTP head."""
import os

from src import library
from src.library import prefab_command, scan_models, write_helpers_file

from .ggufs import kv_str, kv_u32, llama_kvs, write_gguf


def _gguf(path, layers, size=10_000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_gguf(str(path), llama_kvs(layers=layers), size=size)


def _scan(root):
    library.forget_scans()
    return {m["name"]: m for m in scan_models(str(root))}


def test_Helpers_an_mtp_head_beside_its_model_is_attached_not_listed(tmp_path):
    _gguf(tmp_path / "qwen3-next" / "Qwen3-Coder-Next-Q5_K_S.gguf", 48, size=10_000_000)
    _gguf(tmp_path / "qwen3-next" / "Qwen3-Coder-Next-MTP-Q8_0.gguf", 1)
    models = _scan(tmp_path)
    assert list(models) == ["Qwen3-Coder-Next-Q5_K_S"], "the head is a helper, not a model of its own"
    m = models["Qwen3-Coder-Next-Q5_K_S"]
    assert m["mtp_head"] == [str(tmp_path / "qwen3-next" / "Qwen3-Coder-Next-MTP-Q8_0.gguf")]
    cmd = prefab_command(m, {"mtp_args": "-md {file} --spec-type draft-mtp --spec-draft-n-max 3", "mtp_env": "LLAMA_ENABLE_MTP_OPT=1"}).to_string()
    assert "Qwen3-Coder-Next-MTP-Q8_0.gguf" in cmd and "draft-mtp" in cmd and "LLAMA_ENABLE_MTP_OPT=1" in cmd


def test_Helpers_a_whole_model_with_mtp_inside_stays_a_model(tmp_path):
    _gguf(tmp_path / "tiel" / "tiel-coder-35b-a3b-mtp-ud-q8_k_xl.gguf", 48, size=10_000_000)
    m = _scan(tmp_path)["tiel-coder-35b-a3b-mtp-ud-q8_k_xl"]
    assert m["kind"] == "mtp" and m["mtp_head"] == [], "its MTP layers are inside it: no separate head"


def test_Helpers_a_head_in_another_folder_is_found_by_name(tmp_path):
    _gguf(tmp_path / "big" / "GLM-4.6-Q4_K_M.gguf", 92, size=10_000_000)
    _gguf(tmp_path / "big" / "Qwen3-Next-80B-Q4_K_M.gguf", 48, size=10_000_000)
    _gguf(tmp_path / "heads" / "GLM-4.6-MTP-Q8_0.gguf", 1)
    models = _scan(tmp_path)
    assert models["GLM-4.6-Q4_K_M"]["mtp_head"] == [str(tmp_path / "heads" / "GLM-4.6-MTP-Q8_0.gguf")]
    assert models["Qwen3-Next-80B-Q4_K_M"]["mtp_head"] == [], "named for another model"


def test_Helpers_attached_or_detached_by_hand(tmp_path, client, app):
    _gguf(tmp_path / "a" / "Model-A-Q4_K_M.gguf", 32, size=10_000_000)
    _gguf(tmp_path / "x" / "some-mtp-head.gguf", 1)
    _gguf(tmp_path / "x" / "mmproj-F16.gguf", 2)
    models = _scan(tmp_path)
    assert models["Model-A-Q4_K_M"]["mtp_head"] == [] and models["Model-A-Q4_K_M"]["mmproj"] == []
    write_helpers_file(str(tmp_path), {"a/Model-A-Q4_K_M.gguf": {"mtp": "x/some-mtp-head.gguf", "mmproj": "x/mmproj-F16.gguf"}})
    m = _scan(tmp_path)["Model-A-Q4_K_M"]
    assert m["mtp_head"] == [str(tmp_path / "x" / "some-mtp-head.gguf")] and m["mmproj"] == [str(tmp_path / "x" / "mmproj-F16.gguf")]
    assert m["kind"] == "vision" and m["helpers_by_hand"] == ["mmproj", "mtp"]
    write_helpers_file(str(tmp_path), {"a/Model-A-Q4_K_M.gguf": {"mtp": ""}})
    assert _scan(tmp_path)["Model-A-Q4_K_M"]["mtp_head"] == [], "detached"


def test_Helpers_api_lists_and_attaches(app, client):
    root = app.config["MODELS_DIR"]
    from pathlib import Path
    r = Path(root)
    _gguf(r / "a" / "Model-A-Q4_K_M.gguf", 32, size=10_000_000)
    _gguf(r / "x" / "model-a-mtp-q8.gguf", 2)
    _gguf(r / "x" / "mmproj-F16.gguf", 2)
    library.forget_scans()
    files = client.get("/api/library/helpers").get_json()["files"]
    assert files == {"mmproj": ["x/mmproj-F16.gguf"], "mtp": ["x/model-a-mtp-q8.gguf"]}
    model = str(r / "a" / "Model-A-Q4_K_M.gguf")
    out = client.put("/api/library/helpers", json={"path": model, "mmproj": "x/mmproj-F16.gguf", "mtp": ""}).get_json()
    assert out["model"]["mmproj"] == [str(r / "x" / "mmproj-F16.gguf")] and out["model"]["mtp_head"] == []
    assert client.put("/api/library/helpers", json={"path": model, "mtp": "../etc/passwd"}).status_code == 404, "only files in the models folder"
    assert client.put("/api/library/helpers", json={"path": "/etc/passwd"}).status_code == 404
    back = client.put("/api/library/helpers", json={"path": model, "mtp": None, "mmproj": None}).get_json()["model"]
    assert back["mtp_head"] == [str(r / "x" / "model-a-mtp-q8.gguf")], "automatic again: named like the model"


def test_Helpers_a_projector_is_known_by_its_header_whatever_its_name(tmp_path):
    _gguf(tmp_path / "gemma" / "gemma-3-12b-Q4_K_M.gguf", 48, size=10_000_000)
    write_gguf(str(tmp_path / "gemma" / "gemma-3-12b-vision-f16.gguf"),
               [kv_str("general.architecture", "clip"), kv_u32("clip.block_count", 27)])
    models = _scan(tmp_path)
    assert list(models) == ["gemma-3-12b-Q4_K_M"], "the projector is a helper, not a model of its own"
    assert models["gemma-3-12b-Q4_K_M"]["mmproj"] == [str(tmp_path / "gemma" / "gemma-3-12b-vision-f16.gguf")]


def test_Helpers_several_quants_of_one_head_use_q8_first(tmp_path):
    _gguf(tmp_path / "q" / "Qwen3-Coder-Next-Q5_K_S.gguf", 48, size=10_000_000)
    _gguf(tmp_path / "q" / "Qwen3-Coder-Next-MTP-BF16.gguf", 1)
    _gguf(tmp_path / "q" / "Qwen3-Coder-Next-MTP-Q8_0.gguf", 1)
    heads = _scan(tmp_path)["Qwen3-Coder-Next-Q5_K_S"]["mtp_head"]
    assert [os.path.basename(h) for h in heads] == ["Qwen3-Coder-Next-MTP-Q8_0.gguf", "Qwen3-Coder-Next-MTP-BF16.gguf"]


def test_Helpers_a_model_counts_the_add_ons_it_uses_in_its_size(tmp_path):
    _gguf(tmp_path / "q" / "Qwen3-Coder-Next-Q5_K_S.gguf", 48, size=10_000_000)
    _gguf(tmp_path / "q" / "Qwen3-Coder-Next-MTP-Q8_0.gguf", 1, size=1_000_000)
    _gguf(tmp_path / "q" / "Qwen3-Coder-Next-MTP-BF16.gguf", 1, size=2_000_000)
    m = _scan(tmp_path)["Qwen3-Coder-Next-Q5_K_S"]
    assert m["size"] == 10_000_000 and m["total_size"] == 11_000_000, "the model plus the head in use, not both heads"
    assert [(h["name"], h["used"], h["size"]) for h in m["helpers"]] == [
        ("Qwen3-Coder-Next-MTP-Q8_0.gguf", True, 1_000_000), ("Qwen3-Coder-Next-MTP-BF16.gguf", False, 2_000_000)]
    write_helpers_file(str(tmp_path), {"q/Qwen3-Coder-Next-Q5_K_S.gguf": {"mtp": "q/Qwen3-Coder-Next-MTP-BF16.gguf"}})
    m = _scan(tmp_path)["Qwen3-Coder-Next-Q5_K_S"]
    assert m["total_size"] == 12_000_000 and [h["used"] for h in m["helpers"]] == [True, False]
    assert m["helpers"][0]["name"] == "Qwen3-Coder-Next-MTP-BF16.gguf", "the one picked by hand is in use"
