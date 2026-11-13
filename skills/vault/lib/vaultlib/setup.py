"""Install, check and remove the vault agents, SessionStart hook and permissions for Claude Code and Codex."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import core
from . import frontmatter as fmlib

AGENTS = ("claude", "codex")
PARTS = ("agents", "hook", "perms")
HOOK_MATCHER = "^(startup|resume|clear|compact)$"
HOOK_TIMEOUT = 5
HOOK_CMD_RE = re.compile(r"/bin/vault'?\"?\s+context\s+--hook\b")


@dataclass
class Plan:
    agent: str
    scope: str
    writes: dict = field(default_factory=dict)  # path -> new text (None = delete)
    backups: list = field(default_factory=list)  # files to copy to <name>.bak before they are overwritten
    notes: list = field(default_factory=list)


def skill_dir() -> Path:
    return core.SKILL_DIR


def cli_path() -> Path:
    return skill_dir() / "bin" / "vault"


def agent_sources() -> list:
    return sorted((skill_dir() / "agents").glob("*.md"))


def plugin_bundle() -> Path | None:
    """Root of the plugin this skill ships in (its manifest provides the agents and hook), or None."""
    root = skill_dir().parent.parent
    return root if (root / ".claude-plugin" / "plugin.json").is_file() else None


def bundle_agent() -> str | None:
    """Which agent installed this skill as a plugin (its plugin cache lives under that agent's home)."""
    bundle = plugin_bundle()
    if not bundle:
        return None
    for agent, home in (("claude", claude_home()), ("codex", codex_home())):
        if _inside(bundle, home):
            return agent
    return None


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()


VLOOP_NAME = "agent-verify-loop"


def _probe_vloop(cli: Path):
    """(found, rejected): found is the contract dict; rejected means the CLI ran and refused `version`, which only a
    pre-contract install does. A hang or crash is neither."""
    try:
        r = subprocess.run([str(cli), "version", "--json"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None, False
    try:
        data = json.loads(r.stdout)
    except ValueError:
        return None, r.returncode > 0
    if not isinstance(data, dict) or data.get("name") != VLOOP_NAME:
        return None, False
    try:
        contract = int(data.get("contract"))
    except (TypeError, ValueError):
        return None, False
    return {"cli": str(cli), "version": str(data.get("version") or ""), "contract": contract}, False


def _executable(p: Path) -> bool:
    return p.is_file() and os.access(p, os.X_OK)


def find_vloop():
    """The agent-verify-loop CLI as {"cli", "version", "contract"}, or None. Optional integration."""
    import shutil
    rel = Path("skills") / "verify-loop" / "bin" / "vloop"
    env = os.environ.get("VLOOP_CLI")
    cands = [Path(env).expanduser()] if env else []
    on_path = shutil.which("vloop")
    if on_path:
        cands.append(Path(on_path))
    claude, codex = claude_home(), codex_home()
    cache = sorted(
        (p for p in (claude / "plugins" / "cache").glob("*/agent-verify-loop/*/" + rel.as_posix()) if p.is_file()),
        key=lambda p: p.stat().st_mtime, reverse=True)
    cands += cache
    if (codex / "plugins").is_dir():
        cands += sorted(p for p in (codex / "plugins").glob("**/" + rel.as_posix()) if p.is_file())
    legacy = claude / rel
    cands += [legacy, Path("~/.agents").expanduser() / rel]
    seen = set()
    for cand in cands:
        if str(cand) in seen or not _executable(cand):
            continue
        seen.add(str(cand))
        found, rejected = _probe_vloop(cand)
        if found:
            return found
        if cand == legacy and rejected:
            return {"cli": str(cand), "version": "", "contract": 0}
    return None


def find_herdr_agent():
    """The herdr-agent CLI path, or None: env HERDR_AGENT, then PATH, then the user skill install."""
    import shutil
    env = os.environ.get("HERDR_AGENT")
    cands = [Path(env).expanduser()] if env else []
    on_path = shutil.which("herdr-agent")
    if on_path:
        cands.append(Path(on_path))
    cands.append(Path("~/.claude/skills/herdr-agent/bin/herdr-agent").expanduser())
    for cand in cands:
        if _executable(cand):
            return str(cand)
    return None


def base_dir(agent: str, scope: str, repo_root) -> Path:
    if scope == "project":
        if not repo_root:
            raise SystemExit("vault setup: --scope project needs a git repo")
        return Path(repo_root) / (".claude" if agent == "claude" else ".codex")
    return claude_home() if agent == "claude" else codex_home()


def hook_command() -> str:
    return f"'{cli_path()}' context --hook"


def _tilde(p) -> str:
    home = str(Path.home())
    s = str(p)
    return "~" + s[len(home):] if s == home or s.startswith(home + "/") else s


def _perm_path(p) -> str:
    """Claude permission path: `~/…` under home, `//abs` elsewhere (a single `/` is settings-relative)."""
    t = _tilde(p)
    return t if t.startswith("~") else "/" + t


# --------------------------------------------------------------------------- json files


def _load(path: Path) -> dict:
    data = core._read_json(path)
    if data is None:
        raise SystemExit(f"vault setup: {path} is not a valid JSON object; not changed")
    shapes = [("hooks", data.get("hooks"), dict), ("permissions", data.get("permissions"), dict)]
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    perms = data.get("permissions") if isinstance(data.get("permissions"), dict) else {}
    shapes += [("hooks.SessionStart", hooks.get("SessionStart"), list),
               ("permissions.allow", perms.get("allow"), list),
               ("permissions.additionalDirectories", perms.get("additionalDirectories"), list)]
    for key, value, kind in shapes:
        if value is not None and not isinstance(value, kind):
            raise SystemExit(f"vault setup: {path}: `{key}` must be a JSON {'object' if kind is dict else 'array'}; "
                             f"not changed")
    return data


def _dump(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _is_ours(hook: dict) -> bool:
    return isinstance(hook, dict) and bool(HOOK_CMD_RE.search(str(hook.get("command", ""))))


def _strip_hooks(data: dict) -> int:
    """Remove every vault context hook from a settings/hooks object; returns how many were removed."""
    hooks = data.get("hooks")
    if not isinstance(hooks, dict) or not isinstance(hooks.get("SessionStart"), list):
        return 0
    removed, groups = 0, []
    for grp in hooks["SessionStart"]:
        inner = grp.get("hooks") if isinstance(grp, dict) else None
        if not isinstance(inner, list):
            groups.append(grp)
            continue
        keep = [h for h in inner if not _is_ours(h)]
        removed += len(inner) - len(keep)
        if keep:
            groups.append({**grp, "hooks": keep})
    if groups:
        hooks["SessionStart"] = groups
    else:
        hooks.pop("SessionStart")
    if not hooks:
        data.pop("hooks")
    return removed


def _add_hook(data: dict) -> None:
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    groups = hooks.get("SessionStart") if isinstance(hooks.get("SessionStart"), list) else []
    ours = [h for grp in groups if isinstance(grp, dict) and isinstance(grp.get("hooks"), list)
            for h in grp["hooks"] if _is_ours(h)]
    if len(ours) == 1 and ours[0].get("command") == hook_command():
        return
    _strip_hooks(data)
    hooks = data.setdefault("hooks", {})
    hooks.setdefault("SessionStart", []).append({
        "matcher": HOOK_MATCHER,
        "hooks": [{"type": "command", "command": hook_command(), "timeout": HOOK_TIMEOUT}],
    })


def count_hooks(path: Path) -> int:
    data = core._read_json(path) or {}
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    n = 0
    for grp in hooks.get("SessionStart") or []:
        if isinstance(grp, dict) and isinstance(grp.get("hooks"), list):
            n += sum(1 for h in grp["hooks"] if _is_ours(h))
    return n


def claude_rules(vault_root) -> tuple:
    """(allow rules, additionalDirectories) the vault needs in Claude Code. Both the invoked and the real skill path
    are allowed: skills.sh installs the Claude Code copy as a symlink."""
    allow = []
    for sd in dict.fromkeys([skill_dir(), skill_dir().resolve()]):
        cli = sd / "bin" / "vault"
        allow += [f"Bash({cli} *)", f"Bash({_tilde(cli)} *)", f"Read({_perm_path(sd)}/**)"]
    dirs = []
    if vault_root:
        allow += [f"Read({_perm_path(vault_root)}/**)", f"Edit({_perm_path(vault_root)}/**)"]
        dirs.append(_tilde(vault_root))
    return list(dict.fromkeys(allow)), dirs


def _add_perms(data: dict, allow: list, dirs: list) -> None:
    perms = data.setdefault("permissions", {})
    cur = perms.setdefault("allow", [])
    perms["allow"] = cur + [r for r in allow if r not in cur]
    if dirs:
        cur_d = perms.setdefault("additionalDirectories", [])
        perms["additionalDirectories"] = cur_d + [d for d in dirs if d not in cur_d]


def _strip_perms(data: dict, allow: list, dirs: list) -> None:
    perms = data.get("permissions")
    if not isinstance(perms, dict):
        return
    for key, ours in (("allow", allow), ("additionalDirectories", dirs)):
        if isinstance(perms.get(key), list):
            perms[key] = [r for r in perms[key] if r not in ours]
            if not perms[key]:
                perms.pop(key)
    if not perms:
        data.pop("permissions")


# --------------------------------------------------------------------------- agents


def _toml_str(s: str) -> str:
    if "'''" not in s and not re.search(r"[\x00-\x08\x0b-\x1f\x7f]", s):
        return "'''\n" + s + "'''"
    return json.dumps(s, ensure_ascii=False)


def codex_agent_toml(md_text: str) -> str:
    """Codex custom agent (TOML) from a Claude agent file: name, description, instructions and effort.
    The Claude model and tool list are dropped: Codex agents inherit the session model and sandbox."""
    fm, body, _ = fmlib.read(md_text)
    lines = [
        f"name = {json.dumps(str(fm.get('name', '')), ensure_ascii=False)}",
        f"description = {json.dumps(str(fm.get('description', '')), ensure_ascii=False)}",
    ]
    effort = str(fm.get("effort") or "").strip()
    if effort in ("minimal", "low", "medium", "high", "xhigh"):
        lines.append(f'model_reasoning_effort = "{effort}"')
    lines.append(f"developer_instructions = {_toml_str(body.strip() + chr(10))}")
    return "\n".join(lines) + "\n"


def _agent_files(agent: str, base: Path) -> dict:
    out = {}
    for src in agent_sources():
        text = src.read_text(encoding="utf-8")
        if agent == "claude":
            out[base / "agents" / src.name] = text
        else:
            out[base / "agents" / (src.stem + ".toml")] = codex_agent_toml(text)
    return out


def state_file() -> Path:
    """Hashes of the agent files setup wrote, so it never overwrites or deletes a file the user made or edited."""
    return Path(os.environ.get("XDG_DATA_HOME") or "~/.local/share").expanduser() / "agent-vault" / "installed.json"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_state() -> dict:
    data = core._read_json(state_file()) or {}
    files = data.get("files")
    return files if isinstance(files, dict) else {}


def _owned(path: Path, current: str, bundled: str) -> bool:
    """Setup may replace or delete `path`: it is the bundled text, or exactly what setup wrote there before."""
    return current == bundled or _read_state().get(str(path)) == _sha(current)


def _plan_agent(plan: Plan, path: Path, bundled: str, uninstall: bool, force: bool) -> None:
    if not path.is_file():
        if not uninstall:
            plan.writes[path] = bundled
        return
    current = path.read_text(encoding="utf-8", errors="replace")
    owned = _owned(path, current, bundled)
    if uninstall:
        if owned or force:
            plan.writes[path] = None
        else:
            plan.notes.append(f"kept {_tilde(path)}: not written by setup or edited since (--force removes it)")
        return
    if current == bundled:
        return
    if owned:
        plan.writes[path] = bundled
    elif force:
        plan.backups.append(path)
        plan.writes[path] = bundled
    else:
        plan.notes.append(f"kept {_tilde(path)}: not written by setup or edited since "
                          f"(--force replaces it and keeps a .bak copy)")


# --------------------------------------------------------------------------- plans


def build_plan(agent: str, scope: str, parts: tuple, ctx: core.Ctx, uninstall: bool, force: bool) -> Plan:
    plan = Plan(agent, scope)
    base = base_dir(agent, scope, ctx.repo_root)
    bundle = plugin_bundle()
    if bundle and bundle_agent() == agent and not force and not uninstall:
        provided = ("agents", "hook") if agent == "claude" else ("hook",)
        skipped = [p for p in parts if p in provided]
        if skipped:
            plan.notes.append(f"skipped {', '.join(skipped)}: the plugin at {_tilde(bundle)} provides them "
                              f"(--force installs them anyway)")
        parts = tuple(p for p in parts if p not in provided)

    if "agents" in parts:
        for path, text in _agent_files(agent, base).items():
            _plan_agent(plan, path, text, uninstall, force)

    if agent == "codex":
        targets = [base / "hooks.json"]
    elif scope == "user":
        targets = [base / "settings.json"]
    else:
        targets = [base / "settings.local.json"] + ([base / "settings.json"] if uninstall else [])
    vault_root = ctx.vault if ctx.vault and not _inside(ctx.vault, ctx.repo_root) else None
    for target in targets:
        data = _load(target)
        before = _dump(data) if target.exists() else None
        if "hook" in parts:
            if uninstall:
                _strip_hooks(data)
            else:
                _add_hook(data)
        if "perms" in parts and agent == "claude":
            allow, dirs = claude_rules(vault_root)
            if uninstall:
                _strip_perms(data, allow, dirs)
            else:
                _add_perms(data, allow, dirs)
        after = _dump(data)
        if after != (before or _dump({})):
            plan.writes[target] = after

    if agent == "codex" and not uninstall and "perms" in parts and vault_root:
        plan.notes.append(f"Codex sandbox: let the vault agents write outside the repo by adding to "
                          f"{_tilde(codex_home() / 'config.toml')}:\n"
                          f"    [sandbox_workspace_write]\n    writable_roots = [{json.dumps(str(vault_root))}]")
    return plan


def _inside(path: Path, root) -> bool:
    if not root:
        return False
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def backup_path(path: Path) -> Path:
    """First free `<name>.bak`, `<name>.bak.1`, … so an earlier backup is never overwritten."""
    candidate, n = path.with_name(path.name + ".bak"), 0
    while candidate.exists():
        n += 1
        candidate = path.with_name(f"{path.name}.bak.{n}")
    return candidate


def apply(plan: Plan) -> None:
    agent_paths = {p for p in plan.writes if p.parent.name == "agents"}
    for path in plan.backups:
        backup_path(path).write_bytes(path.read_bytes())
    for path, text in plan.writes.items():
        if text is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if agent_paths:
        files = _read_state()
        for path in agent_paths:
            text = plan.writes[path]
            if text is None:
                files.pop(str(path), None)
            else:
                files[str(path)] = _sha(text)
        sf = state_file()
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(_dump({"files": dict(sorted(files.items()))}), encoding="utf-8")


def cmd_setup(args) -> int:
    ctx = core.resolve(args.cwd, args.project, args.vault_dir)
    agents = AGENTS if args.agent == "all" else (args.agent,)
    parts = tuple(p.strip() for p in (args.only or ",".join(PARTS)).split(",") if p.strip())
    bad = [p for p in parts if p not in PARTS]
    if bad:
        raise SystemExit(f"vault setup: unknown part(s) {', '.join(bad)}; use {', '.join(PARTS)}")
    verb = "remove" if args.uninstall else "write"
    for agent in agents:
        plan = build_plan(agent, args.scope, parts, ctx, args.uninstall, args.force)
        print(f"{agent} ({args.scope}):")
        if not plan.writes:
            print("  nothing to change")
        for path, text in plan.writes.items():
            action = "delete" if text is None else verb if not path.exists() else "update"
            bak = f" (backup {backup_path(path).name})" if path in plan.backups else ""
            print(f"  {'would ' if args.dry_run else ''}{action} {_tilde(path)}{bak}")
        for n in plan.notes:
            print(f"  note: {n}")
        if not args.dry_run:
            apply(plan)
    if not args.dry_run and not args.uninstall:
        print("Start a new session so the agents and the hook load.")
    return 0


# --------------------------------------------------------------------------- doctor


def cmd_doctor(args) -> int:
    ctx = core.resolve(args.cwd, args.project, args.vault_dir)
    bundle = plugin_bundle() if bundle_agent() else None
    print(f"Python:   {platform.python_version()}")
    print(f"Skill:    {_tilde(skill_dir())}" + (f"  (plugin bundle {_tilde(bundle)})" if bundle else ""))
    print(f"CLI:      {cli_path()}")
    print(f"Vault:    {_tilde(ctx.project_dir) if ctx.project_dir else '-'}  "
          f"(setting '{ctx.dir_setting}' from {ctx.dir_source}; "
          f"{'initialized' if ctx.initialized else 'not initialized'})")
    vloop = find_vloop()
    print(f"vloop:    {vloop['cli'] + ' (contract ' + str(vloop['contract']) + ')' if vloop else 'not found (optional: agent-verify-loop)'}")
    herdr = find_herdr_agent()
    in_herdr = os.environ.get("HERDR_ENV") == "1"
    print(f"herdr:    {herdr or ('not found' if in_herdr else 'not found (optional: herdr-agents)')}")
    problems = 0
    if in_herdr and not herdr:
        problems += 1
        print("  warning: running inside Herdr but the herdr-agent CLI was not found; vault operations fall back "
              "to the Agent tool (install herdr-agents or set HERDR_AGENT)")
    for agent in AGENTS:
        print(f"{agent}:")
        homes = [("user", base_dir(agent, "user", None))]
        if ctx.repo_root:
            homes.append(("project", base_dir(agent, "project", ctx.repo_root)))
        found = []
        for scope, base in homes:
            for src in agent_sources():
                f = base / "agents" / (src.name if agent == "claude" else src.stem + ".toml")
                if f.exists():
                    found.append(f"{src.stem} ({scope})")
        if bundle and bundle_agent() == agent == "claude":
            found.append("vault-keeper, vault-reviewer (plugin, as agent-vault:<name>)")
        print(f"  agents:  {', '.join(found) if found else 'none installed (vault setup --agent ' + agent + ')'}")
        files = ["settings.json", "settings.local.json"] if agent == "claude" else ["hooks.json"]
        hooks = []
        for scope, base in homes:
            for name in files:
                n = count_hooks(base / name)
                if n:
                    hooks.append((f"{_tilde(base / name)}", n))
        from_plugin = bool(bundle) and bundle_agent() == agent
        total = sum(n for _, n in hooks) + (1 if from_plugin else 0)
        desc = [f"{p} ×{n}" for p, n in hooks] + (["plugin hooks/hooks.json"] if from_plugin else [])
        print(f"  hook:    {'; '.join(desc) if desc else 'none'}")
        if total > 1:
            problems += 1
            print("  warning: the session context is injected more than once; keep one hook "
                  "(vault setup --agent " + agent + " --only hook --uninstall removes the settings copies)")
    return 1 if problems else 0
