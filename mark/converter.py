"""Markdown to Confluence storage-format converter.

Dependency-free renderer covering the Markdown subset mark needs: ATX and
Setext headings, paragraphs, fenced code blocks, GFM tables, nested ordered
and unordered lists, blockquotes, horizontal rules, inline code/emphasis/
links/images, plus raw-HTML passthrough (hand-written ``ac:`` macros,
``Layout`` markers and ``Include`` directives survive untouched).

Confluence mappings:

* Local images become ``<ac:image><ri:attachment .../></ac:image>`` and are
  reported as attachments to upload (``image_align`` sets ``ac:align``).
  ``image_max_width``/``image_max_height`` cap an SVG's rendered size:
  if the SVG's own width/height (or ``viewBox``) exceeds the cap, ``ac:width``/
  ``ac:height`` are set to a proportionally scaled-down size; images already
  within the box, or whose size can't be read, are left with no size
  attributes (natural size, never upscaled or distorted).
* Fenced code blocks become ``code`` structured macros with the info string
  as language.
* A link to another local ``.md`` file listed in ``page_links`` becomes
  ``<ac:link><ri:page ri:content-title="..." /></ac:link>``. Otherwise, with
  ``attach_referenced=True``, links to local files become attachment links
  and are reported as attachments too.

Out of scope for this core module (later passes): cross-space page links
(``page_links`` only resolves within the current space), mermaid/d2/math
rendering, diagram macro expansion, mentions.
"""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit


@dataclass
class RenderResult:
    storage: str
    attachments: list[str] = field(default_factory=list)


_FENCE_RE = re.compile(r"^(\s*)(```+|~~~+)\s*([^\s`]*).*$")
_ATX_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_HR_RE = re.compile(r"^\s{0,3}((\*\s*){3,}|(-\s*){3,}|(_\s*){3,})\s*$")
_LIST_RE = re.compile(r"^(\s*)([*+-]|\d+[.)])\s+(.*)$")
_TASK_RE = re.compile(r"^\[([ xX])\]\s+(.*)$")
_TABLE_DELIM_RE = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_TAG_START_RE = re.compile(r"^</?[a-zA-Z!/?]")
_ENTITY_RE = re.compile(r"&(#\d+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]+);")
_ALIGN_VALUES = ("left", "center", "right")


def _escape_text(text: str) -> str:
    """Escape HTML, leaving already-formed entities alone."""
    parts: list[str] = []
    last = 0
    for match in _ENTITY_RE.finditer(text):
        parts.append(html.escape(text[last:match.start()], quote=False))
        parts.append(match.group(0))
        last = match.end()
    parts.append(html.escape(text[last:], quote=False))
    return "".join(parts)


def _escape_attr(text: str) -> str:
    return html.escape(text, quote=True)


def _cdata(text: str) -> str:
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _is_local_ref(ref: str) -> bool:
    ref = ref.strip()
    if not ref or ref.startswith("#"):
        return False
    if re.match(r"^[a-zA-Z]:[\\/]", ref) or os.path.isabs(ref):
        return True
    return _SCHEME_RE.match(ref) is None


def _attachment_filename(ref: str) -> str:
    path = urlsplit(ref).path
    return unquote(path).replace("\\", "/").rsplit("/", 1)[-1]


_SVG_TAG_RE = re.compile(r"<svg\b[^>]*>", re.IGNORECASE | re.DOTALL)
_SVG_DIM_RE = re.compile(r'\b(width|height)\s*=\s*"([^"]*)"', re.IGNORECASE)
_SVG_DIM_VALUE_RE = re.compile(r"^([\d.]+)\s*(?:px)?$", re.IGNORECASE)
_SVG_VIEWBOX_RE = re.compile(
    r'viewBox\s*=\s*"\s*[-\d.]+\s+[-\d.]+\s+([\d.]+)\s+([\d.]+)\s*"', re.IGNORECASE
)


