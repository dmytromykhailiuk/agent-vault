# Operation: search

**Inputs** (from the task): the question, optional context (what the main session is working on, relevant files), the
project repo root (your cwd).

**Goal:** a short, correct answer grounded in the vault, checked against code when the notes are stale or silent.
When the vault had a gap, fill it.

## Steps
1. `vault resolve`. If `initialized` is false, answer "No vault for this project yet (the vault skill's `init` creates one)" and
   stop. Note `code_root`.
2. Read `<vault_dir>/index.md` (L1) to orient.
3. Turn the question into English search terms: domain words, likely synonyms, code identifiers. Then run 1–3 searches:
   - `vault search "<terms>"` (vary the terms between runs; add `--type guide|problem|decision|feature|module` to narrow)
   - `vault search --code <repo-relative-path>` when the question is about specific files
   - `vault list --type <type>` when you need the whole map of one kind
   - Identifier queries such as `lockTtlMs` or `getVehicleInfo` work too. When notes are thin, the output adds
     `Code matches … → <notes>` (the files at the target that contain it, and which notes cover them). A line
     `→ no note covers this file` is a vault gap: read that file, answer, and file it back in step 6.
4. Read the 1–3 most relevant notes in full. Follow wikilinks one hop when needed.
5. **Verify** when a note you rely on is flagged `STALE` or `UNVERIFIED`, or when the notes don't fully answer: do
   targeted reads or greps in the code at `code_root`. If it is `null`, run `vault tree` and read under that path.
   Don't explore broadly; answer the question.
6. **File it back** if code taught you something the vault lacked, or if a note was wrong. Follow
   `<skill_dir>/ops/remember.md` steps 2–5: update the existing note rather than creating a new one, fix
   the wrong claims, and link it. Then:
   `vault commit -m "search: <topic>" --stamp <notes you fully re-checked against code>`.
   Skip this when nothing new was learned.
   When Edit/Write are unavailable (read-only run), edit nothing: output one `Vault gap: <one line>` line per item
   instead and set `Vault updated: no`.

## Final message (≤ 300 words)
```
Answer: <direct answer; concrete paths + symbols; steps if it's a how-to>
Sources: <vault note paths>; code checked: <files, or "none">
Confidence: high | medium | low — <why: stale notes, inferred decisions, gaps>
Vault updated: <note paths changed, or "no">
[Vault gap: <one line>]  (only when file-back was impossible)
```
