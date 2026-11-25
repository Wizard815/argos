"""Group the model list apps see by family (Qwen, Gemma, GLM...) with cloud providers after them.

OpenAI's /v1/models is a flat list with no place for categories, so three things do the grouping:

- the list is sorted by family, then name, local models first and each cloud provider after them;
- ``owned_by`` holds the family (or the provider), which some apps show;
- optionally (Models -> What apps see -> "family/ in front"), each local name gets its family in front,
  ``qwen/qwen3.8-27b-uncen-mtp-2chat-512k``: Open WebUI, Cline and LiteLLM-style pickers group by the part
  before the ``/``. The name without it keeps working, so an app's saved setting never breaks.
"""
from __future__ import annotations

import os
import re
from typing import Optional

PREFIX_KEY = "_list_family_prefix"

# (pattern on the model's name or file, family): the first that matches wins
FAMILIES = [
    (r"gpt-?oss", "gpt-oss"), (r"qwen|qwq", "qwen"), (r"gemma|medgemma", "gemma"), (r"llama|llava", "llama"),
    (r"mi[sx]tral|ministral|devstral|magistral|codestral|pixtral", "mistral"), (r"glm|chatglm", "glm"),
    (r"deepseek", "deepseek"), (r"\bphi-?\d|phi\d", "phi"), (r"granite", "granite"), (r"olmo", "olmo"),
    (r"hunyuan|\bhy-?\d", "hunyuan"), (r"kimi|moonshot", "kimi"), (r"minimax", "minimax"), (r"nemotron", "nemotron"),
    (r"seed-?oss|seed\b", "seed"), (r"command-?[ra]|c4ai|aya", "cohere"), (r"internlm|intern-?s1", "internlm"),
    (r"smollm", "smollm"), (r"falcon", "falcon"), (r"exaone", "exaone"), (r"ernie", "ernie"), (r"jamba", "jamba"),
    (r"whisper", "whisper"), (r"kokoro", "kokoro"), (r"flux|stable-?diffusion|sdxl|sd3|\bsd-", "image"),
    (r"embed|bge|e5-|nomic|gte-", "embedding"), (r"rerank", "rerank"),
    (r"julia|laya|openjev|\bkev\b|\blev\b|clef", "classifier"),
]
_COMPILED = [(re.compile(p, re.I), f) for p, f in FAMILIES]
KNOWN = {f for _, f in FAMILIES} | {"other"}


def family(*names: str) -> str:
    for n in names:
        if not n:
            continue
        for rx, f in _COMPILED:
            if rx.search(n):
                return f
    return "other"


def _record_family(rec: dict) -> str:
    meta = (rec.get("meta") or {}).get("argos") or {}
    cmd = str(meta.get("cmd") or "")
    m = re.search(r"(?:^|\s)(?:-m|--model)\s+(\S+)", cmd)
    path = os.path.basename(m.group(1)) if m else ""
    return family(str(meta.get("model_id") or ""), rec.get("id") or "", path)


def arrange(data: list[dict], provider_names: set[str], prefix: bool) -> list[dict]:
    """The list sorted by family with ``owned_by`` set, and the family in front of local names when asked."""
    out = []
    for rec in data:
        rec = dict(rec)
        mid = str(rec.get("id") or "")
        cloud = mid.split("/", 1)[0] if "/" in mid and mid.split("/", 1)[0] in provider_names else ""
        fam = cloud or _record_family(rec)
        rec["owned_by"] = fam
        if prefix and not cloud:
            rec["id"] = f"{fam}/{mid}"
            meta = dict(rec.get("meta") or {})
            argos = dict(meta.get("argos") or {})
            argos.setdefault("model_id", mid)
            meta["argos"] = argos
            rec["meta"] = meta
        rec["_sort"] = (1 if cloud else 0, fam, mid.lower())
        out.append(rec)
    out.sort(key=lambda r: r["_sort"])
    for r in out:
        r.pop("_sort", None)
    return out


def strip_prefix(model: str, provider_names: set[str]) -> Optional[str]:
    """``qwen/<name>`` -> ``<name>`` for a family prefix (never a provider's name); None when there is none."""
    head, sep, rest = model.partition("/")
    if sep and rest and head in KNOWN and head not in provider_names:
        return rest
    return None
