"""Temperature: how a model answers, set per model name, and measured.

**Sampling per model** (Profiles -> Temperature): a temperature (and top-p, top-k, min-p) for the name apps ask for
(a profile's label, or its full id). "default" fills it in when the request has none; "force" replaces what the app
sent, since many apps send 1.0 whatever the model. It is applied to each request on its way through the builder, so
it changes nothing in the profile's command and needs no reload.

**A temperature sweep** asks a running model the same things at several temperatures:

- *smart*: questions with one right answer (arithmetic, what a few lines of Python print, dates, sequences), each
  checked against its answer: the share answered right;
- *creative*: one open prompt asked several times: how different the answers are from each other (1 - the overlap of
  their word pairs), 0 when every answer is the same.

Low temperatures usually keep the first high and the second low; somewhere the first starts to fall. The sweep's
"best" is the hottest temperature still near the most right answers with its next hotter one near them too (room
before the cliff), the most varied when that is measured. Results
measured elsewhere (a coding test with hidden checks, say) can be added by hand and are drawn with them.
"""
from __future__ import annotations

import http.client
import json
import re
import threading
import time
import urllib.parse
import uuid
from typing import Any, Optional

KEY = "_sampling"
RESULTS_KEY = "_temp_sweeps"
KEEP_RESULTS = 40
FIELDS = {"temperature": (0.0, 2.0), "top_p": (0.0, 1.0), "top_k": (0, 1000), "min_p": (0.0, 1.0)}
PATHS = ("/v1/chat/completions", "/chat/completions", "/v1/completions", "/completions", "/completion",
         "/v1/responses", "/responses", "/v1/messages")


# ----- sampling per model ----------------------------------------------------------------------------------------
def clean(d: dict) -> dict:
    """One model's sampling: {"mode": "default"|"force", "temperature"?, "top_p"?, "top_k"?, "min_p"?}."""
    mode = str(d.get("mode") or "default")
    if mode not in ("default", "force"):
        raise ValueError("mode is default (when the app sends none) or force (whatever the app sends)")
    out: dict[str, Any] = {"mode": mode}
    for k, (lo, hi) in FIELDS.items():
        v = d.get(k)
        if v in (None, ""):
            continue
        try:
            v = int(v) if k == "top_k" else round(float(v), 3)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{k} is a number from {lo} to {hi}") from None
        if not (lo <= v <= hi):  # also NaN
            raise ValueError(f"{k} is from {lo} to {hi}")
        out[k] = v
    return out


def table(store) -> dict:
    return {k: v for k, v in (store.get_settings().get(KEY) or {}).items() if isinstance(v, dict)}


def rule_for(model: str, rules: dict) -> Optional[dict]:
    """A model's sampling, by the name asked for or the profile label in a full id (<model>--<label>)."""
    if not model or not rules:
        return None
    if model in rules:
        return rules[model]
    if "--" in model and model.split("--", 1)[1] in rules:
        return rules[model.split("--", 1)[1]]
    return None


def apply(path: str, body: dict, rule: Optional[dict]) -> list[str]:
    """Set a request's sampling from a rule; the fields it changed. Anthropic's API has no min_p; its top_k is the
    same. A request that sends a field keeps it under "default"."""
    if not rule or not isinstance(body, dict) or path.rstrip("/") not in PATHS:
        return []
    changed = []
    for k in FIELDS:
        if k not in rule or (path.endswith("/messages") and k == "min_p"):
            continue
        if rule.get("mode") == "force" or body.get(k) is None:
            if body.get(k) != rule[k]:
                body[k] = rule[k]
                changed.append(k)
    return changed


# ----- the sweep's questions -------------------------------------------------------------------------------------
# One right answer each, short to check. Kept to things a 4B model can do at temperature 0, so the sweep shows what
# the temperature does rather than what the model cannot.
QUESTIONS: list[tuple[str, str]] = [
    ("A shop sells pens at 3 for $2. How many dollars do 18 pens cost?", "12"),
    ("What is 17 * 23?", "391"),
    ("What does this Python print?\nprint(sum(i*i for i in range(5)))", "30"),
    ("What does this Python print?\ns = 'banana'\nprint(s.count('an'))", "2"),
    ("What does this Python print?\nprint(len(set('mississippi')))", "4"),
    ("What does this Python print?\na = [3, 1, 2]\na.sort(reverse=True)\nprint(a[1])", "2"),
    ("If today is Wednesday, what day of the week is it 10 days from now?", "saturday"),
    ("A train leaves at 9:40 and the trip takes 2 hours 35 minutes. When does it arrive? Answer as HH:MM.", "12:15"),
    ("How many prime numbers are there between 10 and 30?", "6"),
    ("What does this Python print?\nprint('-'.join(str(x) for x in range(1, 8, 3)))", "1-4-7"),
    ("What is the next number: 2, 6, 12, 20, 30, ?", "42"),
    ("What does this Python print?\nd = {'a': 1, 'b': 2}\nd['a'] += d.get('c', 5)\nprint(d['a'])", "6"),
    ("Write the word 'stressed' backwards.", "desserts"),
    ("What is the sum of the digits of 2 to the power of 10?", "7"),
    ("What does this Python print?\nprint(10 // 3 + 10 % 3)", "4"),
    ("A rectangle is 7 cm by 12 cm. What is its perimeter in cm?", "38"),
]
ASK = ("Answer the question. Work it out briefly if you need to, then end with one last line: ANSWER: <the answer "
       "alone>\n\n")
