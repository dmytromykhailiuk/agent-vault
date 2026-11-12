"""`vault` command line."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import signal
import sys
import webbrowser
from pathlib import Path

from . import core
from . import frontmatter as fmlib
from . import gitutil as g
from . import setup

HOOK_MAX = 6000
LESSONS_BLOCK_MAX = 1000
HOOK_AUDIENCE = ("implementer", "orchestrator")
SKIP_AGENT_PREFIXES = ("vault-", "verify-")


def skip_agent(name) -> bool:
    return str(name or "").rsplit(":", 1)[-1].startswith(SKIP_AGENT_PREFIXES)


def _ctx(args) -> core.Ctx:
    return core.resolve(getattr(args, "cwd", None), getattr(args, "project", None), getattr(args, "vault_dir", None))


def _ctx_for_slug(ctx: core.Ctx, slug: str) -> core.Ctx:
    """Another project of the same shared vault."""
    meta = core.read_meta(ctx.vault / slug)
    for p in meta.get("paths", []):
        if Path(p).expanduser().is_dir():
            return core.resolve(p, slug, ctx.dir_setting)
    return core.resolve(ctx.vault, slug, ctx.dir_setting)


def _tilde(p) -> str:
    home = str(Path.home())
    s = str(p)
    return "~" + s[len(home):] if s == home or s.startswith(home + "/") else s


def _need_project(ctx: core.Ctx) -> None:
    if not ctx.initialized:
        hint = f" (would be '{ctx.slug}')" if ctx.slug else ""
        raise SystemExit(f"vault: no vault for this project{hint}. Run the vault skill's init first.")


def _short(sha) -> str:
    return (sha or "")[:12]


# --------------------------------------------------------------------------- resolve / status


def cmd_resolve(args) -> int:
    ctx = _ctx(args)
    data = ctx.to_dict()
    if args.create:
        if not ctx.slug:
            raise SystemExit("vault: not inside a git repo and no --project given")
        created = core.create_project(ctx)
        saved = None
        if args.vault_dir and args.save != "none" and core.dir_setting(ctx.repo_root)[0] != args.vault_dir:
            saved = str(core.save_dir_setting(args.save, ctx.repo_root, args.vault_dir))
        data = {**ctx.to_dict(), "created": created, "saved_setting": saved}
    print(json.dumps(data, indent=2))
    return 0


def cmd_version(args) -> int:
    if args.json:
        print(json.dumps({"name": "agent-vault", "version": core.VERSION, "contract": core.CONTRACT,
                          "skill_dir": str(core.SKILL_DIR), "cli": str(core.SKILL_DIR / "bin" / "vault")}))
    else:
        print(f"agent-vault {core.VERSION} (contract {core.CONTRACT})")
    return 0


def _counts(ctx: core.Ctx) -> dict:
    counts = {}
    for n in core.load_notes(ctx.project_dir, ctx.vault):
        if n.type in core.CONTENT_TYPES:
            counts[n.type] = counts.get(n.type, 0) + 1
    return counts


def _where(ctx: core.Ctx) -> str:
    git = {"own": "own git repo", "host-repo": "inside the code repo, not auto-committed"}.get(ctx.vault_git, "")
    return f"{ctx.layout} layout, {git}; setting '{ctx.dir_setting}' from {ctx.dir_source}"


def cmd_status(args) -> int:
    ctx = _ctx(args)
    if not ctx.initialized:
        if ctx.slug and ctx.project_dir:
            print(f"No vault for this project yet (slug would be '{ctx.slug}'). Run the vault skill's init.")
            print(f"Vault:    would be {ctx.project_dir}  ({_where(ctx)})")
        else:
            print("Not inside a git repository, and no project registered for this directory.")
        return 0
    counts = _counts(ctx)
    total = sum(counts.values())
    by_type = ", ".join(f"{core.FOLDERS[t]} {counts[t]}" for t in core.CONTENT_TYPES if t in counts) or "none"
    behind = ctx.commits_behind()
    print(f"Project:  {ctx.slug}  ({ctx.project_dir})")
    print(f"Vault:    {_where(ctx)}")
    if ctx.repo_root:
        print(f"Repo:     {ctx.repo_root}  [{ctx.remote or 'no remote'}]")
        on = f"branch {ctx.branch}" if ctx.branch else "detached HEAD"
        print(f"Target:   {ctx.target} @ {_short(ctx.target_sha)}  ({on}; HEAD matches target: "
              f"{'yes' if ctx.head_matches_target else 'no'})")
    if ctx.last_synced:
        tail = f", {behind} commit(s) behind" if behind is not None else ", not an ancestor/unknown"
        print(f"Synced:   {_short(ctx.last_synced)}{tail}")
    else:
        print("Synced:   never")
    print(f"Notes:    {total} ({by_type})")
    return 0


# --------------------------------------------------------------------------- hook


HOOK_RULES = """Rules for this session (`vault <op>` below means the `vault` skill: `/vault <op>` in Claude Code, `$vault <op>` in Codex):
- The vault holds curated knowledge about this project: features, how-to guides, known problems, decisions, module internals. Before exploring unfamiliar code or answering questions about how the project works, ask the vault agent: `vault search <question>`.
- Never read or edit files under {vault} yourself. Every vault interaction goes through the vault skill, which runs a separate vault agent.
- When you learn something durable and non-obvious (a decision and its reason, a gotcha or bug cause, a how-to, how a flow works), record it with `vault remember <brief with file paths>` before finishing the task.
- Project facts go to the vault, not to the agent's own memory."""


