"""Document metadata for mark: HTML headers and YAML front matter.

Mirrors the Go implementation's ``metadata`` package
(``mark-research/metadata/metadata.go``):

* Repeatable headers: ``Parent``, ``Folder``, ``Attachment``, ``Label``.
* Scalar headers: ``Space``, ``Type``, ``Title``, ``Layout``, ``Sidebar``,
  ``Emoji``, ``Order``, ``Image-Align``, ``Content-Appearance``,
  ``Synchronized``, ``Property`` (``key=value``).
* ``Include`` lines are recognised but left in the body for later expansion.
* Headers are read from the leading run of ``<!-- Key: value -->`` comments;
  ordinary comments (``TODO:`` ...) stay in the page, while a *nearly* valid
  header (edit distance <= 1, e.g. ``Titel:``) is an error.
* YAML front matter uses the plural key names (``parents``, ``attachments``,
  ``labels``, ``folders``, ``properties``); HTML headers override matching
  scalar front matter values and append to repeatable ones.

Deviations from the Go version (both documented here):

* Front matter is always parsed (upstream gates it on
  ``--features=frontmatter``).
* The YAML subset parser below covers what mark's keys need (scalars, block
  and flow lists, one level of mappings for ``properties``) so the package
  stays dependency-free. A document needing full YAML should use the header
  syntax instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


HEADER_PARENT = "Parent"
HEADER_FOLDER = "Folder"
HEADER_SPACE = "Space"
HEADER_TYPE = "Type"
HEADER_TITLE = "Title"
HEADER_LAYOUT = "Layout"
HEADER_SIDEBAR = "Sidebar"
HEADER_EMOJI = "Emoji"
HEADER_ATTACHMENT = "Attachment"
HEADER_LABEL = "Label"
HEADER_ORDER = "Order"
HEADER_INCLUDE = "Include"
HEADER_CONTENT_APPEARANCE = "Content-Appearance"
HEADER_IMAGE_ALIGN = "Image-Align"
HEADER_IMAGE_WIDTH = "Image-Width"
HEADER_IMAGE_HEIGHT = "Image-Height"
HEADER_PROPERTY = "Property"
HEADER_SYNCHRONIZED = "Synchronized"

KNOWN_HEADERS = (
    HEADER_ATTACHMENT,
    HEADER_CONTENT_APPEARANCE,
    HEADER_EMOJI,
    HEADER_FOLDER,
    HEADER_IMAGE_ALIGN,
    HEADER_IMAGE_WIDTH,
    HEADER_IMAGE_HEIGHT,
    HEADER_INCLUDE,
    HEADER_LABEL,
    HEADER_LAYOUT,
    HEADER_ORDER,
    HEADER_PARENT,
    HEADER_PROPERTY,
    HEADER_SIDEBAR,
    HEADER_SPACE,
    HEADER_SYNCHRONIZED,
    HEADER_TITLE,
    HEADER_TYPE,
)

FULL_WIDTH_CONTENT_APPEARANCE = "full-width"
FIXED_CONTENT_APPEARANCE = "fixed"
DEFAULT_CONTENT_APPEARANCE = "default"

_COMMENT_OPEN = "<!--"
_COMMENT_CLOSE = "-->"


class MetaError(ValueError):
    """A document's metadata is malformed (typo header, bad value, ...)."""


@dataclass
class Meta:
    parents: list[str] = field(default_factory=list)
    folders: list[str] = field(default_factory=list)
    space: str = ""
    type: str = "page"
    title: str = ""
    layout: str = ""
    sidebar: str = ""
    emoji: str = ""
    attachments: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    content_appearance: str = ""
    declared_parents: bool = False
    synchronized: bool | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    order: int | None = None
    image_align: str = ""
    image_width: str = ""
    image_height: str = ""


def canonical_header(key: str) -> str | None:
    """Return the known header ``key`` names (case-insensitive), or None."""
    key = key.strip()
    for known in KNOWN_HEADERS:
        if key.lower() == known.lower():
            return known
    return None


def _equal_rest(a: list[str], b: list[str]) -> bool:
    return a == b


