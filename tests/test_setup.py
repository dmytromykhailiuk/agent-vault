"""Tests for config precedence, setup/doctor, the session hook and packaging. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from test_vault import BIN, SKILL, VaultTestCase, write

from vaultlib import core, setup  # noqa: E402
from vaultlib import frontmatter as fm  # noqa: E402

try:
    import tomllib
except ImportError:
    tomllib = None

ROOT = SKILL.parent.parent
KEEPER = "vault-keeper"
REVIEWER = "vault-reviewer"


def read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class SetupBase(VaultTestCase):
    def setUp(self):
        super().setUp()
        self._home = os.environ.get("HOME")
        self.claude = self.tmp / "claude-config"
        self.codex = self.tmp / "codex-home"
        self.cli = str(SKILL / "bin" / "vault")

    def tearDown(self):
        if self._home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._home
        super().tearDown()

    def settings(self):
        return read_json(self.claude / "settings.json")


class DirSettingPrecedence(SetupBase):
    def test_local_beats_project_beats_claude_settings(self):
        write(self.repo / ".claude/settings.json", json.dumps({"env": {"CLAUDE_VAULT_DIR": "from-claude-settings"}}))
        self.assertEqual(core.dir_setting(self.repo), ("from-claude-settings", str(self.repo / ".claude/settings.json")))
        write(self.repo / ".claude/settings.local.json", json.dumps({"env": {"CLAUDE_VAULT_DIR": "from-claude-local"}}))
        self.assertEqual(core.dir_setting(self.repo)[0], "from-claude-local")
        write(self.repo / ".agent-vault.json", json.dumps({"vault_dir": "from-project"}))
        self.assertEqual(core.dir_setting(self.repo), ("from-project", str(self.repo / ".agent-vault.json")))
        write(self.repo / ".agent-vault.local.json", json.dumps({"vault_dir": "from-local"}))
        self.assertEqual(core.dir_setting(self.repo), ("from-local", str(self.repo / ".agent-vault.local.json")))

    def test_explicit_beats_everything(self):
        write(self.repo / ".agent-vault.local.json", json.dumps({"vault_dir": "x"}))
        self.assertEqual(core.dir_setting(self.repo, "explicit"), ("explicit", "--vault-dir"))

    def test_agent_vault_dir_beats_claude_vault_dir(self):
        os.environ["CLAUDE_VAULT_DIR"] = "/legacy"
        self.assertEqual(core.dir_setting(None), ("/legacy", "env CLAUDE_VAULT_DIR"))
        os.environ["AGENT_VAULT_DIR"] = "/new"
        self.assertEqual(core.dir_setting(None), ("/new", "env AGENT_VAULT_DIR"))

    def test_env_beats_vault_root_and_vault_root_gets_project_suffix(self):
        self.assertEqual(core.dir_setting(None), (str(self.vault) + "/{project}", "env VAULT_ROOT"))
        os.environ["CLAUDE_VAULT_DIR"] = "/legacy"
        self.assertEqual(core.dir_setting(None)[0], "/legacy")

    def test_repo_config_beats_env(self):
        os.environ["AGENT_VAULT_DIR"] = "/env"
        write(self.repo / ".claude/settings.json", json.dumps({"env": {"CLAUDE_VAULT_DIR": "repo-claude"}}))
        self.assertEqual(core.dir_setting(self.repo)[0], "repo-claude")

    def test_xdg_user_config_beats_claude_user_settings(self):
        os.environ.pop("VAULT_ROOT")
        self.assertEqual(core.dir_setting(None), (core.DEFAULT_DIR, "default"))
        write(self.claude / "settings.json", json.dumps({"env": {"CLAUDE_VAULT_DIR": "from-claude-user"}}))
        self.assertEqual(core.dir_setting(None)[0], "from-claude-user")
        write(self.tmp / "xdg-config/agent-vault/config.json", json.dumps({"vault_dir": "from-xdg"}))
        self.assertEqual(core.dir_setting(None), ("from-xdg", str(self.tmp / "xdg-config/agent-vault/config.json")))

    def test_invalid_json_is_ignored(self):
        write(self.repo / ".agent-vault.local.json", "{not json")
        write(self.repo / ".agent-vault.json", json.dumps(["list"]))
        write(self.repo / ".claude/settings.json", json.dumps({"env": {"CLAUDE_VAULT_DIR": "ok"}}))
        self.assertEqual(core.dir_setting(self.repo)[0], "ok")

    def test_blank_values_are_ignored(self):
        write(self.repo / ".agent-vault.local.json", json.dumps({"vault_dir": "   "}))
        write(self.repo / ".agent-vault.json", json.dumps({"vault_dir": "real"}))
        self.assertEqual(core.dir_setting(self.repo)[0], "real")


class SaveDirSetting(SetupBase):
    def test_user_scope_writes_xdg_config_and_keeps_keys(self):
        path = self.tmp / "xdg-config/agent-vault/config.json"
        write(path, json.dumps({"other": 1}))
        self.assertEqual(core.save_dir_setting("user", self.repo, "/v/{project}"), path)
        self.assertEqual(read_json(path), {"other": 1, "vault_dir": "/v/{project}"})

    def test_user_scope_creates_missing_dirs(self):
        path = core.save_dir_setting("user", None, "/v")
        self.assertEqual(read_json(path), {"vault_dir": "/v"})

    def test_project_scope(self):
        write(self.repo / ".agent-vault.json", json.dumps({"keep": [1]}))
        path = core.save_dir_setting("project", self.repo, "docs/vault")
        self.assertEqual(path, self.repo / ".agent-vault.json")
        self.assertEqual(read_json(path), {"keep": [1], "vault_dir": "docs/vault"})
        self.assertFalse((self.repo / ".agent-vault.local.json").exists())

    def test_local_scope_excludes_file_once(self):
        path = core.save_dir_setting("local", self.repo, "a")
        core.save_dir_setting("local", self.repo, "b")
        self.assertEqual(path, self.repo / ".agent-vault.local.json")
        self.assertEqual(read_json(path), {"vault_dir": "b"})
        exclude = (self.repo / ".git/info/exclude").read_text()
        self.assertEqual(exclude.splitlines().count("/.agent-vault.local.json"), 1)

    def test_local_exclude_keeps_existing_lines(self):
        ex = self.repo / ".git/info/exclude"
        ex.write_text("# c\n*.log")
        core.save_dir_setting("local", self.repo, "a")
        lines = ex.read_text().splitlines()
        self.assertIn("*.log", lines)
        self.assertIn("/.agent-vault.local.json", lines)

    def test_repo_scopes_need_a_repo(self):
        for scope in ("local", "project"):
            with self.assertRaises(SystemExit):
                core.save_dir_setting(scope, None, "x")

    def test_invalid_existing_file_is_not_overwritten(self):
        write(self.repo / ".agent-vault.json", "{broken")
        with self.assertRaises(SystemExit):
            core.save_dir_setting("project", self.repo, "x")
        self.assertEqual((self.repo / ".agent-vault.json").read_text(), "{broken")


class HarnessRoot(SetupBase):
    def test_agent_var_wins_over_claude_var(self):
        os.environ["AGENT_VAULT_HARNESS_DIR"] = str(self.tmp / "new" / "_harness")
        os.environ["CLAUDE_VAULT_HARNESS_DIR"] = str(self.tmp / "old" / "_harness")
        self.assertEqual(core.harness_root(), self.tmp / "new")

    def test_claude_var_used_when_agent_var_unset(self):
        self.assertEqual(core.harness_root(), self.tmp / "harness-vault")

    def test_root_without_harness_suffix_is_used_as_is(self):
        os.environ["AGENT_VAULT_HARNESS_DIR"] = str(self.tmp / "root")
        self.assertEqual(core.harness_root(), self.tmp / "root")

    def test_falls_back_to_xdg_data_home(self):
        os.environ.pop("CLAUDE_VAULT_HARNESS_DIR")
        home = self.tmp / "home"
        home.mkdir()
        os.environ["HOME"] = str(home)
        self.assertEqual(core.harness_root(), self.tmp / "xdg-data" / "agent-vault")

    def test_existing_legacy_dir_is_used(self):
        os.environ.pop("CLAUDE_VAULT_HARNESS_DIR")
        home = self.tmp / "home"
        (home / "claude-vault/_harness").mkdir(parents=True)
        os.environ["HOME"] = str(home)
        self.assertEqual(core.harness_root(), home / "claude-vault")


class ResolveJson(SetupBase):
    def test_skill_dir_and_cli(self):
        out = json.loads(self.run_cli("resolve"))
        self.assertEqual(out["skill_dir"], str(ROOT / "skills" / "vault"))
        self.assertEqual(out["cli"], out["skill_dir"] + "/bin/vault")
        self.assertTrue(os.path.isabs(out["cli"]))
        self.assertTrue(Path(out["cli"]).is_file())


class SetupClaude(SetupBase):
    def test_installs_agents_hook_and_permissions(self):
        out = self.run_cli("setup", "--agent", "claude")
        self.assertIn("Start a new session", out)
        for name in (KEEPER, REVIEWER):
            self.assertEqual((self.claude / "agents" / f"{name}.md").read_text(),
                             (SKILL / "agents" / f"{name}.md").read_text())
        s = self.settings()
        groups = s["hooks"]["SessionStart"]
        self.assertEqual(len(groups), 1)
        hook = groups[0]["hooks"][0]
        self.assertEqual(hook["type"], "command")
        self.assertIn(self.cli, hook["command"])
        self.assertIn("context --hook", hook["command"])
        self.assertEqual(groups[0]["matcher"], setup.HOOK_MATCHER)
        allow = s["permissions"]["allow"]
        self.assertIn(f"Bash({self.cli} *)", allow)
        self.assertTrue(any(r.startswith("Read(") and r.endswith("/skills/vault/**)") for r in allow))
        self.assertTrue(any(r.startswith("Edit(") and str(self.vault).lstrip("/") in r for r in allow))
        self.assertEqual(setup.count_hooks(self.claude / "settings.json"), 1)

    def test_preserves_foreign_content(self):
        foreign = {"type": "command", "command": "echo hi"}
        write(self.claude / "settings.json", json.dumps({
            "theme": "dark",
            "hooks": {"SessionStart": [{"matcher": "x", "hooks": [foreign]}], "Stop": [{"hooks": [foreign]}]},
            "permissions": {"allow": ["Bash(ls)"], "deny": ["Bash(rm *)"]},
        }))
        self.run_cli("setup", "--agent", "claude")
        s = self.settings()
        self.assertEqual(s["theme"], "dark")
        self.assertEqual(s["hooks"]["Stop"], [{"hooks": [foreign]}])
        self.assertEqual(s["hooks"]["SessionStart"][0], {"matcher": "x", "hooks": [foreign]})
        self.assertEqual(len(s["hooks"]["SessionStart"]), 2)
        self.assertEqual(s["permissions"]["deny"], ["Bash(rm *)"])
        self.assertEqual(s["permissions"]["allow"][0], "Bash(ls)")
        self.assertIn(f"Bash({self.cli} *)", s["permissions"]["allow"])

    def test_second_run_is_idempotent(self):
        self.run_cli("setup", "--agent", "claude")
        before = (self.claude / "settings.json").read_text()
        out = self.run_cli("setup", "--agent", "claude")
        self.assertIn("nothing to change", out)
        self.assertEqual((self.claude / "settings.json").read_text(), before)
        self.assertEqual(setup.count_hooks(self.claude / "settings.json"), 1)

    def bundled(self, name=KEEPER):
        return (SKILL / "agents" / f"{name}.md").read_text()

    def test_previous_bundled_version_is_updated(self):
        self.run_cli("setup", "--agent", "claude")
        f = self.claude / "agents" / f"{KEEPER}.md"
        f.write_text("old bundled version")
        state = read_json(setup.state_file())
        state["files"][str(f)] = setup._sha("old bundled version")
        setup.state_file().write_text(json.dumps(state))
        out = self.run_cli("setup", "--agent", "claude")
        self.assertIn(f"update {f}", out)
        self.assertEqual(f.read_text(), self.bundled())
        self.assertEqual(read_json(setup.state_file())["files"][str(f)], setup._sha(self.bundled()))

    def test_user_edited_agent_file_is_kept(self):
        self.run_cli("setup", "--agent", "claude")
        f = self.claude / "agents" / f"{KEEPER}.md"
        f.write_text("my own keeper")
        out = self.run_cli("setup", "--agent", "claude")
        self.assertIn("kept", out)
        self.assertIn("--force", out)
        self.assertEqual(f.read_text(), "my own keeper")
        out = self.run_cli("setup", "--agent", "claude", "--uninstall")
        self.assertEqual(f.read_text(), "my own keeper")
        self.assertFalse((self.claude / "agents" / f"{REVIEWER}.md").exists())

    def test_foreign_agent_file_is_kept_until_force(self):
        f = self.claude / "agents" / f"{KEEPER}.md"
        write(f, "---\nname: vault-keeper\n---\nhand-written\n")
        out = self.run_cli("setup", "--agent", "claude", "--only", "agents")
        self.assertIn(f"kept {f}", out)
        self.assertIn("hand-written", f.read_text())
        self.assertTrue((self.claude / "agents" / f"{REVIEWER}.md").is_file())
        out = self.run_cli("setup", "--agent", "claude", "--only", "agents", "--force")
        self.assertIn(f"update {f} (backup {KEEPER}.md.bak)", out)
        self.assertEqual(f.read_text(), self.bundled())
        self.assertIn("hand-written", f.with_name(f"{KEEPER}.md.bak").read_text())
        f.write_text("edited again")
        out = self.run_cli("setup", "--agent", "claude", "--only", "agents", "--force")
        self.assertIn(f"(backup {KEEPER}.md.bak.1)", out)
        self.assertIn("hand-written", f.with_name(f"{KEEPER}.md.bak").read_text())
        self.assertEqual(f.with_name(f"{KEEPER}.md.bak.1").read_text(), "edited again")
        self.run_cli("setup", "--agent", "claude", "--only", "agents", "--uninstall")
        self.assertFalse(f.exists())

    def test_force_uninstall_removes_foreign_file(self):
        f = self.claude / "agents" / f"{KEEPER}.md"
        write(f, "foreign")
        self.run_cli("setup", "--agent", "claude", "--only", "agents", "--uninstall")
        self.assertTrue(f.exists())
        self.run_cli("setup", "--agent", "claude", "--only", "agents", "--uninstall", "--force")
        self.assertFalse(f.exists())

    def test_malformed_settings_fail_cleanly(self):
        cases = [{"hooks": []}, {"hooks": {"SessionStart": {}}}, {"permissions": []},
                 {"permissions": {"allow": "x"}}, {"permissions": {"additionalDirectories": "x"}}]
        for data in cases:
            with self.subTest(data=data):
                write(self.claude / "settings.json", json.dumps(data))
                out = self.run_cli("setup", "--agent", "claude", "--only", "hook,perms", expect=1)
                self.assertIn("must be a JSON", out)
                self.assertIn("not changed", out)
                self.assertEqual(read_json(self.claude / "settings.json"), data)

    def test_dry_run_writes_nothing(self):
        out = self.run_cli("setup", "--agent", "claude", "--dry-run")
        self.assertIn("would write", out)
        self.assertFalse(self.claude.exists())

    def test_only_limits_the_parts(self):
        self.run_cli("setup", "--agent", "claude", "--only", "hook")
        self.assertFalse((self.claude / "agents").exists())
        s = self.settings()
        self.assertIn("hooks", s)
        self.assertNotIn("permissions", s)

    def test_unknown_part_is_rejected(self):
        self.run_cli("setup", "--agent", "claude", "--only", "bogus", expect=1)
        self.assertFalse(self.claude.exists())

    def test_invalid_settings_json_is_left_alone(self):
        write(self.claude / "settings.json", "{oops")
        self.run_cli("setup", "--agent", "claude", expect=1)
        self.assertEqual((self.claude / "settings.json").read_text(), "{oops")

    def test_uninstall_removes_only_ours(self):
        foreign = {"type": "command", "command": "echo hi"}
        write(self.claude / "settings.json", json.dumps({
            "hooks": {"SessionStart": [{"matcher": "x", "hooks": [foreign]}]},
            "permissions": {"allow": ["Bash(ls)"]},
        }))
        write(self.claude / "agents/other.md", "---\nname: other\n---\nhi\n")
        self.run_cli("setup", "--agent", "claude")
        self.run_cli("setup", "--agent", "claude", "--uninstall")
        self.assertFalse((self.claude / "agents" / f"{KEEPER}.md").exists())
        self.assertFalse((self.claude / "agents" / f"{REVIEWER}.md").exists())
        self.assertTrue((self.claude / "agents/other.md").exists())
        s = self.settings()
        self.assertEqual(s["hooks"]["SessionStart"], [{"matcher": "x", "hooks": [foreign]}])
        self.assertEqual(s["permissions"], {"allow": ["Bash(ls)"]})
        self.assertEqual(setup.count_hooks(self.claude / "settings.json"), 0)

    def test_uninstall_leaves_user_edited_agent_file(self):
        self.run_cli("setup", "--agent", "claude")
        f = self.claude / "agents" / f"{KEEPER}.md"
        f.write_text("---\nname: something-else\n---\nmine\n")
        self.run_cli("setup", "--agent", "claude", "--uninstall")
        self.assertTrue(f.exists())

    def test_uninstall_on_clean_system_changes_nothing(self):
        out = self.run_cli("setup", "--agent", "claude", "--uninstall")
        self.assertIn("nothing to change", out)
        self.assertFalse(self.claude.exists())

    def test_project_scope(self):
        self.run_cli("setup", "--agent", "claude", "--scope", "project")
        local = self.repo / ".claude/settings.local.json"
        self.assertTrue(local.is_file())
        self.assertEqual(setup.count_hooks(local), 1)
        self.assertTrue((self.repo / ".claude/agents" / f"{KEEPER}.md").is_file())
        self.assertTrue((self.repo / ".claude/agents" / f"{REVIEWER}.md").is_file())
        self.assertFalse(self.claude.exists())
        self.run_cli("setup", "--agent", "claude", "--scope", "project", "--uninstall")
        self.assertEqual(setup.count_hooks(local), 0)
        self.assertFalse((self.repo / ".claude/agents" / f"{KEEPER}.md").exists())

    def test_project_scope_needs_a_repo(self):
        outside = self.tmp / "plain"
        outside.mkdir()
        self.run_cli("setup", "--agent", "claude", "--scope", "project", cwd=outside, expect=1)

    def test_vault_inside_repo_adds_no_vault_rules(self):
        self.run_cli("setup", "--agent", "claude", "--vault-dir", "vault")
        allow = self.settings()["permissions"]["allow"]
        self.assertFalse(any(r.startswith("Edit(") for r in allow))
        self.assertNotIn("additionalDirectories", self.settings()["permissions"])


class SymlinkedSkill(SetupBase):
    """skills.sh installs the Claude Code copy as a symlink to .agents/skills/vault."""

    def test_invoked_path_is_reported_and_allowed(self):
        link = self.tmp / "proj/.claude/skills/vault"
        link.parent.mkdir(parents=True)
        link.symlink_to(SKILL, target_is_directory=True)
        cli_link = link / "bin" / "vault"
        env = {k: v for k, v in os.environ.items()}
        run = lambda *a: subprocess.run([str(cli_link), *a], cwd=self.repo, env=env, capture_output=True,
                                        text=True, check=True).stdout
        info = json.loads(run("resolve"))
        self.assertEqual(info["skill_dir"], str(link))
        self.assertEqual(info["cli"], str(cli_link))
        run("setup", "--agent", "claude", "--only", "hook,perms")
        settings = self.settings()
        allow = settings["permissions"]["allow"]
        self.assertIn(f"Bash({cli_link} *)", allow)
        self.assertIn(f"Bash({SKILL.resolve() / 'bin' / 'vault'} *)", allow)
        self.assertIn(f"Read({setup._perm_path(link)}/**)", allow)
        self.assertIn(f"Read({setup._perm_path(SKILL.resolve())}/**)", allow)
        cmds = [h["command"] for g in settings["hooks"]["SessionStart"] for h in g["hooks"]]
        self.assertEqual(cmds, [f"'{cli_link}' context --hook"])


class SetupCodex(SetupBase):
    def md(self, name):
        return fm.read((SKILL / "agents" / f"{name}.md").read_text())

    def test_agents_and_hook(self):
        out = self.run_cli("setup", "--agent", "codex")
        for name in (KEEPER, REVIEWER):
            path = self.codex / "agents" / f"{name}.toml"
            self.assertTrue(path.is_file())
            if tomllib:
                data = tomllib.loads(path.read_text())
                meta, body, _ = self.md(name)
                self.assertEqual(data["name"], meta["name"])
                self.assertEqual(data["description"], meta["description"])
                self.assertEqual(data["developer_instructions"], body.strip() + "\n")
                self.assertEqual(data["model_reasoning_effort"], "medium")
                self.assertNotIn("model", data)
        hooks = read_json(self.codex / "hooks.json")
        self.assertEqual(setup.count_hooks(self.codex / "hooks.json"), 1)
        self.assertIn("context --hook", hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"])
        self.assertFalse((self.codex / "settings.json").exists())
        self.assertIn("writable_roots", out)
        self.assertIn(str(self.vault), out)

    def test_no_writable_roots_note_when_vault_is_in_repo(self):
        out = self.run_cli("setup", "--agent", "codex", "--vault-dir", "vault")
        self.assertNotIn("writable_roots", out)

    def test_idempotent_and_uninstall(self):
        self.run_cli("setup", "--agent", "codex")
        self.assertIn("nothing to change", self.run_cli("setup", "--agent", "codex").split("Start")[0])
        self.run_cli("setup", "--agent", "codex", "--uninstall")
        self.assertFalse((self.codex / "agents" / f"{KEEPER}.toml").exists())
        self.assertEqual(setup.count_hooks(self.codex / "hooks.json"), 0)

    def test_project_scope_uses_repo_codex_dir(self):
        self.run_cli("setup", "--agent", "codex", "--scope", "project")
        self.assertTrue((self.repo / ".codex/agents" / f"{KEEPER}.toml").is_file())
        self.assertEqual(setup.count_hooks(self.repo / ".codex/hooks.json"), 1)

    def test_all_installs_both(self):
        out = self.run_cli("setup")
        self.assertIn("claude (user):", out)
        self.assertIn("codex (user):", out)
        self.assertTrue((self.claude / "agents" / f"{KEEPER}.md").is_file())
        self.assertTrue((self.codex / "agents" / f"{KEEPER}.toml").is_file())

    def test_codex_agent_toml_plain_body(self):
        text = "---\nname: a\ndescription: d \"q\"\nmodel: sonnet\neffort: high\ntools: Read\n---\n\nbody \\n\nline2\n\n"
        out = setup.codex_agent_toml(text)
        self.assertIn("developer_instructions = '''\n", out)
        self.assertNotIn("model =", out)
        self.assertNotIn("tools", out)
        if tomllib:
            data = tomllib.loads(out)
            self.assertEqual(data["description"], 'd "q"')
            self.assertEqual(data["model_reasoning_effort"], "high")
            self.assertEqual(data["developer_instructions"], "body \\n\nline2\n")

    def test_codex_agent_toml_triple_quote_body(self):
        body = "use ''' carefully\n\ttab and \x01 ctrl\n"
        out = setup.codex_agent_toml(f"---\nname: a\ndescription: d\n---\n{body}")
        self.assertTrue(out.split("developer_instructions =")[1].lstrip().startswith('"'))
        if tomllib:
            data = tomllib.loads(out)
            self.assertEqual(data["developer_instructions"], body.strip() + "\n")

    def test_codex_agent_toml_without_effort_or_invalid_effort(self):
        for extra in ("", "effort: bogus\n"):
            out = setup.codex_agent_toml(f"---\nname: a\ndescription: d\n{extra}---\nb\n")
            self.assertNotIn("model_reasoning_effort", out)


class BundleAgent(SetupBase):
    def bundle_in(self, home):
        return mock.patch.object(setup, "plugin_bundle", return_value=home / "plugins/cache/agent-vault")

    def test_detects_owning_agent(self):
        with self.bundle_in(self.claude):
            self.assertEqual(setup.bundle_agent(), "claude")
        with self.bundle_in(self.codex):
            self.assertEqual(setup.bundle_agent(), "codex")
        with self.bundle_in(self.tmp / "elsewhere"):
            self.assertIsNone(setup.bundle_agent())
        with mock.patch.object(setup, "plugin_bundle", return_value=None):
            self.assertIsNone(setup.bundle_agent())

    def test_checkout_outside_agent_homes_is_not_a_bundle(self):
        self.assertEqual(setup.plugin_bundle(), ROOT)
        self.assertIsNone(setup.bundle_agent())

    def test_plan_skips_provided_parts_unless_forced(self):
        ctx = core.resolve(str(self.repo))
        with self.bundle_in(self.claude):
            plan = setup.build_plan("claude", "user", setup.PARTS, ctx, False, False)
            paths = {p.name for p in plan.writes}
            self.assertNotIn(f"{KEEPER}.md", paths)
            self.assertEqual(paths, {"settings.json"})
            self.assertTrue(any("--force" in n for n in plan.notes))
            self.assertNotIn("hooks", json.loads(plan.writes[self.claude / "settings.json"]))

            forced = setup.build_plan("claude", "user", setup.PARTS, ctx, False, True)
            self.assertEqual({p.name for p in forced.writes}, {"settings.json", f"{KEEPER}.md", f"{REVIEWER}.md"})
            self.assertEqual(forced.notes, [])

    def test_cli_force_installs(self):
        with self.bundle_in(self.claude):
            out = self.run_cli("setup", "--agent", "claude")
            self.assertIn("--force", out)
            self.assertFalse((self.claude / "agents").exists())
            self.run_cli("setup", "--agent", "claude", "--force")
        self.assertTrue((self.claude / "agents" / f"{KEEPER}.md").is_file())
        self.assertEqual(setup.count_hooks(self.claude / "settings.json"), 1)

    def test_codex_bundle_keeps_agents_but_skips_hook(self):
        ctx = core.resolve(str(self.repo))
        with self.bundle_in(self.codex):
            plan = setup.build_plan("codex", "user", setup.PARTS, ctx, False, False)
        self.assertEqual({p.name for p in plan.writes}, {f"{KEEPER}.toml", f"{REVIEWER}.toml"})

    def test_uninstall_ignores_bundle_skip(self):
        self.run_cli("setup", "--agent", "claude")
        with self.bundle_in(self.claude):
            self.run_cli("setup", "--agent", "claude", "--uninstall")
        self.assertEqual(setup.count_hooks(self.claude / "settings.json"), 0)


class Doctor(SetupBase):
    def test_single_hook_is_ok(self):
        self.run_cli("setup", "--agent", "all")
        out = self.run_cli("doctor")
        self.assertNotIn("warning", out)
        self.assertIn(KEEPER, out)
        self.assertIn(self.cli, out)

    def test_nothing_installed_is_ok(self):
        out = self.run_cli("doctor")
        self.assertIn("none installed", out)
        self.assertNotIn("warning", out)

    def test_duplicate_hook_warns(self):
        self.run_cli("setup", "--agent", "claude")
        self.run_cli("setup", "--agent", "claude", "--scope", "project")
        out = self.run_cli("doctor", expect=1)
        self.assertIn("warning", out)
        self.assertIn("more than once", out)

    def test_duplicate_hook_in_one_file_warns(self):
        self.run_cli("setup", "--agent", "codex")
        path = self.codex / "hooks.json"
        data = read_json(path)
        data["hooks"]["SessionStart"].append(json.loads(json.dumps(data["hooks"]["SessionStart"][0])))
        path.write_text(json.dumps(data))
        self.assertEqual(setup.count_hooks(path), 2)
        self.assertIn("warning", self.run_cli("doctor", expect=1))

    def test_bundle_plus_settings_hook_warns(self):
        self.run_cli("setup", "--agent", "claude")
        with mock.patch.object(setup, "plugin_bundle", return_value=self.claude / "plugins/x"):
            out = self.run_cli("doctor", expect=1)
        self.assertIn("warning", out)


class HookOutput(SetupBase):
    def run_hook(self, payload):
        buf = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), contextlib.redirect_stdout(buf):
            from vaultlib import cli
            rc = cli.main(["context", "--hook"])
        self.assertEqual(rc, 0)
        return buf.getvalue()

    def test_emits_session_start_json(self):
        self.init_project()
        out = json.loads(self.run_hook({"cwd": str(self.repo), "hook_event_name": "SessionStart"}))
        self.assertEqual(list(out), ["hookSpecificOutput"])
        spec = out["hookSpecificOutput"]
        self.assertEqual(spec["hookEventName"], "SessionStart")
        self.assertIn("`vault search", spec["additionalContext"])
        self.assertNotIn("/retro", spec["additionalContext"])

    def test_vault_agents_get_no_context(self):
        self.init_project()
        for agent in ("agent-vault:vault-keeper", "vault-keeper", "verify-reviewer"):
            self.assertEqual(self.run_hook({"cwd": str(self.repo), "agent_type": agent}), "")

    def test_works_without_sigalrm(self):
        import signal
        self.init_project()
        saved = signal.SIGALRM
        try:
            del signal.SIGALRM
            out = self.run_hook({"cwd": str(self.repo)})
        finally:
            signal.SIGALRM = saved
        self.assertIn("hookSpecificOutput", out)

    def test_no_alarm_left_behind(self):
        import signal
        self.init_project()
        before = signal.getsignal(signal.SIGALRM)
        for payload in ({"cwd": str(self.repo), "agent_type": "vault-keeper"}, {"cwd": str(self.repo)}):
            self.run_hook(payload)
            self.assertEqual(signal.alarm(0), 0)
            self.assertIs(signal.getsignal(signal.SIGALRM), before)

    def test_garbage_input_is_silent(self):
        buf = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO("not json")), contextlib.redirect_stdout(buf):
            from vaultlib import cli
            self.assertEqual(cli.main(["context", "--hook"]), 0)
        self.assertEqual(buf.getvalue(), "")

    def test_runs_as_subprocess(self):
        self.init_project()
        p = subprocess.run([sys.executable, str(BIN), "context", "--hook"], input=json.dumps({"cwd": str(self.repo)}),
                           capture_output=True, text=True, env=os.environ.copy())
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("SessionStart", json.loads(p.stdout)["hookSpecificOutput"]["hookEventName"])


class VersionAndVloop(SetupBase):
    def fake(self, path: Path, body: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def vloop_json(self, name="agent-verify-loop", contract=1, version="2.0.0") -> str:
        return "echo '" + json.dumps({"name": name, "version": version, "contract": contract}) + "'"

    def env(self, **extra):
        home = self.tmp / "home"
        home.mkdir(exist_ok=True)
        env = {"HOME": str(home), "CLAUDE_CONFIG_DIR": str(self.claude), "CODEX_HOME": str(self.codex),
               "PATH": str(self.tmp / "emptybin")}
        env.update(extra)
        return mock.patch.dict(os.environ, env)

    def test_version_json_and_text(self):
        out = subprocess.run([sys.executable, str(BIN), "version", "--json"], capture_output=True, text=True,
                             cwd=str(self.tmp), check=True).stdout
        data = json.loads(out)
        self.assertEqual(set(data), {"name", "version", "contract", "skill_dir", "cli"})
        self.assertEqual((data["name"], data["contract"]), ("agent-vault", 1))
        self.assertEqual(data["version"], read_json(ROOT / ".claude-plugin/plugin.json")["version"])
        self.assertTrue(Path(data["cli"]).is_absolute())
        text = subprocess.run([sys.executable, str(BIN), "version"], capture_output=True, text=True,
                              cwd=str(self.tmp), check=True).stdout
        self.assertEqual(text.strip(), f"agent-vault {data['version']} (contract 1)")

    def test_none_found(self):
        with self.env():
            self.assertIsNone(setup.find_vloop())

    def test_env_override_and_name_check(self):
        good = self.fake(self.tmp / "x" / "vloop", self.vloop_json())
        with self.env(VLOOP_CLI=str(good)):
            self.assertEqual(setup.find_vloop(), {"cli": str(good), "version": "2.0.0", "contract": 1})
        bad = self.fake(self.tmp / "bin" / "vloop", self.vloop_json(name="other"))
        with self.env(PATH=str(bad.parent)):
            self.assertIsNone(setup.find_vloop())

    def test_path_beats_plugin_cache(self):
        on_path = self.fake(self.tmp / "bin" / "vloop", self.vloop_json(version="1.0.0"))
        self.fake(self.claude / "plugins/cache/mk/agent-verify-loop/2.0.0/skills/verify-loop/bin/vloop",
                  self.vloop_json(version="2.0.0"))
        with self.env(PATH=str(on_path.parent)):
            self.assertEqual(setup.find_vloop()["version"], "1.0.0")

    def test_plugin_caches(self):
        rel = "skills/verify-loop/bin/vloop"
        old = self.fake(self.claude / "plugins/cache/mk/agent-verify-loop/1.0.0" / rel, self.vloop_json(version="1.0.0"))
        new = self.fake(self.claude / "plugins/cache/mk/agent-verify-loop/2.0.0" / rel, self.vloop_json(version="2.0.0"))
        os.utime(old, (1, 1))
        with self.env():
            self.assertEqual(setup.find_vloop()["cli"], str(new))
        new.unlink()
        old.unlink()
        cx = self.fake(self.codex / "plugins/cache/x/agent-verify-loop/3.0.0" / rel, self.vloop_json(version="3.0.0"))
        with self.env():
            self.assertEqual(setup.find_vloop()["cli"], str(cx))

    def test_legacy_install_is_contract_zero(self):
        legacy = self.fake(self.claude / "skills/verify-loop/bin/vloop", "echo 'usage: vloop <command>' >&2; exit 2")
        with self.env():
            self.assertEqual(setup.find_vloop(), {"cli": str(legacy), "version": "", "contract": 0})
        legacy.chmod(0o644)
        with self.env():
            self.assertIsNone(setup.find_vloop())

    def test_hanging_or_crashing_legacy_install_is_ignored(self):
        legacy = self.claude / "skills/verify-loop/bin/vloop"
        for body in ("exec /bin/sleep 10", "kill -9 $$", "exit 0"):
            self.fake(legacy, body)
            with self.env(), mock.patch.object(setup.subprocess, "run", wraps=setup.subprocess.run) as run:
                self.assertIsNone(setup.find_vloop())
            self.assertEqual(run.call_count, 1)

    def test_resolve_reports_vloop(self):
        good = self.fake(self.tmp / "x" / "vloop", self.vloop_json())
        env = {**os.environ, "VLOOP_CLI": str(good)}
        out = subprocess.run([sys.executable, str(BIN), "resolve", "--cwd", str(self.repo)], capture_output=True,
                             text=True, cwd=str(self.tmp), env=env, check=True).stdout
        self.assertEqual(json.loads(out)["vloop"]["contract"], 1)
        env = {**os.environ, "VLOOP_CLI": "", "PATH": str(self.tmp / "emptybin"), "CLAUDE_CONFIG_DIR": str(self.claude),
               "CODEX_HOME": str(self.codex), "HOME": str(self.tmp / "home")}
        out = subprocess.run([sys.executable, str(BIN), "resolve", "--cwd", str(self.repo)], capture_output=True,
                             text=True, cwd=str(self.tmp), env=env, check=True).stdout
        self.assertIn("vloop", json.loads(out))
        self.assertIsNone(json.loads(out)["vloop"])

    def test_skip_agent_by_suffix(self):
        from vaultlib import cli
        for name in ("vault-keeper", "vault-reviewer", "agent-vault:vault-keeper", "verify-qa",
                     "agent-verify-loop:verify-reviewer", "agent-verify-loop:verify-triage"):
            self.assertTrue(cli.skip_agent(name), name)
        for name in ("", None, "general-purpose", "vaultish", "my-plugin:claude", "agent-vault:other"):
            self.assertFalse(cli.skip_agent(name), name)
        for agent in ("agent-vault:vault-keeper", "agent-verify-loop:verify-reviewer"):
            r = subprocess.run([sys.executable, str(BIN), "context", "--hook"], input=json.dumps(
                {"cwd": str(self.repo), "agent_type": agent}), capture_output=True, text=True, cwd=str(self.tmp))
            self.assertEqual(r.stdout, "")


class Packaging(unittest.TestCase):
    def j(self, rel):
        return read_json(ROOT / rel)

    def test_manifests_parse(self):
        for rel in (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json", ".codex-plugin/plugin.json",
                    ".agents/plugins/marketplace.json", "hooks/hooks.json"):
            self.assertIsInstance(self.j(rel), dict, rel)

    def test_names_and_versions_agree(self):
        claude, codex = self.j(".claude-plugin/plugin.json"), self.j(".codex-plugin/plugin.json")
        market = self.j(".claude-plugin/marketplace.json")
        self.assertEqual(claude["name"], "agent-vault")
        self.assertEqual(codex["name"], "agent-vault")
        self.assertEqual(claude["version"], codex["version"])
        entry = next(p for p in market["plugins"] if p["name"] == "agent-vault")
        self.assertEqual(entry["version"], claude["version"])
        self.assertTrue(re.fullmatch(r"\d+\.\d+\.\d+", claude["version"]))
        codex_market = self.j(".agents/plugins/marketplace.json")
        self.assertIn("agent-vault", [p["name"] for p in codex_market["plugins"]])

    def test_codex_paths_exist(self):
        codex = self.j(".codex-plugin/plugin.json")
        for key in ("skills", "hooks"):
            self.assertTrue((ROOT / codex[key]).exists(), key)
        market = self.j(".agents/plugins/marketplace.json")
        for p in market["plugins"]:
            self.assertTrue((ROOT / p["source"]["path"]).is_dir())

    def test_plugin_hook_points_at_executable_cli(self):
        cmd = self.j("hooks/hooks.json")["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/skills/vault/bin/vault", cmd)
        self.assertIn("context --hook", cmd)
        cli = ROOT / "skills/vault/bin/vault"
        self.assertTrue(cli.is_file())
        self.assertTrue(cli.stat().st_mode & stat.S_IXUSR)

    def test_plugin_hook_matches_setup_hook(self):
        grp = self.j("hooks/hooks.json")["hooks"]["SessionStart"][0]
        self.assertEqual(grp["matcher"], setup.HOOK_MATCHER)
        self.assertEqual(grp["hooks"][0]["timeout"], setup.HOOK_TIMEOUT)
        self.assertTrue(setup._is_ours(grp["hooks"][0]))

    def test_agent_copies_are_in_sync(self):
        p = subprocess.run([sys.executable, str(ROOT / "scripts/sync_agents.py"), "--check"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for f in (SKILL / "agents").glob("*.md"):
            self.assertEqual((ROOT / "agents" / f.name).read_text(), f.read_text())

    def test_skill_frontmatter(self):
        meta, _, _ = fm.read((SKILL / "SKILL.md").read_text())
        self.assertEqual(meta["name"], "vault")
        self.assertTrue(str(meta["description"]).strip())

    def test_no_stale_references(self):
        tracked = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT,
                                 capture_output=True, text=True).stdout.split("\n")
        self.assertTrue(tracked)
        bad = []
        for rel in filter(None, tracked):
            path = ROOT / rel
            if not path.is_file() or rel.startswith(".git/"):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if rel == "tests/test_setup.py":
                continue
            if "herd" "r" in text.lower():
                bad.append(f"{rel}: herdr")
            if "~/.claude/skills/" "vault" in text and rel != "README.md":
                bad.append(f"{rel}: ~/.claude/skills/vault")
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
