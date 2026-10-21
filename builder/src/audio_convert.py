"""Turn uploaded audio into the WAV whisper reads, before the request reaches the engine.

whisper.cpp reads 16 kHz mono 16-bit WAV. Clients send whatever they recorded: m4a from a phone, webm
from a browser, mp3, ogg. With the ffmpeg add-on installed (Servers -> Local), the proxy converts the
file in a transcription upload and passes the rest of the form through unchanged. Without ffmpeg, or
for a file that already is that WAV, the request goes on as it came.
"""
from __future__ import annotations

import os
import struct
import subprocess
import tempfile
import uuid
from typing import Optional

CRLF = b"\r\n"

Form = dict[str, list[str]]
Files = dict[str, list[tuple[str, str, bytes]]]


def is_whisper_wav(data: bytes) -> bool:
    """16 kHz, mono, 16-bit PCM WAV: nothing to convert."""
    if len(data) < 36 or data[:4] != b"RIFF" or data[8:12] != b"WAVE" or data[12:16] != b"fmt ":
        return False
    fmt, channels, rate = struct.unpack("<HHI", data[20:28])
    bits = struct.unpack("<H", data[34:36])[0]
    return fmt == 1 and channels == 1 and rate == 16000 and bits == 16


def to_wav(data: bytes, ffmpeg: str, timeout: float = 120) -> bytes:
    """Any audio ffmpeg reads -> 16 kHz mono WAV. Raises on failure.

    The input goes through a file, not a pipe: m4a/mp4 keep their index at the end, and from a pipe
    ffmpeg wrote a WAV header with no sound in it."""
    fd, src = tempfile.mkstemp(prefix="argos-audio-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        p = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", src, "-ar", "16000", "-ac", "1",
                            "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"], capture_output=True, timeout=timeout)
    finally:
        try:
            os.unlink(src)
        except OSError:
            pass
    if p.returncode != 0 or len(p.stdout) <= 44:
        raise ValueError((p.stderr or b"").decode("utf-8", "replace").strip()[-300:] or "ffmpeg produced no audio")
    return p.stdout


def _multipart(form: Form, files: Files) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    sep = b"--" + boundary.encode()
    out = bytearray()
    for key, values in form.items():
        for v in values:
            out += sep + CRLF + f'Content-Disposition: form-data; name="{key}"'.encode() + CRLF + CRLF + v.encode() + CRLF
    for key, items in files.items():
        for fname, ftype, fdata in items:
            out += sep + CRLF + f'Content-Disposition: form-data; name="{key}"; filename="{fname}"'.encode() + CRLF
            out += f"Content-Type: {ftype or 'application/octet-stream'}".encode() + CRLF + CRLF + fdata + CRLF
    out += sep + b"--" + CRLF
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def rebuild(form: Form, files: Files) -> tuple[bytes, str, str]:
    """The same upload, unchanged, as a new multipart body (the form had to be read to look at it)."""
    body, ctype = _multipart(form, files)
    return body, ctype, "unchanged"


def convert_form(form: Form, files: Files, ffmpeg: str) -> Optional[tuple[bytes, str, str]]:
    """A new multipart body with the "file" part as WAV: (body, content type, what was done), or None to send as is."""
    parts = files.get("file") or []
    if not ffmpeg or not parts:
        return None
    name, ctype, data = parts[0]
    if is_whisper_wav(data):
        return None
    wav = to_wav(data, ffmpeg)
    files = {**files, "file": [(os.path.splitext(name or "audio")[0] + ".wav", "audio/wav", wav), *parts[1:]]}
    body, ctype_out = _multipart(form, files)
    return body, ctype_out, f"{name or 'audio'} ({ctype or '?'}, {len(data)} B) -> WAV {len(wav)} B"
