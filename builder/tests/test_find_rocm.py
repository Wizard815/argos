"""Settings -> GPU tools -> Find ROCm, and a runner build with a ROCm whose compiler is not where AMD's packages put it."""
import os
import stat

import pytest

from src import gputools, runners

pytestmark = pytest.mark.skipif(os.name == "nt", reason="ROCm folders are a Linux thing")


def _exe(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write("#!/bin/sh\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


def _rocm(root, clang_at=None, smi=False):
    os.makedirs(root / "bin", exist_ok=True)
    os.makedirs(root / "lib" / "rocblas" / "library", exist_ok=True)
    open(root / "lib" / "rocblas" / "library" / "TensileLibrary_lazy_gfx906.dat", "w").close()
    os.makedirs(root / ".info", exist_ok=True)
    open(root / ".info" / "version", "w").write("7.1.0\n")
    if clang_at:
        _exe(str(root / clang_at))
    if smi:
        _exe(str(root / "bin" / "rocm-smi"))
    return str(root)


def test_FindRocm_therock_layout_can_build(tmp_path, monkeypatch):
    sdk = _rocm(tmp_path / "rocm-gfx906", clang_at="lib/llvm/bin/clang")
    runtime = _rocm(tmp_path / "rocm-runtime", smi=True)
    monkeypatch.setattr(gputools, "ROCM_GLOBS", (str(tmp_path / "rocm-*"),))
    gputools.configure({})
    found = gputools.find_rocm()
    assert [r["path"] for r in found] == [sdk, runtime], "the one that can build first"
    assert found[0]["can_build"] and found[0]["clang"].endswith("lib/llvm/bin/clang") and found[0]["archs"] == ["gfx906"]
    assert not found[1]["can_build"] and found[1]["rocm_smi"] and found[1]["version"] == "7.1.0"


def test_FindRocm_a_build_uses_the_configured_rocm_clang(tmp_path, monkeypatch):
    sdk = _rocm(tmp_path / "rocm-gfx906", clang_at="lib/llvm/bin/clang")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # no hipconfig anywhere
    gputools.configure({"rocm_path": sdk})
    try:
        assert gputools.rocm_compiler().endswith("lib/llvm/bin/clang")
        assert "hipconfig" not in runners.missing_tools(["hipconfig"])
        assert "$ROCM_PATH/lib/llvm/bin/clang" in runners.ROCM_CXX
    finally:
        gputools.configure({})


def test_FindRocm_runtime_only_says_so(tmp_path, monkeypatch):
    _rocm(tmp_path / "rocm-runtime", smi=True)
    monkeypatch.setattr(gputools, "ROCM_GLOBS", (str(tmp_path / "rocm-*"),))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    gputools.configure({})
    assert "only its runtime" in runners._rocm_advice()
    assert "hipconfig" in runners.missing_tools(["hipconfig"])


def test_FindRocm_api(client, tmp_path, monkeypatch):
    _rocm(tmp_path / "rocm-gfx906", clang_at="llvm/bin/clang")
    monkeypatch.setattr(gputools, "ROCM_GLOBS", (str(tmp_path / "rocm-*"),))
    found = client.get("/api/gputools/rocm").get_json()["found"]
    assert found[0]["path"].endswith("rocm-gfx906") and found[0]["can_build"]