def _hook_text(ctx: core.Ctx) -> str:
    if not ctx.initialized:
        if ctx.repo_root and ctx.project_dir:
            return (f"Knowledge vault: none for this repository yet. If the user wants project documentation, "
                    f"`vault init` (the vault skill) creates it in {_tilde(ctx.project_dir)} (slug '{ctx.slug}').")
        return ""
    idx_path = ctx.project_dir / "index.md"
    summary = ""
    if idx_path.exists():
        n = core.load_note(idx_path, ctx.vault, ctx.project_dir)
        summary = n.summary if n else ""
    counts = _counts(ctx)
    total = sum(counts.values())
    parts = [f"## Project knowledge vault: {ctx.slug}"]
    if summary:
        parts.append(summary)
    if ctx.last_synced:
        behind = ctx.commits_behind()
        if behind:
            sync = (f"synced to {ctx.target} @{_short(ctx.last_synced)}, {behind} commit(s) behind "
                    f"(the docs may lag; suggest `vault update` to the user when relevant)")
        elif behind == 0:
            sync = f"synced to {ctx.target} @{_short(ctx.last_synced)} (up to date)"
        else:
            sync = f"synced @{_short(ctx.last_synced)} (not an ancestor of {ctx.target}; `vault update` advised)"
    else:
        sync = "never synced with code (`vault update` or `vault init` advised)"
    parts.append(f"Status: {total} notes; {sync}.")
    parts.append(HOOK_RULES.replace("{vault}", _tilde(ctx.vault)))
    return "\n".join(parts)[:HOOK_MAX]


def _lessons_block(ctx: core.Ctx) -> str:
    """Session-context lessons: project lessons for initialized projects (top 5), harness lessons (top 3) and
    the count of open proposals. Empty when there is nothing to show; never longer than LESSONS_BLOCK_MAX."""
    proj = core.lessons(ctx, HOOK_AUDIENCE)[:5] if ctx.initialized else []
    h = core.harness_ctx(ctx)
    harn = core.lessons(h, HOOK_AUDIENCE)[:3] if h else []
    pending = (len(core.open_proposals(ctx)) if ctx.initialized else 0) + (len(core.open_proposals(h)) if h else 0)
    if not (proj or harn or pending):
        return ""
    lines = ["Lessons from past retros (follow them):"]
    lines += [f"- {core.lesson_rule(n)} (×{core.lesson_occurrences(n)}) [[{n.link}]]" for n in proj]
    if harn:
        lines.append("Harness lessons:")
        lines += [f"- {core.lesson_rule(n)} (×{core.lesson_occurrences(n)})" for n in harn]
    tail = (f"Pending: {pending} harness/repo change proposal(s) await your approval → `vault lessons --proposals`, then `vault proposal accept|reject <note>`"
            if pending else "")
    out = []
    budget = LESSONS_BLOCK_MAX - (len(tail) + 1 if tail else 0)
    for ln in lines:
        if len("\n".join(out + [ln])) > budget:
            break
        out.append(ln)
    if tail:
        out.append(tail)
    return "\n".join(out) if len(out) > 1 else ""


def _context_text(ctx: core.Ctx) -> str:
    try:
        block = _lessons_block(ctx)
    except Exception:  # noqa: BLE001 - lessons must never cost the session its vault context
        block = ""
    base = _hook_text(ctx)
    if not block:
        return base
    if not base:
        return block
    return base[: HOOK_MAX - len(block) - 2] + "\n\n" + block


