# Operation: sync

Optional integration with agent-verify-loop (vloop): runs after a verify-loop **pass**, dispatched from the task file
that `vloop sync-prep --run <id>` writes (or as a section of a `full` retro task). It keeps the vault in step with the
change that just passed.

**Inputs:** a sync task file. Expected fields (one `key: value` line each; paths absolute):

| Field | Meaning |
|---|---|
| `run:` | verify-loop run id |
| `repo_root:` | the project repo |
| `project:` | vault project slug |
| `branch:` | git branch of the change |
| `vault_cli:` | the vault CLI to use (else `vault resolve`'s `cli`) |
| `ops:` | absolute path of this file |
| `diff:` | the change's unified diff (the diff that passed) |
| `brief:` | the verify-loop brief (request, acceptance criteria, changed files) |
| `judge:` | the final judge summary (verdict, findings, which the user disputed or dismissed) |
| `covering:` | `vault covering --format json` output for the changed paths (may be empty) |
| `guardrails:` | `vault guardrails --paths … --format json` output given to the reviewer (may be empty) |
| `changed:` | optional list of changed repo paths (else read them from the diff) |

Read only the files the task names and the code they point at. Never write outside the vault.

**Goal:** facts follow the code automatically; anything that would change the project's rules (conventions, the
SAD, ADRs) arrives as a proposal the user accepts.

## Steps
1. **Resolve.** `vault resolve`. Not initialized → stop with `Sync: skipped (no vault)`. `vault status` says
   `Schema: 1 → 2` → stop with `Sync: skipped (vault needs migration: /vault migrate)`.
2. **Read** the diff, the brief and the judge summary. List the changed paths. Read `covering` (or run
   `vault covering <paths> --format json`) to know which notes describe them.
3. **Facts, applied directly** (as `ops/update.md` steps 4–7 and `ops/remember.md` steps 2–5, but only for the
   changed paths):
   - `features/`, `modules/`, `guides/`: rewrite what the change altered (behavior, symbols, flows, config,
     `code_refs` after renames); create a minimal note for meaningful uncovered code and link it in;
   - `problems/`: a fixed bug becomes `resolved`; a new known limitation becomes a problem note;
   - `decisions/`: a non-architectural choice made in this task (from the brief or judge) → a `dec-NNNN` note,
     `accepted` if the brief says the user decided it, else `inferred`.
4. **Guardrail candidates, proposals only.** Compare the change with `guardrails` and the SAD:
   - a new structural choice (new dependency, integration, boundary, layer, datastore) → `adr-new`: a note in
     `architecture/adr/` (`vault next-id adr --name …`), `status: proposed`;
   - a rule the change follows consistently and that is not yet a convention → `convention-new`,
     `status: proposed`, `level: should`, `source: retro`;
   - a convention the user overrode (the judge marks a guardrail finding as disputed/dismissed by the user, or the
     brief says the rule is outdated) → `convention-change` with the adjusted or retired note;
   - containers, context, deployment or a cross-cutting principle changed → `sad-change` for that section (update
     the C4 diagram in the proposed text).

   Change proposals follow "Proposals" in `reference/schema.md`: `proposals/<name>.md`, `proposal_target`,
   `base_hash` from `vault hash <target>`, the full new text under `## Proposed note`. Cite the evidence (diff hunk,
   finding id) under `## Why`. At most 3 proposals per sync; skip weak ones.
   **Never** set `level: must`, `status: active` on a convention or `status: accepted` on an ADR, and never edit an
   existing convention, SAD section or ADR directly.
5. `vault lint`; fix the errors in the notes you touched. The passed change is usually not committed yet: a `code_ref`
   to a file the change created shows as the warning `code_ref only in the working tree`, not an error. Keep the ref.
6. `vault commit -m "sync: <run id> <short topic>" --stamp <fact notes you fully checked against the code>`. Never
   pass `--synced` (the change may still be on a task branch).

## Final message (≤ 150 words)
```
Sync: <updated N, created M notes | skipped (<reason>)>
Facts: updated|created <path> — <what> (one line each)
Proposals awaiting approval: <note> — <kind> → <target>: <one line>   (or: none)
```
