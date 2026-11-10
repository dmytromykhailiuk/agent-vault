---
name: vault
description: Per-project knowledge vault (Obsidian-style markdown in a configurable vault dir) covering the project overview, features, how-to guides, known problems, decisions, and module internals with code links. Every vault operation runs in a separate vault-keeper agent (reviews in a read-only vault-reviewer agent); this skill only dispatches. Use it to answer "how does X work / where is Y" before exploring code (vault search), to record durable knowledge such as decisions, gotchas, bug causes, how-tos and flows (vault remember), to sync the docs after code changes (vault update), to run a health check (vault lint), to audit the notes against the code (vault review), to show status (vault status), to install or check the agents and hook (vault setup, vault doctor), or to generate docs for a repo (vault init, only on explicit request). Triggers include "how does … work in this project", "remember this", "write it down in the docs", "update the docs", "document the project", "що ми знаємо про…", "як працює … в проекті", "запам'ятай це", "онови доку", "задокументуй проект", "vault", "project knowledge base".
argument-hint: "<search|remember|update|lint|review|status|init|setup|doctor> [args]"
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/bin/vault *)
---

# vault (dispatcher)

The knowledge vault is the `vault_dir` that `$V resolve` prints (see "Location"). **Every interaction with it runs in
a separate agent**: `vault-keeper` for every operation that writes or answers, `vault-reviewer` for the read-only
review. In this session you **never** Read, Grep, Glob or Edit files under the vault dir. You only dispatch the
operation and relay the agent's answer. The exceptions are `status`, `doctor`, `setup`, `lessons`, `covering` and
`proposal` (scripts that print metadata or that the user runs on purpose).

**Paths.** `<skill_dir>` is the folder that holds this `SKILL.md` (Claude Code: `${CLAUDE_SKILL_DIR}`; in Codex, the
path the skill was loaded from). `$V` is `<skill_dir>/bin/vault` as an absolute path; it needs `python3` ≥ 3.10.
`$V resolve` prints both as `skill_dir` and `cli`. Always type `$V` the same way so permission rules match.

## Operations

| Request | What you do | Agent | Model (Claude Code) | Mode |
|---|---|---|---|---|
| `vault status` | Run `$V status` and show the output. | none | n/a | n/a |
| `vault doctor` | Run `$V doctor` and show the output. | none | n/a | n/a |
| `vault setup [args]` | Run `$V setup --dry-run [args]`, show it, and only after the user agrees run it without `--dry-run`. | none | n/a | n/a |
| `vault search <question>` | Dispatch `search` | vault-keeper | sonnet | foreground |
| `vault remember [text]` | Write a knowledge brief, dispatch `remember` | vault-keeper | sonnet | foreground |
| `vault update` | Dispatch `update` | vault-keeper | sonnet | background |
| `vault lint` | Dispatch `lint` | vault-keeper | sonnet | background |
| `vault review [focus]` | Dispatch `qa` | vault-reviewer | sonnet | background |
| `vault init [--dir <path\|template>] [--save local\|project\|user] [--depth full\|lite]` | Read `<skill_dir>/ops/init.md` and orchestrate it (several agents) | vault-keeper | opus/sonnet | background |
| retro (from verify-loop) | Dispatch `retro` with the task file verify-loop gives you | vault-keeper | sonnet | background |

Invocation: `/vault <op>` in Claude Code, `$vault <op>` in Codex, or plain words. With no subcommand, infer it:
- a question about the project → search;
- "remember / write it down / запам'ятай" → remember;
- "update the docs / онови доку" → update;
- "check the docs against the code" → review;
- "document the project / задокументуй проект" → init.

Only init when the user explicitly asks; it is expensive.

## Location

The vault dir is a path template. First hit wins:
1. `--vault-dir` on the CLI (`vault init --dir` passes it);
2. `<repo>/.agent-vault.local.json` (personal, git-excluded), then `<repo>/.agent-vault.json` (shared with the team),
   as `{"vault_dir": "<template>"}`;
3. legacy Claude Code settings: `env.CLAUDE_VAULT_DIR` in `<repo>/.claude/settings.local.json`, then
   `<repo>/.claude/settings.json`;
4. the environment: `AGENT_VAULT_DIR`, then `CLAUDE_VAULT_DIR`, then `VAULT_ROOT` (a root; `/{project}` is appended);
5. user config `${XDG_CONFIG_HOME:-~/.config}/agent-vault/config.json`, then `env.CLAUDE_VAULT_DIR` in
   `~/.claude/settings.json`;