def edit_distance_within_one(a: str, b: str) -> bool:
    """One insertion, deletion, substitution or transposition (ignore case)."""
    x = list(a.lower())
    y = list(b.lower())
    if abs(len(x) - len(y)) > 1:
        return False
    i = 0
    while i < len(x) and i < len(y) and x[i] == y[i]:
        i += 1
    if i == len(x) and i == len(y):
        return True
    if len(x) == len(y):
        if _equal_rest(x[i + 1:], y[i + 1:]):
            return True
        return (
            i + 1 < len(x)
            and x[i] == y[i + 1]
            and x[i + 1] == y[i]
            and _equal_rest(x[i + 2:], y[i + 2:])
        )
    if len(x) > len(y):
        return _equal_rest(x[i + 1:], y[i:])
    return _equal_rest(x[i:], y[i + 1:])


def nearest_header(key: str) -> str | None:
    """Header ``key`` was probably meant to be, if within one edit."""
    key = key.strip()
    if not key:
        return None
    for known in KNOWN_HEADERS:
        if edit_distance_within_one(key, known):
            return known
    return None


def parse_header_comment(line: str) -> tuple[str, str] | None:
    """Split a ``<!-- Key: value -->`` line, or return None."""
    line = line.strip()
    if not line.startswith(_COMMENT_OPEN) or not line.endswith(_COMMENT_CLOSE):
        return None
    if len(line) < len(_COMMENT_OPEN) + len(_COMMENT_CLOSE):
        return None
    content = line[len(_COMMENT_OPEN):-len(_COMMENT_CLOSE)].strip()
    key, sep, value = content.partition(":")
    if not sep:
        return None
    return key.strip(), value.strip()


def set_content_appearance(meta: Meta, value: str) -> None:
    value = value.strip()
    if value == FIXED_CONTENT_APPEARANCE:
        meta.content_appearance = FIXED_CONTENT_APPEARANCE
    elif value == DEFAULT_CONTENT_APPEARANCE:
        meta.content_appearance = DEFAULT_CONTENT_APPEARANCE
    else:
        meta.content_appearance = FULL_WIDTH_CONTENT_APPEARANCE


# ---------------------------------------------------------------------------
# Minimal YAML-subset parser (front matter only)
# ---------------------------------------------------------------------------

def _strip_comment(line: str) -> str:
    """Strip a trailing ``#`` comment, honouring single/double quotes."""
    quote: str | None = None
    out: list[str] = []
    i = 0
    while i < len(line):
        ch = line[i]
        if quote is None and ch == "#":
            break
        if ch in ("'", '"'):
            if quote is None:
                quote = ch
            elif quote == ch:
                if ch == "'" and i + 1 < len(line) and line[i + 1] == "'":
                    out.append("''")
                    i += 2
                    continue
                quote = None
        out.append(ch)
        i += 1
    return "".join(out)


def _parse_scalar(raw: str) -> Any:
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        inner = text[1:-1]
        if text[0] == "'":
            inner = inner.replace("''", "'")
        else:
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    lowered = text.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "~", ""):
        return None
    try:
        return int(text, 10)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part) for part in _split_flow(inner)]
    return text


