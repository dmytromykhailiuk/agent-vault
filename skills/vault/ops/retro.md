# Operation: retro

Optional integration with agent-verify-loop (vloop): this operation runs only when a vloop retro dispatches it.

**Inputs:** a retro task file from `vloop retro-plan` (run id, session id, repo root, project slug, the lines `vloop: <abs>`,
`vault_cli: <abs>` and `ops: <abs path of this file>`, the paths of
`metrics.json`, `digest.md` and the per-attempt judge files) or, for a non-repo session, the same without a repo.
Read only the files the task names (plus `plan.json` / `triage.json` next to a judge file when you need them);
never open raw transcripts, never write anything outside the vault.

**Goal:** turn one verified task into evidence-backed lessons: what went well, what went badly and why, counted
across retros, so recurring failures become rules and, at 3 occurrences, proposed harness changes.

`vloop` below is the agent-verify-loop CLI: the path on the task file's `vloop: <abs path>` line, else the `vloop`
key of `vault resolve` (when not null), otherwise skip the ledger baselines and say so in the report. Use
`vault_cli` from the task file as the vault CLI when given.

## Steps
1. **Resolve.**
   - `vault resolve`. Not initialized → bootstrap as `remember` step 1 does. A non-repo session (no repo root in the
     task) writes everything to `--project _harness` instead.
   - `vault resolve --project _harness`. Not initialized → `vault resolve --project _harness --create` and write
     `_harness/index.md` (`type: project`, `status: active`, summary "Cross-project lessons about how the coding agents work
     for this user", a `## Summary` and links to its lessons as you add them).
2. **Read the evidence.** `metrics.json` and `digest.md` fully; skim the judge files (and their `plan.json` /
   `triage.json` when the calibration needs them); `vloop ledger --project <slug> --summary` for baselines. Existing
   lessons: `vault lessons --limit 20` and `vault lessons --project _harness --limit 20`.
3. **Assess** what went well and what went badly across:
   - **outcome quality**: bugs caught before the user saw them; issues that escaped (found later, by the user);
   - **efficiency**: tokens per phase vs the ledger medians; waste signals (re-reads, large outputs, repeated
     failing commands, exploration before asking the vault). Times are wall / active / idle (judge effort by active
     time); `cost_source` says whether a number comes from agent-cost, vloop's own accounting or `claude -p` (triage);
   - **process**: iterations and their root causes, ignored gate blocks, disputes, findings raised late or
     downgraded;
   - **triage calibration**: QA scope vs what QA found or missed (browser QA that found nothing, a bug that checks
     would have caught, an escaped UI bug after `checks`), Opus where Sonnet would do or vice versa, triage cost;
   - **reviewer calibration**: blocking findings the judge downgraded, findings later withdrawn (noise); compare
     them with the reviewer rubric ("Calibration: what blocks and what doesn't" in the
     agent-verify-loop `verify-reviewer` agent file, when your task names it);
   - **environment**: QA blocked reasons, missing run recipes or commands, login state (`auth_missing`,
     `auth_expired`);
   - **communication**: user corrections, interruptions, re-asked questions. Ignore `signals.corrections` in
     `metrics.json`: it counts task notifications and other system turns as corrections. Judge from the real user
     prompts in `digest.md` only.

   Every item cites evidence: a metric, a timestamp, a tool call, a finding id. No evidence → drop the item. At most
   5 "well" + 5 "badly" items.
4. **Root cause and remedy** for each "badly" item. Pick the level:
   - (a) **project lesson** (auto) — a rule for work in this repo → `lessons/` of the project, `scope: project`;
   - (b) **project knowledge gap** (auto) — missing or wrong how-to, gotcha or module knowledge → a guide, problem
     or module note per `ops/remember.md` steps 2–5;
   - (c) **harness lesson** (auto) — how the coding agents work for this user, not tied to one repo → `_harness/lessons/`,
     `scope: harness`;
   - (d) **harness or repo file change** — CLAUDE.md / AGENTS.md, a skill, an agent, hooks, settings, the repo's
     `.claude/verify.json` or a run recipe → a proposal inside the lesson note: `proposal_status: open`,
     `proposal_kind: harness-change | repo-change`, `proposal_target: <path>`, and under `## Proposed change` the
     target path, the exact unified diff and a one-line rationale. **Never edit those files yourself.**
5. **Dedupe and count.** `vault search --type lesson "<terms>"` in the project and with `--project _harness`.
   - Match (same failure pattern) → `occurrences += 1`, `last_seen: <retro date>`, add the evidence link to
     `evidence` and `## Evidence`, sharpen the `## Rule` line if this retro shows it better.
   - No match → create the lesson (§ lesson skeleton in `reference/schema.md`) with `occurrences: 1`,
     `first_seen` = `last_seen` = the retro date, `proposal_status: none` unless step 4 (d) applies.
   - One occurrence = one retro in which the pattern happened again; never count twice within one retro.
   - Pick `audience` deliberately: `implementer`/`orchestrator` lessons reach every session context; `triage`,
     `reviewer`, `qa` lessons reach those agents' task files.
6. **Promotion.** An `active` lesson with `occurrences ≥ 3` and `proposal_status: none` → write a proposal that moves
   the rule into the harness (a CLAUDE.md / AGENTS.md section, or the relevant skill/agent file): `proposal_kind:
   rule-promotion`, `proposal_status: open`, `proposal_target: <file>#<section>`, exact diff under
   `## Proposed change`. The user accepts it (`vault proposal accept <note>` after applying the diff); never apply it yourself.
7. **Write the retro note** `retros/<YYYY-MM-DD>-<kebab-topic>.md` (retro skeleton in `reference/schema.md`;
   frontmatter numbers from `metrics.json`, `outcome` from the loop's closed status). Link lessons ↔ retro both ways
   (the retro's `lessons` field and `## Lessons`; each lesson's `evidence`). Then `vault lint` (and
   `vault lint --project _harness` if you touched it) and fix every error in the notes you touched;
   `vault commit -m "retro: <topic>"`, plus `vault commit --project _harness -m "retro: <topic>"` when the harness
   changed. Never pass `--synced`.

## Final message (≤ 200 words)
```
Retro: <one-line verdict>
Went well: • <item> • <item>
Went badly: • <item> (<evidence>) • <item> (<evidence>)
Lessons: created|updated <path> (×N)
Proposals awaiting approval: <note> — <target>: <one line>   (or: none)
```