6. the default `vault`, i.e. `<repo>/vault`.

`~` expands, `{project}` becomes the project slug, and a relative path is relative to the repo root. When the last
component is `{project}` (e.g. `~/agent-vault/{project}`) the vault is **shared**: one root holds every project,
links start with `<slug>/`, and the root is its own git repo. Anything else is a **per-repo** vault: the dir is its
own Obsidian vault and links have no prefix. A vault inside the code repo is never git-committed by the CLI; the user
commits it with their code. `$V status` shows the effective setting and its source.

## 1. Resolve

Run `$V resolve`. You need `repo_root`, `slug`, `initialized`, `vault_dir`, `work_dir`, `skill_dir` and `cli`. Then,
by case:
- **No `repo_root`**: not a git repo. Use `--project <slug>` only if the user named a vault project; otherwise say
  so and stop.
- **Not initialized:**
  - search, review: tell the user there is no vault yet and that `vault init` creates one;
  - remember: continue, because the agent bootstraps a minimal vault;
  - update or lint: suggest `vault init`.

## 2. Compose the task

The agent sees nothing of this conversation, so the task must be self-contained:

```
Operation: <op>
Repo root: <repo_root>
Skill dir: <skill_dir>
CLI: <cli>
<inputs>
Follow <skill_dir>/ops/<op>.md (after reading <skill_dir>/reference/schema.md).
```

Inputs by operation:
- **search**:
  - `Question: <the question>`;
  - `Context: <1–3 lines: what you are working on and why you need this>`;
  - `Relevant files: <repo-relative paths, if any>`.
- **remember**: a `Knowledge brief:` that you write from this conversation. Use one bullet per item with:
  - what was learned or decided, and why;
  - evidence as repo-relative paths and `Symbol`s;
  - whether the user decided it or it was deduced.

  Add `Branch: <git branch --show-current>`. Be specific; leave out ephemeral task chatter. If the user passed text,
  build the brief around it.
- **update**: optional `Focus: <paths or areas>`.
- **lint**: nothing extra.
- **qa** (review): optional `Focus: <notes, note types or code areas>`.

## 3. Launch the agent

Set the agent's working directory to `<repo_root>` where the agent supports it; otherwise the task's `Repo root:`
line tells it where to run.

- **Claude Code**: the Agent tool with `subagent_type` `vault-keeper` or `vault-reviewer` (installed from the plugin
  they appear as `agent-vault:vault-keeper` / `agent-vault:vault-reviewer`; use whichever is listed), the model from
  the table and `prompt: <task>`. Background ops use `run_in_background: true`; you are notified when they finish, so
  don't poll.
- **Codex**: spawn the custom agent named `vault-keeper` or `vault-reviewer` with the task as its message. Wait for
  foreground ops; let background ops run while you continue.
- **The named agent isn't installed** (not in the agent list): spawn a general-purpose sub-agent whose task starts
  with `Read <skill_dir>/agents/<agent>.md and follow it as your instructions.` followed by the task. Afterwards tell
  the user once that `vault setup` installs the agents and the session hook (`vault doctor` shows what is missing).
- **No sub-agent support at all**: do the operation yourself, following `<skill_dir>/agents/<agent>.md` and the op
  file. This is the only case where you read the vault directly.

Never approve a sub-agent's permission prompt on the user's behalf; show it and ask.

## 4. Relay

- **search**: use the answer in your work. Tell the user the gist plus the source notes when they asked the question
  themselves. Treat `Confidence: low` or stale sources as hints, and verify in code before relying on them.
- **remember, update, lint, init**: summarize the agent's report in 2–5 lines. Don't paste long outputs.
- **review**: give the scores (claim accuracy, search hit@1/hit@3), the wrong claims and the top fixes. Offer
  `vault update` or `vault lint` for what the keeper can fix; changes to ops/schema files are the user's call.

## When to use proactively

The session-start hook repeats these rules:
- **Before exploring unfamiliar code** or answering how part of the project works: search first.
- **After a non-obvious fix, decision or discovery**: remember. Batch the items at the end of the task, in one
  dispatch.
- **If the session context says the vault is many commits behind**: suggest `vault update` to the user. Don't run
  it unasked; it costs tokens.