def cmd_context(args) -> int:
    # The hook must never fail or hang a session start.
    alarm = hasattr(signal, "SIGALRM")
    previous = None
    try:
        if alarm:
            previous = signal.getsignal(signal.SIGALRM)
            signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))
            signal.alarm(4)
        data = {}
        if args.hook and not sys.stdin.isatty():
            raw = sys.stdin.read()
            if raw.strip():
                data = json.loads(raw)
        if skip_agent(data.get("agent_type")):
            return 0
        ctx = core.resolve(data.get("cwd") or args.cwd, vault_dir=args.vault_dir)
        text = _context_text(ctx)
        if not text:
            return 0
        if args.hook:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
        else:
            print(text)
    except BaseException:  # noqa: BLE001 - deliberately silent
        return 0
    finally:
        if alarm:
            with contextlib.suppress(Exception):
                signal.alarm(0)
                signal.signal(signal.SIGALRM, previous if previous is not None else signal.SIG_DFL)
    return 0


# --------------------------------------------------------------------------- search / list


def _print_note_line(n: core.Note, snippet: str = "", stale: dict = None) -> None:
    status = f", {n.status}" if n.status else ""
    print(f"{n.rel}  [{n.type}{status}]")
    print(f"  {n.title}" + (f" — {n.summary}" if n.summary else ""))
    if snippet:
        print(f"  > {snippet}")
    if stale:
        if stale["state"] == "stale":
            files = stale["changed"]
            more = f" (+{len(files) - 3} more)" if len(files) > 3 else ""
            print(f"  ⚠ STALE: {len(files)} referenced file(s) changed since {_short(stale['verified'])}: "
                  f"{', '.join(files[:3])}{more} — verify against code")
        elif stale["state"] == "unverified":
            print("  ⚠ UNVERIFIED against code — verify before relying on details")


def _projects_notes(args, ctx: core.Ctx):
    if args.all and ctx.layout == "shared":
        slugs = [s for s, _ in core.list_projects(ctx.vault)]
    else:  # a per-repo vault holds only this project
        _need_project(ctx)
        slugs = [ctx.slug]
    out = []
    for s in slugs:
        out.extend(core.load_notes(ctx.vault / s if ctx.layout == "shared" else ctx.project_dir, ctx.vault))
    return slugs, out


def cmd_search(args) -> int:
    ctx = _ctx(args)
    slugs, notes = _projects_notes(args, ctx)
    types = set(args.type or [])
    if args.code:
        if not ctx.repo_root:
            raise SystemExit("vault: --code needs to run inside the repo")
        p = Path(args.code)
        p = (ctx.cwd / p) if not p.is_absolute() else p
        try:
            rel = p.resolve().relative_to(ctx.repo_root).as_posix()
        except ValueError:
            rel = args.code
        hits = [n for n in core.notes_for_code(notes, rel) if not types or n.type in types]
        if not hits:
            print(f"No notes reference {rel}.")
            return 0
        st = core.Staleness(ctx)
        print(f"{len(hits)} note(s) cover {rel}:")
        for n in hits:
            _print_note_line(n, stale=st.state(n) if st.available else None)
        return 0
    query = " ".join(args.query)
    results = core.search(notes, query, types=types or None, limit=args.limit)
    idents = core.identifiers(query)
    hits = core.code_hits(ctx, notes, idents) if idents and len(results) < 3 and not args.all else []
    if hits:
        print(f"Code matches for {', '.join(idents)} at {ctx.target} (notes covering those files):")
        for ident, f, covering in hits:
            where = ", ".join(n.rel for n in covering) if covering else "no note covers this file"
            print(f"  {ident} in {f} → {where}")
        print()
    if not results:
        if not hits:
            print(f'No notes match "{query}" in {", ".join(slugs)}. Try synonyms, identifiers, or `vault list`.')
        return 0
    stalers = {}
    print(f'{len(results)} result(s) for "{query}":')
    for _, n, snip in results:
        slug = n.rel.split("/", 1)[0] if ctx.layout == "shared" else ctx.slug
        if not args.no_stale and n.code_refs:
            if slug not in stalers:
                c = ctx if (ctx.initialized and ctx.slug == slug) else _ctx_for_slug(ctx, slug)
                stalers[slug] = core.Staleness(c)
            st = stalers[slug]
            _print_note_line(n, snip, st.state(n) if st.available else None)
        else:
            _print_note_line(n, snip)
    return 0


