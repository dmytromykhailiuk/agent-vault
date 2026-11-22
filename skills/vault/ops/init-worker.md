# Operation: init-worker

**Inputs:** the repo root (your cwd) and a unit id.

**Goal:** write the L3 module notes for your unit from the actual code, and a report that the synthesizer turns into
features, guides, problems, decisions, guardrail candidates (conventions, ADRs, the SAD) and the glossary.

## Steps
1. `vault resolve` to get the slug, `vault_dir` and `link_prefix`. Let `W` be the `work_dir` it prints. Read
   `W/plan.json` and find your unit. Use its `code_root` for all code reads; if that path no longer exists, run
   `vault tree`.
2. If `W/reports/<unit>.md` exists and every module note of the unit exists, reply `already done: <unit>` and stop.
3. **Read the code smartly.** Start with the entry points and the public API: `index.ts`, `public-api.ts`,
   `*.module.ts`, exported classes, facades, services, state (NgRx actions, reducers, effects, selectors), routes,
   config schemas. Then read the key internals.
   - Skim specs for intended behavior.
   - Don't read every file of a big module; prioritize what explains how it works.
   - Grep for usages across the repo when you need to know who uses an API.
4. **Write one note per module** in your unit at `<vault_dir>/<note path from plan>`.
   - Follow the module skeleton in `reference/schema.md`: `type: module`, `status: active`, a specific `summary`,
     useful `aliases`, 3–12 specific `code_refs`, `verified: ""`.
   - Cite `path` + `Symbol` for every behavior claim. Mark guesses `Unverified:`.
   - Link related modules only through paths listed in `all_module_notes` (`[[<link_prefix>modules/x]]`). Leave `## Used by`
     empty; the synthesizer fills it.
   - Budget: ≤ 2500 tokens per note. Split big modules into the `--<area>` notes the plan lists, or into a new
     `modules/<module>--<area>.md` if really needed (mention it in the report).
5. **Write the report** `W/reports/<unit>.md`:
   ```markdown
   # Report <unit>
   ## Notes written
   - modules/x.md — <summary>
   ## Feature candidates
   - <user-facing capability>: <what the user gets> — modules: x, y — evidence: `path` `Symbol`
   ## Problems
   - <title> (bug|gotcha|debt|limitation; high|medium|low): <1–2 lines> — evidence: `path` `Symbol`
   ## Decisions observed
   - <choice> [adr|decision; high|low]: <why it looks deliberate> — evidence: `path` `Symbol`, docs lines
   ## Conventions observed
   - <rule, one imperative sentence> [must|should; high|low] — scope: `src/api/**` — evidence: `path:line` ×N,
     `CLAUDE.md:12`, `.eslintrc.json` rule `no-restricted-imports` — counterexamples: none | `path:line`
   ## Architecture observed
   - <section: constraints|context|building-blocks|runtime|deployment|crosscutting|quality|risks>: <fact> —
     evidence: `path` `Symbol` / config / docs lines
   ## Guide candidates
   - How to <common change>: <3–6 step sketch> — evidence
   ## Glossary
   - <term>: <definition> — evidence
   ## Cross-module relations
   - x → y: <relationship>
   ```
   Report only what the code shows. Problems include TODO/FIXME/HACK clusters, workarounds, risky patterns and
   surprising behavior. Decisions are deliberate patterns (e.g. "custom IndexedDB store instead of a library"), not
   trivia.

   **Guardrail candidates** (the user confirms them later; you only report):
   - **Sources**, strongest first: rules stated in `CLAUDE.md` / `AGENTS.md` / docs (cite the file and line);
     linter, formatter and compiler configs (`.eslintrc*`, `eslint.config.*`, `biome.json`, `.prettierrc*`,
     `tsconfig*.json` `strict`/`paths`, `ruff`/`mypy` sections of `pyproject.toml`, `.editorconfig`); architecture
     tests (dependency-cruiser, ArchUnit-style specs, `nx` `enforce-module-boundaries` tags); then repeated patterns
     in the code of your unit.
   - **Convention**: one durable rule about how code is written (layering, imports, naming, error handling, an API
     pattern). Skip what a formatter already enforces mechanically unless a doc states it as a rule.
   - **ADR vs decision**: would breaking it change a box or an arrow of the C4 diagrams (structure, boundaries,
     dependencies, integrations, datastores, quality attributes)? Yes → `adr`; process, tooling or a local
     implementation choice → `decision`.
   - **Level**: propose `must` only when a doc says must/always/never or a config/architecture test enforces it as
     an error; otherwise `should`.
   - **Confidence**: `high` when an explicit source (doc line, config, architecture test) states it, or you saw ≥ 3
     consistent instances and no counterexample; else `low`. List counterexamples you saw.
   - **Architecture**: external systems and integrations (context), deployable units and datastores (building
     blocks), main runtime flows, deployment hints (Dockerfiles, CI, IaC), cross-cutting concepts (auth, logging,
     error handling, i18n), stated constraints (runtime versions, platforms, licences). Goals and quality targets
     are rarely visible in code; report them only with a doc source.
6. Don't run `vault lint` and don't commit. Links to other units' notes stay broken until those workers finish; the
   synthesizer lints and commits everything.

## Final message (≤ 15 lines)
`done: <unit>`, one line per note written, then the counts of feature, problem, decision, convention, architecture,
guide and glossary items in the report.
