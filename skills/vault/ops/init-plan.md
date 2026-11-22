# Operation: init-plan

**Inputs:** the repo root (your cwd), depth (`full` | `lite`), and optional scope notes from the user.

**Goal:** understand the repository's shape and split the documentation work into units for parallel workers. Write
the plan and recon files; don't write notes.

## Steps
1. `vault resolve --create`. Then `vault resolve` again to get `slug`, `code_root`, `target` and `target_sha`. If
   `code_root` is `null`, run `vault tree` and use that path as the code root from now on. Let `W` be the
   `work_dir` it prints.
2. If `W/plan.json` already exists and the task doesn't ask for a re-plan, reuse it. Report which units already have
   `W/reports/<id>.md` and skip to the final message.
3. **Recon.** Read only what's needed, in the code root:
   - `README*`, `CLAUDE.md` / `AGENTS.md`, `docs/` index files;
   - the manifests: `package.json` (workspaces), `nx.json`, `project.json` files, `angular.json`, `tsconfig.base.json`
     paths, `pyproject.toml`;
   - the size map: `git ls-files | awk -F/ 'NF>2{print $1"/"$2} NF<=2{print $1}' | sort | uniq -c | sort -rn | head -100`,
     plus source counts per package (`git ls-files <dir> | grep -E '\.(ts|tsx|js|jsx|py)$' | wc -l`);
   - history: `git log --oneline -n 60` and `git log --format='%ad %s' --date=short -n 300 | head -300` (skim for
     big decisions and migrations);
   - the project's Claude Code auto-memory, if it exists: Glob `~/.claude/projects/*/memory/*.md` and pick the
     directory whose name is the repo path with `/` and `_` replaced by `-`. Read those files for project facts.
4. **Modules.** One module note per package, lib or top-level source area.
   - Huge modules (≫ 150 source files): split them into areas with notes `modules/<module>--<area>.md`.
   - Skip generated, vendored, dist, node_modules, fixtures, assets-only and lockfiles.
   - Tests aren't modules: they're read together with their module.
5. **Units.** Group modules into work units of about 60–150 source files each, keeping related modules together (same
   domain, heavy imports between them). Aim for 4–14 units. Give each an id `u01`, `u02`, …
6. Write `W/plan.json`:
   ```json
   {
     "slug": "…", "repo_root": "…", "code_root": "…", "target": "origin/master", "target_sha": "…",
     "depth": "full",
     "units": [
       {"id": "u01", "title": "auth & session", "files": 120,
        "modules": [{"note": "modules/auth.md", "title": "auth", "paths": ["libs/auth/"]}]}
     ],
     "all_module_notes": ["modules/auth.md", "…"]
   }
   ```
7. Write `W/recon.md` (≤ 1500 words). Sections:
   - **Purpose** of the product (from the docs);
   - **Tech stack**;
   - **Architecture**: entry points, layers, config system;
   - **Domain terms** seen;
   - **Commands**: point to `CLAUDE.md` / `AGENTS.md`, don't copy them;
   - **Rule sources** for the guardrail candidates: the `CLAUDE.md` / `AGENTS.md` / docs lines that state rules
     (file:line), linter/formatter/tsconfig configs and architecture tests found (paths only);
   - **Notable history**: migrations, decisions from commit messages;
   - **Facts from auto-memory**;
   - **Open questions**.

## Final message (≤ 300 words)
- A table of units: `id | title | modules | source files`.
- Totals: module notes, expected L2 notes (rough), and a rough token estimate for the workers (≈ source files × 1.5k
  tokens).
- 2–4 notable observations.
- The last line exactly: `UNITS: u01 u02 …`.
