"""Each slot (ID) of a llama-server on its own, from its log."""
from src.slot_stats import parse

LOG = """00.01.000.000 I slot load_model: id  0 | task -1 | new slot, n_ctx = 65536
00.01.000.001 I slot load_model: id  1 | task -1 | new slot, n_ctx = 65536
10.35.435.513 I slot launch_slot_: id  1 | task 4719 | processing task, is_child = 0
10.37.538.452 I slot print_timing: id  0 | task 4451 | prompt eval time =     954.33 ms /   192 tokens (    4.97 ms per token,   201.19 tokens per second)
10.37.538.460 I slot print_timing: id  0 | task 4451 |        eval time =   20923.41 ms /   266 tokens (   78.96 ms per token,    12.67 tokens per second)
10.37.541.632 I slot      release: id  0 | task 4451 | stop processing: n_tokens = 45004, truncated = 0
10.42.828.168 I slot print_timing: id  1 | task 4719 | prompt eval time =    2107.08 ms /  1178 tokens (    1.79 ms per token,   559.07 tokens per second)
10.42.828.173 I slot print_timing: id  1 | task 4719 |        eval time =    5285.54 ms /   205 tokens (   25.91 ms per token,    38.60 tokens per second)
10.42.831.092 I slot      release: id  1 | task 4719 | stop processing: n_tokens = 64676, truncated = 0
10.50.154.029 I slot launch_slot_: id  0 | task 4926 | processing task, is_child = 0
10.58.348.728 I slot print_timing: id  0 | task 4926 | prompt processing, n_tokens = 6128, progress = 0.40, t = 5.33 s / 1150.35 tokens per second
"""


def test_SlotStats_each_id_has_its_own_numbers():
    s0, s1 = parse(LOG)
    assert (s0["id"], s1["id"]) == (0, 1)
    assert s0["decode"]["last"] == 12.67 and s1["decode"]["last"] == 38.6, "ID 0 is slow and ID 1 is not: not averaged together"
    assert s1["prefill"]["last"] == 559.07 and s1["ctx_used"] == 64676 and s1["n_ctx"] == 65536
    assert s0["state"] == "prefill" and s0["progress"] == 0.4 and s1["state"] == "idle"
    assert s0["requests"] == 1 and s1["requests"] == 1
    assert round(s0["share"] + s1["share"], 3) == 1.0 and s0["share"] > s1["share"]


def test_SlotStats_a_build_without_timing_blocks_uses_its_last_n_gen():
    log = """1.00.000.000 I slot launch_slot_: id  2 | task 7 | processing task, is_child = 0
1.03.000.000 I slot print_timing: id  2 | task 7 | n_gen =    116, tg =  38.27 t/s, tg_3s =  38.61 t/s
1.06.000.000 I slot print_timing: id  2 | task 7 | n_gen =    234, tg =  38.31 t/s, tg_3s =  38.35 t/s
1.07.000.000 I slot      release: id  2 | task 7 | stop processing: n_tokens = 900, truncated = 0
"""
    (s,) = parse(log)
    assert s["id"] == 2 and s["decode"]["last"] == 38.31 and s["requests"] == 1


def test_SlotStats_minutes_counts_back_from_the_last_line():
    s0, s1 = parse(LOG, minutes=10)  # from 00.58: ID 1's request at 10.42 is in, the load lines are not
    assert s1["requests"] == 1 and s0["requests"] == 1
    s0, s1 = parse(LOG, minutes=0.1)
    assert s0["requests"] == 0 and s0["decode"]["last"] is None, "outside the window"
    assert s0["state"] == "prefill", "what it does now is always known"


def test_SlotStats_route_reads_the_launchs_log(client, app, monkeypatch):
    monkeypatch.setattr(app.extensions["pb"].runner, "read_log", lambda launch_id, tail_bytes=0: LOG)
    r = client.get("/api/launches/abc123/slots").get_json()
    assert [x["id"] for x in r["slots"]] == [0, 1]
