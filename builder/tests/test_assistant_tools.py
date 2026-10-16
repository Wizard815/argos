from src import assistant_tools as T


class _Store:
    def list_models_seen(self):
        return []


class _Inv:
    store = _Store()

    def __init__(self, rows):
        self.rows = rows

    def models_summary(self, now=None):
        return self.rows


def _m(name, gib, origin="library", kind="chat"):
    return {"model_id": name.lower(), "name": name, "origin": origin, "present": True, "kind": kind, "size_gib": gib,
            "quants": ["Q6_K"], "arch": "", "decode": None, "prefill": None, "vram_gib_avg": None, "loaded_seconds": 0, "sessions": 0}


MODELS = [_m("Qwen3-0.6B-Q8_0", 0.6), _m("Qwythos-9B-Claude-Mythos-5-1M-Q6_K", 7.4), _m("Llama-3.1-8B-Q4_K_M", 4.6),
          _m("Big-70B-Q4", 40.0), _m("mtp-draft-Q4_0", 0.06, kind="mtp"), _m("served-alias", None, origin="engine")]


def test_close_to_9b_lists_the_9b_model_first_and_skips_drafts_and_aliases():
    out = T.models_on_disk({"inventory": _Inv(MODELS)}, "what models are close to 9B?")
    names = [line for line in out if line.startswith("- ")]
    assert names[0].startswith("- Qwythos-9B") and any("Llama-3.1-8B" in n for n in names)
    assert not any("mtp-draft" in n or "served-alias" in n or "Big-70B" in n for n in names)


def test_smallest_sorts_by_file_size():
    out = T.models_on_disk({"inventory": _Inv(MODELS)}, "which is the smallest model i have")
    assert out[1].startswith("- Qwen3-0.6B")


def test_list_all_is_built_in_code_and_the_counts_add_up():
    rows = MODELS + [_m("Old-1B-Q4", 0.7)]
    rows[-1]["present"] = False
    ctx = {"inventory": _Inv(rows)}
    text = T.direct_answer(ctx, "list them all, name")
    assert text and f"**{len(rows)} models**" in text
    assert "Qwythos-9B" in text and "Old-1B-Q4" in text and "mtp-draft" in text and "served-alias" in text
    # a filtered question is for the model and the other tools, not a plain list
    assert T.direct_answer(ctx, "which models are close to 9B?") is None
