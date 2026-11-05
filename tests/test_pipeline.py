"""Essais du filtre, sans radio et sans serveur."""

from __future__ import annotations

import unittest
from datetime import datetime

from gateway.clock import FakeClock
from gateway.engine import Engine, default_channels
from gateway.formatutil import truncate_utf8
from gateway.models import Incoming, Rule, Settings
from gateway.pipeline import Runtime, process


def rule(**kwargs) -> Rule:
    base = dict(
        id="r1",
        mt_channel_id="mt:0",
        mc_channel_id="mc:default",
        mt_name="general",
        mc_name="default",
        direction="bi",
        positions=True,
        measurements=True,
    )
    base.update(kwargs)
    return Rule(**base)


def chat(text="Salut", sender="f4xyz", name="F4XYZ", network="mt", channel="mt:0", **kwargs) -> Incoming:
    return Incoming("chat", network, sender, name, channel, text=text, **kwargs)


def run(inc, rules=None, settings=None, blacklist=None, runtime=None, now=0.0, wall=1_700_000_000.0):
    return process(
        inc,
        rules if rules is not None else [rule()],
        settings or Settings(),
        blacklist or [],
        default_channels(),
        runtime or Runtime(),
        now,
        wall,
    )


class FilterOrderTests(unittest.TestCase):
    def test_direct_wins_over_prefix(self):
        outcome = run(Incoming("direct", "mt", "f4xyz", "F4XYZ", "mt:0", text="[MT] secret"))[0]
        self.assertEqual(outcome.motif, "message direct")

    def test_echo_before_prefix(self):
        outcome = run(chat("[MT] retour", sender="gw-mt"))[0]
        self.assertEqual(outcome.motif, "écho")

    def test_canonical_prefix_stays_when_customized(self):
        settings = Settings(prefix_mt="[MESH]", prefix_mc="[CORE]")
        self.assertEqual(run(chat("[MT] déjà passé"), settings=settings)[0].motif, "préfixe")
        self.assertEqual(run(chat("[MESH] maison"), settings=settings)[0].motif, "préfixe")

    def test_callsign_form_is_blocked(self):
        self.assertEqual(run(chat("[MT|F4XYZ] salut"))[0].motif, "préfixe")

    def test_blacklist_uses_technical_id(self):
        listed = [{"network": "mt", "sender_id": "f4xyz"}]
        blocked = run(chat("bonjour", sender="f4xyz", name="autre"), blacklist=listed)[0]
        homonym = run(chat("bonjour", sender="autre", name="F4XYZ"), blacklist=listed)[0]
        self.assertEqual(blocked.motif, "liste noire")
        self.assertEqual(homonym.action, "relay")

    def test_allow_block_and_blacklist_priority(self):
        listed = [{"network": "mt", "sender_id": "f4xyz"}]
        allowed = rule(allow_ids=["f4xyz", "bot"])
        self.assertEqual(run(chat(sender="bot"), rules=[allowed])[0].action, "relay")
        self.assertEqual(run(chat(sender="inconnu"), rules=[allowed])[0].motif, "filtre")
        blocked = rule(block_ids=["bot"])
        self.assertEqual(run(chat(sender="bot"), rules=[blocked])[0].motif, "filtre")
        both = rule(allow_ids=["f4xyz"])
        self.assertEqual(run(chat(sender="f4xyz"), rules=[both], blacklist=listed)[0].motif, "liste noire")

    def test_keyword_ignores_position(self):
        rules = [rule(keyword_excluded="pos")]
        text = run(chat("pos ici"), rules=rules)[0]
        position = run(
            Incoming("position", "mt", "f4xyz", "F4XYZ", "mt:0", lat=48.85, lon=2.35),
            rules=rules,
        )[0]
        self.assertEqual(text.motif, "filtre")
        self.assertEqual(position.action, "relay")

    def test_length_is_before_prefix(self):
        rules = [rule(min_len=5)]
        short = run(chat("oui"), rules=rules)[0]
        self.assertEqual(short.motif, "filtre")

    def test_two_people_same_word_are_not_duplicates(self):
        runtime = Runtime()
        first = run(chat("ok", sender="a", name="A"), runtime=runtime, now=10)[0]
        second = run(chat("ok", sender="b", name="B"), runtime=runtime, now=11)[0]
        again = run(chat("ok", sender="a", name="A"), runtime=runtime, now=12)[0]
        self.assertEqual(first.action, "relay")
        self.assertEqual(second.action, "relay")
        self.assertEqual(again.motif, "doublon")
        self.assertIn("ok", first.emission.text)
        self.assertNotIn("OK", first.emission.text)

    def test_case_is_kept(self):
        outcome = run(chat("Bonjour"))[0]
        self.assertIn("Bonjour", outcome.emission.text)

    def test_utf8_truncation_keeps_characters(self):
        cut = truncate_utf8("ééééé", 6)
        cut.encode("utf-8")
        self.assertTrue(cut.endswith("…") or len(cut.encode("utf-8")) <= 6)

    def test_long_line_is_cut_on_bytes(self):
        rules = [rule(max_bytes=20)]
        outcome = run(chat("é" * 40), rules=rules)[0]
        self.assertLessEqual(len(outcome.emission.text.encode("utf-8")), 20)
        outcome.emission.text.encode("utf-8")