def cmd_list(args) -> int:
    ctx = _ctx(args)
    _, notes = _projects_notes(args, ctx)
    types = set(args.type or [])
    for n in notes:
        if n.type in ("hub", "log") and n.type not in types:
            continue
        if types and n.type not in types:
            continue
        status = f", {n.status}" if n.status else ""
        summary = f" — {n.summary}" if n.summary else ""
        print(f"{n.rel}  [{n.type}{status}]  {n.title}{summary}")
    return 0


# --------------------------------------------------------------------------- affected


def cmd_affected(args) -> int:
    ctx = _ctx(args)
    _need_project(ctx)
    if not ctx.repo_root:
        raise SystemExit("vault: affected needs the project's git repo (run inside it)")
    notes = core.load_notes(ctx.project_dir, ctx.vault)
    st = core.Staleness(ctx)
    stale, unverified = [], []
    for n in notes:
        if not n.code_refs:
            continue
        s = st.state(n)
        missing = st.missing_refs(n)
        if s["state"] == "stale" or missing:
            stale.append({"note": n.rel, "verified": s.get("verified"), "changed": s.get("changed", []),
                          "missing_refs": missing})
        elif s["state"] == "unverified":
            unverified.append(n.rel)
    since = args.since or ctx.last_synced
    renames, deleted, uncovered, commits = [], [], {}, None
    if since and g.is_commit(ctx.repo_root, since):
        commits = g.rev_count(ctx.repo_root, since, ctx.target_sha)
        ignored = core.ignore_matcher(ctx.stale_ignore)
        for status, path, new in g.name_status(ctx.repo_root, since, ctx.target_sha) or []:
            covering = [n.rel for n in core.notes_for_code(notes, path)]
            if status == "R":
                renames.append({"from": path, "to": new, "notes": covering})
            elif status == "D":
                if covering:
                    deleted.append({"path": path, "notes": covering})
            elif status in "AM" and not covering and not ignored(path):
                key = "/".join(path.split("/")[:2]) if "/" in path else "(root)"
                uncovered.setdefault(key, []).append(path)
    data = {
        "project": ctx.slug,
        "target": ctx.target,
        "target_sha": ctx.target_sha,
        "head_matches_target": ctx.head_matches_target,
        "code_root": str(ctx.code_root) if ctx.code_root else None,
        "since": since or None,
        "commits_since": commits,
        "stale_notes": stale,
        "unverified_notes": unverified,
        "renames": renames,
        "deleted_refs": deleted,
        "uncovered_changes": uncovered,
    }
    if not since:
        data["note"] = "never synced: only per-note staleness is reported"
    print(json.dumps(data, indent=2))
    return 0


# --------------------------------------------------------------------------- lint


def cmd_lint(args) -> int:
    ctx = _ctx(args)
    _need_project(ctx)
    vault = ctx.vault
    notes = core.load_notes(ctx.project_dir, vault)
    all_notes = notes
    if ctx.layout == "shared":
        all_notes = []
        for s, _ in core.list_projects(vault):
            all_notes.extend(notes if s == ctx.slug else core.load_notes(vault / s, vault))
    issues = core.lint(ctx, notes, all_notes)
    if args.json:
        print(json.dumps(issues, indent=2))
    else:
        errors = [i for i in issues if i["level"] == "error"]
        warns = [i for i in issues if i["level"] == "warn"]
        if not ctx.repo_root:
            print("note: repo not found, code checks skipped")
        by_note: dict = {}
        for i in issues:
            by_note.setdefault(i["note"], []).append(i)
        for note in sorted(by_note):
            print(note)
            for i in by_note[note]:
                print(f"  {i['level'].upper():5} {i['msg']}")
        content = sum(1 for n in notes if n.type in core.CONTENT_TYPES)
        print(f"\n{content} notes checked: {len(errors)} error(s), {len(warns)} warning(s)")
    return 1 if any(i["level"] == "error" for i in issues) else 0


def cmd_coverage(args) -> int:
    ctx = _ctx(args)
    _need_project(ctx)
    if not ctx.repo_root:
        raise SystemExit("vault: coverage needs the project's git repo (run inside it)")
    notes = core.load_notes(ctx.project_dir, ctx.vault)
    gaps = core.coverage(ctx, notes, depth=args.depth)
    if not gaps:
        print(f"Every source directory (depth {args.depth}) is referenced by at least one note.")
        return 0
    print(f"Source directories at {ctx.target} with no note referencing any of their files (depth {args.depth}):")
    for d, n in gaps:
        print(f"  {d}/  ({n} source file(s))")
    return 0


