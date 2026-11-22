"""Tests for `vault migrate` (schema 1 → 2). Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import json
import shutil
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_vault import VaultTestCase, sh, write  # noqa: E402
from vaultlib import cli, core, migrate  # noqa: E402
from vaultlib import frontmatter as fm  # noqa: E402

L = "demo/"


def decision(title, summary, **fields):
    return fm.render({"title": title, "type": "decision", "status": "accepted", "summary": summary, **fields},
                     f"\n## Summary\n{summary}\n\n## Decision\n{summary}\n")


class TestMigrate(VaultTestCase):
    def setUp(self):
        super().setUp()
        self.p = self.init_project()
        write(self.p / "decisions/adr-0001-db.md", decision("DB layer", "All DB access goes through repositories.",
                                                            tags=["architecture"]))
        write(self.p / "decisions/adr-0002-jest.md", decision("Jest", "Use jest for unit tests."))
        write(self.p / "decisions/legacy-queue.md", decision("Scan queue", "Scans run through one queue."))
        mod = self.p / "modules/auth.md"
        mod.write_text(mod.read_text() + f"See [[{L}decisions/adr-0001-db#Decision|the DB ADR]] and "
                       f"[[{L}decisions/legacy-queue]].\n")
        feat = self.p / "features/login.md"
        feat.write_text(feat.read_text() + f"Tests: [[{L}decisions/adr-0002-jest]], [[{L}decisions/adr-0001-db]].\n")
        self.run_cli("commit", "-m", "fixture", "--stamp-all", "--synced")
        meta = core.read_meta(self.p)
        del meta["schema"]
        core.write_meta(self.p, meta)
        for d in ("conventions", "architecture", "proposals"):
            shutil.rmtree(self.p / d)
        self.run_cli("commit", "-m", "schema 1 vault")
        self.head = sh(self.vault, "git", "rev-parse", "HEAD")

    def migrate(self, *argv, expect=0):
        return self.run_cli("migrate", *argv, expect=expect)

    def plan(self, *argv):
        return json.loads(self.migrate("--plan", "--format", "json", *argv))

    def decisions_file(self, items=None):
        items = items if items is not None else [
            {"id": "dec-01", "verdict": "accepted", "path": "decisions/adr-0001-db.md", "class": "adr"},
            {"id": "dec-02", "verdict": "accepted", "path": "decisions/adr-0002-jest.md", "class": "decision",
             "kind": "tooling"},
            {"id": "dec-03", "verdict": "accepted", "path": "decisions/legacy-queue", "class": "adr"},
            {"id": "conv-01", "verdict": "rejected"},
        ]
        path = self.tmp / "decisions.json"
        path.write_text(json.dumps({"version": 1, "source": "migrate", "items": items}))
        return str(path)

    def snapshot(self):
        return {f.relative_to(self.p).as_posix(): f.read_bytes() for f in sorted(self.p.rglob("*")) if f.is_file()}

    def test_flagged_before_and_not_after(self):
        ctx = core.resolve(self.repo)
        self.assertTrue(core.needs_migration(ctx))
        self.assertEqual(cli._guardrails_block(ctx), cli.MIGRATION_LINE)
        issues = json.loads(self.run_cli("lint", "--json", expect=1))
        self.assertTrue(any("needs migration" in i["msg"] for i in issues))
        self.plan()
        self.migrate("--apply", self.decisions_file())
        ctx = core.resolve(self.repo)
        self.assertFalse(core.needs_migration(ctx))
        self.assertNotIn(cli.MIGRATION_LINE, cli._context_text(ctx))
        issues = json.loads(self.run_cli("lint", "--json", expect=None))
        self.assertEqual([i for i in issues if i["level"] == "error"], [])
        self.assertIn("Schema:   2\n", self.run_cli("status"))

    def test_plan(self):
        data = self.plan()
        self.assertEqual((data["status"], data["from"], data["to"], data["checkpoint"]), ("planned", 1, 2, self.head))
        self.assertEqual([d["path"] for d in data["decisions"]],
                         ["decisions/adr-0001-db.md", "decisions/adr-0002-jest.md", "decisions/legacy-queue.md"])
        db = data["decisions"][0]
        self.assertEqual((db["title"], db["summary"], db["tags"]),
                         ("DB layer", "All DB access goes through repositories.", ["architecture"]))
        self.assertIn("code_refs", db)
        self.assertEqual([(s["name"], s["phase"], s["done"]) for s in data["steps"]],
                         [("folders", "plan", True), ("hubs", "plan", True), ("move-adrs", "apply", False),
                          ("decision-kind", "apply", False)])
        self.assertIn(f"restore --source={self.head}", data["rollback"])
        for d in ("conventions", "architecture", "architecture/adr", "proposals"):
            self.assertTrue((self.p / d / "_index.md").is_file(), d)
        self.assertEqual(json.loads((self.vault / ".work/demo" / migrate.PLAN_FILE).read_text())["checkpoint"],
                         self.head)
        self.assertEqual(core.read_meta(self.p)["migration"]["done"], ["1:folders", "1:hubs"])
        self.assertEqual(core.vault_schema(core.resolve(self.repo)), 1)
        again = self.plan()
        self.assertEqual((again["status"], again["checkpoint"], again["changes"]), ("resuming", self.head, []))
        text = self.migrate("--plan")
        self.assertIn("demo schema 1 → 2: migration in progress (resuming)", text)
        self.assertIn("decisions/legacy-queue.md — Scan queue", text)

    def test_dirty_tree_refused(self):
        mod = self.p / "modules/auth.md"
        mod.write_text(mod.read_text() + "Uncommitted.\n")
        out = self.migrate("--plan", expect=1)
        self.assertIn("uncommitted changes", out)
        self.assertIn("demo/modules/auth.md", out)
        self.assertFalse((self.p / "architecture").exists())
        self.assertNotIn("migration", core.read_meta(self.p))

    def test_apply_moves_adrs_and_rewrites_links(self):
        self.plan()
        self.migrate("--apply", self.decisions_file())
        self.assertFalse((self.p / "decisions/adr-0001-db.md").exists())
        db = fm.read((self.p / "architecture/adr/adr-0001-db.md").read_text())[0]
        self.assertEqual((db["type"], db["status"], db["aliases"]), ("adr", "accepted", ["decisions/adr-0001-db"]))
        queue = fm.read((self.p / "architecture/adr/adr-0003-legacy-queue.md").read_text())[0]
        self.assertEqual((queue["type"], queue["aliases"]), ("adr", ["decisions/legacy-queue"]))
        jest = fm.read((self.p / "decisions/adr-0002-jest.md").read_text())[0]
        self.assertEqual((jest["type"], jest["kind"]), ("decision", "tooling"))
        mod = (self.p / "modules/auth.md").read_text()
        self.assertIn(f"[[{L}architecture/adr/adr-0001-db#Decision|the DB ADR]]", mod)
        self.assertIn(f"[[{L}architecture/adr/adr-0003-legacy-queue]]", mod)
        feat = (self.p / "features/login.md").read_text()
        self.assertIn(f"[[{L}decisions/adr-0002-jest]]", feat)
        self.assertIn(f"[[{L}architecture/adr/adr-0001-db]]", feat)
        meta = core.read_meta(self.p)
        self.assertEqual(meta["schema"], 2)
        self.assertNotIn("migration", meta)
        self.assertEqual(meta["migrations"][0]["checkpoint"], self.head)
        self.assertEqual(sh(self.vault, "git", "log", "--format=%s", "-1"), "migrate schema 1→2")
        self.assertEqual(sh(self.vault, "git", "status", "--porcelain"), "")
        self.assertIn("adr-0001-db", (self.p / "architecture/adr/_index.md").read_text())
        self.assertIn("adr-0001-db", self.run_cli("guardrails"))

    def test_apply_json_and_text(self):
        self.plan()
        res = json.loads(self.migrate("--apply", self.decisions_file(), "--format", "json"))
        self.assertEqual(res["status"], "migrated")
        self.assertIn("move decisions/adr-0001-db.md → architecture/adr/adr-0001-db.md (links rewritten in 3 note(s))",
                      res["changes"])
        self.assertIn("set kind: tooling on decisions/adr-0002-jest.md", res["changes"])
        self.assertIn("committed", res["commit"])

    def test_apply_needs_plan(self):
        self.assertIn("run `vault migrate --plan` first", self.migrate("--apply", self.decisions_file(), expect=1))
        bad = self.decisions_file([{"verdict": "accepted", "path": "decisions/adr-0002-jest.md"}])
        self.plan()
        self.assertIn("needs class adr|decision", self.migrate("--apply", bad, expect=1))

    def test_resume_after_failure(self):
        self.plan()
        real = core.move_note
        calls = []

        def flaky(*a, **kw):
            calls.append(a[2])
            if len(calls) == 2:
                raise RuntimeError("disk full")
            return real(*a, **kw)

        with unittest.mock.patch.object(core, "move_note", flaky):
            with self.assertRaises(RuntimeError):
                self.migrate("--apply", self.decisions_file())
        state = core.read_meta(self.p)["migration"]
        self.assertEqual(state["done"], ["1:folders", "1:hubs"])
        self.assertEqual(state["moves"], {"decisions/adr-0001-db.md": "architecture/adr/adr-0001-db.md",
                                          "decisions/legacy-queue.md": "architecture/adr/adr-0003-legacy-queue.md"})
        self.assertTrue((self.p / "architecture/adr/adr-0001-db.md").exists())
        self.assertTrue((self.p / "decisions/legacy-queue.md").exists())
        self.assertEqual(core.vault_schema(core.resolve(self.repo)), 1)
        self.assertEqual(json.loads(self.migrate("--plan", "--format", "json"))["status"], "resuming")
        out = self.migrate("--apply", self.decisions_file())
        self.assertIn("migrated", out)
        self.assertNotIn("adr-0001-db.md →", out)
        self.assertIn("move decisions/legacy-queue.md → architecture/adr/adr-0003-legacy-queue.md", out)
        db = fm.read((self.p / "architecture/adr/adr-0001-db.md").read_text())[0]
        self.assertEqual(db["aliases"], ["decisions/adr-0001-db"])
        self.assertEqual(core.vault_schema(core.resolve(self.repo)), 2)

    def test_lint_failure_keeps_schema_and_resumes(self):
        self.plan()
        write(self.p / "conventions/broken.md", fm.render(
            {"title": "Broken", "type": "convention", "status": "proposed", "summary": "x", "level": "should",
             "rule": "Do x.", "scope": []}, f"\n## Summary\nSee [[{L}modules/nope]].\n"))
        out = self.migrate("--apply", self.decisions_file(), expect=1)
        self.assertIn("lint errors", out)
        self.assertIn("broken link", out)
        self.assertEqual(core.vault_schema(core.resolve(self.repo)), 1)
        self.assertIn("1:move-adrs", core.read_meta(self.p)["migration"]["done"])
        write(self.p / "conventions/broken.md", (self.p / "conventions/broken.md").read_text()
              .replace("modules/nope", "modules/auth"))
        self.assertIn("migrated", self.migrate("--apply", self.decisions_file()))
        self.assertEqual(core.vault_schema(core.resolve(self.repo)), 2)

    def test_second_run_is_noop(self):
        self.plan()
        self.migrate("--apply", self.decisions_file())
        head = sh(self.vault, "git", "rev-parse", "HEAD")
        before = self.snapshot()
        self.assertEqual(self.migrate("--plan").strip(), "vault: demo is already at schema 2; nothing to migrate")
        self.assertEqual(self.plan()["status"], "up-to-date")
        self.assertIn("nothing to migrate", self.migrate("--apply", self.decisions_file()))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(sh(self.vault, "git", "rev-parse", "HEAD"), head)

    def test_dry_run_writes_nothing(self):
        before = self.snapshot()
        data = self.plan("--dry-run")
        self.assertEqual(data["status"], "dry-run")
        self.assertIn("create architecture/adr/", data["changes"])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.vault / ".work/demo" / migrate.PLAN_FILE).exists())
        self.plan()
        before = self.snapshot()
        res = json.loads(self.migrate("--apply", self.decisions_file(), "--dry-run", "--format", "json"))
        self.assertEqual(res["status"], "dry-run")
        self.assertIn("move decisions/legacy-queue.md → architecture/adr/adr-0003-legacy-queue.md "
                      "(links rewritten in 2 note(s))", res["changes"])
        self.assertIn("set kind: tooling on decisions/adr-0002-jest.md", res["changes"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(core.read_meta(self.p)["migration"]["moves"], {})


if __name__ == "__main__":
    unittest.main()
