# agent-vault

A per-project knowledge vault for coding agents (Claude Code and Codex). It keeps Obsidian-style markdown notes about
each repository: the overview, features, how-to guides, known problems, decisions and module internals. Every note
cites code (`path` + `Symbol`), and the vault tracks which notes went stale as the code moved on.

The main session never reads the vault itself. It dispatches each operation to a separate agent, so the session
context stays small:

| Piece | What it does |
|---|---|
| `vault` skill | Dispatcher: search, remember, update, lint, review, init, status, graph, setup, doctor. |
| `vault-keeper` agent | Runs every operation that answers or writes: search, remember, update, lint, init phases (and the optional agent-verify-loop retro). |
| `vault-reviewer` agent | Read-only audit: checks note claims against code, measures search hit@1/hit@3, finds coverage gaps. |
| SessionStart hook | `vault context --hook`: injects the project's summary, sync state and the usage rules at session start. |
| `vault` CLI | Python 3.10+, no dependencies: resolve, search, list, affected, lint, coverage, commit, graph, tree, lessons, setup, doctor. |

## Install

Requirements: `git` and `python3` ≥ 3.10 on `PATH`.

### Claude Code plugin (skill + agents + hook)

```
/plugin marketplace add dmytromykhailiuk/agent-vault
/plugin install agent-vault@agent-vault
```

Everything is wired up; the agents appear as `agent-vault:vault-keeper` and `agent-vault:vault-reviewer`. Optionally
pre-approve the CLI and your vault dir with `vault setup --agent claude --only perms`.

### Codex plugin (skill + hook)

```
codex plugin marketplace add dmytromykhailiuk/agent-vault
```

