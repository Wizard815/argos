"""Settings applied to every command: env vars and flags that every model needs.

Kept apart from the profiles, so changing one of them changes every launch and every
export at once. A profile's own value always wins: an always-set flag or env var is
only added when the command does not set it already.
"""
from __future__ import annotations

import copy

from .command import LaunchCommand, parse_command


def parse_always(always: dict) -> tuple[dict[str, str], list]:
    """(env, flag args) from the Settings group. Bad lines are skipped, never fatal."""
    env: dict[str, str] = {}
    for line in str(always.get("env") or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            if k.strip():
                env[k.strip()] = v.strip().strip('"').strip("'")
    args = []
    flags = str(always.get("flags") or "").strip()
    if flags:
        try:
            args = parse_command("llama-server " + flags).args
        except ValueError:
            args = []
    return env, args


def apply_always(command: str, always: dict) -> tuple[str, list[str]]:
    """The command with the always-set env vars and flags it lacks, and what was added."""
    env, args = parse_always(always or {})
    if not env and not args:
        return command, []
    try:
        cmd: LaunchCommand = parse_command(command)
    except ValueError:
        return command, []
    added: list[str] = []
    for k, v in env.items():
        if k not in cmd.env:
            cmd.env[k] = v
            added.append(f"{k}={v}")
    for a in args:
        if a.name and not cmd.has(a.name):
            cmd.args.append(copy.copy(a))
            added.append(" ".join(a.tokens()))
    return (cmd.to_string() if added else command), added


__all__ = ["apply_always", "parse_always"]
