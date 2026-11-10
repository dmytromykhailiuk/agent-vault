# Operation: update

**Inputs:** the project repo root (your cwd). Optionally a focus (paths or notes) from the main session.

**Goal:** bring the notes in line with the code on the target branch (`origin/<default>`), so it matches what is
merged, not the current task branch.

## Steps
1. `git fetch --quiet origin` (ignore failures, e.g. expired credentials; then say in your report that the local
   `origin/<default>` may be old). Then `vault resolve`; it must be `initialized`.
2. Get the code root: `code_root` from resolve. If it is `null`, run `vault tree` and use the printed path. Read code
   only there, because the working tree may be a task branch.
3. `vault affected`. It gives JSON with these fields:
   - `stale_notes`: refs changed since each note's `verified`, or refs missing at the target
   - `unverified_notes`
   - `renames`, `deleted_refs`
   - `uncovered_changes`: changed files that no note references, grouped by directory
4. **Stale notes.** For each one:
   - read the note;
   - run `git diff <verified> <target_sha> -- <its changed files>` (in your cwd);
   - read the current code where the diff isn't enough;
   - rewrite only what changed: behavior, symbols, flows, config;
   - fix `code_refs` for renames and deletions;
   - set the status: a fixed problem becomes `resolved`, a removed feature becomes `deprecated`.
5. **Unverified notes.** Check them against the code, fix them, and stamp them.
6. **Uncovered changes.** For groups with meaningful new code (a new module, feature or flow), create or extend notes
   and link them in. Ignore trivial churn.
7. **Ripple up.** If features appeared, disappeared or changed meaning, update `features/*`, the core-feature list in
   `index.md`, and `glossary.md`.
8. `vault lint`. Fix the errors in the notes you touched.
9. Commit:
   `vault commit -m "update <since12>..<target12>" --stamp <every note you checked> --synced`.
   - Pass `--synced` only if every stale and unverified note was handled.
   - If you run low on context, commit what's done without `--synced` and list the remaining notes. A re-run picks
     them up, because staleness is tracked per note.

## Final message (≤ 200 words)
- Range synced (or "partial") and target.
- Counts: notes updated / created / resolved / deprecated.
- Remaining stale notes, if any.
- Anything suspicious you saw (e.g. a decision that the code now contradicts).
