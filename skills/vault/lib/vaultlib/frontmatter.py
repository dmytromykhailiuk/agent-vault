"""Minimal frontmatter reader/writer.

Supports the subset the vault uses: `key: scalar`, `key: [inline, list]` (JSON or
loose), and block lists (`key:` followed by `- item` lines, which Obsidian writes).
Writes only touch the lines of the key being set, so hand or Obsidian formatting
of the rest of the block survives.
"""
from __future__ import annotations

import json
import re

FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
KEY_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:(.*)$")
ITEM_RE = re.compile(r"^\s*-(?:\s+(.*))?$")
SIMPLE_RE = re.compile(r"^[A-Za-z0-9_.\-/]+$")


def split(text: str):
    """Return (frontmatter_text or None, body)."""
    m = FM_RE.match(text)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def _split_items(inner: str) -> list:
    items, buf, quote = [], [], None
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            items.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        items.append("".join(buf))
    return [_scalar(x) for x in items if x.strip()]


def _scalar(raw: str) -> str:
    v = raw.strip()
    if not v:
        return ""
    if v[0] == '"':
        try:
            val = json.loads(v)
            if isinstance(val, str):
                return val
        except ValueError:
            pass
        end = v.rfind('"')
        return v[1:end] if end > 0 else v[1:]
    if v[0] == "'":
        end = v.rfind("'")
        inner = v[1:end] if end > 0 else v[1:]
        return inner.replace("''", "'")
    return re.split(r"\s+#", v, maxsplit=1)[0].strip()


def _inline_list(raw: str) -> list:
    v = raw.strip()
    end = v.rfind("]")
    candidate = v[: end + 1] if end > 0 else v
    try:
        val = json.loads(candidate)
        if isinstance(val, list):
            return [str(x) for x in val]
    except ValueError:
        pass
    inner = v[1:end] if end > 0 else v[1:]
    return _split_items(inner)


def parse(fm_text: str) -> dict:
    data: dict = {}
    key = None
    for line in fm_text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = KEY_RE.match(line)
        if m and not line[0].isspace():
            key, rest = m.group(1), m.group(2).strip()
            if rest == "":
                data[key] = None
            elif rest.startswith("["):
                data[key] = _inline_list(rest)
            else:
                data[key] = _scalar(rest)
            continue
        im = ITEM_RE.match(line)
        if im and key is not None:
            if not isinstance(data.get(key), list):
                data[key] = []
            if im.group(1) is not None and im.group(1).strip():
                data[key].append(_scalar(im.group(1)))
    return {k: ("" if v is None else v) for k, v in data.items()}


def read(text: str):
    """Return (fields dict, body, has_frontmatter)."""
    fm, body = split(text)
    if fm is None:
        return {}, text, False
    return parse(fm), body, True


def dump_value(value) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(json.dumps(str(x), ensure_ascii=False) for x in value) + "]"
    s = str(value)
    if SIMPLE_RE.match(s):
        return s
    return json.dumps(s, ensure_ascii=False)


def set_field(text: str, key: str, value) -> str:
    """Set one frontmatter key, replacing its line (and block-list items) in place."""
    fm, body = split(text)
    new_line = f"{key}: {dump_value(value)}"
    if fm is None:
        return f"---\n{new_line}\n---\n{text}"
    lines = fm.splitlines()
    out, i, replaced = [], 0, False
    key_re = re.compile(rf"^{re.escape(key)}\s*:")
    while i < len(lines):
        line = lines[i]
        if not replaced and key_re.match(line):
            out.append(new_line)
            replaced = True
            i += 1
            while i < len(lines) and ITEM_RE.match(lines[i]) and not KEY_RE.match(lines[i]):
                i += 1
            continue
        out.append(line)
        i += 1
    if not replaced:
        out.append(new_line)
    return "---\n" + "\n".join(out) + "\n---\n" + body


def render(fields: dict, body: str) -> str:
    lines = [f"{k}: {dump_value(v)}" for k, v in fields.items()]
    return "---\n" + "\n".join(lines) + "\n---\n" + body