OPEN_PROMPT = "Write a two-sentence story about a lighthouse keeper who finds something unexpected."
TEMPS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2]


def _norm(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[*`\"'$]", "", s)
    s = re.sub(r"\s*(cm|dollars?|usd)\.?$", "", s)
    return s.strip(" .")


def graded(answer: str, right: str) -> bool:
    """Its last ANSWER: line (else its last line) against the right answer."""
    text = re.sub(r"<think>.*?</think>", "", answer or "", flags=re.S)
    lines = [l for l in text.strip().splitlines() if l.strip()]
    if not lines:
        return False
    tagged = [l for l in lines if re.search(r"answer\s*[:：]", l, re.I)]
    last = re.split(r"answer\s*[:：]", tagged[-1], flags=re.I)[-1] if tagged else lines[-1]
    got = _norm(last)
    got = re.sub(r"(?<=\d)\.0+$", "", got)  # 12.0 is 12
    return got == right or re.fullmatch(rf"(.*\W)?{re.escape(right)}", got) is not None


def variety(texts: list[str]) -> float:
    """How different answers are from each other, 0-1: the mean of 1 - Jaccard overlap of their word pairs."""
    sets = []
    for t in texts:
        w = re.findall(r"\w+", (t or "").lower())
        sets.append({(a, b) for a, b in zip(w, w[1:])} or {tuple(w)})
    pairs = [(a, b) for i, a in enumerate(sets) for b in sets[i + 1:]]
    if not pairs:
        return 0.0
    return round(sum(1 - (len(a & b) / len(a | b) if a | b else 1) for a, b in pairs) / len(pairs), 3)


def best(points: list[dict]) -> Optional[float]:
    """The temperature to use: among those near the most right answers (within one answer, or 3%), the ones whose
    next hotter temperature is near it too, so there is room before the answers start to fall; of those the most
    varied, else the hottest. With no such margin anywhere, the coolest near the top."""
    rows = sorted((p for p in points if p.get("total")), key=lambda p: p["temp"])
    if not rows:
        return None
    share = lambda p: p["correct"] / p["total"]  # noqa: E731
    top = max(share(p) for p in rows)
    near = [p for p in rows if share(p) >= top - max(1 / p["total"], 0.03) - 1e-9]
    safe = [p for i, p in enumerate(rows) if p in near and i + 1 < len(rows) and rows[i + 1] in near]
    if not safe:
        return near[0]["temp"]
    return max(safe, key=lambda p: (p.get("variety") if p.get("variety") is not None else -1, p["temp"]))["temp"]


__all__ = ["KEY", "RESULTS_KEY", "Sweep", "TokensRanOut", "apply", "best", "clean", "clean_manual", "graded",
           "results", "rule_for", "save_result", "table", "variety"]


def _ask(engine_url: str, model: str, prompt: str, temp: float, max_tokens: int, thinking: bool) -> tuple[str, int]:
    parts = urllib.parse.urlsplit(engine_url)
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": temp,
            "max_tokens": max_tokens, "stream": False}
    if not thinking:  # Qwen, Gemma and other templates that think by default answer straight away
        body["chat_template_kwargs"] = {"enable_thinking": False}
    data = json.dumps(body).encode()
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=600)
    try:
        conn.request("POST", "/v1/chat/completions", data, {"Content-Type": "application/json",
                                                             "X-Argos-App": "temperature sweep"})
        r = conn.getresponse()
        raw = r.read()
    finally:
        conn.close()
    if r.status >= 400:
        raise RuntimeError(f"{r.status}: {raw[:200].decode('utf-8', 'replace')}")
    j = json.loads(raw)
    ch = (j.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    text = msg.get("content") or ""
    if not text.strip() and ch.get("finish_reason") == "length":
        raise TokensRanOut("it used all its tokens before answering (thinking, most likely)")
    return text, int((j.get("usage") or {}).get("completion_tokens") or 0)


class TokensRanOut(Exception):
    """An answer cut off before it said anything: counted apart from wrong answers."""


class Sweep:
    """One sweep, run in a thread: its progress is read while it runs."""

    def __init__(self, store, engine_url: str, model: str, temps: list[float], samples: int = 1, stories: int = 4,
                 max_tokens: int = 512, thinking: bool = False, ask=_ask):
        self.id = uuid.uuid4().hex[:12]
        self.store, self.engine_url, self.model = store, engine_url, model
        self.temps, self.samples, self.stories = temps, samples, stories
        self.max_tokens, self.thinking, self.ask = max_tokens, thinking, ask
        self.points: list[dict] = []
        self.state, self.error, self.done_n = "running", "", 0
        self.total_n = len(temps) * (len(QUESTIONS) * samples + stories)
        self.started, self.stop = time.time(), threading.Event()

    def view(self) -> dict:
        return {"id": self.id, "model": self.model, "state": self.state, "error": self.error, "done": self.done_n,
                "total": self.total_n, "points": self.points, "best": best(self.points), "started": self.started,
                "thinking": self.thinking}

    def run(self) -> None:
        try:
            for t in self.temps:
                right, n, toks, errors, cut = 0, 0, 0, 0, 0
                for _ in range(self.samples):
                    for q, a in QUESTIONS:
                        if self.stop.is_set():
                            raise InterruptedError
                        try:
                            text, k = self.ask(self.engine_url, self.model, ASK + q, t, self.max_tokens, self.thinking)
                            right += graded(text, a)
                            toks += k
                        except InterruptedError:
                            raise
                        except TokensRanOut:
                            cut += 1
                            toks += self.max_tokens
                        except Exception as exc:  # noqa: BLE001 - counted wrong, and said
                            errors += 1
                            self.error = str(exc)[:300]
                        n += 1
                        self.done_n += 1
                stories = []
                for _ in range(self.stories):
                    if self.stop.is_set():
                        raise InterruptedError
                    try:
                        # more room than a question: a thinking model thinks longest about a story
                        stories.append(self.ask(self.engine_url, self.model, OPEN_PROMPT, t, self.max_tokens * 2, self.thinking)[0])
                    except InterruptedError:
                        raise
                    except TokensRanOut:
                        cut += 1
                    except Exception as exc:  # noqa: BLE001
                        errors += 1
                        self.error = str(exc)[:300]
                    self.done_n += 1
                told = [x for x in stories if x.strip()]
                self.points.append({"temp": t, "correct": right, "total": n, "accuracy": round(right / n, 3) if n else 0,
                                    # fewer than two stories to compare: not measured, rather than "all the same"
                                    "variety": variety(told) if len(told) >= 2 else None,
                                    "avg_tokens": round(toks / max(n, 1)), "errors": errors, "cut": cut,
                                    "sample": (told[0] if told else "")[:400]})
                if errors == n + self.stories:  # nothing answered: the model is not there
                    raise RuntimeError(f"no answers from {self.model}: {self.error}")
            self.state = "done"
        except InterruptedError:
            self.state = "stopped"
        except Exception as exc:  # noqa: BLE001
            self.state, self.error = "failed", str(exc)[:300]
        finally:
            if self.points:
                save_result(self.store, {"model": self.model, "kind": "sweep", "at": time.time(), "points": self.points,
                                         "best": best(self.points), "thinking": self.thinking, "state": self.state})


# ----- results ---------------------------------------------------------------------------------------------------
def results(store, model: Optional[str] = None) -> list[dict]:
    rows = [r for r in store.get_settings().get(RESULTS_KEY) or [] if isinstance(r, dict)]
    return [r for r in rows if model is None or r.get("model") == model]


def save_result(store, r: dict) -> dict:
    r = {**r, "id": r.get("id") or uuid.uuid4().hex[:12]}
    rows = [x for x in results(store) if x.get("id") != r["id"]] + [r]
    store.set_setting(RESULTS_KEY, rows[-KEEP_RESULTS:])
    return r


def clean_manual(d: dict) -> dict:
    """Results measured elsewhere: {model, label, points: [{temp, passed, total, broken?}]}: drawn with the sweeps."""
    model = str(d.get("model") or "").strip()[:200]
    if not model:
        raise ValueError("say which model")
    pts = []
    for p in d.get("points") or []:
        try:
            t, ok, n = float(p["temp"]), int(p["passed"]), int(p["total"])
            broken = int(p.get("broken") or 0)
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("each point is {temp, passed, total}") from None
        if not (0 <= t <= 2) or n <= 0 or not (0 <= ok <= n) or broken < 0:
            raise ValueError("temp is 0-2, passed is 0 to total, total is above 0")
        pts.append({"temp": round(t, 3), "correct": ok, "total": n, "accuracy": round(ok / n, 3), "broken": broken})
    if not pts:
        raise ValueError("add at least one point")
    pts.sort(key=lambda p: p["temp"])
    return {"model": model, "kind": "manual", "label": str(d.get("label") or "my test")[:80], "at": time.time(),
            "points": pts, "best": best(pts)}
