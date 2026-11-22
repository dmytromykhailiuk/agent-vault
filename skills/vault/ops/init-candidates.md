# Operation: init-candidates

Also used by `migrate` (`Source: migrate` in the task); differences are marked **migrate:**.

**Inputs:** the repo root (your cwd); `Source: init|migrate`.

**Goal:** turn the guardrail material of the worker reports into one deduplicated `candidates.json` that the main
session puts to the user. You write no notes.

## Steps
1. `vault resolve`. Let `W` be the `work_dir`. Read `reference/schema.md` ("Conventions, architecture, ADRs and
   decisions", "C4 diagrams"), `W/recon.md` and the `## Conventions observed`, `## Architecture observed` and
   `## Decisions observed` sections of every `W/reports/*.md`.
   **migrate:** there are no worker reports. Read `W/migrate-plan.json` (its `decisions` list) and, for each listed
   note, the note itself. Then scan the repo yourself with the rules of `ops/init-worker.md` § "Guardrail
   candidates" — lighter than init: start from `CLAUDE.md` / `AGENTS.md`, linter/formatter/tsconfig configs and
   architecture tests, use the existing `modules/` and `features/` notes (`vault list`) for structure, and read code
   only to count instances or find counterexamples. Skip conventions and SAD facts that already exist as notes.
2. **Merge and dedupe.** One candidate per rule or decision. Merging same-rule items from several units: union the
   evidence and scopes; confidence is `high` only if one of them is `high` and no unit reports a counterexample. A
   rule with counterexamples is `low` and says so.
3. **Classify** decisions ADR vs decision with the schema's test (box or arrow of the C4 diagrams → ADR).
   **migrate:** one candidate per existing `decisions/` note, with `path` set; classify it, propose `kind` for the
   ones that stay decisions, and give the reason in `why`.
4. **Draft the SAD** (arc42) from recon and the architecture items: `context` (external systems, users),
   `building-blocks` (containers ≈ deployable units, datastores; module notes as components), `runtime` (1–3 key
   flows), `deployment`, `crosscutting` (`P-n` items), `quality`, `risks` (from high-severity problems). `goals` and
   `constraints` are rarely visible in code: draft what the docs say and always set `"question": true` on them, with
   a `question` the user can answer in one line. Other sections get `"question": false`; their confidence is `high`
   when the evidence is explicit.
5. Write `W/candidates.json` (schema below), then validate it: `python3 -c "import json,sys;json.load(open(sys.argv[1]))" W/candidates.json`.
   Order: conventions, ADRs, decisions, architecture. Ids are stable: `conv-01…`, `adr-01…`, `dec-01…`,
   `arc-<section>`.

## `candidates.json`
```json
{
  "version": 1,
  "source": "init",
  "slug": "acme",
  "candidates": [
    {"id": "conv-01", "type": "convention", "name": "no-direct-db-in-api",
     "title": "No direct DB access from API handlers",
     "rule": "API handlers under src/api/ never import src/db/ directly; they go through a repository.",
     "level": "must", "scope": ["src/api/**"], "confidence": "high",
     "evidence": ["CLAUDE.md:14", ".eslintrc.json no-restricted-imports", "src/api/users.ts UsersHandler"],
     "counterexamples": [], "why": "Stated in CLAUDE.md and enforced by ESLint."},
    {"id": "adr-01", "type": "adr", "name": "repository-layer", "title": "Repository layer for DB access",
     "decision": "All DB access goes through repositories in src/db/repos/.", "status": "accepted",
     "confidence": "high", "evidence": ["docs/architecture.md:20"], "why": "…", "path": null},
    {"id": "dec-01", "type": "decision", "name": "jest", "title": "Jest for unit tests", "kind": "tooling",
     "decision": "Unit tests use jest.", "status": "inferred", "confidence": "low", "evidence": ["package.json"],
     "why": "…", "path": null},
    {"id": "arc-goals", "type": "architecture", "section": "goals", "title": "Goals", "confidence": "low",
     "question": true, "question_text": "What are the 2–3 top quality goals (e.g. offline-first, latency)?",
     "draft": "…", "items": [], "evidence": ["README.md:3"]},
    {"id": "arc-crosscutting", "type": "architecture", "section": "crosscutting", "title": "Cross-cutting concepts",
     "confidence": "high", "question": false, "draft": "…", "items": ["P-1: Errors are returned as Result, never thrown."],
     "evidence": ["src/lib/result.ts Result"], "mermaid": null}
  ]
}
```
- `level` (convention): the proposed level; `must` only under the worker rule (doc says must/always/never, or a
  config/test enforces it as an error).
- `status` (adr/decision): `accepted` when docs or commit history state it, else `inferred`.
- `path`: the existing note's project-relative path (**migrate:** set on every existing decision; `type` is then
  the proposed class). `null` for new items.
- `items`: `C-n:` lines for `constraints`, `P-n:` lines for `crosscutting`; `mermaid`: the diagram draft for the
  diagram sections (see "C4 diagrams" in the schema).

## Final message (≤ 120 words)
Counts by type and confidence (`conventions 7 (5 high, 2 low), adrs 3, decisions 4, architecture 9 (2 questions)`),
the path of `candidates.json`, and anything that looked contradictory between units.
