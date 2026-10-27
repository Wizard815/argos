"""The models each local runner can download: the choices in Servers -> Local's dropdowns.

whisper.cpp reads the ggml builds in ggerganov/whisper.cpp (every size, English-only and quantized); Kokoro
serves its own model and voices from its own folder; stable-diffusion.cpp loads single-file image models, and
a few (Flux) need a VAE and text encoders beside them, downloaded together as one bundle with the arguments
that point sd-server at them.

Each choice is {"key", "label", "group", "note", "approx", "parts": [{"repo", "file", "save_as"?}],
"dest": "models" | "kokoro-model" | "kokoro-voices", "subdir"?, "args"?}. "approx" is a size in bytes for
the list only: the download takes its real size from the Hub, or from the server's own Content-Length.
The whisper list and Kokoro's voices are read from the Hub when it answers; this list stands in when not.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from .command import swap_model_id

WHISPER_REPO = "ggerganov/whisper.cpp"
KOKORO_REPO = "hexgrad/Kokoro-82M"
MB = 1_000_000

# size -> (approx MB of the full model, its quantized builds with their approx MB)
WHISPER_SIZES = {
    "tiny": (75, {"q5_1": 31, "q8_0": 42}),
    "base": (142, {"q5_1": 57, "q8_0": 78}),
    "small": (466, {"q5_1": 181, "q8_0": 252}),
    "medium": (1500, {"q5_0": 514, "q8_0": 785}),
    "large-v1": (2900, {}),
    "large-v2": (2900, {"q5_0": 1080, "q8_0": 1500}),
    "large-v3": (2900, {"q5_0": 1080}),
    "large-v3-turbo": (1500, {"q5_0": 547, "q8_0": 834}),
}
WHISPER_NOTES = {
    "tiny": "fastest, roughest", "base": "fast, fair", "small": "good on the CPU", "medium": "better, slower",
    "large-v1": "the first large", "large-v2": "large, older", "large-v3": "most accurate, slowest",
    "large-v3-turbo": "near large-v3 accuracy at medium speed (the usual pick)",
}

KOKORO_MODEL = [{"repo": KOKORO_REPO, "file": "kokoro-v1_0.pth"}, {"repo": KOKORO_REPO, "file": "config.json"}]
KOKORO_VOICES = (
    "af_heart af_alloy af_aoede af_bella af_jessica af_kore af_nicole af_nova af_river af_sarah af_sky "
    "am_adam am_echo am_eric am_fenrir am_liam am_michael am_onyx am_puck am_santa "
    "bf_alice bf_emma bf_isabella bf_lily bm_daniel bm_fable bm_george bm_lewis "
    "ef_dora em_alex em_santa ff_siwis hf_alpha hf_beta hm_omega hm_psi if_sara im_nicola "
    "jf_alpha jf_gongitsune jf_nezumi jf_tebukuro jm_kumo pf_dora pm_alex pm_santa "
    "zf_xiaobei zf_xiaoni zf_xiaoxiao zf_xiaoyi zm_yunjian zm_yunxi zm_yunxia zm_yunyang").split()
VOICE_LANG = {"a": "American English", "b": "British English", "e": "Spanish", "f": "French", "h": "Hindi",
              "i": "Italian", "j": "Japanese", "p": "Portuguese", "z": "Chinese"}

# stable-diffusion.cpp: single files first, then the bundles that need parts beside them
SDCPP: list[dict] = [
    {"key": "sd-v1-5", "label": "Stable Diffusion 1.5 (fp16)", "group": "Single file", "approx": 2130 * MB,
     "note": "512x512, the lightest; runs on almost anything",
     "parts": [{"repo": "Comfy-Org/stable-diffusion-v1-5-archive", "file": "v1-5-pruned-emaonly-fp16.safetensors"}]},
    {"key": "sd-turbo", "label": "SD-Turbo", "group": "Single file", "approx": 5210 * MB,
     "note": "512x512 in 1-4 steps: add --steps 4 --cfg-scale 1",
     "parts": [{"repo": "stabilityai/sd-turbo", "file": "sd_turbo.safetensors"}], "args": "--steps 4 --cfg-scale 1"},
    {"key": "sdxl-turbo", "label": "SDXL-Turbo (fp16)", "group": "Single file", "approx": 6940 * MB,
     "note": "fast SDXL, 512x512 in 1-4 steps",
     "parts": [{"repo": "stabilityai/sdxl-turbo", "file": "sd_xl_turbo_1.0_fp16.safetensors"}], "args": "--steps 4 --cfg-scale 1"},
    {"key": "sdxl-base", "label": "SDXL 1.0 base", "group": "Single file", "approx": 6940 * MB,
     "note": "1024x1024, about 20-30 steps",
     "parts": [{"repo": "stabilityai/stable-diffusion-xl-base-1.0", "file": "sd_xl_base_1.0.safetensors"}]},
    {"key": "flux1-schnell-q4", "label": "FLUX.1 schnell Q4_0 + VAE + text encoders", "group": "Bundle", "approx": 12300 * MB,
     "note": "best quality here, 4 steps; the diffusion model with its VAE, CLIP-L and T5-XXL (fp8)",
     "subdir": "flux1-schnell", "main": "flux1-schnell-Q4_0.gguf",
     "parts": [{"repo": "city96/FLUX.1-schnell-gguf", "file": "flux1-schnell-Q4_0.gguf"},
               {"repo": "black-forest-labs/FLUX.1-schnell", "file": "ae.safetensors"},
               {"repo": "comfyanonymous/flux_text_encoders", "file": "clip_l.safetensors"},
               {"repo": "comfyanonymous/flux_text_encoders", "file": "t5xxl_fp8_e4m3fn.safetensors"}],
     "args": "--diffusion-model --vae {dir}/ae.safetensors --clip_l {dir}/clip_l.safetensors "
             "--t5xxl {dir}/t5xxl_fp8_e4m3fn.safetensors --cfg-scale 1.0 --sampling-method euler --steps 4"},
]


def whisper_choices(live: Optional[list[dict]] = None) -> list[dict]:
    """Every ggml whisper model: the Hub's listing of WHISPER_REPO when given, else the known list."""
    names = [f["name"] for f in live or [] if re.fullmatch(r"ggml-[\w.-]+\.bin", f["name"]) and "encoder" not in f["name"]]
    sizes = {f["name"]: f.get("size") or 0 for f in live or []}
    if not names:
        for size, (full, quants) in WHISPER_SIZES.items():
            english = size in ("tiny", "base", "small", "medium")
            for suffix in ("", ".en") if english else ("",):
                names.append(f"ggml-{size}{suffix}.bin")
                names += [f"ggml-{size}{suffix}-{q}.bin" for q in quants]
    out = []
    for name in names:
        m = re.fullmatch(r"ggml-(.+?)(\.en)?(?:-(q\d_\d))?\.bin", name)
        size, en, quant = (m.group(1), m.group(2), m.group(3)) if m else (name, None, None)
        full, quants = WHISPER_SIZES.get(size, (0, {}))
        approx = sizes.get(name) or (quants.get(quant, 0) if quant else full) * MB
        label = f"{size}{' (English only)' if en else ''}{f' · {quant}' if quant else ''}"
        note = WHISPER_NOTES.get(size, "")
        if quant:
            note = f"{note}; quantized: smaller, a little less accurate" if note else "quantized"
        out.append({"key": name, "label": label, "group": "English only" if en else "Multilingual", "note": note,
                    "approx": approx, "parts": [{"repo": WHISPER_REPO, "file": name}], "dest": "models",
                    "subdir": "whisper.cpp"})
    order = list(WHISPER_SIZES)
    out.sort(key=lambda c: (c["group"] != "Multilingual", order.index(c["label"].split(" ")[0]) if c["label"].split(" ")[0] in order else 99,
                            "·" in c["label"], c["key"]))  # the full model before its quantized builds
    return out


