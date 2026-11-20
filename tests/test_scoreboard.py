"""Scoreboard (scripts/scoreboard/build.py): measurement verdict parsing, newest verdict per scenario, maps, compat."""
import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
_P = Path(__file__).resolve().parents[1] / "scripts/scoreboard/build.py"
_spec = importlib.util.spec_from_file_location("sb_build", _P)
b = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(b)


def f(name, verdict, sha="aaaa"):
    return f"fixture {name} on clean/integration @{sha}\n{name}: {verdict}\n"


class Parse(unittest.TestCase):
    def test_identical(self):
        n, sha, r = b.parse_measure(f("x", "IDENTICAL 1200 ticks (1201 states, 5 deep+lua+spatial checkpoints IDENTICAL)"))
        self.assertEqual((n, sha, r["status"], r["ticks"], r["turn"]), ("x", "aaaa", "gleich", 1200, None))

    def test_deep_takes_the_earliest_level(self):
        _, _, r = b.parse_measure(f("x", "DEEP-DIVERGENT 1200 ticks (snapshot+tasks+rng IDENTICAL 1200, 10 draws; "
                                         "lua@265(lua_graph.1 differs), spatial@200(exploration_circles differs))"))
        self.assertEqual((r["status"], r["ticks"], r["turn"], r["level"]), ("tief", 1200, 200, "Räumlich"))
        self.assertEqual(r["levels"], {"Lua": 265, "Räumlich": 200})

    def test_divergent_names_only_the_level(self):
        _, _, r = b.parse_measure(f("x", "DIVERGENT turn 329: tasks.task_state_changes digest: reference 0x1, clean 0x2"))
        self.assertEqual((r["status"], r["turn"], r["level"]), ("abweichend", 329, "Tasks"))
        self.assertNotIn("task_state_changes", repr(r))  # no field names on the public page

    def test_no_log_and_missing_image_are_unmeasured(self):
        self.assertEqual(b.parse_measure("fixture x on b @ab\nno log\n")[2]["status"], "ungemessen")
        self.assertEqual(b.parse_measure(f("x", "s5verify: reference image starts at 0, scenario starts at 25"))[2]
                         ["status"], "ungemessen")
        self.assertEqual(b.parse_measure(f("x", "panicked at something"))[2]["status"], "fehler")


def rel(sha, done, results):
    return {"sha": sha, "done": done, "files": {}, "results": results}


G = {"status": "gleich", "ticks": 1000, "turn": None, "level": None, "levels": {}}
T = {"status": "tief", "ticks": 1000, "turn": 525, "level": "Lua", "levels": {"Lua": 525}}
A = {"status": "abweichend", "ticks": None, "turn": 125, "level": "Tasks", "levels": {"Tasks": 125}}
U = {"status": "ungemessen", "ticks": None, "turn": None, "level": None, "levels": {}}


class Build(unittest.TestCase):
    def setUp(self):
        self.rels = {"rm-old": rel("old", "2026-11-20T10:00:00Z", {"a": T, "b": A, "c": U, "map-crater": G}),
                     "rm-new": rel("new", "2026-11-20T12:00:00Z", {"a": G, "b": U})}
        self.meta = {"a": {"start": 25, "turns": 1000}, "b": {"start": 25, "turns": 1000}}
        self.d, self.h = b.build(self.rels, self.meta, [], now=datetime(2026, 11, 20, 13, tzinfo=timezone.utc))

    def test_newest_real_verdict_wins_and_no_log_does_not_hide_an_older_one(self):
        sc = {s["name"]: s for s in self.d["scenarios"]}
        self.assertEqual((sc["a"]["status"], sc["a"]["delta"], sc["a"]["sha"]), ("gleich", "besser", "new"))
        self.assertEqual((sc["b"]["status"], sc["b"]["sha"], sc["b"]["same"]), ("abweichend", "old", 100))
        self.assertEqual(sc["c"]["status"], "ungemessen")

    def test_summary_and_turn_shares(self):
        s = self.d["summary"]
        self.assertEqual((s["gleich"], s["abweichend"], s["ungemessen"], s["besser"]), (2, 1, 1, 1))
        self.assertEqual((s["turns_total"], s["turns_same"]), (3000, 2100))

    def test_maps_and_compat_fields(self):
        crater = [m for m in self.d["maps"]["list"] if m["name"] == "Crater"][0]
        self.assertEqual((crater["status"], crater["scenarios"]), ("gleich", ["map-crater"]))
        sc = {s["name"]: s for s in self.d["scenarios"]}
        self.assertEqual((sc["a"]["identical"], sc["a"]["turn"], sc["a"]["total"]), (True, 1000, 1000))
        self.assertIsNone(sc["c"]["turn"])
        self.assertEqual(self.d["percent"]["test_scope"]["maps"]["total"], 94)
        self.assertEqual(self.d["generated_berlin"], "20.11.2026 14:00")

    def test_history_has_one_point_per_newest_release(self):
        self.assertEqual([h["tag"] for h in self.h], ["rm-old", "rm-new"])
        self.assertEqual([h["gleich"] for h in self.h], [1, 2])
        _, h2 = b.build(self.rels, self.meta, self.h)
        self.assertEqual(len(h2), 2)

    def test_scenario_to_map(self):
        self.assertEqual(b.scenario_map("campaign-04-tradingcaravans"), "tradingcaravans")
        self.assertEqual(b.scenario_map("genc-campcrawford-5000"), "crawford")
        self.assertEqual(b.scenario_map("map-frozenfjord-mapstart"), "frozenfjord")
        self.assertEqual(b.group("combat-waves"), "Kampf")
        self.assertEqual(b.group("castrum"), "Kartenstart")


if __name__ == "__main__":
    unittest.main()