def _split_flow(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    current: list[str] = []
    for ch in text:
        if quote is not None:
            current.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            current.append(ch)
        elif ch == "[":
            depth += 1
            current.append(ch)
        elif ch == "]":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse a flat YAML mapping with one level of block lists/mappings.

    Supports what mark's front matter keys need: ``key: scalar``,
    ``key: [a, b]``, ``key:`` followed by an indented ``- item`` list or an
    indented ``subkey: value`` mapping. Anything deeper raises MetaError.
    """
    result: dict[str, Any] = {}
    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        line = _strip_comment(raw).rstrip()
        i += 1
        if not line.strip():
            continue
        if line[:1] in (" ", "\t"):
            raise MetaError(f"unexpected indented line in front matter: {raw.strip()!r}")
        key, sep, rest = line.partition(":")
        if not sep:
            raise MetaError(f"expected 'key: value' in front matter, got {raw.strip()!r}")
        key = key.strip()
        if not key:
            raise MetaError(f"empty key in front matter: {raw.strip()!r}")
        rest = rest.strip()
        if rest:
            result[key] = _parse_scalar(rest)
            continue
        # Nested block: gather indented lines.
        children: list[str] = []
        while i < n and (lines[i][:1] in (" ", "\t") or not lines[i].strip()):
            if lines[i].strip():
                children.append(lines[i])
            i += 1
        if not children:
            result[key] = None
            continue
        indent = min(len(c) - len(c.lstrip(" \t")) for c in children)
        stripped = [c[indent:] for c in children]
        if stripped and all(s.lstrip().startswith("- ") or s.strip() == "-" for s in stripped):
            items: list[Any] = []
            for s in stripped:
                item = s.strip()[1:].strip()
                if item.startswith("-"):
                    raise MetaError("nested lists are not supported in front matter")
                items.append(_parse_scalar(_strip_comment(item)))
            result[key] = items
        else:
            mapping: dict[str, Any] = {}
            for s in stripped:
                if s[:1] in (" ", "\t") or s.lstrip().startswith("- "):
                    raise MetaError(
                        "only one nesting level is supported in front matter"
                    )
                sub_key, sub_sep, sub_rest = s.partition(":")
                if not sub_sep or not sub_key.strip():
                    raise MetaError(f"expected 'key: value' in front matter, got {s.strip()!r}")
                mapping[sub_key.strip()] = _parse_scalar(_strip_comment(sub_rest.strip()))
            result[key] = mapping
    return result


def _normalise_front_matter_key(key: str) -> str:
    return key.lower().replace("-", "").replace("_", "")


def _to_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.strip()
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, (int, float, bool)):
                out.append(str(item))
        return out
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    return []


def _to_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "on", "1"):
            return True
        if lowered in ("false", "no", "off", "0"):
            return False
    raise MetaError(f"synchronized must be true or false, got {value!r}")


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        raise MetaError(f"order must be a whole number, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != float(int(value)):
            raise MetaError(f"order must be a whole number, got {value!r}")
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip(), 10)
        except ValueError:
            pass
    raise MetaError(f"order must be a whole number, got {value!r}")


def split_front_matter(text: str) -> tuple[str | None, str]:
    """Split ``---`` YAML front matter off the front of ``text``.

    Returns ``(yaml_text_or_None, body)``. When the document does not open
    with a ``---`` line, or no closing ``---``/``...`` line follows, there is
    no front matter and the body is returned unchanged.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None, text
    for idx in range(1, len(lines)):
        if lines[idx].strip() in ("---", "..."):
            yaml_text = "".join(lines[1:idx])
            return yaml_text, "".join(lines[idx + 1:])
    return None, text


def _apply_front_matter(meta: Meta, parsed: dict[str, Any], filename: str) -> list[str]:
    warnings: list[str] = []
    unknown: list[str] = []
    for key, value in parsed.items():
        norm = _normalise_front_matter_key(key)
        if norm == "parents":
            meta.parents.extend(_to_string_list(value))
            meta.declared_parents = True
        elif norm == "folders":
            meta.folders.extend(_to_string_list(value))
        elif norm == "space":
            meta.space = _to_string(value)
        elif norm == "type":
            meta.type = _to_string(value) or "page"
        elif norm == "title":
            meta.title = _to_string(value)
        elif norm == "layout":
            meta.layout = _to_string(value)
        elif norm == "sidebar":
            meta.sidebar = _to_string(value)
        elif norm == "emoji":
            meta.emoji = _to_string(value)
        elif norm == "attachments":
            meta.attachments.extend(_to_string_list(value))
        elif norm == "labels":
            meta.labels.extend(_to_string_list(value))
        elif norm == "contentappearance":
            set_content_appearance(meta, _to_string(value))
        elif norm == "imagealign":
            meta.image_align = _to_string(value).lower()
        elif norm == "imagewidth":
            meta.image_width = _to_string(value)
        elif norm == "imageheight":
            meta.image_height = _to_string(value)
        elif norm == "order":
            meta.order = _to_int(value)
        elif norm == "synchronized":
            meta.synchronized = _to_bool(value)
        elif norm == "properties":
            if isinstance(value, dict):
                meta.properties.update(value)
            elif value is not None:
                raise MetaError(f"properties must be a mapping, got {value!r}")
        else:
            unknown.append(key)
    for key in sorted(unknown):
        where = f"{filename}: " if filename else ""
        warnings.append(
            f"{where}front matter key {key!r} is not read by mark and was ignored"
        )
    if meta.sidebar:
        meta.layout = "article"
    return warnings


# ---------------------------------------------------------------------------
# Leading H1 / filename titles
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")


def _code_fence_spans(lines: list[str]) -> set[int]:
    """Line indexes inside fenced code blocks (fences included)."""
    hidden: set[int] = set()
    fence: str | None = None
    for idx, line in enumerate(lines):
        match = _FENCE_RE.match(line)
        if match:
            marker = match.group(1)[0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            hidden.add(idx)
        elif fence is not None:
            hidden.add(idx)
    return hidden


_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
_INLINE_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_INLINE_EMPH_RE = re.compile(r"(\*\*|__)(.*?)\1")
_INLINE_EM_RE = re.compile(r"(\*|_)(.*?)\1")


def _strip_inline(text: str) -> str:
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _INLINE_LINK_RE.sub(r"\1", text)
    text = _INLINE_EMPH_RE.sub(r"\2", text)
    text = _INLINE_EM_RE.sub(r"\2", text)
    return text.strip()


def extract_leading_h1(markdown_text: str) -> str:
    """First H1's text (ATX ``#`` or Setext ``===``), outside fenced code."""
    lines = markdown_text.splitlines()
    hidden = _code_fence_spans(lines)
    for idx, line in enumerate(lines):
        if idx in hidden:
            continue
        stripped = line.strip()
        if stripped.startswith("# "):
            return _strip_inline(stripped[2:].strip().rstrip("#").strip())
        if stripped == "#":
            return ""
        if (
            stripped
            and idx + 1 < len(lines)
            and idx + 1 not in hidden
            and re.fullmatch(r"=+\s*", lines[idx + 1].strip() or "")
            and not stripped.startswith(("#", ">", "-", "*", "+", "|"))
        ):
            return _strip_inline(stripped)
    return ""


def title_from_name(name: str) -> str:
    """Turn a file or directory name into a page title."""
    return name.replace("_", " ").replace("-", " ").title()


def append_generated_hash(meta: Meta) -> None:
    """Append a short hash of parents/space/title to make a title unique."""
    import hashlib

    path = "/".join([*meta.parents, meta.space, meta.title])
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:8]
    meta.title = f"{meta.title} - {digest}"


def warn_stranded_headers(body: str) -> list[str]:
    """Headers left in the body (below other content) that will not apply."""
    warnings: list[str] = []
    lines = body.splitlines()
    hidden = _code_fence_spans(lines)
    for idx, line in enumerate(lines):
        if idx in hidden:
            continue
        parsed = parse_header_comment(line)
        if parsed is not None and canonical_header(parsed[0]) is not None:
            warnings.append(
                f"line {idx + 1}: header {parsed[0]!r} is below page content "
                "and was ignored (headers must lead the document)"
            )
    return warnings


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def parse_document(
    text: str,
    *,
    filename: str = "",
    title_from_h1: bool = False,
    title_from_filename: bool = False,
    space: str = "",
    parents: list[str] | tuple[str, ...] = (),
    content_appearance: str = "",
    title_append_hash: bool = False,
    front_matter: bool = True,
) -> tuple[Meta, str, list[str]]:
    """Extract metadata from a Markdown document.

    Returns ``(meta, body, warnings)`` where ``body`` is the document with
    its front matter and header run removed. ``meta`` is always a Meta
    (possibly empty); callers must still require a title and space.
    """
    warnings: list[str] = []
    meta = Meta()
    found_metadata = False
    body = text

    if front_matter:
        yaml_text, rest = split_front_matter(text)
        if yaml_text is not None:
            try:
                parsed = parse_simple_yaml(yaml_text)
            except MetaError as exc:
                raise MetaError(f"decode YAML front matter: {exc}") from exc
            warnings.extend(_apply_front_matter(meta, parsed, filename))
            body = rest
            found_metadata = True

    lines = body.splitlines(keepends=True)
    consumed: set[int] = set()
    started = False
    for idx, raw_line in enumerate(lines):
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            continue
        parsed = parse_header_comment(line)
        if parsed is None:
            # Not a comment line at all: a macro or raw HTML above the
            # headers is skipped over, anything once started ends the run.
            if started:
                break
            stripped = line.lstrip()
            if stripped.startswith("<"):
                continue
            break
        key, value = parsed
        header = canonical_header(key)
        if header is None:
            near = nearest_header(key)
            if near is not None:
                raise MetaError(
                    f"unknown header {key!r} in {line.strip()!r} "
                    f"-- did you mean {near!r}?"
                )
            # Ordinary comment: kept in the body either way.
            continue
        if header == HEADER_INCLUDE:
            # Includes are expanded from the body by a later pass.
            started = True
            continue
        consumed.add(idx)
        started = True
        found_metadata = True
        if header == HEADER_PARENT:
            meta.parents.append(value)
            meta.declared_parents = True
        elif header == HEADER_FOLDER:
            meta.folders.append(value)
        elif header == HEADER_SPACE:
            meta.space = value.strip()
        elif header == HEADER_TYPE:
            meta.type = value.strip() or "page"
        elif header == HEADER_TITLE:
            meta.title = value.strip()
        elif header == HEADER_LAYOUT:
            meta.layout = value.strip()
        elif header == HEADER_SIDEBAR:
            meta.layout = "article"
            meta.sidebar = value.strip()
        elif header == HEADER_EMOJI:
            meta.emoji = value.strip()
        elif header == HEADER_ORDER:
            try:
                meta.order = int(value.strip(), 10)
            except ValueError:
                raise MetaError(
                    f"{HEADER_ORDER} header must be a whole number, got {value!r}"
                ) from None
        elif header == HEADER_ATTACHMENT:
            meta.attachments.append(value)
        elif header == HEADER_LABEL:
            meta.labels.append(value)
        elif header == HEADER_CONTENT_APPEARANCE:
            set_content_appearance(meta, value)
        elif header == HEADER_IMAGE_ALIGN:
            meta.image_align = value.strip().lower()
        elif header == HEADER_IMAGE_WIDTH:
            meta.image_width = value.strip()
        elif header == HEADER_IMAGE_HEIGHT:
            meta.image_height = value.strip()
        elif header == HEADER_SYNCHRONIZED:
            lowered = value.strip().lower()
            if lowered in ("true", "yes", "on", "1"):
                meta.synchronized = True
            elif lowered in ("false", "no", "off", "0"):
                meta.synchronized = False
            else:
                raise MetaError(
                    f"{HEADER_SYNCHRONIZED} header must be true or false, "
                    f"got {value!r}"
                )
        elif header == HEADER_PROPERTY:
            prop_key, sep, prop_value = value.partition("=")
            if not sep:
                raise MetaError(
                    f"{HEADER_PROPERTY} header must be written as key=value, "
                    f"got {value!r}"
                )
            prop_key = prop_key.strip()
            if not prop_key:
                raise MetaError(
                    f"{HEADER_PROPERTY} header has no name: {value!r}"
                )
            meta.properties[prop_key] = prop_value.strip()
        else:  # pragma: no cover - canonicalHeader already vetted the name
            raise MetaError(f"header {header!r} is known but not handled")

    if consumed:
        first, last = min(consumed), max(consumed)
        kept: list[str] = []
        for idx, raw_line in enumerate(lines):
            if idx in consumed:
                continue
            if first < idx < last and not raw_line.strip():
                continue
            kept.append(raw_line)
        body = "".join(kept)

    if found_metadata:
        warnings.extend(warn_stranded_headers(body))

    if title_from_h1 and not meta.title:
        meta.title = extract_leading_h1(body)
    if title_from_filename and not meta.title and filename:
        import os

        base = os.path.basename(filename)
        stem = base[: -len(os.path.splitext(base)[1])] if os.path.splitext(base)[1] else base
        meta.title = title_from_name(stem)
    if space and not meta.space:
        meta.space = space
    if content_appearance and not meta.content_appearance:
        set_content_appearance(meta, content_appearance)
    if not meta.content_appearance:
        meta.content_appearance = FULL_WIDTH_CONTENT_APPEARANCE
    if not meta.type:
        meta.type = "page"

    cli_parents = [p for p in parents if p != ""]
    if cli_parents:
        meta.parents = [*cli_parents, *meta.parents]

    if title_append_hash and meta.title:
        append_generated_hash(meta)

    meta.title = meta.title.strip(" ")
    meta.space = meta.space.strip(" ")
    return meta, body, warnings
