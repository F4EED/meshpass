"""Passage unique de filtrage, dans l'ordre du cahier des charges (EF-23)."""

from __future__ import annotations

import math
from datetime import datetime

from gateway.formatutil import (
    chat_line,
    coalesced_line,
    collapse_ws,
    measurement_line,
    position_line,
    truncate_utf8,
)
from gateway.models import Emission, Incoming, Outcome, Rule, Settings

POSITION_MIN_S = 300.0
POSITION_REFRESH_S = 3600.0
DETECTION_MIN_S = 60.0
SPEED_MAX_KMH = 200.0
SENSOR_STEPS = {"temp": 0.5, "hum": 2.0, "press": 1.0, "bat": 5.0}


def blocks_prefix(text: str, settings: Settings) -> bool:
    stripped = text.lstrip()
    if stripped.startswith("[MT") or stripped.startswith("[MC"):
        return True
    for prefix in (settings.prefix_mt, settings.prefix_mc):
        if prefix and stripped.startswith(prefix):
            return True
    return False


def _hm(wall: float) -> tuple[int, int]:
    dt = datetime.fromtimestamp(wall)
    return dt.hour, dt.minute


def in_quiet(wall: float, start: str, end: str) -> bool:
    if not start or not end:
        return False
    sh, sm = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    hour, minute = _hm(wall)
    now = hour * 60 + minute
    a = sh * 60 + sm
    b = eh * 60 + em
    if a == b:
        return False
    if a < b:
        return a <= now < b
    return now >= a or now < b


def _round4(value: float) -> float:
    return round(value, 4)


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _prefix_for(network: str, settings: Settings) -> str:
    return settings.prefix_mt if network == "mt" else settings.prefix_mc


def _dest(rule: Rule, network: str) -> tuple[str, str, str]:
    if network == "mt":
        return "mc", rule.mc_channel_id, rule.mc_name
    return "mt", rule.mt_channel_id, rule.mt_name


def _own_sender(inc: Incoming, settings: Settings) -> bool:
    if inc.from_our_module:
        return True
    own = settings.own_mt_id if inc.network == "mt" else settings.own_mc_id
    return bool(own) and inc.sender_id == own


def _quota_kind(inc: Incoming) -> str:
    return "chat" if inc.kind == "chat" else "telemetry"


class Runtime:
    def __init__(self) -> None:
        self.dedup: dict[tuple, float] = {}
        self.last_sent_pos: dict[tuple, tuple[float, float, float]] = {}
        self.last_sample_pos: dict[tuple, tuple[float, float, float]] = {}
        self.metrics: dict[tuple, float] = {}
        self.detection_sent: dict[tuple, float] = {}
        self.quota: dict[str, list[float]] = {"chat": [], "telemetry": [], "global": []}

    def prune_quota(self, now: float) -> None:
        for key, stamps in self.quota.items():
            self.quota[key] = [t for t in stamps if now - t < 60.0]

    def quota_full(self, kind: str, settings: Settings, now: float) -> bool:
        self.prune_quota(now)
        limits = {
            "chat": settings.quota_chat_per_min,
            "telemetry": settings.quota_telemetry_per_min,
            "global": settings.quota_global_per_min,
        }
        if len(self.quota[kind]) >= limits[kind]:
            return True
        return len(self.quota["global"]) >= limits["global"]

    def note_quota(self, kind: str, now: float) -> None:
        self.quota[kind].append(now)
        self.quota["global"].append(now)


def _global_motif(inc: Incoming, settings: Settings) -> str | None:
    if inc.kind == "direct":
        return "message direct"
    if _own_sender(inc, settings):
        return "écho"
    if inc.kind == "chat" and blocks_prefix(inc.text, settings):
        return "préfixe"
    return None


def _blacklisted(inc: Incoming, blacklist: list[dict]) -> bool:
    for entry in blacklist:
        if entry["network"] == inc.network and entry["sender_id"] == inc.sender_id:
            return True
    return False


def _sender_motif(inc: Incoming, rule: Rule) -> str | None:
    if rule.allow_ids and inc.sender_id not in rule.allow_ids:
        return "filtre"
    if inc.sender_id in rule.block_ids:
        return "filtre"
    return None


def _text_motif(inc: Incoming, rule: Rule) -> str | None:
    if inc.kind != "chat":
        return None
    text = inc.text
    required = rule.keyword_required.strip()
    excluded = rule.keyword_excluded.strip()
    folded = text.casefold()
    if required and required.casefold() not in folded:
        return "filtre"
    if excluded and excluded.casefold() in folded:
        return "filtre"
    if rule.min_len and len(text) < rule.min_len:
        return "filtre"
    if rule.max_len and len(text) > rule.max_len:
        return "filtre"
    return None


