# Changelog

## Unreleased

## 0.2.0 — 2026-11-15

- The SessionStart hook also runs on `resume` (plugin and `vault setup`; re-running `vault setup` upgrades an older
  matcher). A resumed Claude Code session gets the context only when it differs from the last vault context in its
  transcript, and vault/verify agents resumed without `--agent` (Herdr's native restore) are still recognized from the
  transcript. Codex rollout transcripts are not parsed, so a resumed Codex session gets the context again.
- `retro` op: reads the four times wall / active / waiting / idle and `cost_source` / `cost_provider` from
  agent-verify-loop's metrics; repo change proposals target `.agent-verify-loop.json`.
- `vault version [--json]` and the integration contract (1) with agent-verify-loop; `vloop` discovery in
  `vault resolve` / `vault doctor`; agent skipping by name suffix (`agent-vault:`, `agent-verify-loop:` namespaces);
  `path` in `vault lessons --format json`; the retro no longer hardcodes the vloop path.
- `vault graph`: an interactive HTML graph of the project's notes (links, code files, staleness) with a
  detail panel that renders the clicked note.

## 0.1.0

- First release: the `vault` skill (Python CLI, operations, schema), the `vault-keeper` and `vault-reviewer` agents,
  the SessionStart hook, Claude Code and Codex plugin manifests, and `vault setup` / `vault doctor` for installs that
  only copy the skill (skills.sh).
