"""État de la passerelle d'essai : règles, journal, coalescence."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from gateway.formatutil import truncate_utf8
from gateway.models import BlackEntry, Incoming, Rule, Settings
from gateway.pipeline import Runtime, _prefix_for, process, render_coalesced

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "config.json"
CONFIGS = ROOT / "data" / "configs"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,40}")


def _id() -> str:
    return uuid.uuid4().hex[:8]


def _profile_name(name: str) -> str:
    raw = name.strip().replace("\\", "").replace("/", "")
    if raw.lower().endswith(".conf"):
        raw = raw[:-5]
    if not _NAME.fullmatch(raw):
        raise ValueError("nom")
    return raw + ".conf"


def default_channels() -> dict[str, list[dict]]:
    return {
        "mt": [
            {"id": "mt:0", "name": "general", "detail": "canal 0 · PSI public"},
            {"id": "mt:1", "name": "secours", "detail": "canal 1 · AES"},
            {"id": "mt:2", "name": "meteo", "detail": "canal 2"},
        ],
        "mc": [
            {"id": "mc:default", "name": "default", "detail": "public"},
            {"id": "mc:urgence", "name": "urgence", "detail": "AES-256"},
        ],
    }


def default_rules() -> list[Rule]:
    return [
        Rule(
            id="regle-1",
            mt_channel_id="mt:0",
            mc_channel_id="mc:default",
            mt_name="general",
            mc_name="default",
            direction="bi",
        )
    ]


class Bucket:
    def __init__(self, rule: Rule, dest_net: str, dest_id: str, dest_name: str, prefix: str, deadline: float) -> None:
        self.rule_id = rule.id
        self.dest_net = dest_net
        self.dest_id = dest_id
        self.dest_name = dest_name
        self.prefix = prefix
        self.max_bytes = rule.max_bytes
        self.deadline = deadline
        self.parts: list[tuple[str, str]] = []

    def line(self) -> str:
        return render_coalesced(self.prefix, self.parts, self.max_bytes)


class Engine:
    def __init__(self, clock) -> None:
        self.clock = clock
        self.settings = Settings()
        self.channels = default_channels()
        self.rules = default_rules()
        self.blacklist: list[BlackEntry] = []
        self.runtime = Runtime()
        self.journal: list[dict] = []
        self.buckets: dict[str, Bucket] = {}
        self.stats = {"mt2mc": 0, "mc2mt": 0, "drops": {}}
        self.connected = {"mt": True, "mc": True}
        self.active_profile = "normal.conf"

    def load(self) -> None:
        if not DATA.exists():
            return
        raw = json.loads(DATA.read_text(encoding="utf-8"))
        self.settings = Settings(**raw["settings"])
        self.channels = raw["channels"]
        self.rules = [Rule(**item) for item in raw["rules"]]
        self.blacklist = [BlackEntry(**item) for item in raw["blacklist"]]
        self.connected = raw.get("connected", self.connected)
        self.active_profile = raw.get("active_profile", self.active_profile)
        self._ensure_profile()

    def _bundle(self, include_secret: bool) -> dict:
        settings = asdict(self.settings)
        if not include_secret:
            settings.pop("password_hash", None)
            settings.pop("password_salt", None)
        return {
            "settings": settings,
            "channels": self.channels,
            "rules": [asdict(rule) for rule in self.rules],
            "blacklist": [asdict(entry) for entry in self.blacklist],
            "connected": self.connected,
        }

    def save(self) -> None:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        payload = self._bundle(True)
        payload["active_profile"] = self.active_profile
        DATA.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_profile(self.active_profile)

    def profiles(self) -> list[str]:
        if not CONFIGS.exists():
            return []
        return sorted(path.name for path in CONFIGS.glob("*.conf"))

    def save_profile(self, name: str) -> str:
        clean = _profile_name(name)
        self.active_profile = clean
        self.save()
        return clean

    def load_profile(self, name: str) -> str:
        clean = _profile_name(name)
        path = CONFIGS / clean
        if not path.exists():
            raise FileNotFoundError(clean)
        raw = json.loads(path.read_text(encoding="utf-8"))
        secret_hash = self.settings.password_hash
        secret_salt = self.settings.password_salt
        incoming = raw.get("settings", {})
        incoming["password_hash"] = secret_hash
        incoming["password_salt"] = secret_salt
        self.settings = Settings(**incoming)
        self.rules = [Rule(**item) for item in raw.get("rules", [])]
        self.blacklist = [BlackEntry(**item) for item in raw.get("blacklist", [])]
        if "channels" in raw:
            self.channels = raw["channels"]
        if "connected" in raw:
            self.connected = raw["connected"]
        self.runtime = Runtime()
        self.buckets.clear()
        self.active_profile = clean
        self._write_live()
        return clean

    def delete_profile(self, name: str) -> None:
        clean = _profile_name(name)
        if clean == self.active_profile:
            raise ValueError("active")
        path = CONFIGS / clean
        if path.exists():
            path.unlink()

    def _ensure_profile(self) -> None:
        CONFIGS.mkdir(parents=True, exist_ok=True)
        if not any(CONFIGS.glob("*.conf")):
            self._write_profile(self.active_profile)

    def _write_profile(self, name: str) -> None:
        CONFIGS.mkdir(parents=True, exist_ok=True)
        body = self._bundle(False)
        (CONFIGS / name).write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_live(self) -> None:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        payload = self._bundle(True)
        payload["active_profile"] = self.active_profile
        DATA.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _note(self, action: str, motif: str, detail: str) -> None:
        stamp = datetime.fromtimestamp(self.clock.wall()).strftime("%H:%M:%S")
        self.journal.append({"time": stamp, "action": action, "motif": motif, "detail": detail})
        if len(self.journal) > 400:
            self.journal = self.journal[-400:]
        if action == "drop":
            self.stats["drops"][motif] = self.stats["drops"].get(motif, 0) + 1

    def _count_relay(self, network: str) -> None:
        if network == "mc":
            self.stats["mt2mc"] += 1
        else:
            self.stats["mc2mt"] += 1

    def ingest(self, inc: Incoming) -> list[dict]:
        now = self.clock.monotonic()
        wall = self.clock.wall()
        blacklist = [asdict(entry) for entry in self.blacklist]
        outcomes = process(inc, self.rules, self.settings, blacklist, self.channels, self.runtime, now, wall)
        public: list[dict] = []
        for outcome in outcomes:
            if outcome.action == "drop":
                detail = self._detail(inc, outcome.motif)
                self._note("drop", outcome.motif, detail)
                public.append({"action": "drop", "motif": outcome.motif, "detail": detail})
            elif outcome.action == "hold":
                self._hold(outcome, now)
                detail = f"regroupement en cours ({outcome.hold_seconds} s)"
                self._note("hold", "coalescence", detail)
                public.append({"action": "hold", "motif": "coalescence", "detail": detail})
            elif outcome.emission is not None:
                self._emit(outcome.emission)
                detail = outcome.emission.text
                public.append({"action": "relay", "motif": "relayé", "detail": detail, "kind": outcome.emission.kind})
        self._flush_oversize(now)
        return public

    def _detail(self, inc: Incoming, motif: str) -> str:
        who = inc.sender_name or inc.sender_id
        if inc.kind == "chat":
            return f"{who} : {inc.text} — {motif}"
        if inc.kind == "position":
            return f"{who} pos {inc.lat},{inc.lon} — {motif}"
        if inc.kind == "measurement":
            return f"{who} {inc.metrics} — {motif}"
        if inc.kind == "detection":
            return f"{who} détection — {motif}"
        return f"{who} — {motif}"

    def _emit(self, emission) -> None:
        self._count_relay(emission.network)
        label = "repère" if emission.kind == "waypoint" else "message"
        self._note("relay", "relayé", f"{label} → {emission.network} #{emission.channel_name} : {emission.text}")

    def _hold(self, outcome, now: float) -> None:
        rule = next(item for item in self.rules if item.id == outcome.rule_id)
        dest_net, dest_id = outcome.dest_key.split(":", 1)
        dest_name = rule.mc_name if dest_net == "mc" else rule.mt_name
        prefix = _prefix_for("mt" if dest_net == "mc" else "mc", self.settings)
        bucket = self.buckets.get(outcome.dest_key + ":" + rule.id)
        if bucket is None:
            bucket = Bucket(rule, dest_net, dest_id, dest_name, prefix, now + outcome.hold_seconds)
            self.buckets[outcome.dest_key + ":" + rule.id] = bucket
        if outcome.hold_part:
            bucket.parts.append(outcome.hold_part)

    def _flush_oversize(self, now: float) -> None:
        for key, bucket in list(self.buckets.items()):
            if len(bucket.parts) < 2:
                continue
            while len(bucket.parts) >= 2:
                trial = render_coalesced(bucket.prefix, bucket.parts, 10_000)
                if len(trial.encode("utf-8")) <= bucket.max_bytes:
                    break
                early = bucket.parts[:-1]
                bucket.parts = bucket.parts[-1:]
                self._flush_parts(bucket, early, now)
                bucket.deadline = now + 30

    def _flush_parts(self, bucket: Bucket, parts: list[tuple[str, str]], now: float) -> None:
        if not parts:
            return
        if self.runtime.quota_full("chat", self.settings, now):
            self._note("drop", "quota", "groupe de coalescence abandonné")
            return
        line = truncate_utf8(render_coalesced(bucket.prefix, parts, 10_000), bucket.max_bytes)
        self.runtime.note_quota("chat", now)
        self._count_relay(bucket.dest_net)
        self._note("relay", "relayé", f"message → {bucket.dest_net} #{bucket.dest_name} : {line}")

    def flush_due(self) -> None:
        now = self.clock.monotonic()
        for key, bucket in list(self.buckets.items()):
            if now >= bucket.deadline and bucket.parts:
                self._flush_parts(bucket, bucket.parts, now)
                del self.buckets[key]

    def snapshot(self) -> dict:
        rules = []
        for rule in self.rules:
            item = asdict(rule)
            item["orphan"] = rule.orphan(self.channels)
            rules.append(item)
        return {
            "channels": self.channels,
            "rules": rules,
            "blacklist": [asdict(entry) for entry in self.blacklist],
            "settings": {
                "prefix_mt": self.settings.prefix_mt,
                "prefix_mc": self.settings.prefix_mc,
                "include_sender": self.settings.include_sender,
                "dedup_ttl_min": self.settings.dedup_ttl_s / 60,
                "quota_chat_per_min": self.settings.quota_chat_per_min,
                "quota_telemetry_per_min": self.settings.quota_telemetry_per_min,
                "quota_global_per_min": self.settings.quota_global_per_min,
                "mt_interface": self.settings.mt_interface,
                "mc_interface": self.settings.mc_interface,
                "own_mt_id": self.settings.own_mt_id,
                "own_mc_id": self.settings.own_mc_id,
            },
            "connected": self.connected,
            "profiles": self.profiles(),
            "active_profile": self.active_profile,
            "stats": self.stats,
            "journal": list(reversed(self.journal[-80:])),
            "mode": "essai",
        }
