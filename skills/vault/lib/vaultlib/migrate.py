"""Schema migrations of a project vault (`vault migrate`)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import core
from . import frontmatter as fmlib
from . import gitutil as g

DECISION_KINDS = ("process", "tooling", "implementation")
CLASSES = ("adr", "decision")
NUMBERED_STEM_RE = re.compile(r"^(?:adr|dec)-(\d{4})(?:-(.*))?$")
PLAN_FILE = "migrate-plan.json"


class MigrationError(Exception):
    pass


@dataclass
class Run:
    ctx: core.Ctx
    state: dict
    decisions: list = field(default_factory=list)
    dry_run: bool = False
    changes: list = field(default_factory=list)


@dataclass
class Step:
    name: str
    phase: str  # plan: runs on --plan; apply: needs the user's decisions
    description: str
    fn: Callable[[Run], None]


# --------------------------------------------------------------------------- steps 1 → 2


def _folders(run: Run) -> None:
    for folder in core.FOLDERS.values():
        d = run.ctx.project_dir / folder
        if not d.is_dir():
            run.changes.append(f"create {folder}/")
            if not run.dry_run:
                d.mkdir(parents=True, exist_ok=True)


def _hubs(run: Run) -> None:
    if run.dry_run:
        missing = [f for f in core.FOLDERS.values() if not (run.ctx.project_dir / f / "_index.md").exists()]
        run.changes += [f"write {f}/_index.md" for f in missing]
        return
    for p in core.write_hubs(run.ctx):
        run.changes.append(f"write {p.relative_to(run.ctx.project_dir).as_posix()}")


def adr_target(prel: str, number: int) -> str:
    stem = Path(prel).stem
    m = NUMBERED_STEM_RE.match(stem)
    if m:
        rest = f"-{m.group(2)}" if m.group(2) else ""
        return f"{core.FOLDERS['adr']}/adr-{m.group(1)}{rest}.md"
    return f"{core.FOLDERS['adr']}/adr-{number:04d}-{core.slugify(stem)}.md"


def _accepted(run: Run, cls: str) -> list:
    return [d for d in run.decisions if d.get("path") and d.get("verdict") == "accepted" and d.get("class") == cls]


def _move_adrs(run: Run) -> None:
    pdir = run.ctx.project_dir
    moves = run.state.setdefault("moves", {})
    number = core.next_number(pdir)
    for item in _accepted(run, "adr"):
        src = _prel(item["path"])
        if src not in moves:
            if not (pdir / src).is_file():
                run.changes.append(f"skip {src}: not found")
                continue
            if core.note_folder(src) != core.FOLDERS["decision"]:
                run.changes.append(f"skip {src}: not in decisions/")
                continue
            moves[src] = adr_target(src, number)
            if not NUMBERED_STEM_RE.match(Path(src).stem):
                number += 1
    if not run.dry_run:
        _save_state(run)
    for src, dst in moves.items():
        if not (pdir / src).exists() and (pdir / dst).exists():
            continue
        res = core.move_note(run.ctx, pdir / src, dst, dry_run=run.dry_run)
        run.changes.append(f"move {src} → {dst} (links rewritten in {len(res['rewritten'])} note(s))")


def _decision_kind(run: Run) -> None:
    pdir = run.ctx.project_dir
    moved = set(run.state.get("moves", {}))
    for item in _accepted(run, "decision"):
        prel = _prel(item["path"])
        path = pdir / prel
        if prel in moved or not path.is_file():
            continue
        kind = str(item.get("kind") or "").lower()
        text = path.read_text(encoding="utf-8")
        current = str(fmlib.read(text)[0].get("kind") or "")
        kind = kind if kind in DECISION_KINDS else (current if current in DECISION_KINDS else "implementation")
        if current == kind:
            continue
        run.changes.append(f"set kind: {kind} on {prel}")
        if not run.dry_run:
            path.write_text(fmlib.set_field(text, "kind", kind), encoding="utf-8")


MIGRATIONS: dict = {
    1: [
        Step("folders", "plan", "create conventions/, architecture/, architecture/adr/ and proposals/", _folders),
        Step("hubs", "plan", "write the hubs of the new folders", _hubs),
        Step("move-adrs", "apply", "move decisions the user classified as ADR to architecture/adr/ (number kept, "
                                   "links rewritten, old path kept in aliases)", _move_adrs),
        Step("decision-kind", "apply", "set `kind` (process|tooling|implementation) on the decisions that stay",
             _decision_kind),
    ],
}


# --------------------------------------------------------------------------- state


def _prel(path: str) -> str:
    p = str(path).strip().removeprefix("./")
    return p if p.endswith(".md") else p + ".md"


def _save_state(run: Run) -> None:
    meta = core.read_meta(run.ctx.project_dir)
    meta["migration"] = run.state
    core.write_meta(run.ctx.project_dir, meta)
    run.ctx.meta = meta


def pending_steps(schema: int) -> list:
    out = []
    for v in range(schema, core.VAULT_SCHEMA):
        out += [(v, s) for s in MIGRATIONS.get(v, [])]
    return out


def in_progress(ctx: core.Ctx) -> dict | None:
    st = core.read_meta(ctx.project_dir).get("migration")
    return st if isinstance(st, dict) else None


def up_to_date(ctx: core.Ctx) -> bool:
    return core.is_pseudo(ctx.slug) or (core.vault_schema(ctx) >= core.VAULT_SCHEMA and not in_progress(ctx))


def _git_scope(ctx: core.Ctx) -> tuple:
    host = core.git_host(ctx.vault) or ctx.vault.resolve()
    return host, str(ctx.project_dir)


def dirty_paths(ctx: core.Ctx) -> list:
    host, scope = _git_scope(ctx)
    rc, out, _ = g.git(host, "status", "--porcelain", "--", scope)
    if rc != 0:
        return []
    return [ln[3:] for ln in out.splitlines() if ln.strip()]


def _run_steps(run: Run, phase: str) -> list:
    done = set(run.state.get("done", []))
    ran = []
    for version, step in pending_steps(run.state["from"]):
        key = f"{version}:{step.name}"
        if step.phase != phase or key in done:
            continue
        step.fn(run)
        ran.append(key)
        if not run.dry_run:
            run.state.setdefault("done", []).append(key)
            _save_state(run)
    return ran


def _steps_view(state: dict) -> list:
    done = set(state.get("done", []))
    return [{"name": s.name, "from": v, "phase": s.phase, "description": s.description, "done": f"{v}:{s.name}" in done}
            for v, s in pending_steps(state["from"])]


def decision_notes(ctx: core.Ctx) -> list:
    out = []
    for n in core.load_notes(ctx.project_dir, ctx.vault):
        if n.type != "decision" or core.note_folder(n.prel) != core.FOLDERS["decision"]:
            continue
        out.append({"path": n.prel, "link": n.link, "title": n.title, "summary": n.summary, "status": n.status,
                    "kind": str(n.fm.get("kind") or ""), "tags": core._as_list(n.fm.get("tags")),
                    "code_refs": n.code_refs})
    return out


# --------------------------------------------------------------------------- plan / apply


def plan(ctx: core.Ctx, dry_run: bool = False) -> dict:
    if up_to_date(ctx):
        return {"status": "up-to-date", "slug": ctx.slug, "schema": core.vault_schema(ctx),
                "message": f"{ctx.slug} is already at schema {core.VAULT_SCHEMA}; nothing to migrate"}
    state = in_progress(ctx)
    resuming = state is not None
    if not resuming:
        dirty = dirty_paths(ctx)
        if dirty:
            raise MigrationError("the vault has uncommitted changes; commit or discard them first:\n  "
                                 + "\n  ".join(dirty[:20]))
        host, _ = _git_scope(ctx)
        state = {"from": core.vault_schema(ctx), "to": core.VAULT_SCHEMA, "started": core.today(),
                 "checkpoint": g.rev_parse(host, "HEAD"), "done": [], "moves": {}}
    run = Run(ctx, state, dry_run=dry_run)
    if not dry_run:
        _save_state(run)
    _run_steps(run, "plan")
    host, scope = _git_scope(ctx)
    cp = state.get("checkpoint")
    data = {
        "status": "resuming" if resuming else ("dry-run" if dry_run else "planned"),
        "slug": ctx.slug, "from": state["from"], "to": state["to"],
        "checkpoint": cp,
        "rollback": (f"git -C {host} restore --source={cp} --staged --worktree -- {scope} && "
                     f"git -C {host} clean -fdq -- {scope}") if cp else None,
        "steps": _steps_view(state),
        "changes": run.changes,
        "decisions": decision_notes(ctx),
        "plan_file": str(ctx.work_dir / PLAN_FILE) if ctx.work_dir else None,
    }
    if not dry_run and ctx.work_dir:
        ctx.work_dir.mkdir(parents=True, exist_ok=True)
        (ctx.work_dir / PLAN_FILE).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                                              encoding="utf-8")
    return data


def load_decisions(path: Path) -> list:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise MigrationError(f"cannot read decisions file {path}: {e}") from None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise MigrationError(f"{path}: expected an object with an 'items' list")
    bad = [i for i in items if isinstance(i, dict) and i.get("path") and i.get("verdict") == "accepted"
           and i.get("class") not in CLASSES]
    if bad:
        raise MigrationError(f"{path}: an accepted item with a path needs class adr|decision: {bad[0].get('path')}")
    return [i for i in items if isinstance(i, dict)]


def apply(ctx: core.Ctx, decisions: list, dry_run: bool = False) -> dict:
    if up_to_date(ctx):
        return {"status": "up-to-date", "slug": ctx.slug, "schema": core.vault_schema(ctx),
                "message": f"{ctx.slug} is already at schema {core.VAULT_SCHEMA}; nothing to migrate"}
    state = in_progress(ctx)
    if state is None:
        raise MigrationError("no migration in progress: run `vault migrate --plan` first")
    if dry_run:
        state = json.loads(json.dumps(state))
    run = Run(ctx, state, decisions=decisions, dry_run=dry_run)
    _run_steps(run, "plan")
    _run_steps(run, "apply")
    result = {"slug": ctx.slug, "from": state["from"], "to": state["to"], "changes": run.changes,
              "checkpoint": state.get("checkpoint")}
    if dry_run:
        return {**result, "status": "dry-run"}
    core.write_hubs(ctx)
    errors = [i for i in lint_issues(ctx) if i["level"] == "error"]
    if errors:
        return {**result, "status": "lint-failed", "lint": errors}
    meta = core.read_meta(ctx.project_dir)
    history = list(meta.get("migrations") or [])
    history.append({"from": state["from"], "to": state["to"], "date": core.today(),
                    "checkpoint": state.get("checkpoint")})
    meta["migrations"] = history
    meta["schema"] = state["to"]
    meta.pop("migration", None)
    core.write_meta(ctx.project_dir, meta)
    ctx.meta = meta
    return {**result, "status": "migrated"}


def lint_issues(ctx: core.Ctx) -> list:
    notes = core.load_notes(ctx.project_dir, ctx.vault)
    all_notes = notes
    if ctx.layout == "shared":
        all_notes = []
        for s, _ in core.list_projects(ctx.vault):
            all_notes.extend(notes if s == ctx.slug else core.load_notes(ctx.vault / s, ctx.vault))
    return [i for i in core.lint(ctx, notes, all_notes) if "needs migration" not in i["msg"]]
