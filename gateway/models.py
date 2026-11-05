"""Objets échangés entre le banc d'essai, le filtre et l'interface."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Incoming:
    kind: str
    network: str
    sender_id: str
    sender_name: str
    channel_id: str
    text: str = ""
    lat: float | None = None
    lon: float | None = None
    alt: float | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    detection_repeat: bool = False
    from_our_module: bool = False


@dataclass
class Rule:
    id: str
    mt_channel_id: str
    mc_channel_id: str
    mt_name: str
    mc_name: str
    direction: str = "bi"
    active: bool = True
    coalesce: bool = False
    coalesce_seconds: int = 30
    positions: bool = False
    measurements: bool = False
    allow_ids: list[str] = field(default_factory=list)
    block_ids: list[str] = field(default_factory=list)
    keyword_required: str = ""
    keyword_excluded: str = ""
    min_len: int = 0
    max_len: int = 0
    quiet_start: str = ""
    quiet_end: str = ""
    max_bytes: int = 200

    def orphan(self, channels: dict[str, list[dict]]) -> bool:
        mt_ids = {c["id"] for c in channels.get("mt", [])}
        mc_ids = {c["id"] for c in channels.get("mc", [])}
        return self.mt_channel_id not in mt_ids or self.mc_channel_id not in mc_ids


@dataclass
class BlackEntry:
    id: str
    network: str
    sender_id: str
    name: str
    reason: str = ""
    added: str = ""


@dataclass
class Settings:
    prefix_mt: str = "[MT]"
    prefix_mc: str = "[MC]"
    include_sender: bool = True
    dedup_ttl_s: float = 600.0
    quota_chat_per_min: int = 6
    quota_telemetry_per_min: int = 6
    quota_global_per_min: int = 60
    password_hash: str = ""
    password_salt: str = ""
    own_mt_id: str = "gw-mt"
    own_mc_id: str = "gw-mc"
    mt_interface: str = "usb"
    mc_interface: str = "usb"


@dataclass
class Emission:
    kind: str
    network: str
    channel_id: str
    channel_name: str
    text: str
    sender_name: str = ""
    lat: float | None = None
    lon: float | None = None


@dataclass
class Outcome:
    action: str
    motif: str
    rule_id: str | None = None
    emission: Emission | None = None
    hold_part: tuple[str, str] | None = None
    hold_seconds: int = 30
    dest_key: str = ""
