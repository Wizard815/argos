#!/usr/bin/env python3
"""llama_server.py — tiny passthrough for llama-server used by llama_gpu_launch.sh.

llama-swap's cmdTemplate normally ends in `llama-server <flags...>`. We can't
exec that directly from a shell script that also needs to set HIP_VISIBLE_DEVICES
from pick_gpu.py's output, because the flags include `--model <path>` with the
path quoted. So we parse them here and exec the real llama-server.

We simply strip the --pick-* bookkeeping flags and exec `llama-server` with the
rest, after the wrapper set HIP_VISIBLE_DEVICES. This lets llama-swap keep using
its own cmdTemplate (it just has to end in `python3 llama_server.py -- ...`),
and still let the wrapper do device selection.
"""
import os
import shlex
import sys


def main() -> int:
    # Everything after our own `--` is the llama-server arg list.
    if len(sys.argv) < 2 or sys.argv[1] != "--":
        sys.stderr.write("llama_server.py: expected a single '--' separator\n")
        return 2

    rest = sys.argv[2:]

    # Drop --model-id / --model-name / --parallel bookkeeping flags we inserted.
    out = []
    it = iter(range(len(rest)))
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--pick-model-id" and i + 1 < len(rest):
            i += 2
            continue
        if tok == "--pick-model-name" and i + 1 < len(rest):
            i += 2
            continue
        if tok == "--pick-parallel" and i + 1 < len(rest):
            i += 2
            continue
        out.append(tok)
        i += 1

    # The remaining list should start with `llama-server` (the cmdTemplate
    # prefix). Drop that token so we exec llama-server directly.
    if out and out[0] == "llama-server":
        out.pop(0)

    # IMPORTANT: exec (not subprocess.call) so llama-server *replaces* this
    # process. If llama-swap kills the launcher PID to unload/replace a model,
    # the signal must land on llama-server. A subprocess child would be
    # reparented and could survive the kill, leaking GPU VRAM.
    os.execvp("llama-server", ["llama-server"] + out)

    # Should not be reached, but keep the interpreter alive long enough to
    # surface an exec failure instead of a confusing exit code.
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
