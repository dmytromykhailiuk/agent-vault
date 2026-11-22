# Phase: init-confirm (for the MAIN session)

Run by the dispatcher itself, not by a keeper: keepers cannot ask the user. Used by `init` (after `init-candidates`)
and `migrate` (after the classification keeper). The only vault-side files you touch are `<work_dir>/candidates.json`
(read) and `<work_dir>/decisions.json` (write); never read or edit notes.

## Steps
1. Read `<work_dir>/candidates.json` (`work_dir` from `$V resolve`). Split the candidates:
   - **A**: `confidence: high` and not `question: true`;
   - **B**: `confidence: low` and not `question: true`;
   - **C**: `question: true` (arc42 goals and constraints).
2. **One question for list A.** Open with one plain sentence of context (what is being confirmed and why). The
   question text lists every A item on its own line: `conv-01 MUST — <rule>`, `adr-01 ADR — <decision>`,
   `dec-02 decision (tooling) — <decision>`, `arc-context — <one-line gist>`; for an existing note (`path` set) say
   `<path> → ADR` or `<path> stays a decision (<kind>)`. Options:
   - **Accept all** — every A item is accepted as proposed;
   - **Accept with edits** — ask the user for the edits in free text (which ids to drop, which levels to change,
     rewordings), apply them literally, and accept the rest;
   - **Review one by one** — move every A item to list B.
   Skip the question when A is empty.
3. **List B, then C, at most 4 questions per AskUserQuestion call**, one item per question:
   - convention: options `Accept as MUST`, `Accept as SHOULD`, `Reject` (put the proposed level first and mark it
     recommended); the question shows the rule, scope, evidence and counterexamples in one or two lines;
   - ADR/decision (new): `Accept as ADR`, `Accept as decision`, `Reject`;
   - existing note (migrate): `Move to architecture/adr/ (ADR)`, `Keep in decisions/`; there is no reject: the note
     stays where it is unless the user says ADR;
   - architecture section: `Accept the draft`, `Reject`; for C items show `question_text` and the draft, and take
     the user's free-text answer ("Other") as the content.
   Write all user-facing text in the user's language (Ukrainian when the session is in Ukrainian).
4. Never upgrade a level the user did not confirm: `must` only when the user picked it or accepted a list that
   showed it as MUST.
5. If the user stops answering or says "later", mark the rest `unanswered` and continue.
6. Write `<work_dir>/decisions.json`:
   ```json
   {
     "version": 1,
     "source": "init",
     "answered": "2026-11-21",
     "items": [
       {"id": "conv-01", "verdict": "accepted", "level": "must", "edits": null},
       {"id": "conv-02", "verdict": "rejected"},
       {"id": "conv-03", "verdict": "unanswered"},
       {"id": "adr-01", "verdict": "accepted", "class": "adr"},
       {"id": "dec-01", "verdict": "accepted", "path": "decisions/adr-0004-jest.md", "class": "decision",
        "kind": "tooling"},
       {"id": "dec-02", "verdict": "accepted", "path": "decisions/adr-0001-db.md", "class": "adr"},
       {"id": "arc-goals", "verdict": "accepted", "answer": "Offline-first; scans under 2 s on mid-range phones."}
     ]
   }
   ```
   - one item per candidate id; `verdict`: `accepted`, `rejected` or `unanswered`;
   - `level` (convention): the confirmed level; `class` (adr/decision): the confirmed class; `kind` for a decision;
   - `path`: copied from the candidate for existing notes (migrate); `vault migrate --apply` acts only on items
     with `path`, `verdict: accepted` and `class`;
   - `edits`: the user's wording changes for that item, verbatim; `answer`: free-text answers (C items).
7. Tell the user in one line how many items were accepted, rejected and left open, then continue the calling op.
