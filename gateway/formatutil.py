"""Mise en forme des lignes relayées et troncature UTF-8."""

from __future__ import annotations


def collapse_ws(text: str) -> str:
    return " ".join(text.split())


def prefix_with_name(prefix: str, name: str) -> str:
    if prefix.endswith("]"):
        return prefix[:-1] + "|" + name + "]"
    return prefix + "|" + name


def chat_line(prefix: str, name: str, text: str, include_sender: bool) -> str:
    if include_sender and name:
        return f"{prefix_with_name(prefix, name)} {text}"
    return f"{prefix} {text}"


def coalesced_line(prefix: str, parts: list[tuple[str, str]]) -> str:
    body = " / ".join(f"{name} : {text}" for name, text in parts)
    return f"{prefix} {body}"


def position_line(prefix: str, name: str, lat: float, lon: float) -> str:
    head = prefix_with_name(prefix, name) if name else prefix
    return f"{head} pos {lat:.4f},{lon:.4f}"


def measurement_line(prefix: str, name: str, values: dict[str, float]) -> str:
    head = prefix_with_name(prefix, name) if name else prefix
    order = ("temp", "hum", "press", "bat")
    labels = {"temp": "temp", "hum": "hum", "press": "press", "bat": "bat"}
    bits = [f"{labels[k]} {values[k]:g}" for k in order if k in values]
    return f"{head} " + " ".join(bits)


def truncate_utf8(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return text
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    mark = "…".encode("utf-8")
    budget = max_bytes - len(mark) if max_bytes > len(mark) else max_bytes
    cut = raw[:budget]
    while cut:
        try:
            decoded = cut.decode("utf-8")
            if max_bytes > len(mark) and len(raw) > max_bytes:
                return decoded + "…"
            return decoded
        except UnicodeDecodeError:
            cut = cut[:-1]
    return ""