class QuotaTests(unittest.TestCase):
    def test_chat_quota_does_not_eat_positions(self):
        runtime = Runtime()
        settings = Settings(quota_chat_per_min=2, quota_telemetry_per_min=2, quota_global_per_min=10)
        rules = [rule()]
        for i in range(2):
            self.assertEqual(run(chat(f"m{i}", sender=str(i)), settings=settings, rules=rules, runtime=runtime, now=i)[0].action, "relay")
        self.assertEqual(run(chat("trop", sender="x"), settings=settings, rules=rules, runtime=runtime, now=3)[0].motif, "quota")
        position = Incoming("position", "mt", "f4xyz", "F4XYZ", "mt:0", lat=48.1, lon=2.1)
        self.assertEqual(run(position, settings=settings, rules=rules, runtime=runtime, now=4)[0].action, "relay")

    def test_burst_is_not_kept(self):
        runtime = Runtime()
        settings = Settings(quota_chat_per_min=1, quota_global_per_min=10)
        self.assertEqual(run(chat("un", sender="a"), settings=settings, runtime=runtime, now=1)[0].action, "relay")
        self.assertEqual(run(chat("deux", sender="b"), settings=settings, runtime=runtime, now=2)[0].motif, "quota")
        self.assertEqual(len(runtime.quota["chat"]), 1)


class PositionTests(unittest.TestCase):
    def test_off_by_default_shape_and_both_networks(self):
        off = run(Incoming("position", "mt", "f4xyz", "F4XYZ", "mt:0", lat=48.8567, lon=2.3522), rules=[rule(positions=False)])[0]
        self.assertEqual(off.motif, "option arrêtée")
        to_mc = run(Incoming("position", "mt", "f4xyz", "F4XYZ", "mt:0", lat=48.8567, lon=2.3522))[0]
        self.assertEqual(to_mc.emission.kind, "chat")
        self.assertIn("pos 48.8567,2.3522", to_mc.emission.text)
        to_mt = run(Incoming("position", "mc", "ve9", "VE9ABC", "mc:default", lat=45.5, lon=-73.5))[0]
        self.assertEqual(to_mt.emission.kind, "waypoint")
        self.assertEqual(to_mt.emission.sender_name, "VE9ABC")

    def test_identical_too_soon_aberrant_and_refresh(self):
        runtime = Runtime()
        first = Incoming("position", "mt", "f4xyz", "F4XYZ", "mt:0", lat=48.85, lon=2.35, alt=10)
        self.assertEqual(run(first, runtime=runtime, now=0)[0].action, "relay")
        same = Incoming("position", "mt", "f4xyz", "F4XYZ", "mt:0", lat=48.85001, lon=2.35001, alt=80)
        self.assertEqual(run(same, runtime=runtime, now=10)[0].motif, "position inchangée")
        moved = Incoming("position", "mt", "f4xyz", "F4XYZ", "mt:0", lat=48.851, lon=2.35)
        self.assertEqual(run(moved, runtime=runtime, now=20)[0].motif, "position trop rapprochée")
        self.assertEqual(run(moved, runtime=runtime, now=400)[0].action, "relay")
        zero = Incoming("position", "mt", "autre", "X", "mt:0", lat=0.0, lon=0.0)
        self.assertEqual(run(zero, runtime=runtime, now=500)[0].motif, "position aberrante")
        jump = Incoming("position", "mt", "f4xyz", "F4XYZ", "mt:0", lat=0.0, lon=2.35)
        self.assertEqual(run(jump, runtime=runtime, now=410)[0].motif, "position aberrante")
        still = Incoming("position", "mt", "f4xyz", "F4XYZ", "mt:0", lat=48.851, lon=2.35)
        self.assertEqual(run(still, runtime=runtime, now=400 + 3600)[0].action, "relay")

    def test_two_destinations_one_each(self):
        rules = [
            rule(id="a", mc_channel_id="mc:default", mc_name="default"),
            rule(id="b", mc_channel_id="mc:urgence", mc_name="urgence"),
        ]
        outcomes = run(chat("deux voies"), rules=rules)
        relays = [item for item in outcomes if item.action == "relay"]
        self.assertEqual(len(relays), 2)

    def test_same_destination_once(self):
        rules = [
            rule(id="a"),
            rule(id="b"),
        ]
        outcomes = run(chat("une fois"), rules=rules)
        self.assertEqual(sum(item.action == "relay" for item in outcomes), 1)
        self.assertEqual(sum(item.motif == "destination déjà servie" for item in outcomes), 1)