def _position_motif(inc: Incoming, runtime: Runtime, now: float) -> str | None:
    if inc.lat is None or inc.lon is None:
        return "position aberrante"
    lat, lon = _round4(inc.lat), _round4(inc.lon)
    if lat == 0.0 and lon == 0.0:
        return "position aberrante"
    key = (inc.network, inc.sender_id)
    sample = runtime.last_sample_pos.get(key)
    if sample is not None:
        plat, plon, pwhen = sample
        elapsed = max(now - pwhen, 0.001)
        speed = _distance_m(plat, plon, lat, lon) / elapsed * 3.6
        if speed > SPEED_MAX_KMH:
            return "position aberrante"
    sent = runtime.last_sent_pos.get(key)
    if sent is None:
        return None
    slat, slon, swhen = sent
    if (lat, lon) == (slat, slon):
        if now - swhen >= POSITION_REFRESH_S:
            return None
        return "position inchangée"
    if now - swhen < POSITION_MIN_S:
        return "position trop rapprochée"
    return None


def _changed_metrics(inc: Incoming, runtime: Runtime) -> dict[str, float] | str:
    changed: dict[str, float] = {}
    for name, step in SENSOR_STEPS.items():
        if name not in inc.metrics:
            continue
        value = float(inc.metrics[name])
        key = (inc.network, inc.sender_id, name)
        previous = runtime.metrics.get(key)
        if previous is None or abs(value - previous) >= step:
            changed[name] = value
    if not changed:
        return "mesure inchangée"
    return changed


def _detection_motif(inc: Incoming, runtime: Runtime, now: float) -> str | None:
    key = (inc.network, inc.sender_id)
    if inc.detection_repeat:
        return "mesure inchangée"
    last = runtime.detection_sent.get(key)
    if last is not None and now - last < DETECTION_MIN_S:
        return "détection limitée"
    return None


def _dedup_key(inc: Incoming) -> tuple:
    if inc.kind == "chat":
        return ("chat", inc.network, inc.sender_id, inc.channel_id, collapse_ws(inc.text))
    if inc.kind == "position" and inc.lat is not None and inc.lon is not None:
        return ("pos", inc.network, inc.sender_id, _round4(inc.lat), _round4(inc.lon))
    if inc.kind == "measurement":
        items = tuple(sorted((k, float(v)) for k, v in inc.metrics.items()))
        return ("mes", inc.network, inc.sender_id, items)
    return ("det", inc.network, inc.sender_id, inc.channel_id, inc.detection_repeat)


def _remember_dedup(inc: Incoming, runtime: Runtime, now: float, ttl: float) -> None:
    if inc.kind == "detection":
        runtime.dedup = {k: t for k, t in runtime.dedup.items() if now - t < ttl}
        return
    runtime.dedup[_dedup_key(inc)] = now
    runtime.dedup = {k: t for k, t in runtime.dedup.items() if now - t < ttl}


def _is_dup(inc: Incoming, runtime: Runtime, now: float, ttl: float) -> bool:
    if inc.kind == "detection":
        return False
    seen = runtime.dedup.get(_dedup_key(inc))
    return seen is not None and now - seen < ttl


def _mark_sent(inc: Incoming, runtime: Runtime, now: float, changed: dict[str, float] | None) -> None:
    if inc.kind == "position" and inc.lat is not None and inc.lon is not None:
        runtime.last_sent_pos[(inc.network, inc.sender_id)] = (
            _round4(inc.lat),
            _round4(inc.lon),
            now,
        )
    if changed:
        for name, value in changed.items():
            runtime.metrics[(inc.network, inc.sender_id, name)] = value
    if inc.kind == "detection":
        runtime.detection_sent[(inc.network, inc.sender_id)] = now


def _emission_for(
    inc: Incoming,
    rule: Rule,
    settings: Settings,
    changed: dict[str, float] | None,
) -> tuple[Emission, bool]:
    dest_net, dest_id, dest_name = _dest(rule, inc.network)
    prefix = _prefix_for(inc.network, settings)
    alone = False
    if inc.kind == "chat":
        line = chat_line(prefix, inc.sender_name, inc.text, settings.include_sender)
        raw_len = len(line.encode("utf-8"))
        if raw_len > rule.max_bytes:
            alone = True
            line = truncate_utf8(line, rule.max_bytes)
        emission = Emission("chat", dest_net, dest_id, dest_name, line, inc.sender_name)
        return emission, alone
    if inc.kind == "position":
        assert inc.lat is not None and inc.lon is not None
        lat, lon = _round4(inc.lat), _round4(inc.lon)
        line = position_line(prefix, inc.sender_name, lat, lon)
        if dest_net == "mt":
            emission = Emission("waypoint", dest_net, dest_id, dest_name, line, inc.sender_name, lat, lon)
        else:
            emission = Emission("chat", dest_net, dest_id, dest_name, truncate_utf8(line, rule.max_bytes), inc.sender_name)
        return emission, True
    if inc.kind == "measurement":
        line = measurement_line(prefix, inc.sender_name, changed or {})
        emission = Emission("chat", dest_net, dest_id, dest_name, truncate_utf8(line, rule.max_bytes), inc.sender_name)
        return emission, True
    line = chat_line(prefix, inc.sender_name, "alerte détection", True)
    emission = Emission("chat", dest_net, dest_id, dest_name, truncate_utf8(line, rule.max_bytes), inc.sender_name)
    return emission, True