def kokoro_choices(live: Optional[list[dict]] = None) -> list[dict]:
    """Kokoro's model and every voice (the Hub's voices/ when given, else the known list)."""
    voices = sorted(os.path.basename(f["name"])[:-3] for f in live or [] if re.fullmatch(r"voices/[\w-]+\.pt", f["name"]))
    sizes = {f["name"]: f.get("size") or 0 for f in live or []}
    out = [{"key": "model", "label": "Kokoro v1.0 model (82M)", "group": "Model", "approx": sizes.get("kokoro-v1_0.pth") or 327 * MB,
            "note": "the one model Kokoro-FastAPI runs; Install fetches it too", "parts": KOKORO_MODEL, "dest": "kokoro-model"}]
    every = voices or KOKORO_VOICES
    out.append({"key": "voices-all", "label": f"All {len(every)} voices", "group": "Voices", "approx": len(every) * 523_000,
                "note": "every voice not here yet", "dest": "kokoro-voices",
                "parts": [{"repo": KOKORO_REPO, "file": f"voices/{v}.pt", "save_as": f"{v}.pt"} for v in every]})
    for v in every:
        lang = VOICE_LANG.get(v[0], "")
        sex = {"f": "female", "m": "male"}.get(v[1:2], "")
        out.append({"key": f"voice-{v}", "label": v, "group": "Voices", "approx": sizes.get(f"voices/{v}.pt") or 523_000,
                    "note": ", ".join(x for x in (lang, sex) if x), "dest": "kokoro-voices",
                    "parts": [{"repo": KOKORO_REPO, "file": f"voices/{v}.pt", "save_as": f"{v}.pt"}]})
    return out