class MeasureTests(unittest.TestCase):
    def test_threshold_and_one_line(self):
        runtime = Runtime()
        first = Incoming("measurement", "mt", "f4xyz", "F4XYZ", "mt:0", metrics={"temp": 18.0, "bat": 80})
        sent = run(first, runtime=runtime, now=1)[0]
        self.assertEqual(sent.action, "relay")
        self.assertIn("temp 18", sent.emission.text)
        self.assertIn("bat 80", sent.emission.text)
        same = Incoming("measurement", "mt", "f4xyz", "F4XYZ", "mt:0", metrics={"temp": 18.2, "bat": 82})
        self.assertEqual(run(same, runtime=runtime, now=2)[0].motif, "mesure inchangée")
        moved = Incoming("measurement", "mt", "f4xyz", "F4XYZ", "mt:0", metrics={"temp": 19.0, "bat": 82})
        line = run(moved, runtime=runtime, now=3)[0]
        self.assertEqual(line.action, "relay")
        self.assertIn("temp 19", line.emission.text)
        self.assertNotIn("bat", line.emission.text)

    def test_detection_rate_and_quiet_hours(self):
        clock_wall = 1_700_000_000.0
        hour = datetime.fromtimestamp(clock_wall).hour
        rules = [rule(quiet_start=f"{hour:02d}:00", quiet_end=f"{(hour + 1) % 24:02d}:00")]
        self.assertEqual(run(chat("nuit"), rules=rules, wall=clock_wall)[0].motif, "heure silencieuse")
        runtime = Runtime()
        ping = Incoming("detection", "mt", "f4xyz", "F4XYZ", "mt:0")
        self.assertEqual(run(ping, rules=rules, runtime=runtime, now=10, wall=clock_wall)[0].action, "relay")
        repeat = Incoming("detection", "mt", "f4xyz", "F4XYZ", "mt:0", detection_repeat=True)
        self.assertEqual(run(repeat, rules=rules, runtime=runtime, now=20, wall=clock_wall)[0].motif, "mesure inchangée")
        again = Incoming("detection", "mt", "f4xyz", "F4XYZ", "mt:0")
        self.assertEqual(run(again, rules=rules, runtime=runtime, now=30, wall=clock_wall)[0].motif, "détection limitée")
        self.assertEqual(run(again, rules=rules, runtime=runtime, now=80, wall=clock_wall)[0].action, "relay")


class EngineTests(unittest.TestCase):
    def test_coalesce_keeps_names_in_order(self):
        clock = FakeClock()
        engine = Engine(clock)
        engine.rules = [rule(coalesce=True, coalesce_seconds=30)]
        engine.ingest(chat("a", sender="a", name="Nom1"))
        clock.advance(1)
        engine.ingest(chat("b", sender="b", name="Nom2"))
        clock.advance(30)
        engine.flush_due()
        relayed = [line for line in engine.journal if line["action"] == "relay"]
        self.assertTrue(relayed)
        self.assertIn("Nom1 : a / Nom2 : b", relayed[-1]["detail"])

    def test_named_profiles_keep_the_password(self):
        import tempfile
        from pathlib import Path
        import gateway.engine as eng

        with tempfile.TemporaryDirectory() as tmp:
            old_data, old_configs = eng.DATA, eng.CONFIGS
            eng.DATA = Path(tmp) / "config.json"
            eng.CONFIGS = Path(tmp) / "configs"
            try:
                engine = Engine(FakeClock())
                engine.settings.password_hash = "secret"
                engine.settings.password_salt = "salt"
                engine.save_profile("normal")
                engine.rules[0].direction = "mt2mc"
                engine.settings.mt_interface = "ble"
                engine.channels["mt"][0]["name"] = "salon"
                engine.save_profile("crise")
                engine.load_profile("normal.conf")
                self.assertEqual(engine.rules[0].direction, "bi")
                self.assertEqual(engine.settings.mt_interface, "usb")
                self.assertEqual(engine.channels["mt"][0]["name"], "general")
                self.assertEqual(engine.settings.password_hash, "secret")
                engine.load_profile("crise")
                self.assertEqual(engine.rules[0].direction, "mt2mc")
                self.assertEqual(engine.active_profile, "crise.conf")
                engine.delete_profile("normal.conf")
                self.assertEqual(engine.profiles(), ["crise.conf"])
            finally:
                eng.DATA, eng.CONFIGS = old_data, old_configs

    def test_orphan_rule_relays_nothing(self):
        clock = FakeClock()
        engine = Engine(clock)
        engine.rules = [rule(mt_channel_id="mt:absent")]
        engine.ingest(chat("perdu"))
        self.assertEqual(engine.journal[-1]["motif"], "aucune règle")
        self.assertTrue(engine.snapshot()["rules"][0]["orphan"])


if __name__ == "__main__":
    unittest.main()
