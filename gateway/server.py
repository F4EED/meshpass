"""Serveur web local du banc d'essai."""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from gateway.clock import Clock
from gateway.engine import Engine
from gateway.models import BlackEntry, Incoming, Rule

ROOT_PAGE = __import__("pathlib").Path(__file__).resolve().parent / "web" / "index.html"
SESSIONS: set[str] = set()
ENGINE = Engine(Clock())
LOCK = threading.Lock()


def _json_bytes(payload: dict, status: int = 200) -> tuple[int, bytes]:
    return status, json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _rule_from(body: dict, previous: Rule | None = None) -> Rule:
    base = previous or Rule(
        id=body.get("id") or "regle",
        mt_channel_id=body["mt_channel_id"],
        mc_channel_id=body["mc_channel_id"],
        mt_name=body.get("mt_name", ""),
        mc_name=body.get("mc_name", ""),
    )
    data = {
        "id": body.get("id") or base.id,
        "mt_channel_id": body.get("mt_channel_id", base.mt_channel_id),
        "mc_channel_id": body.get("mc_channel_id", base.mc_channel_id),
        "mt_name": body.get("mt_name", base.mt_name),
        "mc_name": body.get("mc_name", base.mc_name),
        "direction": body.get("direction", base.direction),
        "active": body.get("active", base.active),
        "coalesce": body.get("coalesce", base.coalesce),
        "coalesce_seconds": int(body.get("coalesce_seconds", base.coalesce_seconds)),
        "positions": body.get("positions", base.positions),
        "measurements": body.get("measurements", base.measurements),
        "allow_ids": body.get("allow_ids", base.allow_ids),
        "block_ids": body.get("block_ids", base.block_ids),
        "keyword_required": body.get("keyword_required", base.keyword_required),
        "keyword_excluded": body.get("keyword_excluded", base.keyword_excluded),
        "min_len": int(body.get("min_len", base.min_len) or 0),
        "max_len": int(body.get("max_len", base.max_len) or 0),
        "quiet_start": body.get("quiet_start", base.quiet_start),
        "quiet_end": body.get("quiet_end", base.quiet_end),
        "max_bytes": int(body.get("max_bytes", base.max_bytes) or 200),
    }
    return Rule(**data)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def _cookie_token(self) -> str:
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "meshpass":
                return value
        return ""

    def _authorized(self) -> bool:
        return self._cookie_token() in SESSIONS

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            page = ROOT_PAGE.read_bytes()
            self._send(200, page, "text/html; charset=utf-8")
            return
        if path == "/api/state":
            if not self._authorized():
                self._send(*_json_bytes({"error": "auth"}, 401), "application/json")
                return
            with LOCK:
                ENGINE.flush_due()
                payload = ENGINE.snapshot()
            self._send(*_json_bytes(payload), "application/json")
            return
        self._send(404, b"introuvable", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self._read_json()
        except json.JSONDecodeError:
            self._send(*_json_bytes({"error": "json"}, 400), "application/json")
            return
        if path == "/api/login":
            self._login(body)
            return
        if not self._authorized():
            self._send(*_json_bytes({"error": "auth"}, 401), "application/json")
            return
        routes = {
            "/api/logout": self._logout,
            "/api/inject": self._inject,
            "/api/rules": self._save_rule,
            "/api/rules/delete": self._delete_rule,
            "/api/blacklist": self._save_black,
            "/api/blacklist/delete": self._delete_black,
            "/api/settings": self._save_settings,
            "/api/flush": self._flush,
            "/api/profiles/save": self._save_profile,
            "/api/profiles/load": self._load_profile,
            "/api/profiles/delete": self._delete_profile,
        }
        handler = routes.get(path)
        if handler is None:
            self._send(404, b"introuvable", "text/plain; charset=utf-8")
            return
        handler(body)

    def _login(self, body: dict) -> None:
        from gateway.auth import check_password

        with LOCK:
            ok = check_password(body.get("password", ""), ENGINE.settings)
        if not ok:
            self._send(*_json_bytes({"error": "mot de passe"}, 403), "application/json")
            return
        token = secrets.token_urlsafe(24)
        SESSIONS.add(token)
        self._send(
            *_json_bytes({"ok": True}),
            "application/json",
            {"Set-Cookie": f"meshpass={token}; HttpOnly; SameSite=Lax; Path=/"},
        )

    def _logout(self, _body: dict) -> None:
        SESSIONS.discard(self._cookie_token())
        self._send(
            *_json_bytes({"ok": True}),
            "application/json",
            {"Set-Cookie": "meshpass=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"},
        )

    def _inject(self, body: dict) -> None:
        metrics = {k: float(v) for k, v in (body.get("metrics") or {}).items() if v not in ("", None)}
        inc = Incoming(
            kind=body.get("kind", "chat"),
            network=body.get("network", "mt"),
            sender_id=body.get("sender_id", ""),
            sender_name=body.get("sender_name", ""),
            channel_id=body.get("channel_id", ""),
            text=body.get("text", ""),
            lat=_float_or_none(body.get("lat")),
            lon=_float_or_none(body.get("lon")),
            metrics=metrics,
            detection_repeat=bool(body.get("detection_repeat")),
            from_our_module=bool(body.get("from_our_module")),
        )
        with LOCK:
            results = ENGINE.ingest(inc)
        self._send(*_json_bytes({"results": results}), "application/json")

    def _save_rule(self, body: dict) -> None:
        with LOCK:
            names = _names(ENGINE, body.get("mt_channel_id", ""), body.get("mc_channel_id", ""))
            body = {**body, **names}
            current = next((rule for rule in ENGINE.rules if rule.id == body.get("id")), None)
            rule = _rule_from(body, current)
            if current is None:
                if not rule.id:
                    rule.id = _new_id()
                ENGINE.rules.append(rule)
            else:
                index = ENGINE.rules.index(current)
                ENGINE.rules[index] = rule
            ENGINE.save()
        self._send(*_json_bytes({"ok": True}), "application/json")

    def _delete_rule(self, body: dict) -> None:
        with LOCK:
            ENGINE.rules = [rule for rule in ENGINE.rules if rule.id != body.get("id")]
            ENGINE.save()
        self._send(*_json_bytes({"ok": True}), "application/json")

    def _save_black(self, body: dict) -> None:
        entry = BlackEntry(
            id=_new_id(),
            network=body["network"],
            sender_id=body["sender_id"],
            name=body.get("name") or body["sender_id"],
            reason=body.get("reason", ""),
            added=body.get("added", ""),
        )
        with LOCK:
            ENGINE.blacklist.append(entry)
            ENGINE.save()
        self._send(*_json_bytes({"ok": True}), "application/json")

    def _delete_black(self, body: dict) -> None:
        with LOCK:
            ENGINE.blacklist = [entry for entry in ENGINE.blacklist if entry.id != body.get("id")]
            ENGINE.save()
        self._send(*_json_bytes({"ok": True}), "application/json")

    def _save_settings(self, body: dict) -> None:
        with LOCK:
            settings = ENGINE.settings
            for key in ("prefix_mt", "prefix_mc", "mt_interface", "mc_interface", "own_mt_id", "own_mc_id"):
                if key in body:
                    setattr(settings, key, body[key])
            if "include_sender" in body:
                settings.include_sender = bool(body["include_sender"])
            if "dedup_ttl_min" in body:
                settings.dedup_ttl_s = float(body["dedup_ttl_min"]) * 60
            for key in ("quota_chat_per_min", "quota_telemetry_per_min", "quota_global_per_min"):
                if key in body:
                    setattr(settings, key, int(body[key]))
            if body.get("password"):
                from gateway.auth import set_password

                set_password(settings, body["password"])
            ENGINE.save()
        self._send(*_json_bytes({"ok": True}), "application/json")

    def _save_profile(self, body: dict) -> None:
        try:
            with LOCK:
                name = ENGINE.save_profile(body.get("name", ""))
        except ValueError:
            self._send(*_json_bytes({"error": "nom invalide"}, 400), "application/json")
            return
        self._send(*_json_bytes({"ok": True, "name": name}), "application/json")

    def _load_profile(self, body: dict) -> None:
        try:
            with LOCK:
                name = ENGINE.load_profile(body.get("name", ""))
        except (ValueError, FileNotFoundError):
            self._send(*_json_bytes({"error": "introuvable"}, 404), "application/json")
            return
        self._send(*_json_bytes({"ok": True, "name": name}), "application/json")

    def _delete_profile(self, body: dict) -> None:
        try:
            with LOCK:
                ENGINE.delete_profile(body.get("name", ""))
        except ValueError:
            self._send(*_json_bytes({"error": "configuration active"}, 400), "application/json")
            return
        self._send(*_json_bytes({"ok": True}), "application/json")

    def _flush(self, _body: dict) -> None:
        with LOCK:
            ENGINE.clock.advance(0) if hasattr(ENGINE.clock, "advance") else None
            now = ENGINE.clock.monotonic()
            for bucket in ENGINE.buckets.values():
                bucket.deadline = now
            ENGINE.flush_due()
        self._send(*_json_bytes({"ok": True}), "application/json")


def _float_or_none(value) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _new_id() -> str:
    return secrets.token_hex(4)


def _names(engine: Engine, mt_id: str, mc_id: str) -> dict:
    mt = next((c["name"] for c in engine.channels["mt"] if c["id"] == mt_id), "")
    mc = next((c["name"] for c in engine.channels["mc"] if c["id"] == mc_id), "")
    return {"mt_name": mt, "mc_name": mc}


def serve(host: str = "127.0.0.1", port: int = 8080, open_browser: bool = False) -> None:
    from gateway.auth import ensure_password

    with LOCK:
        ENGINE.load()
        created = ensure_password(ENGINE.settings)
        ENGINE.save()
    if created:
        print("Mot de passe d'essai : meshpass", flush=True)
    print(f"Banc d'essai : http://{host}:{port}", flush=True)
    if open_browser:
        threading.Timer(0.6, _open, args=(f"http://{host}:{port}",)).start()
    httpd = ThreadingHTTPServer((host, port), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Arrêt.")
    finally:
        httpd.server_close()


def _open(url: str) -> None:
    import webbrowser

    webbrowser.open(url)
