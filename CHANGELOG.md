# Changelog

## 0.3.0 — 2026-11-22

- Vault schema 2 (`_meta.json` `schema: 2` for new vaults): `conventions/` (one rule per note, `level` must/should,
  `rule`, `scope` globs), `architecture/` (arc42 sections with Mermaid C4 diagrams, `C-n` constraints and `P-n`
  principles), `architecture/adr/` for architectural ADRs while `decisions/` keeps the rest (`dec-NNNN`), and
  `proposals/`. Note types follow the deepest folder; hubs sort conventions must-first, the SAD in arc42 order and
  ADRs by number.
- `vault guardrails [--paths …] [--format bullets|json] [--budget N]` (integration contract 2) and a ≤ 900-char
  guardrails digest in the session context. `vault version --json` adds `contracts: [1, 2]` and `schema`.
- Proposals for any note: `adr-new`, `convention-new`, `convention-change`, `sad-change`. A change proposal carries
  `base_hash`; `vault proposal accept` writes nothing and exits 3 with `needs-rebase` when the target changed, and
  the new `proposal-rebase` op carries the change over and returns a diff for the user. New `vault hash`.
- `vault mv <old> <new>` moves a note and rewrites every wikilink to it; `vault next-id adr|decision`.
- Lint: convention `level`/`rule` errors, scope, C4 and `C-/P-` item warnings, duplicate arc42 sections,
  architectural-looking decisions, proposal checks, and an error for schema-1 vaults (`/vault migrate`).
- New `sync` op for agent-verify-loop: after a pass the keeper updates fact notes and proposes ADR, convention and
  SAD changes; the keeper never sets `must`, `active` or `accepted` itself.
- Interactive `init`: workers report `Conventions observed`, `Architecture observed` and ADR-vs-decision
  classification with evidence, proposed level and confidence; the new `init-candidates` phase (opus) merges them
  into `candidates.json` with an arc42 draft; the main session confirms them with the user (`init-confirm`: one
  question for the confident list, separate questions for doubtful items and arc42 goals/constraints) into
  `decisions.json`; `init-synth` writes only what was confirmed (conventions `active` / `init-confirmed`, `must`
  only when confirmed; unanswered → `proposed`), ADRs to `architecture/adr/`, other decisions to `decisions/`, and
  the 9 arc42 notes with Mermaid C4.
- `vault migrate --plan | --apply <decisions.json> [--dry-run] [--format json]` and the `/vault migrate` op for
  schema-1 vaults: refuses a dirty vault, records a git checkpoint, creates the new folders and hubs, moves the
  decisions the user classified as ADR to `architecture/adr/` (number kept, links rewritten, old path as alias),
  sets `kind` on the rest, gates on lint, sets `schema: 2` and commits `migrate schema 1→2`. Steps are ordered per
  schema (`MIGRATIONS` in `migrate.py`), idempotent and resumable (`_meta.json` `migration`); a schema-2 vault is a
  no-op. The keeper writes the confirmed conventions and SAD notes before the apply.
- `vault guardrails --paths` accepts space- or comma-separated paths and repeats; the session digest shows
  `--paths <file> <file>...`.
- `vault commit --stamp a --stamp b` stamps every note (only the last one was stamped).
- Lint: a `code_ref` that exists only in the repo working tree is a warning, not an error, so the post-pass `sync`
  lint works before the change is committed; links to the hub of an existing folder resolve before `vault commit`
  writes it (init-synth lints before the commit).

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
