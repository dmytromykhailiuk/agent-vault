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
   | `code_ref not found` | Find the new path (`git log --follow --diff-filter=R --name-status -- <old path>` or a grep for the symbol) and fix the ref and the text. |
   | Possible secret | Remove it. |
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
4. **Stale or unverified notes.**
   - Few of them (≤ 10): handle them as in `ops/update.md` steps 2, 4 and 5.
   - More: leave them and recommend `vault update` (the vault skill).
5. `vault coverage`. For directories with real logic and no note, add refs to the right module note or recommend a
   note in your report.
6. `vault lint` again, then `vault commit -m "lint: fixes" --stamp <notes you re-checked against code>`.

## Final message (≤ 120 words)
Errors fixed, warnings fixed, warnings remaining by kind, and whether `vault update` is recommended.
