# Operation: init — orchestration (for the MAIN session)

The main session coordinates and never reads notes or code for the docs itself. Each phase is a separate vault-keeper
agent, launched as described in `SKILL.md` § "Launch the agent" (with the `Skill dir:` and `CLI:` lines). Scratch files live in the `work_dir` printed by
`vault resolve`, and the agents read and write them; you only pass ids around.

Arguments:
- `--depth full|lite` (default `full`). `lite` = L1 + L2 + module stubs, no workers.
- `--dir <path|template>`: where the vault goes, e.g. `docs/vault` (relative to the repo root) or
  `~/vaults/{project}` (`{project}` = the slug). Without it the configured vault dir applies (see `SKILL.md`
  § "Location"; default `<repo>/vault`).
- `--save local|project|user` (default `local`): where `--dir` is remembered: `<repo>/.agent-vault.local.json`
  (git-excluded), `<repo>/.agent-vault.json` (shared with the team) or `~/.config/agent-vault/config.json`.

## Steps
1. With `--dir`: `vault resolve --create --vault-dir <dir> --save <scope>`, which creates the skeleton and stores the
   setting so every agent resolves the same vault. Without it: `vault resolve`. Tell the user the `vault_dir`. If
   `initialized` was already true and notes exist, ask the user (AskUserQuestion in Claude Code, a plain question
   elsewhere) whether to run `vault update` instead, to re-init (the planner reuses an existing plan, and workers skip finished units), or to cancel.
2. **Plan.** Launch the agent in the background with:
   - operation `init-plan`, model **opus** (Claude Code; Codex uses the session model), label `vault-init-plan`;
   - inputs: repo root, depth, and anything the user said about scope (e.g. "skip e2e/").

   Its final message is a plan summary ending with a line `UNITS: u01 u02 …`.
3. **Approve.** Show the user the plan summary: units, files per unit, expected notes, and token estimate. Ask them to
   proceed, adjust (re-run the planner with their notes) or cancel.
4. **Workers** (skip for `lite`). For each unit id, launch the agent with:
   - operation `init-worker`, model **sonnet**, label `vault-init-<unit>`;
   - inputs: repo root, unit id.

   Run at most **5 at a time**, as parallel sub-agents launched in one message. Start the next batch as agents
   finish. A worker whose unit is already done replies "already done"; that's fine. Collect only their short final messages, and retry a failed unit once.
5. **Guardrail candidates** (skip for `lite`). When all workers are done, launch the agent in the background with:
   - operation `init-candidates`, model **opus**, label `vault-init-candidates`;
   - inputs: repo root, `Source: init`.

   It writes `<work_dir>/candidates.json` and replies with counts.
6. **Confirm** (skip for `lite`): follow `<skill_dir>/ops/init-confirm.md` yourself. It asks the user one question
   for the high-confidence list and separate questions for the doubtful items and the arc42 goals/constraints, and
   writes `<work_dir>/decisions.json`.
7. **Synthesis.** Launch the agent in the background with:
   - operation `init-synth`, model **opus**, label `vault-init-synth`;
   - inputs: repo root, depth.
8. **Report** to the user, from the synth's final message:
   - what was created, including the conventions (MUST/SHOULD), ADRs and SAD sections;
   - inferred decisions, conventions left `proposed` and open problems to review;
   - remaining lint warnings.

   Then, by `vault_git` from `vault resolve`:
   - `own`: suggest `git -C <vault_root> show --stat`;
   - `host-repo`: the vault is inside the code repo and was not committed; tell the user to review and commit
     `<vault_dir>` with their code.

   Also suggest opening `<vault_root>` in Obsidian, and offer `vault tree --remove` if a worktree was created.

Long phases (plan, synth, big workers) run in the background where the agent supports it; you are notified when
they finish, so don't poll. Never approve a sub-agent's permission dialog on the user's behalf.
