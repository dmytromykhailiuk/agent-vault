# Operation: proposal-rebase

**Inputs:** the proposal note (link or path) that `vault proposal accept` refused with `needs-rebase` (exit code 3),
and the project repo root (your cwd).

**Goal:** carry the proposal's change over to the current version of its target note, keeping every edit made to the
target after the proposal was written, so the user can accept it without re-creating it.

## Steps
1. `vault resolve`. Read the proposal note and its target (`proposal_target`). Only `convention-change` and
   `sad-change` proposals are rebased; anything else → report `not a change proposal` and stop.
2. Reconstruct what the proposal meant to change: compare the `## Proposed note` text with the target as it was at
   `base_hash`. If the vault is its own git repo, find that version with
   `git -C <vault_root> log -p -- <target path>` (read-only); otherwise infer it from the proposal's `## Why`.
3. Apply that change to the **current** target text. Keep everything that changed in the target since then. When a
   later edit contradicts the proposal (same sentence changed both ways), keep the current text and say so under
   `## Why` as `Conflict: …`; never silently drop either side.
4. Write the result back into the proposal: the new full text under `## Proposed note` (4-backtick fence), and
   `base_hash` = `vault hash <target>` of the current target. Don't touch `proposal_status` and never edit the target
   note itself.
5. `vault lint` (the proposal must no longer warn `needs rebase`), then
   `vault commit -m "proposal-rebase: <proposal link>"`.
6. Never run `vault proposal accept`: the user decides after seeing the diff.

## Final message
```
Rebased: <proposal link> → <target link> (base_hash <old> → <new>)
Conflicts: <one line each, or none>
Diff (current target → proposed):
<unified diff of the current target text against the new proposed text, ≤ 80 lines; summarize the rest>
```
