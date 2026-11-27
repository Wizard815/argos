"""The system monitor pop-out: raw rocm-smi, nvidia-smi and a top-style view, read only when asked."""
from __future__ import annotations

from flask import Blueprint, request

from .. import sysmon
from ..web import err, ok

bp = Blueprint("sysmon", __name__)


@bp.get("/api/sysmon")
def read():
    tab = request.args.get("tab", "top")
    if tab == "top":
        return ok(tab=tab, **sysmon.top())
    if tab in ("rocm-smi", "nvidia-smi"):
        return ok(tab=tab, **sysmon.smi(tab))
    return err("tab must be top, rocm-smi or nvidia-smi")