def matching_rules(inc: Incoming, rules: list[Rule], channels: dict[str, list[dict]]) -> list[Rule]:
    found: list[Rule] = []
    for rule in rules:
        if not rule.active or rule.orphan(channels):
            continue
        if inc.network == "mt" and rule.direction in ("bi", "mt2mc") and inc.channel_id == rule.mt_channel_id:
            found.append(rule)
        elif inc.network == "mc" and rule.direction in ("bi", "mc2mt") and inc.channel_id == rule.mc_channel_id:
            found.append(rule)
    return found


def process(
    inc: Incoming,
    rules: list[Rule],
    settings: Settings,
    blacklist: list[dict],
    channels: dict[str, list[dict]],
    runtime: Runtime,
    now: float,
    wall: float,
) -> list[Outcome]:
    motif = _global_motif(inc, settings)
    if motif:
        return [Outcome("drop", motif)]

    selected = matching_rules(inc, rules, channels)
    if not selected:
        return [Outcome("drop", "aucune règle")]

    outcomes: list[Outcome] = []
    served: set[str] = set()
    accepted = False
    missing = object()
    pos_motif: str | None | object = missing
    metric_result: dict[str, float] | str | object = missing
    det_motif: str | None | object = missing
    for rule in selected:
        dest_net, dest_id, _dest_name = _dest(rule, inc.network)
        dest_key = f"{dest_net}:{dest_id}"
        if dest_key in served:
            outcomes.append(Outcome("drop", "destination déjà servie", rule.id, dest_key=dest_key))
            continue
        if _blacklisted(inc, blacklist):
            outcomes.append(Outcome("drop", "liste noire", rule.id))
            continue
        sender_motif = _sender_motif(inc, rule)
        if sender_motif:
            outcomes.append(Outcome("drop", sender_motif, rule.id))
            continue
        text_motif = _text_motif(inc, rule)
        if text_motif:
            outcomes.append(Outcome("drop", text_motif, rule.id))
            continue
        if inc.kind != "detection" and in_quiet(wall, rule.quiet_start, rule.quiet_end):
            outcomes.append(Outcome("drop", "heure silencieuse", rule.id))
            continue
        changed: dict[str, float] | None = None
        if inc.kind == "position":
            if not rule.positions:
                outcomes.append(Outcome("drop", "option arrêtée", rule.id))
                continue
            if pos_motif is missing:
                pos_motif = _position_motif(inc, runtime, now)
            if pos_motif:
                outcomes.append(Outcome("drop", pos_motif, rule.id))
                continue
        elif inc.kind == "measurement":
            if not rule.measurements:
                outcomes.append(Outcome("drop", "option arrêtée", rule.id))
                continue
            if metric_result is missing:
                metric_result = _changed_metrics(inc, runtime)
            if isinstance(metric_result, str):
                outcomes.append(Outcome("drop", metric_result, rule.id))
                continue
            changed = metric_result
        elif inc.kind == "detection":
            if not rule.measurements:
                outcomes.append(Outcome("drop", "option arrêtée", rule.id))
                continue
            if det_motif is missing:
                det_motif = _detection_motif(inc, runtime, now)
            if det_motif:
                outcomes.append(Outcome("drop", det_motif, rule.id))
                continue
        if _is_dup(inc, runtime, now, settings.dedup_ttl_s):
            outcomes.append(Outcome("drop", "doublon", rule.id))
            continue
        quota_kind = _quota_kind(inc)
        if runtime.quota_full(quota_kind, settings, now):
            outcomes.append(Outcome("drop", "quota", rule.id))
            continue

        emission, alone = _emission_for(inc, rule, settings, changed)
        if inc.kind == "chat" and rule.coalesce and not alone:
            outcomes.append(
                Outcome(
                    "hold",
                    "coalescence",
                    rule.id,
                    hold_part=(inc.sender_name or inc.sender_id, inc.text),
                    hold_seconds=rule.coalesce_seconds,
                    dest_key=dest_key,
                )
            )
        else:
            outcomes.append(Outcome("relay", "relayé", rule.id, emission, dest_key=dest_key))
            runtime.note_quota(quota_kind, now)
        served.add(dest_key)
        accepted = True

    if inc.kind == "position" and pos_motif is not missing and pos_motif != "position aberrante":
        if inc.lat is not None and inc.lon is not None:
            runtime.last_sample_pos[(inc.network, inc.sender_id)] = (
                _round4(inc.lat),
                _round4(inc.lon),
                now,
            )
    if accepted:
        sent_metrics = changed if inc.kind == "measurement" and isinstance(changed, dict) else None
        _mark_sent(inc, runtime, now, sent_metrics)
        _remember_dedup(inc, runtime, now, settings.dedup_ttl_s)
    return outcomes


def render_coalesced(prefix: str, parts: list[tuple[str, str]], max_bytes: int) -> str:
    return truncate_utf8(coalesced_line(prefix, parts), max_bytes)
