# Operation: lint

**Inputs:** the project repo root (your cwd).

**Goal:** a healthy vault, with no errors and as few warnings as is cheap to fix.

## Steps
1. `vault resolve` (must be `initialized`), then `vault lint`.
2. Fix the **errors**:

   | Error | Fix |
   |---|---|
   | Broken link | Point it at the right note, or remove it. |
   | Missing field | Add it. |
   | `code_ref only in the working tree` | Warning: the file is not committed at the target yet. Keep the ref; it clears once the change lands. |
   | `code_ref not found` | Find the new path (`git log --follow --diff-filter=R --name-status -- <old path>` or a grep for the symbol) and fix the ref and the text. |
   | Possible secret | Remove it. |
   | Convention without `level` / `rule` | Add `rule` from the note's `## Rule`. For `level`, use `should` and say in the report that the user decides on `must`. |
   | Invalid or duplicate `section` | Rename the section to one of the arc42 names or merge the duplicate into the existing note. |
   | Change proposal: target or block missing | Fix `proposal_target` / the `## Proposed note` fence; never accept it yourself. |
   | Schema < 2: needs migration | Don't fix it here; recommend `/vault migrate` in the report. |
3. Fix the cheap **warnings**:

   | Warning | Fix |
   |---|---|
   | Non-absolute link | Rewrite it vault-absolute. |
   | Missing `## Summary` | Write one. |
   | Orphan | Link it from the right feature, module, index or guide. |
   | Symbol not found | Check the code; fix the name or mark it `Unverified:`. |
   | Duplicate alias | Keep it on the better note. |
   | Over budget | Split into a sub-note. |
   | Broad glob | Replace it with the specific files. |
   | Scope matches no file | Fix the glob after a rename; if the code is gone, write a `convention-change` proposal (retire it). |
   | No C4 diagram / no C-n, P-n items | Leave it and list it; SAD content changes go through proposals. |
   | Decision looks architectural | List it; moving it is the user's call (`vault mv`, or `/vault migrate`). |
   | Proposal needs rebase | List it; the dispatcher runs `ops/proposal-rebase.md`. |
4. **Stale or unverified notes.**
   - Few of them (≤ 10): handle them as in `ops/update.md` steps 2, 4 and 5.
   - More: leave them and recommend `vault update` (the vault skill).
5. `vault coverage`. For directories with real logic and no note, add refs to the right module note or recommend a
   note in your report.
6. `vault lint` again, then `vault commit -m "lint: fixes" --stamp <notes you re-checked against code>`.

## Final message (≤ 120 words)
Errors fixed, warnings fixed, warnings remaining by kind, and whether `vault update` is recommended.
