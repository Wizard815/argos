"""Tips for a profile's GPU layout: how it splits over the cards, and how much a prompt slows the chats beside it.

- A model that fits on one card but is spread over two: each token crosses PCIe between them, and the second card
  is taken for nothing. Run it as one copy per card instead (two chats at full speed), or keep it on one card.
- --split-mode row or tensor: both cards work on every token, swapping data over PCIe each time. Without a fast link
  between the cards (MI50s talk over PCIe) layer split is usually faster: Measure both.
- --parallel 2 or more with a big --ubatch-size: llama-server reads a prompt in chunks of that many tokens, and
  every chat's next token waits for the chunk. At the model's measured reading speed that is how long each answer
  stalls per step while another chat's prompt is read. A smaller -ub keeps the answers moving (the prompt itself
  reads a little slower).
"""
from __future__ import annotations

import os
import re
from typing import Optional

from .command import parse_command

GIB = 2 ** 30
DRM = os.environ.get("ARGOS_DRM_ROOT") or "/sys/class/drm"
DEFAULT_UBATCH = 512
STALL_TIP_S = 0.25  # an answer stalled this long per step is a visible stutter


def card_totals(root: str = DRM) -> list[float]:
    """Each GPU's VRAM in GiB, from the kernel (cards with VRAM, in order)."""
    out = []
    try:
        names = sorted(os.listdir(root), key=lambda n: int(n[4:]) if n[4:].isdigit() else 1 << 30)
    except OSError:
        return out
    for n in names:
        if not re.fullmatch(r"card\d+", n):
            continue
        try:
            with open(os.path.join(root, n, "device", "mem_info_vram_total"), encoding="utf-8") as f:
                out.append(int(f.read().strip()) / GIB)
        except (OSError, ValueError):
            continue
    return out


def _int(v) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def advise(command: str, gpu_gib: Optional[float], cards: list[float], prefill_tps: Optional[float] = None) -> list[dict]:
    """[{"kind", "title", "why"}] for one profile. ``gpu_gib``: its VRAM estimate (GPU share); ``cards``: each
    card's VRAM in GiB; ``prefill_tps``: how fast it reads prompts, when measured."""
    try:
        cmd = parse_command(command)
    except ValueError:
        return []
    tips: list[dict] = []
    s = cmd.summary()
    split = (s.get("split_mode") or "").lower()
    env = " ".join(f"{k}={v}" for k, v in cmd.env.items())
    pinned = bool(re.search(r"(HIP|ROCR|CUDA)_VISIBLE_DEVICES=\d+(\s|$)", env)) or split == "none" or cmd.has("main-gpu") \
        or (cmd.get("tensor-split") and len([x for x in re.split(r"[,/]", cmd.get("tensor-split") or "") if x.strip() and float(x) > 0]) == 1)
    ngl = cmd.get("n-gpu-layers")
    on_gpu = ngl is None or ngl in ("", "all", "auto") or (_int(ngl) or 0) > 0
    parallel = _int(s.get("parallel")) or 1

    if len(cards) >= 2 and on_gpu and gpu_gib and not pinned:
        smallest = min(cards)
        if gpu_gib <= smallest * 0.9:
            tips.append({"kind": "copies", "title": "Fits on one card: run one copy per card",
                         "why": f"It needs about {gpu_gib:.1f} GiB and each card has {smallest:.0f} GiB, but it is spread over "
                                f"{len(cards)} cards, so each token crosses PCIe between them. As one copy per card "
                                "(Models → ⋯ → GPU mode: one per GPU) two chats run at full speed side by side; or add "
                                "-sm none to keep it on one card and the other free."})
    if len(cards) >= 2 and split in ("row", "tensor"):
        tips.append({"kind": "split", "title": f"--split-mode {split}: check it against layer",
                     "why": f"With {split} split both cards work on every token and swap data over PCIe each time. Without "
                            "a fast link between the cards (MI50s talk over PCIe) --split-mode layer is usually faster "
                            "for one chat and for several. Measure both on Benchmarks."})
    if parallel >= 2:
        ub = _int(s.get("ubatch_size")) or DEFAULT_UBATCH
        if prefill_tps and prefill_tps > 0:
            stall = ub / prefill_tps
            if stall > STALL_TIP_S:
                better = max(128, int(prefill_tps * STALL_TIP_S) // 64 * 64)
                tips.append({"kind": "ubatch", "title": f"Answers stutter while a prompt is read: try -ub {better}",
                             "why": f"It reads prompts at about {prefill_tps:.0f} tokens a second in chunks of {ub} "
                                    f"(-ub), so while another chat's prompt is read each answer waits ~{stall:.2f} s per "
                                    f"token. -ub {better} keeps that under {STALL_TIP_S} s; the prompt itself reads a "
                                    "little slower."})
        elif ub >= 1024:
            tips.append({"kind": "ubatch", "title": f"-ub {ub} with {parallel} chats at once",
                         "why": f"While a prompt is read, every other chat's next token waits for a chunk of {ub} prompt "
                                "tokens. Once this profile has read a few prompts Argos knows its speed and says what "
                                "-ub keeps the answers smooth; 512 is a safe start."})
    return tips