Install `agent-vault` from `/plugins`, then add the agents (Codex plugins can't ship custom agents):

```
<skill_dir>/bin/vault setup --agent codex --only agents
```

### skills.sh (skill only, any agent)

```
npx skills add dmytromykhailiuk/agent-vault -g -a claude-code -a codex
```

skills.sh copies only the skill folder, so install the agents, the hook and the permissions once:

```
~/.claude/skills/vault/bin/vault setup --agent claude     # skill dir for Claude Code
~/.agents/skills/vault/bin/vault setup --agent codex      # skill dir for Codex
```

Or just ask the agent: `/vault setup` (Claude Code) or `$vault setup` (Codex). Without the agents the skill still
works: it runs a general-purpose sub-agent that reads `skills/vault/agents/<agent>.md` as its instructions.

### `vault setup` and `vault doctor`

```
vault setup [--agent claude|codex|all] [--scope user|project] [--only agents,hook,perms] [--dry-run] [--uninstall] [--force]
vault doctor
```

| Agent | agents | hook | perms |
|---|---|---|---|
| Claude Code | `~/.claude/agents/vault-*.md` | SessionStart in `~/.claude/settings.json` | allow rules for the CLI, the skill dir and an out-of-repo vault, plus `additionalDirectories` |
| Codex | `~/.codex/agents/vault-*.toml` | SessionStart in `~/.codex/hooks.json` | prints the `writable_roots` line for an out-of-repo vault |

`--scope project` writes to `<repo>/.claude/settings.local.json`, `<repo>/.claude/agents/`, `<repo>/.codex/…`.
Setup is idempotent and keeps every other key in those files. It replaces or removes only the agent files it wrote
itself (hashes in `${XDG_DATA_HOME:-~/.local/share}/agent-vault/installed.json`); an agent file you wrote or edited
is kept unless you pass `--force`, which saves a `.bak` copy first. When the skill runs from an installed plugin,
setup skips what the plugin already provides. `vault doctor` shows what is installed where and warns when the hook would
run twice.

## Use

`/vault <op>` in Claude Code, `$vault <op>` in Codex, or plain words ("how does checkout work in this project?",
"remember that …").

| Operation | What happens |
|---|---|
| `vault search <question>` | The keeper answers from the notes, checks stale notes against code, and files back what was missing. |
| `vault remember [text]` | The session writes a knowledge brief; the keeper stores each item once, in the right note. |
| `vault update` | Brings the notes in line with `origin/<default>`: stale notes, renames, uncovered changes. |
| `vault lint` | Fixes broken links, missing fields, wrong code refs, orphans. |
| `vault review [focus]` | The reviewer audits claims against code and search quality; read-only. |
| `vault init [--dir …] [--save …] [--depth full\|lite]` | Generates the vault for a repo: planner, parallel workers, synthesizer. Expensive; only on request. |
| `vault graph [--no-stale] [--out <file>] [--no-open]` | Writes an interactive graph of the project's notes to `<work_dir>/graph.html` and opens it: notes as nodes, wikilinks as edges, code files and lessons/retros behind toggles, stale/unverified notes marked, and a click shows the rendered note with its links, backlinks and code refs. The page loads cytoscape.js, marked and DOMPurify from cdnjs. |
| `vault status` / `vault doctor` | Vault state / installation state. |

## Where the vault lives

The vault dir is a path template; the first hit wins:

1. `--vault-dir` (`vault init --dir` passes it);
2. `<repo>/.agent-vault.local.json` (personal, added to `.git/info/exclude`), then `<repo>/.agent-vault.json`
   (shared with the team): `{"vault_dir": "<template>"}`;
3. legacy Claude Code settings: `env.CLAUDE_VAULT_DIR` in `<repo>/.claude/settings.local.json` / `settings.json`;
4. environment: `AGENT_VAULT_DIR`, `CLAUDE_VAULT_DIR`, `VAULT_ROOT`;
5. `${XDG_CONFIG_HOME:-~/.config}/agent-vault/config.json`, then `env.CLAUDE_VAULT_DIR` in `~/.claude/settings.json`;
6. default `vault` (`<repo>/vault`).

`~` expands, `{project}` becomes the project slug. A template ending in `/{project}` (e.g. `~/agent-vault/{project}`)
is a **shared** vault: one git repo and one Obsidian vault for every project. Anything else is a **per-repo** vault
(e.g. `docs/vault`), committed by you together with the code.

```
<vault_dir>/
  index.md  glossary.md  log.md  _meta.json
  features/  guides/  problems/  decisions/  modules/
  lessons/  retros/          # used by the optional agent-verify-loop retro
```

The full note format is in [`skills/vault/reference/schema.md`](skills/vault/reference/schema.md); each operation's
procedure is in [`skills/vault/ops/`](skills/vault/ops).

## Migrating from a hand-installed `~/.claude/skills/vault`

The CLI reads `CLAUDE_VAULT_DIR` and `CLAUDE_VAULT_HARNESS_DIR`, so an existing vault (e.g. `~/claude-vault/{project}`)
resolves unchanged. Remove the old SessionStart hook and agent before installing the plugin, or run `vault doctor`
afterwards: it warns when two hooks inject the context.

## Integration with agent-verify-loop

agent-verify-loop (`vloop`) is optional. Both CLIs answer `version --json` with a `contract` number (currently 1) so
each can check the other.

- **Discovery.** `vault resolve` and `vault doctor` report `vloop`: env `VLOOP_CLI`, then `vloop` on `PATH`, then the
  Claude plugin cache, the Codex plugin cache, and the skill dirs (`~/.claude/skills/verify-loop`,
  `~/.agents/skills/verify-loop`). A candidate counts only when `vloop version --json` names `agent-verify-loop`; a
  legacy install without a `version` command is accepted as contract 0. vloop finds this CLI the same way
  (env `AGENT_VAULT_CLI`, `PATH`, plugin caches, skill dirs).
- **What vloop uses.** `vault resolve`, `vault lessons --format json`, `vault covering --format json`,
  `vault proposal`, and retros through the `vault-keeper` agent plus `ops/retro.md`. The retro task file carries
  `vloop:`, `vault_cli:` and `ops:` lines.
- **When one is absent.** Without vloop the vault works unchanged (retro ledger baselines are skipped). Without the
  vault, vloop skips lessons and retro notes.

## Development

```
python3 -m unittest discover -s tests
python3 scripts/sync_agents.py          # copy skills/vault/agents/*.md to the plugin's agents/
python3 scripts/sync_agents.py --check  # CI: fail when they drift
```

Layout: `skills/vault/` is the skill (what skills.sh installs; it carries the canonical agent files),
`agents/` and `hooks/` are the Claude Code plugin parts, `.claude-plugin/` holds the Claude Code (and skills.sh)
manifests, `.codex-plugin/` and `.agents/plugins/` the Codex ones.

## License

MIT
