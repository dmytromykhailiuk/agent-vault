"""Vault model: project resolution, notes, staleness, search, lint, hubs."""
from __future__ import annotations

import datetime as _dt
import hashlib
import math
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import frontmatter as fmlib
from . import gitutil as g

SCHEMA = 1
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
CONTENT_TYPES = ("feature", "guide", "problem", "decision", "module", "lesson", "retro")
FOLDERS = {
    "feature": "features",
    "guide": "guides",
    "problem": "problems",
    "decision": "decisions",
    "module": "modules",
    "lesson": "lessons",
    "retro": "retros",
}
FOLDER_TYPES = {v: k for k, v in FOLDERS.items()}
HUB_TITLES = {
    "features": "Features",
    "guides": "Guides",
    "problems": "Problems",
    "decisions": "Decisions",
    "modules": "Modules",
    "lessons": "Lessons",
    "retros": "Retros",
}
STATUSES = {
    "project": {"active", "deprecated"},
    "feature": {"active", "draft", "deprecated"},
    "guide": {"active", "draft", "deprecated"},
    "module": {"active", "draft", "deprecated"},
    "glossary": {"active", "draft"},
    "problem": {"open", "resolved", "wontfix"},
    "decision": {"accepted", "superseded", "inferred", "proposed"},
    "lesson": {"active", "promoted", "retired"},
    "retro": {"recorded"},
}
VALID_TYPES = set(STATUSES) | {"hub", "log"}
# Token budgets for the note body (chars / 4).
BUDGETS = {
    "project": 1200,
    "feature": 1000,
    "guide": 1000,
    "problem": 1000,
    "decision": 1000,
    "module": 2500,
    "glossary": 2500,
    "lesson": 500,
    "retro": 800,
}
# Lesson / retro field values (lint warns on others).
LESSON_SCOPES = ("project", "harness")
LESSON_CATEGORIES = ("efficiency", "correctness", "process", "tooling", "environment", "communication")
LESSON_AUDIENCES = ("implementer", "orchestrator", "triage", "reviewer", "qa", "retro")
PROPOSAL_STATUSES = ("none", "open", "applied", "rejected")
PROPOSAL_KINDS = ("none", "rule-promotion", "harness-change", "repo-change")
RETRO_OUTCOMES = ("passed", "accepted", "abandoned", "skipped")
STALE_LESSON_DAYS = 90
DEFAULT_STALE_IGNORE = [
    "*.spec.ts", "*.spec.tsx", "*.spec.js", "*.test.*", "*_test.py", "test_*.py",
    "*.snap", "*.scss", "*.sass", "*.less", "*.css", "*.md", "**/i18n/**",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
]
STOPWORDS = set(
    "a an and are as at be by for from how in is it of on or that the this to what when where which who why with "
    "does do did can could should would will i we you our your about into over under".split()
)
WIKILINK_RE = re.compile(r"\[\[([^\]\|#\n]+)(#[^\]\|\n]*)?(?:\|([^\]\n]*))?\]\]")
FENCE_RE = re.compile(r"```.*?```", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
SYMBOL_RE = re.compile(r"`([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)(\(\))?`")
FILE_EXT_RE = re.compile(r"\.(ts|tsx|js|jsx|mjs|cjs|json|py|md|scss|css|html|yml|yaml|sh|txt)$", re.I)
SECRET_RES = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(secret|token|password|passwd|api[_-]?key)\b\s*[:=]\s*['\"]?[A-Za-z0-9/+_\-]{20,}"),
]


DIR_KEY = "AGENT_VAULT_DIR"
COMPAT_DIR_KEY = "CLAUDE_VAULT_DIR"
CONFIG_KEY = "vault_dir"
HARNESS = "_harness"  # cross-project pseudo-project (lessons about how the agents work)
HARNESS_KEY = "AGENT_VAULT_HARNESS_DIR"
COMPAT_HARNESS_KEY = "CLAUDE_VAULT_HARNESS_DIR"
LEGACY_HARNESS_DIR = "~/claude-vault/_harness"
DEFAULT_DIR = "vault"  # <repo_root>/vault
SAVE_SCOPES = ("local", "project", "user")
REPO_CONFIG = {"local": ".agent-vault.local.json", "project": ".agent-vault.json"}


def today() -> str:
    return _dt.date.today().isoformat()


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s or "project"


# --------------------------------------------------------------------------- notes


@dataclass
class Note:
    path: Path
    rel: str  # relative to the vault root: what links use
    fm: dict
    body: str
    has_fm: bool
    prel: str = ""  # relative to the project dir

    @property
    def link(self) -> str:
        return self.rel[:-3] if self.rel.endswith(".md") else self.rel

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def inferred_type(self) -> str:
        parts = (self.prel or self.rel).split("/")
        if self.name == "_index.md":
            return "hub"
        if len(parts) == 1:
            return {"index.md": "project", "glossary.md": "glossary", "log.md": "log"}.get(self.name, "note")
        if parts[0] in FOLDER_TYPES:
            return FOLDER_TYPES[parts[0]]
        return "note"

    @property
    def type(self) -> str:
        t = self.fm.get("type")
        return t if isinstance(t, str) and t else self.inferred_type

    @property
    def title(self) -> str:
        t = self.fm.get("title")
        if isinstance(t, str) and t:
            return t
        m = re.search(r"^#\s+(.+)$", self.body, re.M)
        return m.group(1).strip() if m else self.path.stem

    @property
    def summary(self) -> str:
        s = self.fm.get("summary")
        return s if isinstance(s, str) else ""

    @property
    def status(self) -> str:
        s = self.fm.get("status")
        return s if isinstance(s, str) else ""

    @property
    def aliases(self) -> list:
        return _as_list(self.fm.get("aliases"))

    @property
    def code_refs(self) -> list:
        return [r for r in _as_list(self.fm.get("code_refs")) if r.strip()]

    @property
    def verified(self) -> str:
        v = self.fm.get("verified")
        return v if isinstance(v, str) else ""

    @property
    def tokens(self) -> int:
        return len(self.body) // 4

    @property
    def is_content(self) -> bool:
        return self.type in CONTENT_TYPES or self.type in ("project", "glossary")


def _as_list(v) -> list:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [str(v)]


def load_note(path: Path, root: Path, project_dir: Path = None) -> Note | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fields, body, has = fmlib.read(text)
    rel = path.relative_to(root).as_posix()
    prel = path.relative_to(project_dir).as_posix() if project_dir else rel.split("/", 1)[-1]
    return Note(path=path, rel=rel, fm=fields, body=body, has_fm=has, prel=prel)


def load_notes(project_dir: Path, root: Path) -> list:
    notes = []
    if not project_dir.is_dir():
        return notes
    for dirpath, dirnames, filenames in os.walk(project_dir):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                n = load_note(Path(dirpath) / fn, root, project_dir)
                if n:
                    notes.append(n)
    return notes


def strip_code(text: str) -> str:
    return INLINE_CODE_RE.sub("", FENCE_RE.sub("", text))


def wikilinks(text: str) -> list:
    return [m.group(1).strip() for m in WIKILINK_RE.finditer(strip_code(text))]


# --------------------------------------------------------------------------- matching


def glob_to_regex(pat: str):
    i, out = 0, ""
    while i < len(pat):
        c = pat[i]
        if c == "*":
            if pat[i : i + 2] == "**":
                i += 2
                if pat[i : i + 1] == "/":
                    i += 1
                    out += "(?:.*/)?"
                else:
                    out += ".*"
                continue
            out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        elif c == "[":
            j = pat.find("]", i + 1)
            if j == -1:
                out += re.escape(c)
            else:
                out += pat[i : j + 1]
                i = j
        else:
            out += re.escape(c)
        i += 1
    return re.compile("^" + out + "$")


def is_glob(ref: str) -> bool:
    return any(c in ref for c in "*?[")


def ref_matcher(ref: str):
    r = ref.strip()
    if r.startswith("./"):
        r = r[2:]
    if is_glob(r):
        rx = glob_to_regex(r)
        return lambda p: rx.match(p) is not None
    if r.endswith("/"):
        return lambda p: p.startswith(r)
    return lambda p: p == r or p.startswith(r + "/")


def ignore_matcher(patterns: list):
    compiled = []
    for pat in patterns:
        compiled.append(("/" in pat, glob_to_regex(pat)))

    def ignored(path: str) -> bool:
        base = path.rsplit("/", 1)[-1]
        for has_slash, rx in compiled:
            if rx.match(path if has_slash else base):
                return True
        return False

    return ignored


# --------------------------------------------------------------------------- project


# --------------------------------------------------------------------------- vault dir setting


def claude_user_settings() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser() / "settings.json"


def user_config() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "agent-vault" / "config.json"


def settings_file(scope: str, repo_root) -> Path | None:
    """The agent-vault config file a `--save <scope>` writes to."""
    if scope == "user":
        return user_config()
    if not repo_root:
        return None
    return Path(repo_root) / REPO_CONFIG["local" if scope == "local" else "project"]


def _read_json(path) -> dict | None:
    """Parsed object, {} if missing, None if unreadable or not a JSON object."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _clean(v) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _config_in(path) -> str | None:
    return _clean((_read_json(path) or {}).get(CONFIG_KEY)) if path else None


def _claude_setting_in(path) -> str | None:
    env = (_read_json(path) or {}).get("env") if path else None
    return _clean(env.get(COMPAT_DIR_KEY)) if isinstance(env, dict) else None


def _candidates(repo_root):
    """(reader, path or env key) in priority order; first hit wins."""
    if repo_root:
        root = Path(repo_root)
        yield _config_in, root / REPO_CONFIG["local"]
        yield _config_in, root / REPO_CONFIG["project"]
        yield _claude_setting_in, root / ".claude" / "settings.local.json"
        yield _claude_setting_in, root / ".claude" / "settings.json"
    for key in (DIR_KEY, COMPAT_DIR_KEY, "VAULT_ROOT"):
        yield None, key
    yield _config_in, user_config()
    yield _claude_setting_in, claude_user_settings()


def dir_setting(repo_root=None, explicit: str = None) -> tuple:
    """(template, source) for the vault dir; first hit wins."""
    if explicit:
        return explicit, "--vault-dir"
    for reader, where in _candidates(repo_root):
        if reader is None:
            v = _clean(os.environ.get(where))
            if v:
                return (v.rstrip("/") + "/{project}" if where == "VAULT_ROOT" else v), f"env {where}"
            continue
        v = reader(where)
        if v:
            return v, str(where)
    return DEFAULT_DIR, "default"


def _exclude_locally(repo_root, name: str) -> None:
    """Keep the personal repo config out of git without touching the shared .gitignore."""
    git_dir = g.git_dir(repo_root)
    if not git_dir:
        return
    exclude = Path(git_dir) / "info" / "exclude"
    try:
        lines = exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []
        if name in lines or f"/{name}" in lines:
            return
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as f:
            if lines and lines[-1].strip():
                f.write("\n")
            f.write(f"/{name}\n")
    except OSError:
        pass


def save_dir_setting(scope: str, repo_root, value: str) -> Path:
    """Set `vault_dir` in the agent-vault config file of `scope`, keeping every other key."""
    path = settings_file(scope, repo_root)
    if not path:
        raise SystemExit(f"vault: --save {scope} needs a git repo")
    data = _read_json(path)
    if data is None:
        raise SystemExit(f"vault: {path} is not a valid JSON object; not changed")
    data[CONFIG_KEY] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if scope == "local":
        _exclude_locally(repo_root, path.name)
    return path


def shared_root(template: str) -> str | None:
    """Root template of a shared vault (last component exactly `{project}`), else None."""
    head, _, last = template.rstrip("/").rpartition("/")
    if last == "{project}" and "{project}" not in head:
        return head or ("/" if template.startswith("/") else ".")
    return None


def expand_dir(template: str, slug, base) -> Path:
    p = Path(os.path.expanduser(template.replace("{project}", slug or "")))
    if not p.is_absolute():
        p = Path(base or os.getcwd()) / p
    return Path(os.path.normpath(p))


def git_host(path: Path) -> Path | None:
    """Toplevel of the git work tree containing `path` (or its nearest existing ancestor)."""
    while not path.exists() and path.parent != path:
        path = path.parent
    top = g.toplevel(path)
    return top.resolve() if top else None


@dataclass
class Ctx:
    vault: Path | None  # vault root: the shared root, or the project dir itself
    cwd: Path
    repo_root: Path | None = None
    remote_raw: str = ""
    remote: str = ""
    slug: str | None = None
    matched: bool = False
    meta: dict = field(default_factory=dict)
    default_branch: str | None = None
    target: str | None = None
    target_sha: str | None = None
    head_sha: str | None = None
    branch: str | None = None
    layout: str = "shared"  # shared: <root>/<slug> · repo: the project dir is its own vault
    dir_setting: str = ""
    dir_source: str = ""

    @property
    def project_dir(self) -> Path | None:
        if not (self.slug and self.vault):
            return None
        return self.vault / self.slug if self.layout == "shared" else self.vault

    @property
    def link_prefix(self) -> str:
        return f"{self.slug}/" if self.layout == "shared" and self.slug else ""

    @property
    def vault_git(self) -> str | None:
        """own: the vault root is (or becomes) its own repo; host-repo: it lives inside another repo."""
        if not self.vault:
            return None
        host = git_host(self.vault)
        return "own" if host is None or host == self.vault.resolve() else "host-repo"

    @property
    def initialized(self) -> bool:
        return bool(self.matched and self.project_dir and (self.project_dir / "_meta.json").exists())

    @property
    def head_matches_target(self) -> bool:
        return bool(self.head_sha and self.head_sha == self.target_sha)

    @property
    def work_dir(self) -> Path | None:
        if self.layout == "shared":
            return self.vault / ".work" / (self.slug or "_") if self.vault else None
        if not self.project_dir:
            return None
        # Outside the vault: worktrees must not land inside the code repo.
        h = hashlib.sha1(str(self.project_dir).encode()).hexdigest()[:8]
        cache = Path(os.environ.get("XDG_CACHE_HOME") or "~/.cache").expanduser()
        return cache / "agent-vault" / f"{self.slug}-{h}"

    @property
    def tree_dir(self) -> Path | None:
        return self.work_dir / "tree" if self.work_dir else None

    @property
    def code_root(self) -> Path | None:
        if not self.repo_root:
            return None
        if self.head_matches_target or not self.target_sha:
            return self.repo_root
        if self.tree_dir and self.tree_dir.is_dir() and g.out(self.tree_dir, "rev-parse", "HEAD") == self.target_sha:
            return self.tree_dir
        return None

    @property
    def stale_ignore(self) -> list:
        v = self.meta.get("stale_ignore")
        return v if isinstance(v, list) else DEFAULT_STALE_IGNORE

    @property
    def last_synced(self) -> str:
        v = self.meta.get("last_synced")
        return v if isinstance(v, str) else ""

    def commits_behind(self) -> int | None:
        if not (self.repo_root and self.last_synced and self.target_sha):
            return None
        if not g.is_commit(self.repo_root, self.last_synced):
            return None
        return g.rev_count(self.repo_root, self.last_synced, self.target_sha)

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "initialized": self.initialized,
            "vault_dir": str(self.project_dir) if self.project_dir else None,
            "vault_root": str(self.vault) if self.vault else None,
            "link_prefix": self.link_prefix,
            "work_dir": str(self.work_dir) if self.work_dir else None,
            "layout": self.layout,
            "dir_setting": self.dir_setting,
            "dir_source": self.dir_source,
            "vault_git": self.vault_git,
            "repo_root": str(self.repo_root) if self.repo_root else None,
            "remote": self.remote,
            "default_branch": self.default_branch,
            "branch": self.branch,
            "target": self.target,
            "target_sha": self.target_sha,
            "head_matches_target": self.head_matches_target,
            "code_root": str(self.code_root) if self.code_root else None,
            "last_synced": self.last_synced or None,
            "commits_behind": self.commits_behind(),
            "skill_dir": str(SKILL_DIR),
            "cli": str(SKILL_DIR / "bin" / "vault"),
        }


def read_meta(project_dir: Path) -> dict:
    try:
        data = json.loads((project_dir / "_meta.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_meta(project_dir: Path, meta: dict) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    tmp = project_dir / "_meta.json.tmp"
    tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(project_dir / "_meta.json")


def list_projects(vault: Path) -> list:
    out = []
    if not vault.is_dir():
        return out
    for d in sorted(vault.iterdir()):
        if d.is_dir() and not d.name.startswith(".") and (d / "_meta.json").exists():
            out.append((d.name, read_meta(d)))
    return out


def _path_match(paths, *candidates) -> bool:
    for p in paths or []:
        try:
            pp = Path(p).expanduser().resolve()
        except OSError:
            continue
        for c in candidates:
            if c and (c == pp or pp in c.parents):
                return True
    return False


def resolve(cwd=None, project: str = None, vault_dir: str = None) -> Ctx:
    cwd_p = Path(cwd or os.getcwd()).expanduser().resolve()
    ctx = Ctx(vault=None, cwd=cwd_p)
    ctx.repo_root = g.toplevel(cwd_p)
    if ctx.repo_root:
        ctx.repo_root = ctx.repo_root.resolve()
        ctx.remote_raw = g.remote_url(ctx.repo_root)
        ctx.remote = g.normalize_remote(ctx.remote_raw)

    if not project and ctx.repo_root and read_meta(ctx.repo_root).get("schema"):
        # Running from inside a standalone per-repo vault (its own git repo): it names the project.
        ctx.layout, ctx.vault = "repo", ctx.repo_root
        ctx.dir_setting, ctx.dir_source = str(ctx.repo_root), "cwd"
        ctx.slug, ctx.matched = str(read_meta(ctx.repo_root).get("slug") or ctx.repo_root.name), True
        ctx.repo_root, ctx.remote, ctx.remote_raw = None, "", ""
    else:
        ctx.dir_setting, ctx.dir_source = dir_setting(ctx.repo_root, vault_dir)
        root_t = shared_root(ctx.dir_setting)
        if root_t is not None:
            ctx.vault = expand_dir(root_t, None, ctx.repo_root or cwd_p)
            project = _resolve_shared(ctx, project)
        elif project == HARNESS:
            ctx.vault = harness_root()
            project = _resolve_shared(ctx, project)
        else:
            ctx.layout = "repo"
            _resolve_repo(ctx, project)
    if is_pseudo(ctx.slug):
        # A pseudo-project belongs to no repo, wherever it is resolved from.
        ctx.repo_root, ctx.remote, ctx.remote_raw = None, "", ""

    if ctx.matched:
        ctx.meta = read_meta(ctx.project_dir)
        belongs = bool(ctx.repo_root) and (
            ctx.remote in [str(r).lower() for r in ctx.meta.get("remotes", [])]
            or _path_match(ctx.meta.get("paths"), ctx.repo_root)
        )
        if (project or not ctx.repo_root) and not belongs:
            ctx.repo_root = None
            for p in ctx.meta.get("paths", []):
                top = g.toplevel(Path(p).expanduser())
                if top:
                    ctx.repo_root = top.resolve()
                    ctx.remote_raw = g.remote_url(ctx.repo_root)
                    ctx.remote = g.normalize_remote(ctx.remote_raw)
                    break
    if ctx.repo_root:
        ctx.default_branch = ctx.meta.get("default_branch") or g.default_branch(ctx.repo_root)
        ctx.head_sha = g.rev_parse(ctx.repo_root, "HEAD")
        ctx.branch = g.current_branch(ctx.repo_root)
        for ref in (f"origin/{ctx.default_branch}", ctx.default_branch):
            sha = g.rev_parse(ctx.repo_root, ref)
            if sha:
                ctx.target, ctx.target_sha = ref, sha
                break
        if not ctx.target_sha and ctx.head_sha:
            ctx.target, ctx.target_sha = "HEAD", ctx.head_sha
    return ctx


def harness_root() -> Path:
    """Shared root holding `_harness` in a per-repo layout. $AGENT_VAULT_HARNESS_DIR (or the legacy
    $CLAUDE_VAULT_HARNESS_DIR) names the `_harness` dir itself or a root that holds it; otherwise an existing
    ~/claude-vault/_harness, else $XDG_DATA_HOME/agent-vault/_harness."""
    raw = _clean(os.environ.get(HARNESS_KEY)) or _clean(os.environ.get(COMPAT_HARNESS_KEY))
    if not raw:
        legacy = Path(LEGACY_HARNESS_DIR).expanduser()
        data = Path(os.environ.get("XDG_DATA_HOME") or "~/.local/share").expanduser()
        raw = str(legacy if legacy.is_dir() else data / "agent-vault" / HARNESS)
    p = Path(os.path.normpath(os.path.expanduser(raw)))
    if not p.is_absolute():
        p = Path.home() / p
    return p.parent if p.name == HARNESS else p


def harness_ctx(ctx: Ctx) -> Ctx | None:
    """Context of the `_harness` project that goes with `ctx` (same root in a shared layout). None when `ctx`
    already is the harness. Cheap: no git calls."""
    if ctx.slug == HARNESS:
        return None
    root = ctx.vault if ctx.layout == "shared" and ctx.vault else harness_root()
    h = Ctx(vault=root, cwd=ctx.cwd, slug=HARNESS, layout="shared", dir_setting=ctx.dir_setting,
            dir_source=ctx.dir_source)
    h.matched = (root / HARNESS / "_meta.json").exists()
    if h.matched:
        h.meta = read_meta(root / HARNESS)
    return h


def is_pseudo(slug) -> bool:
    """Pseudo-projects (`_harness`) never get paths/remotes, so no directory resolves to them."""
    return str(slug or "").startswith("_")


def _resolve_shared(ctx: Ctx, project):
    """Shared root holding many projects: find ours by remote / path. Returns the explicit project, if any."""
    vault, cwd_p = ctx.vault, ctx.cwd
    projects = list_projects(vault)
    if not project:
        # Running from inside the vault (e.g. ~/claude-vault/<slug>/modules): the path names the project.
        try:
            rel = cwd_p.relative_to(vault.resolve())
            if rel.parts and (vault / rel.parts[0] / "_meta.json").exists():
                project = rel.parts[0]
                ctx.repo_root, ctx.remote, ctx.remote_raw = None, "", ""
        except ValueError:
            pass
    if project:
        ctx.slug = project
        ctx.matched = (vault / project / "_meta.json").exists()
        return project
    if ctx.remote:
        for slug, meta in projects:
            if ctx.remote in [str(r).lower() for r in meta.get("remotes", [])]:
                ctx.slug, ctx.matched = slug, True
                return None
    for slug, meta in projects:
        if _path_match(meta.get("paths"), ctx.repo_root, cwd_p):
            ctx.slug, ctx.matched = slug, True
            return None
    if ctx.repo_root or ctx.remote:
        base = ctx.remote.rsplit("/", 1)[-1] if ctx.remote else ctx.repo_root.name
        slug = slugify(base)
        n = 2
        while (vault / slug / "_meta.json").exists():
            slug = f"{slugify(base)}-{n}"
            n += 1
        ctx.slug = slug
    return None


def _resolve_repo(ctx: Ctx, project) -> None:
    """Per-repo / fixed dir: the template names this project's vault directly."""
    base = ctx.remote.rsplit("/", 1)[-1] if ctx.remote else (ctx.repo_root.name if ctx.repo_root else None)
    slug = project or (slugify(base) if base else None)
    t = ctx.dir_setting
    if ("{project}" in t and not slug) or (not ctx.repo_root and not os.path.isabs(os.path.expanduser(t))):
        return
    ctx.vault = expand_dir(t, slug, ctx.repo_root)
    if not (ctx.vault / "_meta.json").exists():
        ctx.slug = slug
        return
    mslug = str(read_meta(ctx.vault).get("slug") or slug or ctx.vault.name)
    ctx.slug, ctx.matched = (project, False) if project and mslug != project else (mslug, True)


def ensure_vault_root(vault: Path) -> None:
    vault.mkdir(parents=True, exist_ok=True)
    gi = vault / ".gitignore"
    if not gi.exists():
        gi.write_text(".work/\n.lock/\n.DS_Store\n.obsidian/workspace*.json\n.obsidian/cache\n", encoding="utf-8")
    obs = vault / ".obsidian"
    obs.mkdir(exist_ok=True)
    app = obs / "app.json"
    if not app.exists():
        app.write_text(
            json.dumps({"newLinkFormat": "absolute", "useMarkdownLinks": False, "alwaysUpdateLinks": True}, indent=2)
            + "\n",
            encoding="utf-8",
        )
    if not git_host(vault):
        g.run(["git", "init", "-q", str(vault)])


def create_project(ctx: Ctx) -> bool:
    """Create the project skeleton; returns True if it was created now."""
    ensure_vault_root(ctx.vault)
    pdir = ctx.project_dir
    created = not (pdir / "_meta.json").exists()
    meta = read_meta(pdir) if not created else {}
    meta.setdefault("slug", ctx.slug)
    meta.setdefault("schema", SCHEMA)
    meta.setdefault("created", today())
    meta.setdefault("last_synced", None)
    meta.setdefault("stale_ignore", DEFAULT_STALE_IGNORE)
    if ctx.default_branch:
        meta.setdefault("default_branch", ctx.default_branch)
    remotes = list(meta.get("remotes", []))
    if ctx.remote and ctx.remote not in remotes and not is_pseudo(ctx.slug):
        remotes.append(ctx.remote)
    meta["remotes"] = remotes
    if not is_pseudo(ctx.slug):
        register_path(meta, ctx.repo_root or ctx.cwd)
    write_meta(pdir, meta)
    for folder in FOLDERS.values():
        (pdir / folder).mkdir(parents=True, exist_ok=True)
    log = pdir / "log.md"
    if not log.exists():
        log.write_text(
            fmlib.render({"title": f"{ctx.slug} log", "type": "log", "summary": "Append-only history of vault updates."},
                         f"\n# Log — {ctx.slug}\n"),
            encoding="utf-8",
        )
    ctx.matched = True
    ctx.meta = meta
    return created


def register_path(meta: dict, path) -> bool:
    if not path:
        return False
    p = str(Path(path).resolve())
    paths = list(meta.get("paths", []))
    if p not in paths:
        paths.append(p)
        meta["paths"] = paths
        return True
    return False


# --------------------------------------------------------------------------- staleness


class Staleness:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.root = ctx.repo_root
        self.target = ctx.target_sha
        self.ignored = ignore_matcher(ctx.stale_ignore)
        self._diff: dict = {}
        self._tree = None

    @property
    def available(self) -> bool:
        return bool(self.root and self.target)

    def changed_since(self, sha: str):
        if sha not in self._diff:
            if not g.is_commit(self.root, sha):
                self._diff[sha] = None
            else:
                files = g.changed_files(self.root, sha, self.target)
                self._diff[sha] = None if files is None else [f for f in files if not self.ignored(f)]
        return self._diff[sha]

    def tree(self) -> set:
        if self._tree is None:
            self._tree = g.ls_tree(self.root, self.target) if self.available else set()
        return self._tree

    def missing_refs(self, note: Note) -> list:
        if not self.available:
            return []
        tree = self.tree()
        missing = []
        for ref in note.code_refs:
            m = ref_matcher(ref)
            if not any(m(p) for p in tree):
                missing.append(ref)
        return missing

    def state(self, note: Note) -> dict:
        refs = note.code_refs
        if not refs:
            return {"state": "n/a"}
        if not self.available:
            return {"state": "no-repo"}
        v = note.verified
        changed = self.changed_since(v) if v else None
        if changed is None:
            return {"state": "unverified", "verified": v}
        matchers = [ref_matcher(r) for r in refs]
        hits = [f for f in changed if any(m(f) for m in matchers)]
        return {"state": "stale" if hits else "ok", "verified": v, "changed": hits}


# --------------------------------------------------------------------------- search


def tokenize(q: str) -> list:
    raw = re.findall(r"[A-Za-z0-9_$]+(?:[-.][A-Za-z0-9_$]+)*", q.lower())
    terms = []
    for t in raw:
        for part in [t] + re.split(r"[-._]", t):
            if len(part) >= 2 and part not in STOPWORDS and part not in terms:
                terms.append(part)
    return terms


def score_note(note: Note, terms: list) -> tuple:
    title = note.title.lower()
    aliases = " ".join(note.aliases).lower()
    meta = " ".join([title, aliases, note.summary.lower(), note.path.stem.lower()])
    refs = " ".join(note.code_refs).lower()
    body = note.body.lower()
    score, matched = 0.0, 0
    for t in terms:
        hit = False
        if t in title or t in aliases:
            score += 4
            hit = True
        elif t in meta:
            score += 3
            hit = True
        if t in refs:
            score += 2
            hit = True
        c = body.count(t)
        if c:
            score += min(c, 6) * 0.5
            hit = True
        matched += hit
    if terms and matched == len(terms) and len(terms) > 1:
        score += 2
    return score, matched


def snippet(note: Note, terms: list, width: int = 160) -> str:
    for line in note.body.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        low = s.lower()
        if any(t in low for t in terms):
            return s if len(s) <= width else s[: width - 1] + "…"
    return ""


def search(notes: list, query: str, types=None, limit: int = 8) -> list:
    terms = tokenize(query)
    if not terms:
        return []
    results = []
    for n in notes:
        if n.type in ("hub", "log") and not (types and n.type in types):
            continue
        if types and n.type not in types:
            continue
        if n.type == "retro" and not types:
            continue
        score, matched = score_note(n, terms)
        if matched:
            results.append((score, n))
    results.sort(key=lambda x: (-x[0], x[1].rel))
    return [(s, n, snippet(n, terms)) for s, n in results[:limit]]


IDENT_RE = re.compile(r"^(?=.*[a-z])(?=.*[A-Z_])[A-Za-z_$][\w$]{2,}$|^[a-z]+(?:_[a-z0-9]+)+$")


def identifiers(query: str) -> list:
    """Query words that look like code identifiers (camelCase, PascalCase, snake_case, CONST_CASE)."""
    out = []
    for w in re.findall(r"[A-Za-z_$][\w$]*", query):
        if IDENT_RE.match(w) and w not in out:
            out.append(w)
    return out[:3]


def code_hits(ctx: Ctx, notes: list, idents: list, max_files: int = 12) -> list:
    """(identifier, file, [notes]) for files at the target that contain the identifier."""
    if not (ctx.repo_root and ctx.target_sha):
        return []
    hits = []
    for ident in idents:
        rc, so, _ = g.git(ctx.repo_root, "grep", "-l", "-w", "-F", "-I", "-e", ident, ctx.target_sha, timeout=60)
        if rc != 0:
            continue
        prefix = ctx.target_sha + ":"
        files = [ln[len(prefix):] if ln.startswith(prefix) else ln for ln in so.splitlines()][:max_files]
        for f in files:
            hits.append((ident, f, notes_for_code(notes, f)))
    return hits


def coverage(ctx: Ctx, notes: list, depth: int = 2, exts=(".ts", ".tsx", ".js", ".jsx", ".py")) -> list:
    """Directories (to `depth`) with source files at the target but no note referencing any of them.
    Returns [(dir, source_files)] sorted by size."""
    st = Staleness(ctx)
    if not st.available:
        return []
    matchers = [ref_matcher(r) for n in notes for r in n.code_refs]
    ignored = ignore_matcher(ctx.stale_ignore + ["**/node_modules/**", "**/dist/**", "**/*.d.ts"])
    dirs: dict = {}
    for f in st.tree():
        if not f.endswith(exts) or ignored(f):
            continue
        parts = f.split("/")
        key = "/".join(parts[: min(depth, len(parts) - 1)]) or "(root)"
        total, covered = dirs.get(key, (0, 0))
        dirs[key] = (total + 1, covered + (1 if any(m(f) for m in matchers) else 0))
    gaps = [(d, t) for d, (t, c) in dirs.items() if c == 0]
    return sorted(gaps, key=lambda x: -x[1])


def notes_for_code(notes: list, rel_path: str) -> list:
    out = []
    for n in notes:
        if any(ref_matcher(r)(rel_path) for r in n.code_refs):
            out.append(n)
    return out


# --------------------------------------------------------------------------- lint


def resolve_link(vault: Path, target: str, basenames: dict):
    """Return (rel_path or None, absolute: bool)."""
    t = target.strip()
    cand = t if t.endswith(".md") else t + ".md"
    if (vault / cand).is_file():
        return cand, True
    base = cand.rsplit("/", 1)[-1]
    hits = basenames.get(base, [])
    if len(hits) == 1:
        return hits[0], False
    return None, False


def lint(ctx: Ctx, notes: list, all_notes: list = None) -> list:
    """Return issues as dicts: level (error|warn), note, msg."""
    issues = []

    def add(level, note, msg):
        issues.append({"level": level, "note": note.rel if isinstance(note, Note) else note, "msg": msg})

    vault = ctx.vault
    all_notes = all_notes or notes
    basenames: dict = {}
    for n in all_notes:
        basenames.setdefault(n.name, []).append(n.rel)
    st = Staleness(ctx)
    inbound: dict = {n.rel: 0 for n in notes}
    alias_owner: dict = {}
    contents_cache: dict = {}
    unresolved: list = []  # (note, symbol) not found in the note's own code_refs

    for n in notes:
        if n.type in CONTENT_TYPES:
            alias_owner.setdefault(n.title.strip().lower(), n.rel)
            alias_owner.setdefault(n.path.stem.lower(), n.rel)

    if not any(n.type == "project" for n in notes):
        add("error", f"{ctx.link_prefix}index.md", "missing project index (L1)")

    for n in notes:
        t = n.type
        if t in ("hub", "log"):
            continue
        if not n.has_fm:
            add("error", n, "no frontmatter")
            continue
        for key in ("title", "type", "summary"):
            if not n.fm.get(key):
                add("error", n, f"missing '{key}'")
        if t not in VALID_TYPES:
            add("error", n, f"unknown type '{t}'")
        elif n.status and n.status not in STATUSES.get(t, {n.status}):
            add("warn", n, f"status '{n.status}' not valid for {t} ({', '.join(sorted(STATUSES[t]))})")
        if t != n.inferred_type and n.inferred_type != "note":
            add("warn", n, f"type '{t}' but lives where '{n.inferred_type}' notes go")
        if "## Summary" not in n.body and t in CONTENT_TYPES + ("project",):
            add("warn", n, "no '## Summary' section")
        budget = BUDGETS.get(t)
        if t == "feature" and "--" in n.path.stem:
            budget = BUDGETS["module"]
        if budget and n.tokens > budget:
            add("warn", n, f"~{n.tokens} tokens > budget {budget}; split it")
        for a in n.aliases:
            # Shared synonyms are normal; an alias that is another note's name makes lookups ambiguous.
            owner = alias_owner.get(a.strip().lower())
            if owner and owner != n.rel:
                add("warn", n, f"alias '{a}' is the name of {owner}")
        if t == "module" and not n.code_refs:
            add("warn", n, "module note without code_refs")
        for ref in n.code_refs:
            if "**" in ref:
                add("warn", n, f"broad glob in code_refs: {ref} (prefer specific files)")
        for rx in SECRET_RES:
            if rx.search(n.body):
                add("error", n, "possible secret in note body")
                break
        if t == "lesson":
            _lint_lesson(n, add)
        elif t == "retro" and str(n.fm.get("outcome", "")) not in RETRO_OUTCOMES:
            add("warn", n, f"outcome '{n.fm.get('outcome', '')}' not valid ({', '.join(RETRO_OUTCOMES)})")

        # links
        for target in wikilinks(n.body):
            rel, absolute = resolve_link(vault, target, basenames)
            if rel is None:
                add("error", n, f"broken link [[{target}]]")
            else:
                if not absolute:
                    add("warn", n, f"link [[{target}]] is not vault-absolute; use [[{rel[:-3]}]]")
                if rel in inbound and rel != n.rel:
                    inbound[rel] += 1

        # code checks
        if st.available and n.code_refs:
            for ref in st.missing_refs(n):
                add("error", n, f"code_ref not found at {ctx.target}: {ref}")
            s = st.state(n)
            if s["state"] == "stale":
                files = s["changed"]
                more = f" (+{len(files) - 3} more)" if len(files) > 3 else ""
                add("warn", n, f"stale: {len(files)} referenced file(s) changed since {s['verified'][:12]}: "
                               f"{', '.join(files[:3])}{more}")
            elif s["state"] == "unverified":
                add("warn", n, "unverified: 'verified' missing or not a known commit")
            exact = [r for r in n.code_refs if not is_glob(r) and not r.endswith("/")]
            if exact:
                blob = ""
                for r in exact[:30]:
                    if r not in contents_cache:
                        contents_cache[r] = g.show(st.root, st.target, r) or ""
                    blob += contents_cache[r]
                if blob:
                    for sym in sorted(set(symbols_in(n.body))):
                        last = sym.split(".")[-1]
                        if not re.search(rf"(?<![\w$]){re.escape(last)}(?![\w$])", blob):
                            unresolved.append((n, sym))

    # Symbols defined outside the note's own refs are fine; warn only about names that
    # exist nowhere in the repo at the target commit (likely hallucinated or renamed).
    if unresolved:
        found = g.grep_words(st.root, st.target, sorted({s.split(".")[-1] for _, s in unresolved}))
        for n, sym in unresolved:
            if sym.split(".")[-1] not in found:
                add("warn", n, f"symbol `{sym}` not found anywhere in the repo at {ctx.target}")

    for n in notes:
        if n.type in CONTENT_TYPES and n.type != "retro" and inbound.get(n.rel, 0) == 0:
            add("warn", n, "orphan: no inbound links from other notes")
    return issues


def _lint_lesson(n: Note, add) -> None:
    if lesson_occurrences(n) < 1:
        add("error", n, f"occurrences must be an integer >= 1 (got '{n.fm.get('occurrences', '')}')")
    for key, allowed in (("scope", LESSON_SCOPES), ("category", LESSON_CATEGORIES),
                         ("proposal_status", PROPOSAL_STATUSES), ("proposal_kind", PROPOSAL_KINDS)):
        v = n.fm.get(key, "")
        if not isinstance(v, str) or v not in allowed:
            add("warn", n, f"{key} '{v}' not valid ({', '.join(allowed)})")
    for a in _as_list(n.fm.get("audience")):
        if a not in LESSON_AUDIENCES:
            add("warn", n, f"audience '{a}' not valid ({', '.join(LESSON_AUDIENCES)})")
    last = n.fm.get("last_seen")
    if n.status == "active" and isinstance(last, str) and last:
        try:
            age = (_dt.date.today() - _dt.date.fromisoformat(last[:10])).days
        except ValueError:
            age = None
        if age is not None and age > STALE_LESSON_DAYS:
            add("warn", n, f"stale lesson: last seen {last[:10]}; retire or confirm")


# --------------------------------------------------------------------------- graph


def graph_data(ctx: Ctx, notes: list, stale: bool = True) -> dict:
    """Nodes (notes and code files) and edges (wikilinks, code_refs) for `vault graph`."""
    shown = [n for n in notes if n.type not in ("hub", "log")]
    basenames: dict = {}
    for n in notes:
        basenames.setdefault(n.name, []).append(n.rel)
    by_rel = {n.rel: n for n in shown}
    st = Staleness(ctx) if stale else None
    check = bool(st and st.available)
    nodes, edges, code_nodes, seen = [], [], {}, set()
    for n in shown:
        broken = []
        for target in wikilinks(n.body):
            rel, _ = resolve_link(ctx.vault, target, basenames)
            if rel is None:
                if target not in broken:
                    broken.append(target)
                continue
            other = by_rel.get(rel)
            if other is None or other.rel == n.rel or (n.link, other.link) in seen:
                continue
            seen.add((n.link, other.link))
            edges.append({"source": n.link, "target": other.link, "kind": "link"})
        node = {
            "id": n.link, "kind": "note", "rel": n.rel, "type": n.type, "status": n.status, "title": n.title,
            "summary": n.summary, "aliases": n.aliases, "code_refs": n.code_refs, "verified": n.verified,
            "tokens": n.tokens, "body": n.body, "broken_links": broken,
        }
        if check:
            s = st.state(n)
            node["state"] = s["state"]
            node["changed"] = s.get("changed", [])
            node["missing"] = st.missing_refs(n)
        nodes.append(node)
        for ref in dict.fromkeys(n.code_refs):
            cid = f"code:{ref}"
            if cid not in code_nodes:
                code_nodes[cid] = {"id": cid, "kind": "code", "type": "code", "title": ref, "ref": ref,
                                   "exact": not is_glob(ref) and not ref.endswith("/")}
            edges.append({"source": n.link, "target": cid, "kind": "ref"})
    counts: dict = {}
    for n in shown:
        counts[n.type] = counts.get(n.type, 0) + 1
    return {
        "meta": {
            "slug": ctx.slug, "target": ctx.target, "target_sha": (ctx.target_sha or "")[:12],
            "repo_root": str(ctx.repo_root) if ctx.repo_root else None, "link_prefix": ctx.link_prefix,
            "generated": _dt.datetime.now().isoformat(timespec="seconds"), "counts": counts,
            "stale_checked": check,
        },
        "nodes": nodes + list(code_nodes.values()),
        "edges": edges,
    }


# --------------------------------------------------------------------------- lessons


def lesson_occurrences(n: Note) -> int:
    """`occurrences` as an int; 0 when missing or not an integer."""
    v = n.fm.get("occurrences")
    s = str(v).strip() if v is not None else ""
    return int(s) if re.fullmatch(r"\d+", s) else 0


def lesson_rule(n: Note) -> str:
    """First text line under `## Rule`; falls back to the summary, then the title."""
    in_rule = False
    for line in n.body.splitlines():
        s = line.strip()
        if s.startswith("#"):
            if in_rule:
                break
            in_rule = s.lstrip("#").strip().lower() == "rule"
            continue
        if in_rule and s and not s.startswith("<!--"):
            return s
    return n.summary or n.title


def lesson_sort_key(n: Note):
    return (-lesson_occurrences(n), _neg_date(n.fm.get("last_seen")), n.rel)


def _neg_date(v) -> int:
    """Sort key for 'newest first': minus the date's ordinal (undated last)."""
    try:
        return -_dt.date.fromisoformat(str(v)[:10]).toordinal()
    except ValueError:
        return 0


def lessons(ctx: Ctx, audience=None, statuses=("active",)) -> list:
    """Lesson notes of ctx's project with a status in `statuses`, ordered occurrences desc, last_seen desc.
    `audience`: a name or a collection of names; keep lessons whose audience contains any of them."""
    if not (ctx and ctx.matched and ctx.project_dir and ctx.project_dir.is_dir()):
        return []
    want = {audience} if isinstance(audience, str) else set(audience or ())
    out = []
    for n in load_notes(ctx.project_dir / FOLDERS["lesson"], ctx.vault):
        if n.type != "lesson" or (statuses and n.status not in statuses):
            continue
        if want and not want & set(_as_list(n.fm.get("audience"))):
            continue
        out.append(n)
    return sorted(out, key=lesson_sort_key)


def open_proposals(ctx: Ctx) -> list:
    """Lessons (any status) whose proposal_status is `open`."""
    return [n for n in lessons(ctx, statuses=None) if n.fm.get("proposal_status") == "open"]


def lesson_dict(n: Note) -> dict:
    return {
        "link": n.link, "title": n.title, "rule": lesson_rule(n), "occurrences": lesson_occurrences(n),
        "last_seen": str(n.fm.get("last_seen") or ""), "scope": str(n.fm.get("scope") or ""),
        "audience": _as_list(n.fm.get("audience")), "category": str(n.fm.get("category") or ""),
        "proposal_status": str(n.fm.get("proposal_status") or "none"),
        "proposal_kind": str(n.fm.get("proposal_kind") or "none"),
        "proposal_target": str(n.fm.get("proposal_target") or ""),
    }


def harness_limit(n: int) -> int:
    return math.ceil(n / 2) if n > 0 else 0


def symbols_in(body: str) -> list:
    """Backticked tokens that look like code identifiers (camelCase, PascalCase, snake_case, calls)."""
    body = FENCE_RE.sub("", body)
    out = []
    for m in SYMBOL_RE.finditer(body):
        tok, call = m.group(1), m.group(2)
        if FILE_EXT_RE.search(tok):
            continue
        last = tok.split(".")[-1]
        mixed = bool(re.search(r"[a-z]", last) and re.search(r"[A-Z]", last))
        if call or mixed or ("_" in last.strip("_") and len(last) > 3):
            out.append(tok)
    return out


# --------------------------------------------------------------------------- hubs / index / log


def hub_text(slug: str, folder: str, notes: list) -> str:
    title = HUB_TITLES[folder]
    items = [n for n in notes if n.prel.startswith(f"{folder}/") and n.type not in ("hub", "log")]
    if folder == "problems":
        order = {"open": 0, "wontfix": 1, "resolved": 2}
        items.sort(key=lambda n: (order.get(n.status, 0), n.title.lower()))
    elif folder == "decisions":
        items.sort(key=lambda n: n.path.stem)
    elif folder == "lessons":
        items.sort(key=lesson_sort_key)
    elif folder == "retros":
        items.sort(key=lambda n: (_neg_date(n.fm.get("date") or n.path.stem[:10]), n.path.stem))
    else:
        items.sort(key=lambda n: n.title.lower())
    lines = [f"# {title} — {slug}", "", "<!-- generated by `vault commit`; do not edit -->", ""]
    if not items:
        lines.append("_No notes yet._")
    for n in items:
        status = f" _({n.status})_" if n.status and n.status not in ("active", "recorded") else ""
        summary = f" — {n.summary}" if n.summary else ""
        lines.append(f"- [[{n.link}|{n.title}]]{summary}{status}")
    fields = {"title": f"{title} ({slug})", "type": "hub", "summary": f"Generated list of {folder} notes."}
    return fmlib.render(fields, "\n" + "\n".join(lines) + "\n")


def write_hubs(ctx: Ctx) -> list:
    notes = load_notes(ctx.project_dir, ctx.vault)
    written = []
    for folder in FOLDERS.values():
        if not (ctx.project_dir / folder).is_dir():
            continue
        path = ctx.project_dir / folder / "_index.md"
        text = hub_text(ctx.slug, folder, notes)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            written.append(path)
    return written


def write_root_index(vault: Path) -> Path:
    lines = ["# Projects", "", "<!-- generated by `vault commit`; do not edit -->", ""]
    for slug, meta in list_projects(vault):
        pdir = vault / slug
        idx = load_note(pdir / "index.md", vault, pdir) if (pdir / "index.md").exists() else None
        count = sum(1 for n in load_notes(pdir, vault) if n.type in CONTENT_TYPES)
        summary = f" — {idx.summary}" if idx and idx.summary else ""
        synced = meta.get("last_synced")
        synced_s = f" · synced @{synced[:12]}" if synced else " · not synced"
        link = f"[[{slug}/index|{slug}]]" if idx else slug
        lines.append(f"- {link}{summary} · {count} notes{synced_s}")
    text = fmlib.render(
        {"title": "Knowledge vault", "type": "hub", "summary": "Generated list of projects in the vault."},
        "\n" + "\n".join(lines) + "\n",
    )
    path = vault / "index.md"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return path


def content_digest(project_dir: Path) -> str:
    """Fingerprint of the project's files (minus log.md): tells a no-op `vault commit` in a host repo."""
    h = hashlib.sha1()
    for dirpath, dirnames, filenames in os.walk(project_dir):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fn in sorted(filenames):
            f = Path(dirpath) / fn
            if f == project_dir / "log.md":
                continue
            h.update(f.relative_to(project_dir).as_posix().encode() + b"\0")
            try:
                h.update(f.read_bytes())
            except OSError:
                pass
    return h.hexdigest()


def append_log(ctx: Ctx, line: str, details: list = None) -> None:
    log = ctx.project_dir / "log.md"
    if not log.exists():
        create_project(ctx)
    with log.open("a", encoding="utf-8") as f:
        f.write(f"\n## [{today()}] {line}\n")
        for d in details or []:
            f.write(f"- {d}\n")


# --------------------------------------------------------------------------- lock


class Lock:
    def __init__(self, vault: Path, timeout: float = 60, stale_after: float = 900):
        self.dir = vault / ".lock"
        self.timeout = timeout
        self.stale_after = stale_after

    def _stale(self) -> bool:
        try:
            pid = int((self.dir / "pid").read_text().strip())
            age = time.time() - (self.dir / "pid").stat().st_mtime
        except (OSError, ValueError):
            try:
                return time.time() - self.dir.stat().st_mtime > 30
            except OSError:
                return True
        if age > self.stale_after:
            return True
        try:
            os.kill(pid, 0)
            return False
        except ProcessLookupError:
            return True
        except PermissionError:
            return False

    def __enter__(self):
        deadline = time.time() + self.timeout
        self.dir.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self.dir.mkdir()
                (self.dir / "pid").write_text(str(os.getpid()))
                return self
            except FileExistsError:
                if self._stale():
                    self._release()
                    continue
                if time.time() > deadline:
                    raise TimeoutError(f"vault is locked ({self.dir}); retry later")
                time.sleep(0.3)

    def _release(self):
        try:
            (self.dir / "pid").unlink()
        except OSError:
            pass
        try:
            self.dir.rmdir()
        except OSError:
            pass

    def __exit__(self, *exc):
        self._release()
        return False
