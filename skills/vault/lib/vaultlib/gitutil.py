"""Thin git helpers. Every call fails soft (returns None / empty) instead of raising."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


def run(args, cwd=None, timeout: float = 30, input_text: str = None):
    try:
        p = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)


def git(cwd, *args, timeout: float = 30):
    return run(["git", "-C", str(cwd), *args], timeout=timeout)


def out(cwd, *args, timeout: float = 30):
    rc, so, _ = git(cwd, *args, timeout=timeout)
    return so.strip() if rc == 0 else None


def toplevel(cwd) -> Path | None:
    p = out(cwd, "rev-parse", "--show-toplevel")
    return Path(p) if p else None


def git_dir(cwd) -> Path | None:
    """The common git dir (shared by every worktree), which holds info/exclude."""
    p = out(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(p) if p else None


def remote_url(root) -> str:
    url = out(root, "config", "--get", "remote.origin.url")
    if url:
        return url
    remotes = out(root, "remote")
    if remotes:
        first = remotes.splitlines()[0].strip()
        return out(root, "config", "--get", f"remote.{first}.url") or ""
    return ""


def normalize_remote(url: str) -> str:
    """Stable key for a remote: same repo via https/ssh/grc → same key."""
    u = (url or "").strip()
    if not u:
        return ""
    m = re.match(r"^codecommit::([\w-]+)://(?:[^@/]+@)?(.+?)/?$", u)
    if m:
        return f"codecommit/{m.group(1)}/{m.group(2)}".lower()
    m = re.match(r"^codecommit://(?:[^@/]+@)?(.+?)/?$", u)
    if m:
        return f"codecommit/{m.group(1)}".lower()
    m = re.match(
        r"^(?:https?|ssh)://(?:[^@/]+@)?git-codecommit\.([\w-]+)\.amazonaws\.com(?:\.cn)?(?::\d+)?/v1/repos/([^/?#]+)",
        u,
    )
    if m:
        return f"codecommit/{m.group(1)}/{m.group(2)}".lower()
    if "://" in u:
        m = re.match(r"^[\w+.-]+://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+)$", u)
        if not m:
            return u.lower()
        host, path = m.groups()
    else:
        m = re.match(r"^(?:[^@/]+@)?([^:/]+):(.+)$", u)
        if not m:
            return u.rstrip("/").lower()
        host, path = m.groups()
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"{host}/{path}".lower()


def rev_parse(root, ref: str) -> str | None:
    return out(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")


def current_branch(root) -> str | None:
    return out(root, "symbolic-ref", "--short", "-q", "HEAD")


def default_branch(root) -> str:
    """Mainline branch name. origin/HEAD can point at a stale branch (e.g. `develop` while work merges into
    `master`), so among origin/HEAD, master and main the one with the newest tip wins."""
    head = out(root, "symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD")
    cands = []
    for name in ((head.split("/", 1)[1] if head and "/" in head else None), "master", "main"):
        if name and name not in cands and rev_parse(root, f"origin/{name}"):
            cands.append(name)
    if len(cands) > 1:
        def tip_time(name):
            v = out(root, "log", "-1", "--format=%ct", f"origin/{name}")
            return int(v) if v and v.isdigit() else 0
        return max(cands, key=tip_time)
    if cands:
        return cands[0]
    for name in ("master", "main", "develop"):
        if rev_parse(root, name):
            return name
    return current_branch(root) or "HEAD"


def is_commit(root, sha: str) -> bool:
    if not sha or not re.match(r"^[0-9a-fA-F]{4,40}$", str(sha)):
        return False
    return rev_parse(root, sha) is not None


def changed_files(root, a: str, b: str) -> list | None:
    rc, so, _ = git(root, "diff", "--name-only", "--no-renames", a, b, timeout=60)
    if rc != 0:
        return None
    return [ln for ln in so.splitlines() if ln.strip()]


def name_status(root, a: str, b: str) -> list | None:
    """List of (status_letter, path, new_path_or_None) with rename detection."""
    rc, so, _ = git(root, "diff", "--name-status", "-M", a, b, timeout=60)
    if rc != 0:
        return None
    rows = []
    for ln in so.splitlines():
        parts = ln.split("\t")
        if not parts or not parts[0]:
            continue
        st = parts[0][0]
        if st in "RC" and len(parts) >= 3:
            rows.append((st, parts[1], parts[2]))
        elif len(parts) >= 2:
            rows.append((st, parts[1], None))
    return rows


def ls_tree(root, ref: str) -> set:
    rc, so, _ = git(root, "ls-tree", "-r", "--name-only", ref, timeout=60)
    return set(so.splitlines()) if rc == 0 else set()


def show(root, ref: str, path: str, max_bytes: int = 400_000) -> str | None:
    rc, so, _ = git(root, "show", f"{ref}:{path}", timeout=30)
    if rc != 0:
        return None
    return so[:max_bytes]


def grep_words(root, ref: str, words: list) -> set:
    """Subset of `words` that occur as whole words anywhere in the tree at `ref`."""
    found: set = set()
    for i in range(0, len(words), 200):
        chunk = words[i : i + 200]
        args = ["grep", "-o", "-h", "-w", "-F", "-I"]
        for w in chunk:
            args += ["-e", w]
        rc, so, _ = git(root, *args, ref, timeout=120)
        if rc in (0, 1):
            found.update(ln.split(":", 1)[-1].strip() for ln in so.splitlines())
    return found & set(words)


def rev_count(root, a: str, b: str) -> int | None:
    v = out(root, "rev-list", "--count", f"{a}..{b}")
    return int(v) if v and v.isdigit() else None