# --------------------------------------------------------------------------- commit


def _note_path(ctx: core.Ctx, ref: str) -> Path:
    p = Path(ref).expanduser()
    cands = [p] if p.is_absolute() else [ctx.vault / ref, ctx.project_dir / ref]
    for c in list(cands):
        if c.suffix != ".md":
            cands.append(c.with_name(c.name + ".md"))
    for c in cands:
        if c.is_file():
            return c
    raise SystemExit(f"vault: note not found: {ref}")


def cmd_commit(args, ctx: core.Ctx = None) -> int:
    ctx = ctx or _ctx(args)
    _need_project(ctx)
    vault = ctx.vault
    details = []
    with core.Lock(vault):
        stamp = []
        if args.stamp_all:
            stamp = [n.path for n in core.load_notes(ctx.project_dir, vault) if n.code_refs]
        stamp += [_note_path(ctx, s) for s in (args.stamp or [])]
        if stamp or args.synced:
            if not ctx.target_sha:
                raise SystemExit("vault: --stamp/--synced need the project's git repo (run inside it)")
        for p in dict.fromkeys(stamp):
            text = p.read_text(encoding="utf-8")
            p.write_text(fmlib.set_field(text, "verified", _short(ctx.target_sha)), encoding="utf-8")
        if stamp:
            details.append(f"verified {len(set(stamp))} note(s) at {ctx.target} @{_short(ctx.target_sha)}")
        meta = core.read_meta(ctx.project_dir)
        changed_meta = (core.register_path(meta, ctx.repo_root)
                        if ctx.repo_root and not core.is_pseudo(ctx.slug) else False)
        if args.synced:
            meta["last_synced"] = ctx.target_sha
            changed_meta = True
            details.append(f"synced to {ctx.target} @{_short(ctx.target_sha)}")
        if changed_meta:
            core.write_meta(ctx.project_dir, meta)
            ctx.meta = meta
        core.write_hubs(ctx)
        if ctx.layout == "shared":
            core.write_root_index(vault)
            paths = [p for p in (ctx.slug, "index.md", ".gitignore", ".obsidian/app.json", "README.md")
                     if (vault / p).exists()]
        else:
            paths = ["."]
        host = core.git_host(vault)
        if not host:
            g.run(["git", "init", "-q", str(vault)])
            host = vault.resolve()
        if host != vault.resolve():
            # The vault lives inside another repo (e.g. the code repo): never commit there.
            rc, porcelain, _ = g.git(host, "status", "--porcelain", "--", *[str(vault / p) for p in paths])
            mark = ctx.work_dir / "last-commit"
            digest = core.content_digest(ctx.project_dir)
            if not porcelain.strip() or (mark.exists() and mark.read_text().strip() == digest):
                print("vault: nothing to commit")
                return 0
            core.append_log(ctx, args.message, details)
            mark.parent.mkdir(parents=True, exist_ok=True)
            mark.write_text(digest + "\n")
            print(f"vault: updated {ctx.project_dir} (inside repo {host}; not committed — commit it with your code)")
            for d in details:
                print(f"  {d}")
            return 0
        rc, porcelain, _ = g.git(vault, "status", "--porcelain", "--", *paths)
        if not porcelain.strip():
            print("vault: nothing to commit")
            return 0
        core.append_log(ctx, args.message, details)
        g.git(vault, "add", "-A", "--", *paths)
        ident = []
        if not g.out(vault, "config", "user.email"):
            ident = ["-c", "user.name=Agent Vault", "-c", "user.email=vault@localhost"]
        rc, so, se = g.run(["git", "-C", str(vault), *ident, "commit", "-q", "-m", args.message, "--", *paths])
        if rc != 0:
            raise SystemExit(f"vault: git commit failed: {se.strip() or so.strip()}")
        sha = g.out(vault, "rev-parse", "--short", "HEAD")
        files = g.out(vault, "show", "--stat", "--format=", "HEAD") or ""
        nfiles = max(0, len(files.splitlines()) - 1)
        print(f"vault: committed {sha} \"{args.message}\" ({nfiles} file(s))")
        for d in details:
            print(f"  {d}")
    return 0


