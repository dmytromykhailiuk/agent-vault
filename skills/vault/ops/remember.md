# Operation: remember

**Inputs:** a knowledge brief from the main session: what was learned or decided, why, evidence (files, symbols),
and the branch it happened on. You also get the project repo root (your cwd).

**Goal:** store each durable piece of knowledge exactly once, in the right note, linked into the graph.

## Steps
1. `vault resolve`. If `initialized` is false:
   - run `vault resolve --create`;
   - write a stub `index.md` (`type: project`, `status: active`) with a one-paragraph Summary taken from the repo
     README, a `## What it does` section, and links to what you add.

   Then continue: the vault can grow from `remember` alone.
2. **Classify** each item in the brief:

   | Item | Goes to |
   |---|---|
   | A choice that shapes structure, boundaries, dependencies, integrations or quality attributes | an ADR `architecture/adr/adr-NNNN-<name>.md` (path from `vault next-id adr --name <name>`). New ADRs are `status: proposed`, `proposal_status: open`, `proposal_kind: adr-new`, even when decided in the session: the user accepts them with `vault proposal accept`. Deduced only → `inferred`. |
   | Any other choice (process, tooling, local implementation) | `decisions/dec-NNNN-<name>.md` (`vault next-id decision --name <name>`), with `kind`. Status is `accepted` if decided in the session, `inferred` if only deduced. |
   | A rule for how code is written here ("always…", "never…", "we do X via Y") | `conventions/<name>.md`. Stated by the user → `status: active`, `source: user`, `level: must` only when the user said "must / always / never / завжди / ніколи", else `should`. Deduced or proposed by you → `status: proposed`, `proposal_status: open`, `proposal_kind: convention-new`, `level: should`. |
   | A change to an existing convention, SAD section or ADR | never edit it: write a change proposal (`proposals/<name>.md`, see "Proposals" in `reference/schema.md`). The only exception is the user explicitly dictating the new text in the brief. |
   | System context, containers, deployment, a cross-cutting principle | the matching `architecture/<section>.md`. A new section is written as `status: proposed`; an existing one changes via a `sad-change` proposal. |
   | Bug, gotcha, trap, limitation, tech debt | `problems/<name>.md`. Status is `open`, or `resolved` if the brief says it was fixed. |
   | "How to do X" | `guides/<name>.md` |
   | How some code or flow works | the relevant `modules/<module>.md`, or `features/<feature>.md` if it's user-facing. Create a minimal module note if none exists. |
   | A domain term | `glossary.md` |

   See "Conventions, architecture, ADRs and decisions" in `reference/schema.md` for the ADR/decision boundary.
   Ephemeral task details, plans and anything already in the repo's `CLAUDE.md` / `AGENTS.md` don't belong in the vault: drop them.
3. **Dedupe.** `vault search "<key terms>"` (and `--code <path>` for the files involved). If a note already covers it,
   edit that note: add or adjust a section, fix outdated claims, and resolve the problem if it's fixed now.
4. **Verify cheaply.** Check the cited paths and symbols exist (`code_root` from resolve; `vault tree` if null). If
   the change exists only on a task branch, not on the target yet, say so in the note:
   `On branch <x>, not yet in <target>.`
5. **Write** per `reference/schema.md`: frontmatter, `## Summary`, citations, budgets. Link it in both directions: the
   new note from a relevant feature, module or index, plus a back-link.
6. `vault lint`. Fix the errors in the notes you touched.
7. `vault commit -m "remember: <short topic>"`. Add `--stamp <note>` only for notes you fully checked against the
   target code. Never pass `--synced`.

## Final message (≤ 150 words)
One line per note: `created|updated <path> — <what changed>`. Add one line on anything you dropped or couldn't verify.
