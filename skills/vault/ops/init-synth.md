# Operation: init-synth

**Inputs:** the repo root (your cwd) and depth (`full` | `lite`).

**Goal:** turn the plan, the recon and the worker reports into the L2 and L1 layers, link everything, lint and commit.

## Steps
1. `vault resolve` to get the slug, `vault_dir` and `link_prefix`. Let `W` be the `work_dir` it prints. Read
   `reference/schema.md`, `W/plan.json`, `W/recon.md`, every `W/reports/*.md`, and (`full` only)
   `W/candidates.json` with the user's answers in `W/decisions.json`. Don't read code broadly; do
   targeted reads only to settle conflicts between reports or to confirm a feature's flow.
2. **`lite` only:** create a stub note for every module in the plan with `status: draft`, a specific `summary` from
   the recon, `code_refs` = the module paths, and a `## Summary` saying details are pending.
3. Write the **features** (`features/<name>.md`): merge feature candidates across reports into coherent user-facing
   capabilities. That's usually 5–20; cross-unit features are the most valuable. Follow the feature skeleton, and in
   "How it works" walk the flow across modules with links and citations taken from the reports and module notes.
4. Write the **guides** (`guides/*.md`): the 3–10 most useful how-tos for changing this codebase. Prefer recurring
   tasks like adding a step, adding a config option or adding a lib.
5. Write the **problems** (`problems/*.md`): dedupe across reports; status `open`; kind and severity in the body.
   Merge tiny ones into one note per area if needed.
6. **Guardrails, only what the user confirmed** (`full`; join `candidates.json` and `decisions.json` by `id`):
   - `verdict: rejected` → write nothing;
   - **conventions** (`conventions/<name>.md`, convention skeleton): `accepted` → `status: active`,
     `source: init-confirmed`, `level` exactly as in `decisions.json` (never `must` unless it says `must`), the
     user's `edits` applied; `unanswered` → `status: proposed`, `source: inferred`, `level: should`. `Do` / `Don't`
     examples come from the evidence;
   - **ADRs** (`class: adr`) → `architecture/adr/adr-NNNN-<name>.md` from `vault next-id adr --name <name>`;
     **decisions** (`class: decision`) → `decisions/dec-NNNN-<name>.md` from `vault next-id decision --name <name>`
     with `kind`. Accepted by the user → `status: accepted`, `**Source:** confirmed by the user at init` plus the
     doc or commit source if any; `unanswered` → `status: proposed` with `**Evidence:**` (not a guardrail until
     the user accepts it). Never invent context or options. Run `next-id` before each write so numbers don't collide;
   - **SAD**: the 9 arc42 notes `architecture/<section>.md` (`goals`, `constraints`, `context`, `building-blocks`,
     `runtime`, `deployment`, `crosscutting`, `quality`, `risks`) with the Mermaid diagrams from "C4 diagrams" in
     the schema (`context` → `C4Context`, `building-blocks` → `C4Container`, `runtime` → `sequenceDiagram`,
     `deployment` → `C4Deployment`). Accepted sections → `status: active`; `unanswered` → `status: proposed`; a
     rejected section is not written (the arc42 hub just lacks it). Goals and constraints use the user's `answer`;
     `constraints` lists `C-n:` items and `crosscutting` `P-n:` items. Diagram elements use module note names as aliases; link module and feature
     notes from the text;
   - link every convention from the module notes it scopes (or from `architecture/crosscutting.md`), every ADR from
     `architecture/building-blocks.md` or the modules it affects, so none is an orphan.

   `lite`: no guardrail notes; non-architectural decisions found in the recon go to `decisions/` as `inferred`.
7. Write **`glossary.md`** from the reports' glossary items and the recon's domain terms.
8. Write **`index.md`** (L1, ≤ 1200 tokens) per the skeleton: Summary, What it does (→ features), Architecture at a
   glance (→ key modules), Tech stack, Where to start (→ guides, glossary, hubs), Repo docs (`CLAUDE.md` / `AGENTS.md` / README by
   name, not copied).
9. **Back-links.** Fill the `## Used by` section in each module note with the features that use it. Make sure every
   content note has an inbound link from another content note.
10. `vault lint` (links to `<folder>/_index` hubs already resolve; step 11 writes them). Fix all errors and the cheap warnings (orphans, non-absolute links, missing Summary, symbols not found:
    check against code). Repeat until there are no errors.
    Then run `vault coverage` (and `vault coverage --depth 3` for big repos). For each listed directory with real logic,
    add its key files to the `code_refs` of the right module note, mentioning them in the body, or write a note for it.
11. `vault commit -m "init @<target12>" --stamp-all --synced`.

## Final message (≤ 300 words)
- Counts per type (features, guides, problems, decisions, ADRs, conventions, architecture, modules), plus whether a
  glossary was written.
- Guardrails: conventions by level and status, ADRs, decisions, SAD sections (`active` / `proposed`).
- **Review list:** titles of the `inferred` decisions and ADRs, conventions left `proposed`, and the high-severity
  open problems.
- Remaining lint warnings by kind.
- Anything the workers flagged as uncertain.