def sdcpp_choices() -> list[dict]:
    return [{**c, "dest": "models", "subdir": c.get("subdir") or c["parts"][0]["repo"].split("/")[-1]} for c in SDCPP]


def choices(kind: str, live: Optional[list[dict]] = None) -> list[dict]:
    if kind == "whisper":
        return whisper_choices(live)
    if kind == "kokoro":
        return kokoro_choices(live)
    if kind == "sdcpp":
        return sdcpp_choices()
    return []


LIVE_REPO = {"whisper": WHISPER_REPO, "kokoro": KOKORO_REPO}


def folder_for(choice: dict, models_dir: str, kokoro_folder: str = "") -> str:
    """Where a choice's files land."""
    if choice["dest"] == "kokoro-model":
        return os.path.join(kokoro_folder, "api", "src", "models", "v1_0")
    if choice["dest"] == "kokoro-voices":
        return os.path.join(kokoro_folder, "api", "src", "voices", "v1_0")
    return os.path.join(models_dir, choice.get("subdir") or "")


def missing(choice: dict, folder: str) -> list[dict]:
    """The parts not downloaded yet (each lands at its save_as, else its name, under the folder)."""
    return [p for p in choice["parts"] if not os.path.isfile(os.path.join(folder, p.get("save_as") or p["file"]))]


def model_args(choice: dict, folder: str) -> tuple[str, str]:
    """(model id, sd-server arguments) a bundle sets for its main file, or ("", "")."""
    if not choice.get("args"):
        return "", ""
    main = choice.get("main") or choice["parts"][0]["file"]
    stem = re.sub(r"\.(gguf|safetensors|ckpt)$", "", os.path.basename(main), flags=re.IGNORECASE)
    d = folder.replace("\\", "/")
    words = [w.replace("{dir}", d) for w in choice["args"].split()]
    return swap_model_id(stem), " ".join(f'"{w}"' if " " in w else w for w in words)


__all__ = ["LIVE_REPO", "choices", "folder_for", "missing", "model_args"]
