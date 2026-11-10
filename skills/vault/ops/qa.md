# Operation: qa (read-only review)

**Inputs:** the project repo root (your cwd); optionally a `Focus:` (notes, note types or code areas) that
narrows where you sample claims and questions.

**Goal:** an honest measure of the vault's quality. Do the notes match the code, and does search find the right note?
**Read-only:** no Edit or Write anywhere and no `vault commit`. Only report.

## Steps
1. `vault resolve` to get `code_root`; if it is `null`, run `vault tree` and read code under the printed path. Run
   `vault list` to see the map, and read `index.md`.
2. **Claim audit** (~10 claims). Pick notes across types: 2 features, 1 guide, 2 problems, 1 decision and 4 modules.
   From each, take 1–2 concrete, checkable claims (behavior, symbol, flow step, config key) and verify them in the
   code. Classify each as `correct`, `partial`, `wrong` or `unverifiable`, with the evidence (`path` + `Symbol`).
3. **Search eval** (~8 questions).
   - Write questions a developer would ask, derived from the **code**, not from the notes. Pick real functions,
     flows or config and ask "where/how/why …".
   - Find the true answer in code first.
   - Then search the way the search op does: `vault search "<natural terms>"`, plus 1 retry with other terms.
   - Record the rank of the right note (1–3, or miss) and whether its content actually answers the question.
4. **Coverage.** List important code areas with missing or thin notes.
5. **Structure.** Check that L1 is useful as a 30-second overview, that features read as product capabilities rather
   than code tours, that links help navigation, and that no notes are bloated or duplicated.

## Final message (≤ 450 words)
- **Claim accuracy:** N correct / partial / wrong / unverifiable, listing each wrong or partial one with its note and
  evidence.
- **Search:** hit@1 and hit@3, answer quality, and the misses with why they missed (aliases, summary, missing note).
- **Coverage gaps and structure issues.**
- **Top 5 concrete changes** to the prompts, schema or CLI (`ops/init-*.md`, `reference/schema.md`, search ranking)
  that would fix what you saw.