# --------------------------------------------------------------------------- lessons / covering / proposal


def _print_json(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def cmd_lessons(args) -> int:
    ctx = _ctx(args)
    h = None if args.no_harness else core.harness_ctx(ctx)
    own = ctx.initialized
    if args.proposals:
        notes = (core.open_proposals(ctx) if own else []) + (core.open_proposals(h) if h else [])
        if args.format == "json":
            if notes:
                _print_json([core.lesson_dict(n) for n in notes])
            return 0
        for n in notes:
            d = core.lesson_dict(n)
            print(f"- [[{n.link}]] — {d['proposal_kind']} → {d['proposal_target']}: {n.summary}")
        return 0
    limit = max(0, args.limit)
    notes = core.lessons(ctx, args.audience)[:limit] if own else []
    if h:
        notes += core.lessons(h, args.audience)[:core.harness_limit(limit)]
    if args.format == "json":
        if notes:
            _print_json([core.lesson_dict(n) for n in notes])
        return 0
    for n in notes:
        scope = n.fm.get("scope") or ("harness" if n.rel.startswith(core.HARNESS + "/") else "project")
        print(f"- {core.lesson_rule(n)} (×{core.lesson_occurrences(n)}, {scope}) [[{n.link}]]")
    return 0


def _repo_rel(ctx: core.Ctx, raw: str) -> str:
    p = Path(raw).expanduser()
    if not ctx.repo_root:
        return raw[2:] if raw.startswith("./") else raw
    full = Path(os.path.normpath(p if p.is_absolute() else ctx.cwd / p))
    for base in (ctx.repo_root, ctx.repo_root.resolve()):
        try:
            return full.relative_to(base).as_posix()
        except ValueError:
            continue
    try:
        return full.resolve().relative_to(ctx.repo_root.resolve()).as_posix()
    except (OSError, ValueError):
        return raw


def cmd_covering(args) -> int:
    ctx = _ctx(args)
    if not ctx.initialized:
        return 0
    rels = [_repo_rel(ctx, p) for p in args.paths]
    notes = [n for n in core.load_notes(ctx.project_dir, ctx.vault) if n.code_refs and n.type not in ("hub", "log")]
    hits = []
    for n in notes:
        matchers = [core.ref_matcher(r) for r in n.code_refs]
        k = sum(1 for rel in rels if any(m(rel) for m in matchers))
        if k:
            hits.append((k, n))
    hits.sort(key=lambda x: (-x[0], x[1].rel))
    hits = [n for _, n in hits[: max(0, args.limit)]]
    st = core.Staleness(ctx)
    rows = []
    for n in hits:
        stale = bool(st.available and st.state(n)["state"] == "stale")
        rows.append({"link": n.link, "title": n.title, "summary": n.summary, "type": n.type, "stale": stale})
    if args.format == "json":
        if rows:
            _print_json(rows)
        return 0
    for r in rows:
        summary = f": {r['summary']}" if r["summary"] else ""
        print(f"- [[{r['link']}]] — {r['title']}{summary}{' (stale)' if r['stale'] else ''}")
    return 0


def _find_note(ctxs: list, ref: str):
    """(ctx, path) of the note `ref` names: a link (`[[x]]`, `x#Heading`), a vault- or project-relative path."""
    r = ref.strip()
    if r.startswith("[[") and r.endswith("]]"):
        r = r[2:-2]
    r = r.split("|", 1)[0].split("#", 1)[0].strip()
    for c in ctxs:
        if not (c and c.matched and c.project_dir):
            continue
        p = Path(r).expanduser()
        cands = [p] if p.is_absolute() else [c.vault / r, c.project_dir / r]
        for cand in cands:
            for f in (cand, cand.with_name(cand.name + ".md")):
                if f.is_file():
                    try:
                        f.resolve().relative_to(c.project_dir.resolve())
                    except ValueError:
                        continue
                    return c, f
    raise SystemExit(f"vault: note not found: {ref}")


def cmd_proposal(args) -> int:
    ctx = _ctx(args)
    owner, path = _find_note([ctx, core.harness_ctx(ctx)], args.note)
    with core.Lock(owner.vault):
        note = core.load_note(path, owner.vault, owner.project_dir)
        if not note or note.type != "lesson":
            raise SystemExit(f"vault: {args.note} is not a lesson note")
        state = str(note.fm.get("proposal_status") or "none")
        if state != "open":
            raise SystemExit(f"vault: {note.link} has no open proposal (proposal_status: {state})")
        text = path.read_text(encoding="utf-8")
        if args.action == "accept":
            text = fmlib.set_field(text, "proposal_status", "applied")
            if note.fm.get("proposal_kind") == "rule-promotion":
                text = fmlib.set_field(text, "status", "promoted")
                text = fmlib.set_field(text, "promoted_to", str(note.fm.get("proposal_target") or ""))
        else:
            text = fmlib.set_field(text, "proposal_status", "rejected")
        path.write_text(text, encoding="utf-8")
    verb = "accepted" if args.action == "accept" else "rejected"
    commit_args = argparse.Namespace(message=f"proposal: {args.action} {note.link}", stamp=None, stamp_all=False,
                                     synced=False)
    with contextlib.redirect_stdout(io.StringIO()):
        cmd_commit(commit_args, owner)
    print(f"proposal {verb}: {note.link}")
    return 0


# --------------------------------------------------------------------------- tree


def cmd_tree(args) -> int:
    ctx = _ctx(args)
    _need_project(ctx)
    if not ctx.repo_root or not ctx.target_sha:
        raise SystemExit("vault: tree needs the project's git repo (run inside it)")
    tree = ctx.tree_dir
    if args.remove:
        if tree.exists():
            g.git(ctx.repo_root, "worktree", "remove", "--force", str(tree))
            g.git(ctx.repo_root, "worktree", "prune")
        print("removed" if not tree.exists() else f"could not remove {tree}")
        return 0
    if ctx.head_matches_target and not args.force:
        print(ctx.repo_root)
        return 0
    if tree.exists():
        rc, _, se = g.git(tree, "checkout", "-q", "--detach", "--force", ctx.target_sha, timeout=300)
    else:
        tree.parent.mkdir(parents=True, exist_ok=True)
        g.git(ctx.repo_root, "worktree", "prune")
        rc, _, se = g.git(ctx.repo_root, "worktree", "add", "-q", "--detach", str(tree), ctx.target_sha, timeout=300)
    if rc != 0:
        raise SystemExit(f"vault: worktree failed: {se.strip()}")
    print(tree)
    return 0


# --------------------------------------------------------------------------- main


GRAPH_TEMPLATE = core.SKILL_DIR / "assets" / "graph.html"
GRAPH_PLACEHOLDER = "/*__VAULT_DATA__*/null"


def cmd_graph(args) -> int:
    ctx = _ctx(args)
    _need_project(ctx)
    notes = core.load_notes(ctx.project_dir, ctx.vault)
    data = core.graph_data(ctx, notes, stale=not args.no_stale)
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "\\u003c!--")
    html = GRAPH_TEMPLATE.read_text(encoding="utf-8").replace(GRAPH_PLACEHOLDER, payload, 1)
    out = Path(args.out).expanduser() if args.out else ctx.work_dir / "graph.html"
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    notes_n = sum(1 for n in data["nodes"] if n["kind"] == "note")
    code_n = len(data["nodes"]) - notes_n
    print(f"vault: graph of {ctx.slug}: {notes_n} note(s), {code_n} code file(s), {len(data['edges'])} edge(s)")
    print(out)
    if not args.no_open:
        webbrowser.open(out.as_uri())
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cwd", help="directory to resolve the project from (default: current)")
    common.add_argument("--project", help="vault project slug (overrides resolution)")
    common.add_argument("--vault-dir", help="vault dir or template, e.g. 'vault' or '~/agent-vault/{project}' "
                                            "(overrides AGENT_VAULT_DIR and the config files)")

    p = argparse.ArgumentParser(prog="vault", description="Per-project knowledge vault.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("resolve", parents=[common], help="print project resolution as JSON")
    s.add_argument("--create", action="store_true", help="create the project skeleton if missing")
    s.add_argument("--save", choices=["local", "project", "user", "none"], default="local",
                   help="with --create --vault-dir: where to persist it (default: repo .agent-vault.local.json)")
    s.set_defaults(fn=cmd_resolve)

    s = sub.add_parser("version", help="print the version and the integration contract number")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_version)

    s = sub.add_parser("status", parents=[common], help="human-readable vault status")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("context", parents=[common], help="session-start context (for the SessionStart hook)")
    s.add_argument("--hook", action="store_true", help="read hook JSON on stdin and emit hook JSON")
    s.set_defaults(fn=cmd_context)

    s = sub.add_parser("search", parents=[common], help="search notes")
    s.add_argument("query", nargs="*")
    s.add_argument("--type", action="append", choices=sorted(core.VALID_TYPES))
    s.add_argument("--code", help="list notes whose code_refs cover this file")
    s.add_argument("--all", action="store_true", help="search all projects")
    s.add_argument("--limit", type=int, default=8)
    s.add_argument("--no-stale", action="store_true", help="skip staleness checks")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("list", parents=[common], help="list notes with summaries")
    s.add_argument("--type", action="append", choices=sorted(core.VALID_TYPES))
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("affected", parents=[common], help="stale notes and uncovered changes as JSON")
    s.add_argument("--since", help="commit to diff from for renames/uncovered (default: last_synced)")
    s.set_defaults(fn=cmd_affected)

    s = sub.add_parser("coverage", parents=[common], help="source directories no note references")
    s.add_argument("--depth", type=int, default=2, help="directory depth to group by (default 2)")
    s.set_defaults(fn=cmd_coverage)

    s = sub.add_parser("lint", parents=[common], help="check the project's notes")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_lint)

    s = sub.add_parser("commit", parents=[common], help="stamp, regenerate hubs, log and git-commit")
    s.add_argument("-m", "--message", required=True)
    s.add_argument("--stamp", nargs="+", metavar="NOTE", help="mark notes verified at the target commit")
    s.add_argument("--stamp-all", action="store_true", help="stamp every note that has code_refs")
    s.add_argument("--synced", action="store_true", help="record the target commit as last_synced")
    s.set_defaults(fn=cmd_commit)

    s = sub.add_parser("lessons", parents=[common], help="active lessons of the project, then harness lessons")
    s.add_argument("--audience", help="only lessons for this audience (implementer, qa, reviewer, …)")
    s.add_argument("--limit", type=int, default=5, help="project lessons to show; harness gets ceil(N/2)")
    s.add_argument("--format", choices=["bullets", "json"], default="bullets")
    s.add_argument("--proposals", action="store_true", help="lessons with an open proposal instead")
    s.add_argument("--no-harness", action="store_true", help="leave out _harness lessons")
    s.set_defaults(fn=cmd_lessons)

    s = sub.add_parser("covering", parents=[common], help="notes whose code_refs cover any of the paths")
    s.add_argument("paths", nargs="+", metavar="PATH")
    s.add_argument("--format", choices=["bullets", "json"], default="bullets")
    s.add_argument("--limit", type=int, default=8)
    s.set_defaults(fn=cmd_covering)

    s = sub.add_parser("proposal", parents=[common], help="accept or reject a lesson's open proposal")
    s.add_argument("action", choices=["accept", "reject"])
    s.add_argument("note", help="lesson note: link or path")
    s.set_defaults(fn=cmd_proposal)

    s = sub.add_parser("graph", parents=[common], help="interactive HTML graph of the project's notes")
    s.add_argument("--out", help="output file (default: <work_dir>/graph.html)")
    s.add_argument("--no-open", action="store_true", help="don't open it in the browser")
    s.add_argument("--no-stale", action="store_true", help="skip staleness checks")
    s.set_defaults(fn=cmd_graph)

    s = sub.add_parser("tree", parents=[common], help="detached worktree of the target ref for reading code")
    s.add_argument("--remove", action="store_true")
    s.add_argument("--force", action="store_true", help="create it even when HEAD matches the target")
    s.set_defaults(fn=cmd_tree)

    s = sub.add_parser("setup", parents=[common], help="install the vault agents, hook and permissions")
    s.add_argument("--agent", choices=["claude", "codex", "all"], default="all")
    s.add_argument("--scope", choices=["user", "project"], default="user")
    s.add_argument("--only", metavar="PARTS", help="comma list of: agents, hook, perms (default: all)")
    s.add_argument("--uninstall", action="store_true", help="remove what setup installs")
    s.add_argument("--force", action="store_true", help="install agents/hook even when a plugin provides them; "
                   "replace or remove agent files setup did not write (keeps a .bak)")
    s.add_argument("--dry-run", action="store_true", help="show the changes without writing")
    s.set_defaults(fn=lambda a: setup.cmd_setup(a))

    s = sub.add_parser("doctor", parents=[common], help="check the installation for Claude Code and Codex")
    s.set_defaults(fn=lambda a: setup.cmd_doctor(a))
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args) or 0
    except TimeoutError as e:
        print(f"vault: {e}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0
