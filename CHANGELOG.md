# Changelog

## Unreleased

- `vault version [--json]` and the integration contract (1) with agent-verify-loop; `vloop` discovery in
  `vault resolve` / `vault doctor`; agent skipping by name suffix (`agent-vault:`, `agent-verify-loop:` namespaces);
  `path` in `vault lessons --format json`; the retro no longer hardcodes the vloop path.
- `vault graph`: an interactive HTML graph of the project's notes (links, code files, staleness) with a
  detail panel that renders the clicked note.

## 0.1.0

- First release: the `vault` skill (Python CLI, operations, schema), the `vault-keeper` and `vault-reviewer` agents,
  the SessionStart hook, Claude Code and Codex plugin manifests, and `vault setup` / `vault doctor` for installs that
  only copy the skill (skills.sh).
