"""Tests for the vault CLI. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent / "skills" / "vault"
sys.path.insert(0, str(SKILL / "lib"))

from vaultlib import cli, core  # noqa: E402
from vaultlib import frontmatter as fm  # noqa: E402
from vaultlib import gitutil as g  # noqa: E402

BIN = SKILL / "bin" / "vault"
REMOTE = "https://git-codecommit.eu-west-1.amazonaws.com/v1/repos/demo"


def sh(cwd, *args):
    p = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise AssertionError(f"{args} failed: {p.stderr}")
    return p.stdout.strip()


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class VaultTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vault-test-")).resolve()
        self.vault = self.tmp / "vault"
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        # Never let the user's real settings or environment leak in.
        self._env = {k: os.environ.get(k) for k in ("VAULT_ROOT", "CLAUDE_VAULT_DIR", "CLAUDE_CONFIG_DIR",
                                                    "XDG_CACHE_HOME", "CLAUDE_VAULT_HARNESS_DIR", "AGENT_VAULT_DIR",
                                                    "AGENT_VAULT_HARNESS_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
                                                    "CODEX_HOME")}
        os.environ.pop("CLAUDE_VAULT_DIR", None)
        os.environ.pop("AGENT_VAULT_DIR", None)
        os.environ.pop("AGENT_VAULT_HARNESS_DIR", None)
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdg-config")
        os.environ["XDG_DATA_HOME"] = str(self.tmp / "xdg-data")
        os.environ["CODEX_HOME"] = str(self.tmp / "codex-home")
        # Per-repo layouts look for _harness here, never in the real ~/claude-vault.
        os.environ["CLAUDE_VAULT_HARNESS_DIR"] = str(self.tmp / "harness-vault" / "_harness")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.tmp / "claude-config")
        os.environ["XDG_CACHE_HOME"] = str(self.tmp / "cache")
        os.environ["VAULT_ROOT"] = str(self.vault)
        sh(self.repo, "git", "init", "-q", "-b", "master")
        sh(self.repo, "git", "config", "user.email", "t@example.com")
        sh(self.repo, "git", "config", "user.name", "T")
        sh(self.repo, "git", "remote", "add", "origin", REMOTE)
        write(self.repo / "src/auth/login.ts",
              "export class AuthService {}\nexport function refreshToken() { return 1; }\n")
        write(self.repo / "src/auth/login.spec.ts", "test('x', () => {});\n")
        write(self.repo / "src/scan/scan.ts", "export const startScan = () => 0;\n")
        write(self.repo / "README.md", "# demo\n")
        self.commit("init")

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    # helpers
    def commit(self, msg):
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-q", "-m", msg)
        sh(self.repo, "git", "update-ref", "refs/remotes/origin/master", "HEAD")
        return sh(self.repo, "git", "rev-parse", "HEAD")

    def run_cli(self, *argv, cwd=None, expect=0):
        buf = io.StringIO()
        old = os.getcwd()
        os.chdir(cwd or self.repo)
        try:
            with contextlib.redirect_stdout(buf):
                try:
                    rc = cli.main(list(argv))
                except SystemExit as e:
                    rc = e.code if isinstance(e.code, int) else 1
                    if not isinstance(e.code, int) and e.code:
                        buf.write(str(e.code))
        finally:
            os.chdir(old)
        if expect is not None:
            self.assertEqual(rc, expect, buf.getvalue())
        return buf.getvalue()

    def init_project(self, p=None, L="demo/", *create_args):
        self.run_cli("resolve", "--create", *create_args)
        p = p or self.vault / "demo"
        write(p / "index.md", fm.render(
            {"title": "Demo", "type": "project", "status": "active",
             "summary": "Demo app: login and vehicle scanning."},
            f"\n## Summary\nDemo.\n\n## Core features\n- [[{L}features/login|Login]]\n- [[{L}guides/add-scan-step]]\n"))
        write(p / "features/login.md", fm.render(
            {"title": "Login", "type": "feature", "status": "active", "summary": "Cognito login and token refresh.",
             "aliases": ["sign in", "auth"], "code_refs": ["src/auth/login.ts"]},
            f"\n## Summary\nUsers sign in.\n\n## How it works\nSee [[{L}modules/auth]].\n"))
        write(p / "modules/auth.md", fm.render(
            {"title": "auth module", "type": "module", "status": "active",
             "summary": "Session handling and token refresh.", "code_refs": ["src/auth/login.ts"]},
            "\n## Summary\nToken refresh lives in `refreshToken()` of `AuthService`. Also `missingThing()`. Calls `startScan()` after login.\n"
            f"Back to [[{L}features/login]].\n"))
        write(p / "guides/add-scan-step.md", fm.render(
            {"title": "Add a scan step", "type": "guide", "status": "active", "summary": "How to add a scan step.",
             "code_refs": ["src/scan/scan.ts"]},
            "\n## Summary\nEdit `startScan` in the scan module.\n"))
        return p


class TestRemote(unittest.TestCase):
    def test_normalize(self):
        n = g.normalize_remote
        self.assertEqual(n("https://git-codecommit.eu-west-1.amazonaws.com/v1/repos/boom"),
                         "codecommit/eu-west-1/boom")
        self.assertEqual(n("ssh://APKA@git-codecommit.eu-west-1.amazonaws.com/v1/repos/Boom"),
                         "codecommit/eu-west-1/boom")
        self.assertEqual(n("codecommit::eu-west-1://prof@boom"), "codecommit/eu-west-1/boom")
        self.assertEqual(n("git@github.com:AmwayACS/amer-shop.git"), "github.com/amwayacs/amer-shop")
        self.assertEqual(n("https://user:tok@github.com/AmwayACS/amer-shop.git"), "github.com/amwayacs/amer-shop")
        self.assertEqual(n("ssh://git@github.pie.apple.com:22/dm/trifecta-ui.git"), "github.pie.apple.com/dm/trifecta-ui")
        self.assertEqual(n(""), "")


class TestFrontmatter(unittest.TestCase):
    TEXT = (
        "---\n"
        'title: "Scan: flow"\n'
        "type: feature   # a comment\n"
        "summary: 'It''s quoted'\n"
        'aliases: ["a, b", "c"]\n'
        "tags: [x, 'y z']\n"
        "code_refs:\n"
        "  - libs/a.ts\n"
        '  - "libs/b c.ts"\n'
        "verified: abc123\n"
        "---\n# Body\n"
    )

    def test_parse(self):
        d, body, has = fm.read(self.TEXT)
        self.assertTrue(has)
        self.assertEqual(d["title"], "Scan: flow")
        self.assertEqual(d["type"], "feature")
        self.assertEqual(d["summary"], "It's quoted")
        self.assertEqual(d["aliases"], ["a, b", "c"])
        self.assertEqual(d["tags"], ["x", "y z"])
        self.assertEqual(d["code_refs"], ["libs/a.ts", "libs/b c.ts"])
        self.assertEqual(body, "# Body\n")

    def test_set_field_preserves_and_replaces_block(self):
        t = fm.set_field(self.TEXT, "verified", "def456")
        self.assertIn("verified: def456", t)
        self.assertIn("type: feature   # a comment", t)
        t2 = fm.set_field(t, "code_refs", ["only.ts"])
        d, body, _ = fm.read(t2)
        self.assertEqual(d["code_refs"], ["only.ts"])
        self.assertEqual(d["verified"], "def456")
        self.assertEqual(body, "# Body\n")
        t3 = fm.set_field("# no fm\n", "verified", "x1")
        self.assertEqual(fm.read(t3)[0]["verified"], "x1")

    def test_render_roundtrip(self):
        fields = {"title": "A: b", "summary": 'Say "hi"', "aliases": ["x", "y"], "verified": "abc"}
        d, body, _ = fm.read(fm.render(fields, "\nbody\n"))
        self.assertEqual(d, fields)


class TestResolveAndCommit(VaultTestCase):
    def test_resolve_create_and_clone(self):
        out = json.loads(self.run_cli("resolve", "--create"))
        self.assertEqual(out["slug"], "demo")
        self.assertTrue(out["created"])
        self.assertTrue((self.vault / "demo/_meta.json").exists())
        self.assertTrue((self.vault / ".obsidian/app.json").exists())
        clone = self.tmp / "clone"
        sh(self.tmp, "git", "clone", "-q", str(self.repo), str(clone))
        sh(clone, "git", "remote", "set-url", "origin", "ssh://K@git-codecommit.eu-west-1.amazonaws.com/v1/repos/demo")
        out2 = json.loads(self.run_cli("resolve", cwd=clone))
        self.assertEqual(out2["slug"], "demo")
        self.assertTrue(out2["initialized"])
        plain = self.tmp / "plain"
        plain.mkdir()
        out3 = json.loads(self.run_cli("resolve", cwd=plain))
        self.assertIsNone(out3["slug"])

    def test_commit_stamps_hubs_log(self):
        p = self.init_project()
        out = self.run_cli("commit", "-m", "init", "--stamp-all", "--synced")
        self.assertIn("committed", out)
        head = sh(self.repo, "git", "rev-parse", "HEAD")
        self.assertEqual(core.load_note(p / "modules/auth.md", self.vault).verified, head[:12])
        self.assertEqual(core.read_meta(p)["last_synced"], head)
        hub = (p / "features/_index.md").read_text()
        self.assertIn("[[demo/features/login|Login]] — Cognito login", hub)
        self.assertIn("[[demo/index|demo]]", (self.vault / "index.md").read_text())
        self.assertIn("] init", (p / "log.md").read_text())
        self.assertEqual(sh(self.vault, "git", "log", "--format=%s", "-1"), "init")
        self.assertIn("nothing to commit", self.run_cli("commit", "-m", "again"))

    def test_commit_only_own_paths(self):
        self.init_project()
        write(self.vault / "other/_meta.json", "{}")
        write(self.vault / "other/x.md", "x")
        self.run_cli("commit", "-m", "init", "--stamp-all")
        tracked = sh(self.vault, "git", "ls-files")
        self.assertNotIn("other/x.md", tracked)
        self.assertIn("demo/modules/auth.md", tracked)


class TestDefaultBranch(VaultTestCase):
    def test_stale_origin_head_loses_to_newer_master(self):
        base = sh(self.repo, "git", "rev-parse", "HEAD")
        sh(self.repo, "git", "update-ref", "refs/remotes/origin/develop", base)
        sh(self.repo, "git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/develop")
        os.utime(self.repo / "README.md")
        write(self.repo / "README.md", "# demo v2\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-q", "-m", "newer", "--date", "2099-01-01T00:00:00")
        env = dict(os.environ, GIT_COMMITTER_DATE="2099-01-01T00:00:00")
        subprocess.run(["git", "commit", "-q", "--amend", "--no-edit"], cwd=self.repo, env=env, check=True)
        sh(self.repo, "git", "update-ref", "refs/remotes/origin/master", "HEAD")
        self.assertEqual(g.default_branch(self.repo), "master")
        sh(self.repo, "git", "update-ref", "-d", "refs/remotes/origin/master")
        self.assertEqual(g.default_branch(self.repo), "develop")


class TestSearch(VaultTestCase):
    def test_search_ranking_and_code(self):
        self.init_project()
        self.run_cli("commit", "-m", "init", "--stamp-all", "--synced")
        out = self.run_cli("search", "token", "refresh")
        self.assertIn("demo/modules/auth.md", out)
        self.assertNotIn("STALE", out)
        out = self.run_cli("search", "sign", "in")
        self.assertTrue(out.splitlines()[1].startswith("demo/features/login.md"), out)
        out = self.run_cli("search", "--code", "src/auth/login.ts")
        self.assertIn("demo/modules/auth.md", out)
        self.assertIn("demo/features/login.md", out)
        out = self.run_cli("search", "zzzunknown")
        self.assertIn("No notes match", out)
        out = self.run_cli("list", "--type", "guide")
        self.assertIn("demo/guides/add-scan-step.md", out)
        self.assertNotIn("modules/auth", out)

    def test_staleness_flow(self):
        p = self.init_project()
        self.run_cli("commit", "-m", "init", "--stamp-all", "--synced")
        write(self.repo / "src/auth/login.spec.ts", "test('y', () => {});\n")
        self.commit("spec only")
        self.assertNotIn("STALE", self.run_cli("search", "token"))
        write(self.repo / "src/auth/login.ts", "export class AuthService {}\nexport function refreshToken() {}\n// x\n")
        self.commit("change auth")
        out = self.run_cli("search", "token")
        self.assertIn("STALE", out)
        aff = json.loads(self.run_cli("affected"))
        stale = {s["note"] for s in aff["stale_notes"]}
        self.assertEqual(stale, {"demo/modules/auth.md", "demo/features/login.md"})
        self.assertEqual(aff["commits_since"], 2)
        self.run_cli("commit", "-m", "update", "--stamp", "modules/auth.md", "demo/features/login.md", "--synced")
        aff = json.loads(self.run_cli("affected"))
        self.assertEqual(aff["stale_notes"], [])
        self.assertIn("synced to", (p / "log.md").read_text())

    def test_rename_delete_uncovered_and_unknown_sha(self):
        p = self.init_project()
        self.run_cli("commit", "-m", "init", "--stamp-all", "--synced")
        sh(self.repo, "git", "mv", "src/scan/scan.ts", "src/scan/scanner.ts")
        write(self.repo / "src/new/feature.ts", "export const x = 1;\n")
        self.commit("rename + new")
        aff = json.loads(self.run_cli("affected"))
        self.assertEqual(aff["renames"][0]["from"], "src/scan/scan.ts")
        self.assertEqual(aff["renames"][0]["notes"], ["demo/guides/add-scan-step.md"])
        self.assertIn("src/new", aff["uncovered_changes"])
        missing = [s for s in aff["stale_notes"] if s["missing_refs"]]
        self.assertEqual(missing[0]["note"], "demo/guides/add-scan-step.md")
        path = p / "modules/auth.md"
        path.write_text(fm.set_field(path.read_text(), "verified", "deadbeefcafe"))
        aff = json.loads(self.run_cli("affected"))
        self.assertIn("demo/modules/auth.md", aff["unverified_notes"])


class TestIdentifiersCoverageInVault(VaultTestCase):
    def test_identifier_fallback_coverage_and_vault_cwd(self):
        p = self.init_project()
        write(self.repo / "src/util/helpers.ts", "export function computeChecksum() { return 0; }\n")
        self.commit("helpers")
        self.run_cli("commit", "-m", "init", "--stamp-all", "--synced")
        out = self.run_cli("search", "computeChecksum")
        self.assertIn("computeChecksum in src/util/helpers.ts → no note covers this file", out)
        out = self.run_cli("search", "startScan")
        self.assertIn("demo/guides/add-scan-step.md", out)
        out = self.run_cli("coverage", "--depth", "2")
        self.assertIn("src/util/", out)
        self.assertNotIn("src/auth/", out)
        out = json.loads(self.run_cli("resolve", cwd=p / "modules"))
        self.assertEqual(out["slug"], "demo")
        self.assertEqual(out["repo_root"], str(self.repo))


class TestLint(VaultTestCase):
    def test_lint_detects_issues(self):
        p = self.init_project()
        self.run_cli("commit", "-m", "init", "--stamp-all", "--synced")
        out = self.run_cli("lint", expect=0)
        self.assertIn("symbol `missingThing` not found", out)
        self.assertNotIn("refreshToken", out)
        self.assertNotIn("startScan", out)  # defined outside code_refs but exists in repo
        self.assertNotIn("demo/guides/add-scan-step.md", out)  # linked from index → not an orphan
        write(p / "problems/bug.md", fm.render(
            {"title": "Bug", "type": "problem", "status": "broken", "summary": "x",
             "code_refs": ["src/nope.ts", "src/**"]},
            "\nSee [[demo/features/nope]] and [[login]]. password = abcdefghijklmnopqrstuvwxyz123\n"))
        issues = json.loads(self.run_cli("lint", "--json", expect=1))
        msgs = [i["msg"] for i in issues if i["note"] == "demo/problems/bug.md"]
        joined = "\n".join(msgs)
        self.assertIn("broken link [[demo/features/nope]]", joined)
        self.assertIn("not vault-absolute", joined)
        self.assertIn("code_ref not found", joined)
        self.assertIn("broad glob", joined)
        self.assertIn("status 'broken'", joined)
        self.assertIn("possible secret", joined)
        self.assertIn("orphan", joined)
        self.assertIn("no '## Summary'", joined)


class TestHookAndTree(VaultTestCase):
    def hook(self, payload: str):
        env = dict(os.environ, VAULT_ROOT=str(self.vault))
        p = subprocess.run([sys.executable, str(BIN), "context", "--hook"], input=payload,
                           capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_hook(self):
        out = self.hook(json.dumps({"cwd": str(self.repo)}))
        self.assertIn("none for this repository yet", out)
        self.init_project()
        self.run_cli("commit", "-m", "init", "--stamp-all", "--synced")
        data = json.loads(self.hook(json.dumps({"cwd": str(self.repo), "source": "startup"})))
        ctx = data["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Demo app: login and vehicle scanning.", ctx)
        self.assertIn("up to date", ctx)
        self.assertIn("`vault search <question>`", ctx)
        self.assertLess(len(ctx), 6001)
        self.assertEqual(self.hook(json.dumps({"cwd": str(self.repo), "agent_type": "vault-keeper"})), "")
        self.assertEqual(self.hook("{not json"), "")
        self.assertEqual(self.hook(json.dumps({"cwd": str(self.tmp)})), "")
        write(self.vault / "demo/_meta.json", "{broken")
        self.assertEqual(self.hook(json.dumps({"cwd": str(self.repo)})).count("additionalContext"), 1)

    def test_tree_when_on_task_branch(self):
        self.init_project()
        self.run_cli("commit", "-m", "init", "--stamp-all", "--synced")
        target = sh(self.repo, "git", "rev-parse", "HEAD")
        sh(self.repo, "git", "checkout", "-q", "-b", "123-to-master")
        write(self.repo / "src/wip.ts", "wip\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-q", "-m", "wip")
        info = json.loads(self.run_cli("resolve"))
        self.assertFalse(info["head_matches_target"])
        self.assertIsNone(info["code_root"])
        tree = Path(self.run_cli("tree").strip())
        self.assertEqual(sh(tree, "git", "rev-parse", "HEAD"), target)
        self.assertFalse((tree / "src/wip.ts").exists())
        info = json.loads(self.run_cli("resolve"))
        self.assertEqual(info["code_root"], str(tree))
        self.assertIn("removed", self.run_cli("tree", "--remove"))
        self.assertFalse(tree.exists())


class TestVaultDir(VaultTestCase):
    def settings(self, path: Path, value, extra=None):
        write(path, json.dumps({**(extra or {}), "env": {"CLAUDE_VAULT_DIR": value}}))

    def test_precedence(self):
        os.environ.pop("VAULT_ROOT")
        user = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json"
        self.assertEqual(core.dir_setting(self.repo), ("vault", "default"))
        self.settings(user, "~/u/{project}")
        self.assertEqual(core.dir_setting(self.repo), ("~/u/{project}", str(user)))
        os.environ["VAULT_ROOT"] = "/legacy"
        self.assertEqual(core.dir_setting(self.repo), ("/legacy/{project}", "env VAULT_ROOT"))
        os.environ["CLAUDE_VAULT_DIR"] = "/env/{project}"
        self.assertEqual(core.dir_setting(self.repo), ("/env/{project}", "env CLAUDE_VAULT_DIR"))
        self.settings(self.repo / ".claude/settings.json", "docs/kb")
        self.assertEqual(core.dir_setting(self.repo)[0], "docs/kb")
        write(self.repo / ".claude/settings.local.json", "{broken")  # invalid JSON is ignored
        self.assertEqual(core.dir_setting(self.repo)[0], "docs/kb")
        self.settings(self.repo / ".claude/settings.local.json", "kb-local")
        self.assertEqual(core.dir_setting(self.repo),
                         ("kb-local", str(self.repo / ".claude/settings.local.json")))
        self.assertEqual(core.dir_setting(self.repo, "cli")[0], "cli")
        out = json.loads(self.run_cli("resolve"))
        self.assertEqual(out["vault_dir"], str(self.repo / "kb-local"))
        self.assertEqual(out["layout"], "repo")

    def test_default_in_repo_layout(self):
        os.environ.pop("VAULT_ROOT")
        p = self.repo / "vault"
        self.init_project(p, "")
        info = json.loads(self.run_cli("resolve"))
        self.assertEqual((info["vault_dir"], info["vault_root"], info["layout"], info["link_prefix"]),
                         (str(p), str(p), "repo", ""))
        self.assertEqual((info["dir_setting"], info["dir_source"], info["vault_git"]), ("vault", "default", "host-repo"))
        self.assertNotIn(str(self.repo), info["work_dir"])
        self.assertTrue(info["work_dir"].startswith(str(self.tmp / "cache/agent-vault/demo-")))
        self.assertFalse((p / ".git").exists())
        head = sh(self.repo, "git", "rev-parse", "HEAD")
        out = self.run_cli("commit", "-m", "init", "--stamp-all", "--synced")
        self.assertIn("not committed", out)
        self.assertEqual(sh(self.repo, "git", "rev-parse", "HEAD"), head)
        self.assertIn("vault/", sh(self.repo, "git", "status", "--porcelain"))
        self.assertIn("nothing to commit", self.run_cli("commit", "-m", "again"))
        self.assertEqual((p / "log.md").read_text().count("] "), 1)
        self.assertIn("[[features/login|Login]] — Cognito login", (p / "features/_index.md").read_text())
        self.assertFalse((self.repo / "index.md").exists())
        self.assertEqual(core.load_note(p / "modules/auth.md", p, p).verified, head[:12])
        out = self.run_cli("lint")
        self.assertNotIn("broken link", out)
        self.assertNotIn("missing project index", out)
        self.assertNotIn("lives where", out)
        self.assertIn("symbol `missingThing` not found", out)
        out = self.run_cli("search", "token", "refresh")
        self.assertIn("modules/auth.md  [module", out)
        self.assertNotIn("STALE", out)
        self.assertIn("modules/auth.md", self.run_cli("search", "--all", "token"))
        self.assertIn("Vault:    repo layout, inside the code repo", self.run_cli("status"))
        write(p / "guides/x.md", fm.render({"title": "X", "type": "guide", "summary": "x"},
                                           "\n## Summary\nSee [[demo/modules/auth]], [[auth]] and [[modules/nope]].\n"))
        issues = json.loads(self.run_cli("lint", "--json", expect=1))
        msgs = "\n".join(i["msg"] for i in issues if i["note"] == "guides/x.md")
        self.assertIn("link [[demo/modules/auth]] is not vault-absolute; use [[modules/auth]]", msgs)
        self.assertIn("link [[auth]] is not vault-absolute; use [[modules/auth]]", msgs)
        self.assertIn("broken link [[modules/nope]]", msgs)
        out = self.run_cli("commit", "-m", "guide")
        self.assertIn("not committed", out)
        self.assertEqual(sh(self.repo, "git", "rev-parse", "HEAD"), head)
        # Running from inside the vault dir still resolves the project.
        self.assertEqual(json.loads(self.run_cli("resolve", cwd=p / "modules"))["slug"], "demo")
        tree = Path(self.run_cli("tree", "--force").strip())
        self.assertTrue(str(tree).startswith(info["work_dir"]))
        self.assertEqual(sh(tree, "git", "rev-parse", "HEAD"), head)
        self.assertIn("removed", self.run_cli("tree", "--remove"))

    def test_create_saves_setting(self):
        os.environ.pop("VAULT_ROOT")
        local = self.repo / ".agent-vault.local.json"
        write(local, json.dumps({"other": {"keep": True}}))
        kb = str(self.tmp / "kbs/{project}")
        out = json.loads(self.run_cli("resolve", "--create", "--vault-dir", kb))
        self.assertEqual(out["saved_setting"], str(local))
        self.assertEqual(out["vault_dir"], str(self.tmp / "kbs/demo"))
        data = json.loads(local.read_text())
        self.assertEqual(data, {"other": {"keep": True}, "vault_dir": kb})
        self.assertTrue(local.read_text().endswith("}\n"))
        self.assertIn("/.agent-vault.local.json", (self.repo / ".git/info/exclude").read_text().splitlines())
        out = json.loads(self.run_cli("resolve"))
        self.assertTrue(out["initialized"])
        self.assertEqual((out["layout"], out["link_prefix"], out["dir_source"]), ("shared", "demo/", str(local)))
        out = json.loads(self.run_cli("resolve", "--create", "--vault-dir", kb))
        self.assertIsNone(out["saved_setting"])
        self.run_cli("resolve", "--create", "--vault-dir", "other", "--save", "project")
        self.assertEqual(json.loads((self.repo / ".agent-vault.json").read_text()), {"vault_dir": "other"})

    def test_project_in_middle_of_template(self):
        tmpl = str(self.tmp / "kb/{project}/notes")
        p = self.tmp / "kb/demo/notes"
        self.init_project(p, "", "--vault-dir", tmpl, "--save", "user")
        user = json.loads((Path(os.environ["XDG_CONFIG_HOME"]) / "agent-vault/config.json").read_text())
        self.assertEqual(user["vault_dir"], tmpl)
        os.environ.pop("VAULT_ROOT")
        info = json.loads(self.run_cli("resolve"))
        self.assertEqual((info["vault_dir"], info["layout"], info["vault_git"]), (str(p), "repo", "own"))
        self.assertTrue((p / ".git").exists())
        out = self.run_cli("commit", "-m", "init", "--stamp-all", "--synced")
        self.assertIn("committed", out)
        self.assertNotIn("not committed", out)
        self.assertIn("modules/auth.md", sh(p, "git", "ls-files"))
        self.assertEqual(sh(p, "git", "log", "--format=%s", "-1"), "init")
        self.assertEqual(json.loads(self.run_cli("resolve", cwd=p / "modules"))["repo_root"], str(self.repo))
        hook = cli._hook_text(core.resolve(self.repo))
        self.assertIn(f"Never read or edit files under {p}", hook)


def lesson_text(title="Run related tests", rule="Run jest --findRelatedTests, not the whole suite.", **fields):
    base = {"title": title, "type": "lesson", "status": "active", "summary": f"{title} summary.",
            "aliases": [], "scope": "project", "audience": ["implementer", "qa"], "category": "efficiency",
            "occurrences": "1", "first_seen": "2026-10-01", "last_seen": core.today(), "evidence": [],
            "proposal_status": "none", "proposal_kind": "none", "proposal_target": "", "promoted_to": "",
            "code_refs": [], "verified": ""}
    base.update(fields)
    return fm.render(base, f"\n## Summary\n{title}.\n\n## Rule\n{rule}\n\n## Why\nIt was slow.\n\n## Evidence\n\n"
                           f"## Proposed change\n\n## Related\n")


def retro_text(date="2026-10-13", **fields):
    base = {"title": f"Retro {date}", "type": "retro", "status": "recorded", "summary": f"PASS on {date}.",
            "aliases": [], "session": "s1", "run": "r1", "date": date, "branch": "b", "change_size": "medium",
            "qa_scope": "browser", "review_tier": "standard", "attempts": "2", "outcome": "passed",
            "lessons": [], "code_refs": [], "verified": ""}
    base.update(fields)
    return fm.render(base, "\n## Summary\nWidget retry went fine.\n\n## Went well\n\n## Went badly\n")


class TestLessonsAndRetros(VaultTestCase):
    def setUp(self):
        super().setUp()
        self.p = self.init_project()

    def lesson(self, name, project=None, **fields):
        path = (project or self.p) / "lessons" / f"{name}.md"
        write(path, lesson_text(**fields))
        return path

    def harness(self):
        out = json.loads(self.run_cli("resolve", "--project", "_harness", "--create"))
        h = Path(out["vault_dir"])
        write(h / "index.md", fm.render({"title": "Harness", "type": "project", "status": "active",
                                         "summary": "Cross-project lessons about how Claude works for this user"},
                                        "\n## Summary\nHarness.\n"))
        return h

    def test_types_folders_and_search(self):
        self.assertTrue((self.p / "lessons").is_dir())
        self.assertTrue((self.p / "retros").is_dir())
        write(self.p / "retros/2026-10-13-widget.md", retro_text())
        self.lesson("widget-tests", title="Widget tests")
        out = self.run_cli("search", "widget")
        self.assertIn("demo/lessons/widget-tests.md  [lesson, active]", out)
        self.assertNotIn("retros/", out)
        out = self.run_cli("search", "--type", "retro", "widget")
        self.assertIn("demo/retros/2026-10-13-widget.md  [retro, recorded]", out)
        self.assertNotIn("lessons/", out)

    def test_lint_rules(self):
        self.lesson("ok")
        self.lesson("bad", title="Bad one", occurrences="0", scope="global", category="speed",
                    audience=["implementer", "boss"], proposal_status="maybe", proposal_kind="rewrite")
        self.lesson("noint", title="No int", occurrences="two")
        self.lesson("old", title="Old one", last_seen="2020-01-01")
        self.lesson("retired-old", title="Retired old", status="retired", last_seen="2020-01-01")
        write(self.p / "retros/2026-10-13-x.md", retro_text())
        write(self.p / "retros/2026-10-14-y.md", retro_text("2026-10-14", outcome="exploded"))
        issues = json.loads(self.run_cli("lint", "--json", expect=1))
        by = {}
        for i in issues:
            by.setdefault(i["note"], []).append((i["level"], i["msg"]))
        bad = by["demo/lessons/bad.md"]
        self.assertIn("error", [lv for lv, m in bad if m.startswith("occurrences must be")])
        for key in ("scope 'global'", "category 'speed'", "audience 'boss'", "proposal_status 'maybe'",
                    "proposal_kind 'rewrite'"):
            self.assertTrue(any(lv == "warn" and m.startswith(key) for lv, m in bad), (key, bad))
        self.assertFalse(any("audience 'implementer'" in m for _, m in bad))
        self.assertTrue(any(lv == "error" and m.startswith("occurrences") for lv, m in by["demo/lessons/noint.md"]))
        self.assertIn(("warn", "stale lesson: last seen 2020-01-01; retire or confirm"), by["demo/lessons/old.md"])
        self.assertFalse(any("stale lesson" in m for _, m in by.get("demo/lessons/retired-old.md", [])))
        ok = [m for _, m in by.get("demo/lessons/ok.md", [])]
        self.assertEqual([m for m in ok if not m.startswith("orphan")], [])
        self.assertTrue(any(m.startswith("orphan") for m in ok))  # lessons still need inbound links
        self.assertNotIn("demo/retros/2026-10-13-x.md", by)  # retros are exempt from the orphan warning
        self.assertIn(("warn", "outcome 'exploded' not valid (passed, accepted, abandoned, skipped)"),
                      by["demo/retros/2026-10-14-y.md"])

    def test_hub_ordering(self):
        self.lesson("a", title="A few", occurrences="2", last_seen="2026-10-01")
        self.lesson("b", title="B most", occurrences="5", last_seen="2026-09-01")
        self.lesson("c", title="C few newer", occurrences="2", last_seen="2026-10-10")
        write(self.p / "retros/2026-10-01-old.md", retro_text("2026-10-01"))
        write(self.p / "retros/2026-10-13-new.md", retro_text("2026-10-13"))
        self.run_cli("commit", "-m", "lessons")
        hub = (self.p / "lessons/_index.md").read_text()
        order = [hub.index(f"demo/lessons/{x}|") for x in "bca"]
        self.assertEqual(order, sorted(order), hub)
        rhub = (self.p / "retros/_index.md").read_text()
        self.assertLess(rhub.index("2026-10-13-new"), rhub.index("2026-10-01-old"))
        self.assertNotIn("(recorded)", rhub)

    def test_lessons_command(self):
        h = self.harness()
        self.lesson("one", title="One", rule="Rule one.", occurrences="1")
        self.lesson("three", title="Three", rule="Rule three.", occurrences="3", last_seen="2026-10-01")
        self.lesson("three-newer", title="Three newer", rule="Rule three newer.", occurrences="3",
                    last_seen="2026-10-05", audience=["reviewer"])
        self.lesson("retired", title="Retired", rule="Never shown.", status="retired", occurrences="9")
        self.lesson("prop", title="Prop", rule="Rule prop.", occurrences="2", proposal_status="open",
                    proposal_kind="repo-change", proposal_target=".claude/verify.json")
        for i in range(3):
            self.lesson(f"h{i}", project=h, title=f"H{i}", rule=f"Harness rule {i}.", occurrences=str(5 - i),
                        scope="harness")
        out = self.run_cli("lessons").splitlines()
        self.assertEqual(out[:4], [
            "- Rule three newer. (×3, project) [[demo/lessons/three-newer]]",
            "- Rule three. (×3, project) [[demo/lessons/three]]",
            "- Rule prop. (×2, project) [[demo/lessons/prop]]",
            "- Rule one. (×1, project) [[demo/lessons/one]]",
        ])
        self.assertEqual(out[4:], ["- Harness rule 0. (×5, harness) [[_harness/lessons/h0]]",
                                   "- Harness rule 1. (×4, harness) [[_harness/lessons/h1]]",
                                   "- Harness rule 2. (×3, harness) [[_harness/lessons/h2]]"])
        out = self.run_cli("lessons", "--limit", "3").splitlines()
        self.assertEqual(len(out), 3 + 2)  # harness gets ceil(3/2)
        out = self.run_cli("lessons", "--audience", "reviewer", "--no-harness").splitlines()
        self.assertEqual(out, ["- Rule three newer. (×3, project) [[demo/lessons/three-newer]]"])
        data = json.loads(self.run_cli("lessons", "--format", "json", "--limit", "1", "--no-harness"))
        self.assertEqual(data[0]["link"], "demo/lessons/three-newer")
        self.assertEqual(set(data[0]), {"link", "path", "title", "rule", "occurrences", "last_seen", "scope", "audience",
                                        "category", "proposal_status", "proposal_kind", "proposal_target"})
        self.assertEqual(data[0]["occurrences"], 3)
        self.lesson("hp", project=h, title="HP", scope="harness", proposal_status="open",
                    proposal_kind="rule-promotion", proposal_target="~/.claude/CLAUDE.md")
        out = self.run_cli("lessons", "--proposals").splitlines()
        self.assertEqual(out, [
            "- [[demo/lessons/prop]] — repo-change → .claude/verify.json: Prop summary.",
            "- [[_harness/lessons/hp]] — rule-promotion → ~/.claude/CLAUDE.md: HP summary.",
        ])
        self.assertEqual(self.run_cli("lessons", "--audience", "triage", "--no-harness"), "")
        plain = self.tmp / "plain"
        plain.mkdir()
        out = self.run_cli("lessons", cwd=plain).splitlines()  # not initialized → harness only
        self.assertTrue(out and all("[[_harness/" in ln for ln in out), out)

    def test_covering(self):
        self.run_cli("commit", "-m", "init", "--stamp-all", "--synced")
        out = self.run_cli("covering", "src/auth/login.ts", str(self.repo / "src/scan/scan.ts"))
        lines = out.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("- [[demo/features/login]] — Login: Cognito login and token refresh.", lines)
        self.assertIn("- [[demo/guides/add-scan-step]] — Add a scan step: How to add a scan step.", lines)
        self.assertEqual(len(self.run_cli("covering", "src/auth/login.ts", "--limit", "1").splitlines()), 1)
        write(self.repo / "src/auth/login.ts", "export class AuthService {}\nexport function refreshToken() {}\n//x\n")
        self.commit("auth change")
        data = json.loads(self.run_cli("covering", "src/auth/login.ts", "--format", "json"))
        self.assertTrue(all(d["stale"] for d in data))
        self.assertIn("(stale)", self.run_cli("covering", "src/auth/login.ts"))
        self.assertEqual(self.run_cli("covering", "README.md"), "")

    def test_proposal_accept_reject(self):
        h = self.harness()
        self.lesson("promo", title="Promo", occurrences="3", proposal_status="open", proposal_kind="rule-promotion",
                    proposal_target="~/.claude/CLAUDE.md#Verify loop")
        self.lesson("chg", title="Chg", proposal_status="open", proposal_kind="repo-change",
                    proposal_target=".claude/verify.json")
        self.lesson("hx", project=h, title="Hx", scope="harness", proposal_status="open",
                    proposal_kind="harness-change", proposal_target="~/.claude/skills/x/SKILL.md")
        self.run_cli("commit", "-m", "init")
        out = self.run_cli("proposal", "accept", "[[demo/lessons/promo]]")
        self.assertEqual(out.strip(), "proposal accepted: demo/lessons/promo")
        d = fm.read((self.p / "lessons/promo.md").read_text())[0]
        self.assertEqual((d["proposal_status"], d["status"], d["promoted_to"]),
                         ("applied", "promoted", "~/.claude/CLAUDE.md#Verify loop"))
        self.assertEqual(sh(self.vault, "git", "log", "--format=%s", "-1"), "proposal: accept demo/lessons/promo")
        self.assertEqual(sh(self.vault, "git", "status", "--porcelain", "--", "demo"), "")
        out = self.run_cli("proposal", "accept", "lessons/chg.md")
        d = fm.read((self.p / "lessons/chg.md").read_text())[0]
        self.assertEqual((d["proposal_status"], d["status"]), ("applied", "active"))
        out = self.run_cli("proposal", "reject", "_harness/lessons/hx")
        self.assertEqual(out.strip(), "proposal rejected: _harness/lessons/hx")
        self.assertEqual(fm.read((h / "lessons/hx.md").read_text())[0]["proposal_status"], "rejected")
        self.assertEqual(sh(self.vault, "git", "log", "--format=%s", "-1"), "proposal: reject _harness/lessons/hx")
        self.assertIn("no open proposal", self.run_cli("proposal", "reject", "demo/lessons/promo", expect=1))
        self.assertIn("not found", self.run_cli("proposal", "accept", "demo/lessons/nope", expect=1))

    def hook(self, payload: dict, env_extra=None):
        env = dict(os.environ, VAULT_ROOT=str(self.vault), **(env_extra or {}))
        p = subprocess.run([sys.executable, str(BIN), "context", "--hook"], input=json.dumps(payload),
                           capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_context_hook_lessons_block(self):
        h = self.harness()
        self.run_cli("commit", "-m", "init", "--stamp-all", "--synced")
        self.lesson("impl", title="Impl", rule="Implementer rule.", occurrences="3")
        self.lesson("qa-only", title="QA only", rule="QA rule.", audience=["qa"])
        self.lesson("promoted", title="Promoted", rule="Promoted rule.", status="promoted", occurrences="7")
        for i in range(6):
            self.lesson(f"orch{i}", title=f"Orch {i}", rule=f"Orchestrator rule {i}. " + "x" * 30,
                        audience=["orchestrator"], occurrences="2")
        for i in range(4):
            self.lesson(f"h{i}", project=h, title=f"H{i}", rule=f"Harness rule {i}.", scope="harness",
                        occurrences=str(9 - i), proposal_status="open" if i == 0 else "none",
                        proposal_kind="harness-change" if i == 0 else "none")
        data = json.loads(self.hook({"cwd": str(self.repo), "source": "startup"}))
        ctx = data["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(ctx), 6000)
        self.assertIn("Demo app: login and vehicle scanning.", ctx)
        block = ctx[ctx.index("Lessons from past retros (follow them):"):]
        self.assertLessEqual(len(block), 1000)
        lines = block.splitlines()
        self.assertEqual(lines[1], "- Implementer rule. (×3) [[demo/lessons/impl]]")
        project_lines = lines[1:lines.index("Harness lessons:")]
        self.assertEqual(len(project_lines), 5)
        self.assertNotIn("QA rule.", block)
        self.assertNotIn("Promoted rule.", block)
        harness_lines = lines[lines.index("Harness lessons:") + 1:-1]
        self.assertEqual(harness_lines, ["- Harness rule 0. (×9)", "- Harness rule 1. (×8)", "- Harness rule 2. (×7)"])
        self.assertEqual(lines[-1], "Pending: 1 harness/repo change proposal(s) await your approval → "
                                    "`vault lessons --proposals`, then `vault proposal accept|reject <note>`")
        # Huge index summary: the total still fits.
        idx = self.p / "index.md"
        idx.write_text(fm.set_field(idx.read_text(), "summary", "y" * 7000))
        ctx = json.loads(self.hook({"cwd": str(self.repo)}))["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(ctx), 6000)
        self.assertIn("Pending: 1", ctx)
        # Non-git cwd: harness-only block.
        plain = self.tmp / "plain"
        plain.mkdir()
        ctx = json.loads(self.hook({"cwd": str(plain)}))["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(ctx.startswith("Lessons from past retros (follow them):\nHarness lessons:\n"), ctx)
        self.assertNotIn("demo/lessons", ctx)
        # Verify and vault agents get nothing.
        for agent in ("verify-reviewer", "verify-qa", "vault-keeper"):
            self.assertEqual(self.hook({"cwd": str(self.repo), "agent_type": agent}), "")

    def test_context_hook_without_lessons_unchanged(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertEqual(self.hook({"cwd": str(plain)}), "")
        ctx = json.loads(self.hook({"cwd": str(self.repo)}))["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("Lessons from past retros", ctx)


class TestHarness(VaultTestCase):
    def test_create_without_repo_shared(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        out = json.loads(self.run_cli("resolve", "--project", "_harness", "--create", cwd=plain))
        self.assertEqual((out["slug"], out["vault_dir"], out["repo_root"]),
                         ("_harness", str(self.vault / "_harness"), None))
        meta = core.read_meta(self.vault / "_harness")
        self.assertEqual((meta.get("paths", []), meta["remotes"]), ([], []))
        self.assertTrue((self.vault / "_harness/lessons").is_dir())

    def test_create_from_repo_never_registers(self):
        out = json.loads(self.run_cli("resolve", "--project", "_harness", "--create"))
        self.assertTrue(out["created"])
        self.assertIsNone(out["repo_root"])
        meta = core.read_meta(self.vault / "_harness")
        self.assertEqual((meta.get("paths", []), meta["remotes"]), ([], []))
        self.assertNotIn("default_branch", meta)
        write(self.vault / "_harness/index.md", fm.render({"title": "H", "type": "project", "summary": "h"},
                                                          "\n## Summary\nh\n"))
        self.run_cli("commit", "--project", "_harness", "-m", "harness")
        meta = core.read_meta(self.vault / "_harness")
        self.assertEqual((meta.get("paths", []), meta["remotes"]), ([], []))
        # The repo still resolves to its own (not yet created) project, never to _harness.
        out = json.loads(self.run_cli("resolve"))
        self.assertEqual((out["slug"], out["initialized"]), ("demo", False))

    def test_per_repo_layout_uses_harness_dir(self):
        os.environ.pop("VAULT_ROOT")
        hdir = Path(os.environ["CLAUDE_VAULT_HARNESS_DIR"])
        out = json.loads(self.run_cli("resolve", "--project", "_harness", "--create"))
        self.assertEqual((out["vault_dir"], out["layout"], out["link_prefix"]), (str(hdir), "shared", "_harness/"))
        self.assertTrue((hdir / "_meta.json").exists())
        self.assertFalse((self.repo / "vault").exists())
        h = core.harness_ctx(core.resolve(self.repo))
        self.assertEqual((h.project_dir, h.matched), (hdir, True))
        os.environ["CLAUDE_VAULT_HARNESS_DIR"] = str(self.tmp / "hroot")  # a root that holds _harness
        self.assertEqual(core.harness_ctx(core.resolve(self.repo)).project_dir, self.tmp / "hroot/_harness")
        self.assertIsNone(core.harness_ctx(core.resolve(self.repo, "_harness")))


class TestGraph(VaultTestCase):
    def graph(self, *extra):
        out_file = self.tmp / "out" / "graph.html"
        from unittest import mock
        with mock.patch.object(cli.webbrowser, "open") as opened:
            out = self.run_cli("graph", "--no-open", "--out", str(out_file), *extra)
        opened.assert_not_called()
        self.assertIn(str(out_file), out.splitlines())
        html = out_file.read_text(encoding="utf-8")
        self.assertNotIn(cli.GRAPH_PLACEHOLDER, html)
        start = html.index("const DATA = ") + len("const DATA = ")
        data, _ = json.JSONDecoder().raw_decode(html[start:])
        return html, data

    def test_graph_data(self):
        p = self.init_project()
        write(p / "problems/bug.md", fm.render(
            {"title": "Bug", "type": "problem", "status": "open", "summary": "A bug.",
             "code_refs": ["src/auth/login.ts"]},
            "\n## Summary\nSee [[demo/modules/auth]] and [[demo/modules/auth|again]] and [[demo/nope]].\n"
            "`[[demo/in-code]]`\n</script><script>alert(1)</script>\n"))
        self.run_cli("commit", "-m", "init", "--stamp-all", "--synced")
        html, data = self.graph()
        self.assertNotIn("</script><script>alert(1)", html)
        notes = {n["id"]: n for n in data["nodes"] if n["kind"] == "note"}
        self.assertIn("demo/index", notes)
        self.assertFalse(any(n["type"] in ("hub", "log") for n in notes.values()))
        self.assertIn("</script>", notes["demo/problems/bug"]["body"])
        self.assertEqual(notes["demo/problems/bug"]["broken_links"], ["demo/nope"])
        links = [(e["source"], e["target"]) for e in data["edges"] if e["kind"] == "link"]
        self.assertEqual(links.count(("demo/problems/bug", "demo/modules/auth")), 1)
        self.assertIn(("demo/features/login", "demo/modules/auth"), links)
        code = {n["id"]: n for n in data["nodes"] if n["kind"] == "code"}
        self.assertEqual(set(code), {"code:src/auth/login.ts", "code:src/scan/scan.ts"})
        refs = {(e["source"], e["target"]) for e in data["edges"] if e["kind"] == "ref"}
        self.assertIn(("demo/problems/bug", "code:src/auth/login.ts"), refs)
        self.assertEqual(notes["demo/modules/auth"]["state"], "ok")
        self.assertTrue(data["meta"]["stale_checked"])

        write(self.repo / "src/auth/login.ts", "export class AuthService {}\nexport function refreshToken() {}\n// x\n")
        self.commit("change auth")
        _, data = self.graph()
        notes = {n["id"]: n for n in data["nodes"] if n["kind"] == "note"}
        self.assertEqual(notes["demo/modules/auth"]["state"], "stale")
        self.assertEqual(notes["demo/modules/auth"]["changed"], ["src/auth/login.ts"])
        self.assertEqual(notes["demo/guides/add-scan-step"]["state"], "ok")

        _, data = self.graph("--no-stale")
        self.assertFalse(data["meta"]["stale_checked"])
        self.assertTrue(all("state" not in n for n in data["nodes"]))

    def test_graph_default_path_and_no_vault(self):
        out = self.run_cli("graph", "--no-open", expect=None)
        self.assertNotIn("graph.html", out)
        self.init_project()
        out = self.run_cli("graph", "--no-open")
        path = Path(out.splitlines()[-1])
        self.assertEqual(path, (self.vault / ".work" / "demo" / "graph.html").resolve())
        self.assertTrue(path.is_file())


class TestLock(unittest.TestCase):
    def test_stale_lock_is_reclaimed(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            lock_dir = tmp / ".lock"
            lock_dir.mkdir()
            (lock_dir / "pid").write_text("999999")
            with core.Lock(tmp, timeout=2):
                self.assertTrue((tmp / ".lock").exists())
            self.assertFalse((tmp / ".lock").exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
