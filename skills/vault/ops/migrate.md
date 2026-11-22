# Operation: migrate (for the MAIN session, with keeper phases)

Upgrades an existing vault to the current schema (1 → 2: `conventions/`, `architecture/` with arc42 + C4,
`architecture/adr/`, `proposals/`). Runs **only** on an explicit `/vault migrate`; never start it because the
session context or lint says the vault needs migration.

Split of work:
- `vault migrate --plan` and `vault migrate --apply` do the deterministic parts: clean-tree check, checkpoint,
  folders, hubs, moving notes the user classified as ADR (`vault mv` semantics: number kept, links rewritten, old
  path in `aliases`, `type: adr`), `kind` on the decisions that stay, lint gate, `schema: 2`, commit
  `migrate schema 1→2`. Progress lives in `_meta.json` `migration`, so a re-run after a failure resumes; every step
  is idempotent.
- Writing new convention and SAD notes needs judgement, so a keeper does it before `--apply`; the apply's lint gate
  and single commit cover those notes too.

## Steps
1. `$V resolve`. Not initialized → tell the user there is nothing to migrate.
2. **Plan.** Run `$V migrate --plan --format json`.
   - `status: up-to-date` → relay its `message` and stop.
   - Exit 1 with `uncommitted changes` → show the listed paths; the user commits or discards them (for a vault in
     the code repo, commits it with their code), then re-run. Don't clean the tree yourself.
   - `planned` / `resuming` → note `checkpoint`, `rollback` and the `decisions` count. On `resuming` with every
     `apply` step done, skip to step 5.
3. **Classify and scan.** Launch the keeper (foreground or background, model **opus**, label `vault-migrate-scan`)
   with operation `init-candidates` and the input `Source: migrate`. It classifies every existing decision ADR vs
   decision with confidence, scans the repo for conventions and the SAD, and writes `<work_dir>/candidates.json`.
4. **Confirm**: follow `<skill_dir>/ops/init-confirm.md` yourself (one question for the high-confidence list,
   separate questions for the doubtful items and for arc42 goals/constraints). It writes
   `<work_dir>/decisions.json`.
5. **Write and apply.** Launch the keeper (background, model **opus**, label `vault-migrate-apply`) with operation
   `migrate` and the input `Phase: apply`. It follows "Keeper: apply phase" below.
6. **Report** from its final message: ADRs moved, decisions kept (with `kind`), conventions by level and status,
   SAD sections, remaining lint warnings, the commit, and the rollback command from step 2 (`git … restore
   --source=<checkpoint> …`; after the commit, the user can also `git revert` it).

## Keeper: apply phase
**Inputs:** repo root, `Phase: apply`.
1. `vault resolve`; let `W` be the `work_dir`. Read `reference/schema.md`, `W/migrate-plan.json`,
   `W/candidates.json` and `W/decisions.json`.
2. Write the confirmed **convention** and **SAD** notes exactly as `ops/init-synth.md` step 6 says (accepted →
   `active` / `source: init-confirmed` / the confirmed level; unanswered → `proposed`, `level: should`; rejected →
   nothing). New ADRs or decisions without `path` (none usually) go through `vault next-id`. Don't touch the
   existing `decisions/` notes: `--apply` moves them. Linking to an existing decision by its old path is fine;
   the move rewrites the link.
3. `vault migrate --apply W/decisions.json --dry-run` and check the listed moves and `kind` changes match the
   decisions file.
4. `vault migrate --apply W/decisions.json`. On exit 1 with `lint errors`, fix the listed errors in the notes and
   run it again (it resumes; finished steps are skipped). Pre-existing errors count too; fix them minimally.
5. On a step failure (an exception), re-run the same command once; if it fails again, stop and report the error
   and the `migration` state from `_meta.json`.

## Final message (≤ 200 words)
The `migrated` line and the commit, ADRs moved (old → new path), decisions kept with `kind`, conventions written
(MUST/SHOULD, active/proposed), SAD sections written, lint warnings left, anything you could not resolve.