def _svg_intrinsic_size(path: str) -> tuple[float, float] | None:
    """Return an SVG's own (width, height) in px, or None if unavailable.

    Reads explicit ``width``/``height`` attributes (ignoring percentages,
    which aren't a fixed pixel size) and falls back to ``viewBox``.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return None
    tag_match = _SVG_TAG_RE.search(text)
    tag = tag_match.group(0) if tag_match else text
    dims: dict[str, float] = {}
    for attr, value in _SVG_DIM_RE.findall(tag):
        value_match = _SVG_DIM_VALUE_RE.match(value.strip())
        if value_match:
            dims[attr.lower()] = float(value_match.group(1))
    if "width" in dims and "height" in dims:
        return dims["width"], dims["height"]
    viewbox_match = _SVG_VIEWBOX_RE.search(tag)
    if viewbox_match:
        return float(viewbox_match.group(1)), float(viewbox_match.group(2))
    return None


class _InlineRenderer:
    def __init__(
        self,
        *,
        image_align: str = "",
        image_max_width: str = "",
        image_max_height: str = "",
        image_base_dir: str = "",
        page_links: dict[str, str] | None = None,
        attach_referenced: bool = False,
        attachments: list[str] | None = None,
    ) -> None:
        self.image_align = image_align if image_align in _ALIGN_VALUES else ""
        self.image_max_width = float(image_max_width) if image_max_width else 0.0
        self.image_max_height = float(image_max_height) if image_max_height else 0.0
        self.image_base_dir = image_base_dir
        self.page_links = page_links or {}
        self.attach_referenced = attach_referenced
        self.attachments: list[str] = attachments if attachments is not None else []
        self._spans: list[str] = []
        self._escapes: list[str] = []

    def _remember_attachment(self, ref: str) -> None:
        ref = ref.strip()
        if ref and ref not in self.attachments:
            self.attachments.append(ref)

    def _fitted_size(self, src: str) -> tuple[int, int] | None:
        """Scale an SVG's intrinsic size down to fit the max box, if needed.

        Preserves aspect ratio and never upscales. Returns None if there is
        no max box configured, the ref isn't an SVG, or its size can't be
        determined (callers should render without size attributes then).
        """
        if not (self.image_max_width or self.image_max_height):
            return None
        if not src.lower().endswith(".svg"):
            return None
        path = os.path.normpath(os.path.join(self.image_base_dir, src))
        size = _svg_intrinsic_size(path)
        if size is None:
            return None
        width, height = size
        if width <= 0 or height <= 0:
            return None
        scale = 1.0
        if self.image_max_width and width > self.image_max_width:
            scale = min(scale, self.image_max_width / width)
        if self.image_max_height and height > self.image_max_height:
            scale = min(scale, self.image_max_height / height)
        if scale >= 1.0:
            return None
        return round(width * scale), round(height * scale)

    def _resolve_page_link(self, href: str) -> str | None:
        """Look up a relative link to another synced ``.md`` file by title.

        Returns None (falls back to a plain ``<a>``) for anything not in
        ``page_links``: remote URLs, non-``.md`` local refs, or ``.md``
        files outside this sync batch. A ``#fragment``/``?query`` suffix is
        dropped; Confluence page links have no equivalent.
        """
        if not self.page_links:
            return None
        path_only = href.split("#", 1)[0].split("?", 1)[0]
        if not path_only.lower().endswith(".md"):
            return None
        path = os.path.normpath(os.path.join(self.image_base_dir, path_only))
        return self.page_links.get(path)

    # -- protected spans --------------------------------------------------
    # Generated HTML (code spans, images, links) is stashed behind \x00N\x00
    # placeholders so the later emphasis pass neither escapes nor parses it.
    def _stash_html(self, html_text: str) -> str:
        self._spans.append(html_text)
        return f"\x00{len(self._spans) - 1}\x00"

    def _stash_code(self, text: str) -> str:
        def _repl(match: re.Match[str]) -> str:
            code = match.group(1).replace("\n", " ")
            return self._stash_html(f"<code>{_escape_text(code.strip())}</code>")

        return re.sub(r"`+([^`]*?)`+", _repl, text)

    def _stash_escapes(self, text: str) -> str:
        def _repl(match: re.Match[str]) -> str:
            self._escapes.append(match.group(1))
            return f"\x01{len(self._escapes) - 1}\x01"

        return re.sub(r"\\([\\`*_{}\[\]()#+\-.!|~>])", _repl, text)

    def _restore(self, text: str) -> str:
        def _repl(match: re.Match[str]) -> str:
            return self._spans[int(match.group(1))]

        # To a fixpoint: link HTML may itself hold code-span placeholders.
        for _ in range(10):
            if "\x00" not in text:
                break
            text = re.sub(r"\x00(\d+)\x00", _repl, text)
        return text

    def _restore_escapes(self, text: str) -> str:
        def _repl(match: re.Match[str]) -> str:
            return self._escapes[int(match.group(1))]

        return re.sub(r"\x01(\d+)\x01", _repl, text)

    # -- entry point ------------------------------------------------------
    def render(self, text: str) -> str:
        text = self._stash_escapes(text)
        text = self._stash_code(text)
        text = self._render_images(text)
        text = self._render_links(text)
        text = self._render_autolinks(text)
        text = self._render_emphasis(text)
        text = self._restore(text)
        return self._restore_escapes(text)

    # -- images / links ---------------------------------------------------
    _IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(\s*(\S+?)(?:\s+"[^"]*")?\s*\)')
    _LINK_RE = re.compile(r'\[([^\]]*)\]\(\s*(\S+?)(?:\s+"[^"]*")?\s*\)')
    _AUTOLINK_RE = re.compile(r"<((?:https?|ftp):[^<>\s]+|mailto:[^<>\s]+)>")

    def _render_images(self, text: str) -> str:
        def _repl(match: re.Match[str]) -> str:
            alt, src = match.group(1), match.group(2).strip("<>")
            if _is_local_ref(src):
                self._remember_attachment(src)
                align = (
                    f' ac:align="{self.image_align}"' if self.image_align else ""
                )
                fitted = self._fitted_size(src)
                width = f' ac:width="{fitted[0]}"' if fitted else ""
                height = f' ac:height="{fitted[1]}"' if fitted else ""
                filename = _escape_attr(_attachment_filename(src))
                out = (
                    f'<ac:image{align}{width}{height}>'
                    f'<ri:attachment ri:filename="{filename}" />'
                )
                if alt.strip():
                    out += f"<ac:caption>{_escape_text(alt.strip())}</ac:caption>"
                return self._stash_html(out + "</ac:image>")
            return self._stash_html(
                f'<img src="{_escape_attr(src)}" alt="{_escape_attr(alt)}" />'
            )

        return self._IMAGE_RE.sub(_repl, text)

    def _render_links(self, text: str) -> str:
        def _repl(match: re.Match[str]) -> str:
            label, href = match.group(1), match.group(2).strip("<>")
            if _is_local_ref(href):
                title = self._resolve_page_link(href)
                if title is not None:
                    body = label.strip() or title
                    return self._stash_html(
                        "<ac:link>"
                        f'<ri:page ri:content-title="{_escape_attr(title)}" />'
                        f"<ac:plain-text-link-body>{_cdata(body)}</ac:plain-text-link-body>"
                        "</ac:link>"
                    )
                if self.attach_referenced:
                    self._remember_attachment(href)
                    filename = _escape_attr(_attachment_filename(href))
                    return self._stash_html(
                        "<ac:link>"
                        f'<ri:attachment ri:filename="{filename}" />'
                        f"<ac:plain-text-link-body>{_cdata(label)}</ac:plain-text-link-body>"
                        "</ac:link>"
                    )
            inner = self._render_emphasis(label)
            return self._stash_html(f'<a href="{_escape_attr(href)}">{inner}</a>')

        return self._LINK_RE.sub(_repl, text)

    def _render_autolinks(self, text: str) -> str:
        def _repl(match: re.Match[str]) -> str:
            href = match.group(1)
            return self._stash_html(
                f'<a href="{_escape_attr(href)}">{_escape_text(href)}</a>'
            )

        return self._AUTOLINK_RE.sub(_repl, text)

    # -- emphasis -----------------------------------------------------------
    def _render_emphasis(self, text: str) -> str:
        # Escape first so emphasis markers added below are not re-processed.
        text = _escape_text(text)
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"__(.+?)__", r"<strong>\1</strong>", text)
        text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
        text = re.sub(r"\b_(.+?)_\b", r"<em>\1</em>", text)
        text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
        return text


class _BlockRenderer:
    def __init__(
        self,
        inline: _InlineRenderer,
        *,
        drop_h1: bool = False,
    ) -> None:
        self.inline = inline
        self.drop_h1 = drop_h1
        self._dropped_h1 = False

    def render_blocks(self, lines: list[str]) -> list[str]:
        blocks: list[str] = []
        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            fence = _FENCE_RE.match(line)
            if fence:
                html_block, i = self._fenced_code(lines, i)
                blocks.append(html_block)
                continue
            atx = _ATX_RE.match(line.lstrip())
            if atx and line.startswith(("#", " ", "\t")) and line.lstrip().startswith("#"):
                level = len(atx.group(1))
                if self.drop_h1 and level == 1 and not self._dropped_h1:
                    self._dropped_h1 = True
                else:
                    blocks.append(f"<h{level}>{self.inline.render(atx.group(2))}</h{level}>")
                i += 1
                continue
            if _HR_RE.match(line):
                # Setext H1/H2 takes precedence over a "-" rule when the
                # previous emitted content allows; handled paragraph-side.
                blocks.append("<hr />")
                i += 1
                continue
            if _TAG_START_RE.match(line.lstrip()):
                html_block, i = self._raw_html(lines, i)
                blocks.append(html_block)
                continue
            if line.lstrip().startswith(">"):
                html_block, i = self._blockquote(lines, i)
                blocks.append(html_block)
                continue
            if _LIST_RE.match(line):
                html_block, i = self._list(lines, i)
                blocks.append(html_block)
                continue
            if "|" in line and i + 1 < n and _TABLE_DELIM_RE.match(lines[i + 1]):
                html_block, i = self._table(lines, i)
                blocks.append(html_block)
                continue
            html_block, i = self._paragraph(lines, i)
            if html_block is not None:
                blocks.append(html_block)
        return blocks

    # -- blocks -------------------------------------------------------------
    def _fenced_code(self, lines: list[str], start: int) -> tuple[str, int]:
        opener = _FENCE_RE.match(lines[start])
        assert opener is not None
        fence_char = opener.group(2)[0]
        fence_len = len(opener.group(2))
        language = (opener.group(3) or "").strip()
        collected: list[str] = []
        i = start + 1
        while i < len(lines):
            closer = re.match(r"^\s*(```+|~~~+)\s*$", lines[i])
            if closer and closer.group(1)[0] == fence_char and len(closer.group(1)) >= fence_len:
                i += 1
                break
            collected.append(lines[i].rstrip("\n"))
            i += 1
        code = "\n".join(collected)
        macro = (
            '<ac:structured-macro ac:name="code">'
            f'<ac:parameter ac:name="language">{_escape_text(language)}</ac:parameter>'
            f"<ac:plain-text-body>{_cdata(code)}</ac:plain-text-body>"
            "</ac:structured-macro>"
        )
        return macro, i

    def _raw_html(self, lines: list[str], start: int) -> tuple[str, int]:
        collected: list[str] = []
        i = start
        while i < len(lines) and lines[i].strip():
            collected.append(lines[i].rstrip("\n"))
            i += 1
        return "\n".join(collected), i

    def _blockquote(self, lines: list[str], start: int) -> tuple[str, int]:
        inner: list[str] = []
        i = start
        while i < len(lines):
            stripped = lines[i].lstrip()
            if not stripped.startswith(">"):
                if not lines[i].strip() and i + 1 < len(lines) and lines[i + 1].lstrip().startswith(">"):
                    inner.append("")
                    i += 1
                    continue
                break
            inner.append(re.sub(r"^>\s?", "", stripped).rstrip("\n"))
            i += 1
        rendered = "".join(self.render_blocks(inner))
        return f"<blockquote>{rendered}</blockquote>", i

    def _list(self, lines: list[str], start: int) -> tuple[str, int]:
        items: list[tuple[int, bool, str, list[str]]] = []
        i = start
        while i < len(lines):
            match = _LIST_RE.match(lines[i])
            if not match:
                if not lines[i].strip():
                    # Blank ends the list unless another item follows.
                    j = i + 1
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    if j < len(lines) and _LIST_RE.match(lines[j]):
                        i = j
                        continue
                    break
                # Continuation line: append to the previous item.
                if items and (lines[i].startswith((" ", "\t")) or not lines[i].strip().startswith("#")):
                    indent, ordered, text, extra = items[-1]
                    extra.append(lines[i].strip())
                    i += 1
                    continue
                break
            indent = len(match.group(1).replace("\t", "    "))
            ordered = match.group(2)[0].isdigit()
            text = match.group(3)
            items.append((indent, ordered, text, []))
            i += 1

        # Group into nested lists by indent.
        def build(pos: int, level: int) -> tuple[str, int]:
            parts: list[str] = []
            tag: str | None = None
            while pos < len(items):
                indent, ordered, text, extra = items[pos]
                if indent > level:
                    sub, pos = build(pos, indent)
                    if parts:
                        parts[-1] = parts[-1][: -len("</li>")] + sub + "</li>"
                    else:
                        parts.append(sub)
                    continue
                if indent < level:
                    break
                want = "ol" if ordered else "ul"
                if tag is None:
                    tag = want
                    parts.append(f"<{tag}>")
                elif want != tag:
                    parts.append(f"</{tag}>")
                    tag = want
                    parts.append(f"<{tag}>")
                body = " ".join([text, *extra]).strip()
                task = _TASK_RE.match(body)
                if task:
                    mark = "complete" if task.group(1).lower() == "x" else "incomplete"
                    body_html = self.inline.render(task.group(2))
                    parts.append(
                        "<li>"
                        "<ac:task-list><ac:task>"
                        f"<ac:task-status>{mark}</ac:task-status>"
                        f"<ac:task-body>{body_html}</ac:task-body>"
                        "</ac:task></ac:task-list>"
                        "</li>"
                    )
                else:
                    parts.append(f"<li>{self.inline.render(body)}</li>")
                pos += 1
            if tag is not None:
                parts.append(f"</{tag}>")
            return "".join(parts), pos

        html, _ = build(0, items[0][0])
        return html, i

    def _split_row(self, line: str) -> list[str]:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        return cells

    def _table(self, lines: list[str], start: int) -> tuple[str, int]:
        header = self._split_row(lines[start])
        i = start + 2
        rows: list[list[str]] = []
        while i < len(lines) and "|" in lines[i] and lines[i].strip():
            rows.append(self._split_row(lines[i]))
            i += 1
        width = len(header)
        head = "".join(
            f"<th>{self.inline.render(cell)}</th>" for cell in header
        )
        body_rows = []
        for row in rows:
            row = (row + [""] * width)[:width]
            body_rows.append(
                "<tr>" + "".join(f"<td>{self.inline.render(cell)}</td>" for cell in row) + "</tr>"
            )
        return (
            f"<table><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody></table>"
        ), i

    def _paragraph(self, lines: list[str], start: int) -> tuple[str | None, int]:
        collected: list[str] = []
        i = start
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                i += 1
                break
            if (
                _FENCE_RE.match(line)
                or _LIST_RE.match(line)
                or line.lstrip().startswith(">")
                or _TAG_START_RE.match(line.lstrip())
                or (line.lstrip().startswith("#") and _ATX_RE.match(line.lstrip()))
            ):
                break
            if "|" in line and i + 1 < len(lines) and _TABLE_DELIM_RE.match(lines[i + 1]):
                break
            collected.append(line.strip())
            # Setext underline?
            if i + 1 < len(lines) and re.fullmatch(r"(=+|-+)\s*", lines[i + 1].strip() or ""):
                underline = lines[i + 1].strip()
                level = 1 if underline.startswith("=") else 2
                text = self.inline.render(" ".join(collected))
                i += 2
                if self.drop_h1 and level == 1 and not self._dropped_h1:
                    self._dropped_h1 = True
                    return None, i
                return f"<h{level}>{text}</h{level}>", i
            i += 1
        if not collected:
            return None, i
        text = self.inline.render(" ".join(collected))
        return f"<p>{text}</p>", i


def render(
    markdown_text: str,
    *,
    drop_h1: bool = False,
    strip_linebreaks: bool = False,  # accepted for CLI parity; see below
    image_align: str = "",
    image_max_width: str = "",
    image_max_height: str = "",
    image_base_dir: str = "",
    page_links: dict[str, str] | None = None,
    attach_referenced: bool = False,
) -> RenderResult:
    """Render Markdown to Confluence storage format.

    ``strip_linebreaks`` needs no action: this renderer never emits raw
    newlines inside block elements (fenced code bodies excepted, where they
    are content), which is exactly the state that flag asks for.

    ``page_links`` maps a normalized absolute path of another local ``.md``
    file (typically every file in the same sync batch) to that file's
    Confluence page title. A markdown link to one of those paths becomes a
    proper intra-space Confluence page link instead of a dead ``<a href>``
    pointing at a relative filename.
    """
    _ = strip_linebreaks
    attachments: list[str] = []
    inline = _InlineRenderer(
        image_align=image_align,
        image_max_width=image_max_width,
        image_max_height=image_max_height,
        image_base_dir=image_base_dir,
        page_links=page_links,
        attach_referenced=attach_referenced,
        attachments=attachments,
    )
    blocks = _BlockRenderer(inline, drop_h1=drop_h1)
    storage = "".join(blocks.render_blocks(markdown_text.splitlines()))
    return RenderResult(storage=storage, attachments=attachments)
