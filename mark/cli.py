"""Command-line interface for mark (Python clone)."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys

from mark import __version__
from mark.config import (
    DEFAULT_FEATURES,
    KNOWN_FEATURES,
    Config,
    ConfigError,
    load_config,
    parse_target_url,
    validate_config,
)
from mark.confluence import ConfluenceClient, ConfluenceError
from mark.converter import render
from mark.metadata import MetaError, parse_document, title_from_name

_LOG_LEVELS = {
    "trace": 0,
    "debug": 10,
    "info": 20,
    "warning": 30,
    "error": 40,
    "fatal": 50,
}
_LOG_LEVEL = _LOG_LEVELS["info"]


def set_log_level(name: str) -> None:
    global _LOG_LEVEL
    _LOG_LEVEL = _LOG_LEVELS.get(name.strip().lower(), _LOG_LEVELS["info"])


def _debug(message: str) -> None:
    if _LOG_LEVEL <= _LOG_LEVELS["debug"]:
        print(f"DEBUG {message}", file=sys.stderr)


def _info(message: str) -> None:
    if _LOG_LEVEL <= _LOG_LEVELS["info"]:
        print(message)


def _warn(message: str) -> None:
    if _LOG_LEVEL <= _LOG_LEVELS["warning"]:
        print(f"WARNING {message}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mark",
        description="Sync Markdown files to Confluence (Python clone).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    sub = parser.add_subparsers(dest="command")

    sync = sub.add_parser("sync", help="Sync Markdown files to Confluence.")
    sync.add_argument("--config", "-c", default="", help="Config file (TOML). [$MARK_CONFIG]")
    sync.add_argument(
        "--files", "-f", nargs="+", default=[],
        help="Markdown files to sync (glob patterns allowed, quote them). [$MARK_FILES]",
    )
    sync.add_argument("--space", default="", help="Confluence space key. [$MARK_SPACE]")
    sync.add_argument("--base-url", "-b", default="", help="Confluence base URL. [$MARK_BASE_URL]")
    sync.add_argument(
        "--target-url", "-l", default="",
        help="Edit the Confluence page at this URL (pageId query param); "
        "file metadata is ignored. [$MARK_TARGET_URL]",
    )
    sync.add_argument("--username", "-u", default="", help="Confluence username. [$MARK_USERNAME]")
    sync.add_argument(
        "--password", "-p", default="",
        help="API token/password ('-' reads stdin). [$MARK_PASSWORD]",
    )
    sync.add_argument(
        "--password-command", default="",
        help="Command printing the token on its first stdout line. [$MARK_PASSWORD_COMMAND]",
    )
    sync.add_argument(
        "--parents", default="",
        help="Parent pages prepended to each document's own, delimiter-separated. [$MARK_PARENTS]",
    )
    sync.add_argument(
        "--parents-delimiter", default="",
        help="Delimiter for --parents (default '/'). [$MARK_PARENTS_DELIMITER]",
    )
    sync.add_argument(
        "--parents-from-path", action="store_true",
        help="Place each page under pages named after its directories. [$MARK_PARENTS_FROM_PATH]",
    )
    sync.add_argument(
        "--parents-from-path-root", default="",
        help="Directory --parents-from-path measures from (default: glob root). "
        "[$MARK_PARENTS_FROM_PATH_ROOT]",
    )
    sync.add_argument(
        "--title-from-h1", action="store_true",
        help="Take the page title from the leading H1 heading. [$MARK_TITLE_FROM_H1]",
    )
    sync.add_argument(
        "--title-from-filename", action="store_true",
        help="Take the page title from the file name. [$MARK_TITLE_FROM_FILENAME]",
    )
    sync.add_argument(
        "--title-append-generated-hash", action="store_true",
        help="Append a short parents/space/title hash to each title. "
        "[$MARK_TITLE_APPEND_GENERATED_HASH]",
    )
    sync.add_argument(
        "--drop-h1", action="store_true",
        help="Do not publish the first H1 heading. [$MARK_DROP_H1]",
    )
    sync.add_argument(
        "--strip-linebreaks", "-L", action="store_true",
        help="Accepted for compatibility; the renderer never emits raw linebreaks. "
        "[$MARK_STRIP_LINEBREAKS]",
    )
    sync.add_argument(
        "--compile-only", action="store_true",
        help="Print resulting storage HTML without touching Confluence. [$MARK_COMPILE_ONLY]",
    )
    sync.add_argument(
        "--dry-run", action="store_true",
        help="Resolve pages/ancestry and print storage HTML without writing. [$MARK_DRY_RUN]",
    )
    sync.add_argument(
        "--minor-edit", action="store_true",
        help="Do not send notifications on update. [$MARK_MINOR_EDIT]",
    )
    sync.add_argument(
        "--version-message", default="",
        help="Page version message. [$MARK_VERSION_MESSAGE]",
    )
    sync.add_argument(
        "--changes-only", action="store_true",
        help="Skip pages whose content hash already matches (hash is stored in "
        "the version message). [$MARK_CHANGES_ONLY]",
    )
    sync.add_argument(
        "--edit-lock", "-k", action="store_true",
        help="(Accepted, not implemented: page edit locking.) [$MARK_EDIT_LOCK]",
    )
    sync.add_argument(
        "--ci", action="store_true",
        help="Do not fail when file patterns match nothing. [$MARK_CI]",
    )
    sync.add_argument(
        "--continue-on-error", action="store_true",
        help="Keep processing remaining files after a failure. [$MARK_CONTINUE_ON_ERROR]",
    )
    sync.add_argument(
        "--insecure-skip-tls-verify", action="store_true",
        help="Skip TLS certificate verification. [$MARK_INSECURE_SKIP_TLS_VERIFY]",
    )
    sync.add_argument(
        "--image-align", default="",
        help="Image alignment: left, center or right (header wins per file). [$MARK_IMAGE_ALIGN]",
    )
    sync.add_argument(
        "--content-appearance", default="",
        help="Default content appearance: full-width, fixed or default. "
        "[$MARK_CONTENT_APPEARANCE]",
    )
    sync.add_argument(
        "--append-labels", action="store_true",
        help="Add labels without removing existing ones. [$MARK_APPEND_LABELS]",
    )
    sync.add_argument(
        "--attach-referenced", action="store_true",
        help="Upload local files that links point at as attachments. [$MARK_ATTACH_REFERENCED]",
    )
    sync.add_argument(
        "--include-path", default="",
        help="(Accepted, not implemented: fallback directory for Include.) "
        "[$MARK_INCLUDE_PATH]",
    )
    sync.add_argument(
        "--log-level", default="",
        help="Log level: TRACE, DEBUG, INFO, WARNING, ERROR, FATAL. [$MARK_LOG_LEVEL]",
    )
    sync.add_argument(
        "--color", default="",
        help="Accepted for compatibility (auto, never); output is plain text. [$MARK_COLOR]",
    )
    sync.add_argument(
        "--output-format", default="",
        help="Run report: url (default), json, or github. [$MARK_OUTPUT_FORMAT]",
    )
    sync.add_argument(
        "--check-links", action="append", default=[],
        help="(Accepted, not implemented: fail on unresolvable links. "
        "Any of internal, confluence, external, all; repeatable.) [$MARK_CHECK_LINKS]",
    )
    sync.add_argument(
        "--check-links-warn-only", action="store_true",
        help="(Accepted, not implemented: report bad links without failing.) "
        "[$MARK_CHECK_LINKS_WARN_ONLY]",
    )
    sync.add_argument(
        "--global-properties", default="",
        help="(Accepted, not implemented: YAML/JSON content properties file.) "
        "[$MARK_GLOBAL_PROPERTIES]",
    )
    sync.add_argument(
        "--no-overwrite", action="store_true",
        help="(Accepted, not implemented: needs --track-pages.) [$MARK_NO_OVERWRITE]",
    )
    sync.add_argument(
        "--track-pages", action="store_true",
        help="(Accepted, not implemented: page manifest tracking.) [$MARK_TRACK_PAGES]",
    )
    sync.add_argument(
        "--manifest-page", default="",
        help="(Accepted, not implemented: manifest storage page.) [$MARK_MANIFEST_PAGE]",
    )
    sync.add_argument(
        "--manifest-prefix", default="",
        help="(Accepted, not implemented: manifest property prefix.) [$MARK_MANIFEST_PREFIX]",
    )
    sync.add_argument(
        "--on-orphan", default="",
        help="(Accepted, not implemented: report, archive, or delete.) [$MARK_ON_ORPHAN]",
    )
    sync.add_argument(
        "--orphan-under", default="",
        help="(Accepted, not implemented: limit orphan scope.) [$MARK_ORPHAN_UNDER]",
    )
    sync.add_argument(
        "--preserve-comments", action="store_true",
        help="(Accepted, not implemented: keep inline comments.) [$MARK_PRESERVE_COMMENTS]",
    )
    sync.add_argument(
        "--mermaid-scale", type=float, default=None,
        help="(Accepted, not implemented: mermaid render scale.) [$MARK_MERMAID_SCALE]",
    )
    sync.add_argument(
        "--mermaid-engine", default="",
        help="(Accepted, not implemented: chrome or merman.) [$MARK_MERMAID_ENGINE]",
    )
    sync.add_argument(
        "--mermaid-output", default="",
        help="(Accepted, not implemented: png or svg.) [$MARK_MERMAID_OUTPUT]",
    )
    sync.add_argument(
        "--mermaid-bundle", action="store_true",
        help="(Accepted, not implemented: embed diagram source in SVG.) [$MARK_MERMAID_BUNDLE]",
    )
    sync.add_argument(
        "--math-format", default="",
        help="(Accepted, not implemented: png or svg.) [$MARK_MATH_FORMAT]",
    )
    sync.add_argument(
        "--math-scale", type=float, default=None,
        help="(Accepted, not implemented: math render scale.) [$MARK_MATH_SCALE]",
    )
    sync.add_argument(
        "--d2-output", default="",
        help="(Accepted, not implemented: png or svg.) [$MARK_D2_OUTPUT]",
    )
    sync.add_argument(
        "--d2-bundle-remote", action="store_true",
        help="(Accepted, not implemented: inline remote refs in SVG.) [$MARK_D2_BUNDLE_REMOTE]",
    )
    sync.add_argument(
        "--d2-scale", type=float, default=None,
        help="(Accepted, not implemented: d2 render scale.) [$MARK_D2_SCALE]",
    )
    sync.add_argument(
        "--features", action="append", default=[],
        help="(Accepted, not implemented: optional render features; "
        f"known: {', '.join(KNOWN_FEATURES)}; defaults: "
        f"{', '.join(DEFAULT_FEATURES)}.) [$MARK_FEATURES]",
    )
    return parser


def resolve_files(patterns: list[str], *, ci: bool) -> list[str]:
    matched: list[str] = []
    for pattern in patterns:
        hits = sorted(glob.glob(pattern, recursive=True))
        files = [h for h in hits if os.path.isfile(h)]
        if not files and not ci:
            raise ConfigError(f"no files match pattern {pattern!r}")
        matched.extend(files)
    return list(dict.fromkeys(matched))


def glob_root(pattern: str) -> str:
    """Everything before the first wildcard segment (upstream page.GlobRoot)."""
    if not pattern:
        return ""
    segments = pattern.replace("\\", "/").split("/")
    fixed: list[str] = []
    for segment in segments:
        if any(ch in segment for ch in "*?[{"):
            break
        fixed.append(segment)
    if len(fixed) == len(segments) and fixed:
        fixed = fixed[:-1]
    return "/".join(fixed)


def run_root(patterns: list[str], override: str) -> str:
    if override:
        return override
    for pattern in patterns:
        root = glob_root(pattern)
        if root:
            return root
    return ""


_INDEX_STEMS = {"index", "readme"}


def parents_from_path(file: str, root: str) -> tuple[list[str], bool]:
    """Directory parents for ``file`` measured from ``root``.

    Returns ``(parents, is_index)``; an index/README file stands for its own
    directory rather than sitting inside it.
    """
    norm_file = os.path.normpath(file)
    norm_root = os.path.normpath(root) if root else ""
    directory = os.path.dirname(os.path.abspath(norm_file))
    if norm_root:
        abs_root = os.path.abspath(norm_root)
        try:
            relative = os.path.relpath(directory, abs_root)
        except ValueError:
            relative = ""
        if relative in (".", ""):
            parts: list[str] = []
        elif relative.startswith(".."):
            parts = []
        else:
            parts = [p for p in relative.split(os.sep) if p not in (".", "")]
    else:
        parts = [p for p in directory.split(os.sep) if p]
        parts = parts[-2:] if len(parts) > 2 else parts
    stem = os.path.splitext(os.path.basename(norm_file))[0].lower()
    is_index = stem in _INDEX_STEMS
    if is_index and parts:
        parts = parts[:-1]
    return [title_from_name(p) for p in parts], is_index


def apply_layout(storage: str, layout: str, sidebar: str) -> str:
    """Wrap ``storage`` in the article layout (upstream ac:layout template)."""
    if layout != "article":
        return storage
    return (
        "<ac:layout>"
        '<ac:layout-section ac:type="two_right_sidebar">'
        f"<ac:layout-cell>{storage}</ac:layout-cell>"
        f"<ac:layout-cell>{sidebar}</ac:layout-cell>"
        "</ac:layout-section>"
        "</ac:layout>"
    )


_HASH_LEADING = re.compile(r"^\[v([a-f0-9]{40})\]")
_HASH_TRAILING = re.compile(r"\[v([a-f0-9]{40})\]$")


def content_hash(storage: str) -> str:
    return hashlib.sha1(storage.encode("utf-8")).hexdigest()


def format_version_message(message: str, digest: str) -> str:
    tag = f"[v{digest}]"
    return tag if not message else f"{tag} {message}"


def read_content_hash(message: str) -> str:
    match = _HASH_LEADING.match(message or "")
    if match:
        return match.group(1)
    match = _HASH_TRAILING.search(message or "")
    return match.group(1) if match else ""


def resolve_image_align(config_align: str, meta_align: str) -> str:
    align = (meta_align or config_align or "").strip().lower()
    if align and align not in ("left", "center", "right"):
        raise MetaError(
            f"unknown image-align {align!r}, expected one of: left, center, right"
        )
    return align


_INCLUDE_RE = re.compile(r"<!--\s*Include:\s*(.*?)\s*-->")


def find_includes(body: str) -> list[str]:
    return [m.group(1).strip() for m in _INCLUDE_RE.finditer(body) if m.group(1).strip()]


def warn_unsupported_headers(path: str, meta: object, body: str) -> None:
    folders: list[str] = getattr(meta, "folders", []) or []
    properties: dict = getattr(meta, "properties", {}) or {}
    order = getattr(meta, "order", None)
    if folders:
        _warn(f"{path}: Folder headers are not supported and were ignored")
    if properties:
        _warn(f"{path}: Property/properties are not applied (content properties unsupported)")
    if order is not None:
        _warn(f"{path}: Order header is recorded but sibling ordering is not applied")
    for include in find_includes(body):
        _warn(f"{path}: Include {include!r} is not expanded (includes unsupported)")


def warn_unsupported_options(config: Config) -> None:
    if config.edit_lock:
        _warn("--edit-lock is accepted but page edit locking is not implemented")
    if config.no_overwrite:
        _warn("--no-overwrite is accepted but concurrency guarding is not implemented")
    if config.track_pages or config.manifest_page or config.orphan_under:
        _warn("--track-pages/--manifest-page/--orphan-under are accepted but "
              "page-manifest tracking is not implemented")
    if config.on_orphan != "report":
        _warn(f"--on-orphan {config.on_orphan} is accepted but orphan handling "
              "is not implemented")
    if config.preserve_comments:
        _warn("--preserve-comments is accepted but comment merging is not implemented")
    if config.check_links:
        _warn("--check-links is accepted but link checking is not implemented")
    if config.global_properties:
        _warn("--global-properties is accepted but content properties are not implemented")
    if config.include_path:
        _warn("--include-path is accepted but Include expansion is not implemented")
    for feature in config.features:
        if feature == "frontmatter":
            _debug("frontmatter feature is always enabled in this clone")
        else:
            _warn(f"--features {feature!r} is accepted but not implemented")
    diagram_touched = (
        config.mermaid_bundle or config.d2_bundle_remote
        or config.mermaid_engine != "chrome" or config.mermaid_output != "png"
        or config.math_format != "png" or config.d2_output != "png"
        or config.mermaid_scale != 1.0 or config.math_scale != 2.0
        or config.d2_scale != 1.0
    )
    if diagram_touched:
        _warn("mermaid/math/d2 options are accepted but diagram rendering is not implemented")


def _attachment_paths(patterns: list[str], source_dir: str) -> list[str]:
    paths: list[str] = []
    for pattern in patterns:
        candidate = pattern
        if not os.path.isabs(candidate):
            candidate = os.path.join(source_dir, candidate)
        hits = sorted(glob.glob(candidate, recursive=True))
        if hits:
            paths.extend(h for h in hits if os.path.isfile(h))
        elif os.path.isfile(candidate):
            paths.append(candidate)
        else:
            raise ConfigError(f"attachment {pattern!r} does not exist")
    return list(dict.fromkeys(paths))


def ensure_parent_chain(
    client: ConfluenceClient,
    space: str,
    page_type: str,
    parents: list[str],
    *,
    dry_run: bool = False,
) -> str | None:
    """Find or create each parent in turn; return the deepest parent's id.

    Under ``dry_run`` nothing is created: missing parents are reported and
    the deepest *existing* parent's id is returned (or None).
    """
    parent_id: str | None = None
    for title in parents:
        page = client.find_page(space, title, page_type)
        if page is None:
            if dry_run:
                _info(f"would create parent page {title!r} in space {space}")
                return parent_id
            page = client.create_page(space, title, "", page_type, parent_id)
            _info(f"created parent page {title!r} in space {space}")
        parent_id = str(page["id"])
    return parent_id


def page_url(client: ConfluenceClient, page: dict) -> str:
    links = page.get("_links", {}) or {}
    base = links.get("base", "") or client.base_url
    webui = links.get("webui", "")
    return base.rstrip("/") + webui if webui else base


class SyncResult(str):
    """The action taken for one file, plus a machine-readable ``info`` dict.

    Behaves as the plain action string (``"created"``, ``"updated"``,
    ``"unchanged"``, ``"skipped"``, ``"compiled"``, ``"dry-run"``) so older
    callers comparing against strings keep working; ``run_sync`` reads
    ``info`` for the ``--output-format`` report.
    """

    def __new__(cls, action: str, info: dict | None = None) -> "SyncResult":
        obj = super().__new__(cls, action)
        obj.info: dict = info or {}
        return obj


def sync_file(
    path: str,
    config: Config,
    client: ConfluenceClient | None,
    page_id: str = "",
    _path_root: str = "",
) -> SyncResult:
    """Sync one file; returns the action taken. Raises on failure."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    source_dir = os.path.dirname(os.path.abspath(path)) or "."

    meta, body, warnings = parse_document(
        text,
        filename=path,
        title_from_h1=config.title_from_h1,
        title_from_filename=config.title_from_filename,
        space=config.space,
        parents=config.parents,
        content_appearance=config.content_appearance,
        title_append_hash=config.title_append_hash,
    )
    for warning in warnings:
        _warn(f"{path}: {warning}")

    if page_id:
        _warn(f"{path}: file metadata is ignored due to --target-url")
    if config.parents_from_path and not page_id and not meta.declared_parents:
        extra, _ = parents_from_path(path, _path_root or run_root(config.files, config.parents_from_path_root))
        if extra:
            doc_parents = meta.parents[len(config.parents):] if config.parents else list(meta.parents)
            meta.parents = [*config.parents, *extra, *doc_parents]
            _debug(f"{path}: parents from path: {extra!r}")

    if meta.synchronized is False:
        _info(f"skipped {path}: Synchronized is false")
        return SyncResult("skipped", {"file": path, "status": "skipped",
                                      "reason": "Synchronized is false"})

    image_align = resolve_image_align(config.image_align, meta.image_align)
    result = render(
        body,
        drop_h1=config.drop_h1,
        strip_linebreaks=config.strip_linebreaks,
        image_align=image_align,
        attach_referenced=config.attach_referenced,
    )
    warn_unsupported_headers(path, meta, body)

    if config.compile_only or (client is None and not page_id):
        print(f"--- {path} -> {meta.space} / {meta.title or '(no title)'} ---")
        print(result.storage or "(empty page)")
        return SyncResult("compiled", {"file": path, "status": "compiled",
                                       "space": meta.space, "title": meta.title})

    assert client is not None
    if page_id:
        return _sync_to_page_id(path, config, client, result.storage, source_dir, result.attachments)

    if not meta.title:
        raise MetaError(
            f"{path}: page title is not set (add a Title header, "
            "--title-from-h1 or --title-from-filename)"
        )
    if not meta.space:
        raise MetaError(
            f"{path}: space is not set (add a Space header or --space)"
        )

    attachments = _attachment_paths(meta.attachments, source_dir)
    for ref in result.attachments:
        ref_path = ref if os.path.isabs(ref) else os.path.join(source_dir, ref)
        ref_path = os.path.normpath(ref_path)
        if os.path.isfile(ref_path):
            if ref_path not in attachments:
                attachments.append(ref_path)
        else:
            _warn(f"{path}: referenced file {ref!r} does not exist; skipped")

    if config.dry_run:
        parent_id = ensure_parent_chain(
            client, meta.space, meta.type, meta.parents, dry_run=True,
        )
        _ = parent_id
        page = client.find_page(meta.space, meta.title, meta.type)
        where = f"under {meta.parents[-1]!r} " if meta.parents else ""
        print(
            f"dry-run {path}: would publish {meta.title!r} "
            f"({page['id'] if page else 'new'}) {where}in space {meta.space}"
        )
        print(result.storage or "(empty page)")
        return SyncResult("dry-run", {"file": path, "status": "dry-run",
                                      "space": meta.space, "title": meta.title})

    parent_id = ensure_parent_chain(client, meta.space, meta.type, meta.parents)
    page = client.find_page(meta.space, meta.title, meta.type)

    storage = apply_layout(result.storage, meta.layout, meta.sidebar)
    digest = content_hash(storage) if config.changes_only else ""

    if page is None:
        page = client.create_page(
            meta.space, meta.title, storage, meta.type, parent_id
        )
        action = "created"
    else:
        current = client.get_page_by_id(str(page["id"]))
        current_storage = ((current.get("body", {}) or {}).get("storage", {}) or {}).get(
            "value", ""
        )
        if config.changes_only:
            previous = read_content_hash(
                ((current.get("version", {}) or {}).get("message", "")) or ""
            )
            if previous == digest:
                action = "unchanged"
            else:
                client.update_page(
                    page,
                    storage,
                    minor_edit=config.minor_edit,
                    version_message=format_version_message(config.version_message, digest),
                    appearance=meta.content_appearance or "full-width",
                    emoji=meta.emoji,
                )
                action = "updated"
        elif current_storage == storage:
            action = "unchanged"
        else:
            client.update_page(
                page,
                storage,
                minor_edit=config.minor_edit,
                version_message=config.version_message,
                appearance=meta.content_appearance or "full-width",
                emoji=meta.emoji,
            )
            action = "updated"

    for local_path in attachments:
        _, changed = client.ensure_attachment(str(page["id"]), local_path)
        if changed:
            _info(f"  attachment {os.path.basename(local_path)} uploaded")

    if meta.labels or not config.append_labels:
        client.sync_labels(
            str(page["id"]), meta.labels, append=config.append_labels
        )

    url = page_url(client, page)
    _info(f"{action} {path}: {url}")
    return SyncResult(action, {
        "file": path, "status": action, "space": meta.space,
        "title": meta.title, "page_id": str(page["id"]), "url": url,
    })


def _sync_to_page_id(
    path: str,
    config: Config,
    client: ConfluenceClient,
    storage: str,
    source_dir: str,
    referenced: list[str],
) -> SyncResult:
    target_base, target_page_id = parse_target_url(config.target_url)
    _ = target_base
    page = client.get_page_by_id(target_page_id)
    if config.dry_run:
        print(f"dry-run {path}: would publish to page id {target_page_id}")
        print(storage or "(empty page)")
        return SyncResult("dry-run", {"file": path, "status": "dry-run",
                                      "page_id": target_page_id})
    current_storage = ((page.get("body", {}) or {}).get("storage", {}) or {}).get("value", "")
    if current_storage == storage:
        action = "unchanged"
    else:
        client.update_page(
            page,
            storage,
            minor_edit=config.minor_edit,
            version_message=config.version_message,
            appearance=config.content_appearance.strip() or "full-width",
        )
        action = "updated"
    attachments: list[str] = []
    for ref in referenced:
        ref_path = ref if os.path.isabs(ref) else os.path.join(source_dir, ref)
        ref_path = os.path.normpath(ref_path)
        if os.path.isfile(ref_path):
            if ref_path not in attachments:
                attachments.append(ref_path)
        else:
            _warn(f"{path}: referenced file {ref!r} does not exist; skipped")
    for local_path in attachments:
        _, changed = client.ensure_attachment(str(page["id"]), local_path)
        if changed:
            _info(f"  attachment {os.path.basename(local_path)} uploaded")
    url = page_url(client, page)
    _info(f"{action} {path}: {url}")
    return SyncResult(action, {
        "file": path, "status": action, "page_id": str(page["id"]),
        "title": page.get("title", ""), "url": url,
    })


def run_sync(args: argparse.Namespace) -> int:
    try:
        config, warnings = load_config(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    set_log_level(config.log_level)
    for warning in warnings:
        _warn(warning)
    try:
        validate_config(config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    warn_unsupported_options(config)
    _debug(f"effective config: space={config.space!r} files={config.files!r} "
           f"parents={config.parents!r} output={config.output_format}")

    page_id = ""
    if config.target_url:
        try:
            target_base, page_id = parse_target_url(config.target_url)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if not config.base_url:
            config.base_url = target_base
        if not page_id and not config.compile_only:
            print("error: --target-url has no pageId query parameter "
                  "(only ?pageId= URLs select a page)", file=sys.stderr)
            return 1

    try:
        files = resolve_files(config.files, ci=config.ci)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not files:
        if config.ci:
            if config.output_format == "json":
                print(json.dumps({"pages": [], "errors": []}))
            return 0
        print("error: no files to sync (use --files)", file=sys.stderr)
        return 1

    if not config.compile_only and not config.base_url:
        print("error: confluence base URL should be specified using -b/--base-url "
              "flag, --target-url, or be stored in configuration file", file=sys.stderr)
        return 1

    client: ConfluenceClient | None = None
    if not config.compile_only:
        try:
            password = config.resolve_password()
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if not password:
            print("error: confluence password should be specified using -p flag "
                  "or be stored in configuration file", file=sys.stderr)
            return 1
        try:
            client = ConfluenceClient(
                config.base_url,
                config.username,
                password,
                insecure_skip_tls_verify=config.insecure_skip_tls_verify,
            )
        except ConfluenceError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    path_root = run_root(config.files, config.parents_from_path_root)
    results: list[dict] = []
    errors: list[str] = []
    failures = 0
    for file_path in files:
        try:
            assert client is not None or config.compile_only
            outcome = sync_file(file_path, config, client, page_id, path_root)
            info = dict(outcome.info) if isinstance(outcome, SyncResult) else {}
            info.setdefault("file", file_path)
            info.setdefault("status", str(outcome))
            results.append(info)
        except (MetaError, ConfigError, ConfluenceError, OSError) as exc:
            failures += 1
            message = f"{file_path}: {exc}"
            errors.append(message)
            results.append({"file": file_path, "status": "failed", "reason": str(exc)})
            if config.output_format == "github":
                print(f"::error file={file_path}::{exc}")
            else:
                print(f"error: {message}", file=sys.stderr)
            if not config.continue_on_error:
                break
    if config.output_format == "json":
        print(json.dumps({"pages": results, "errors": errors}, indent=2))
    elif config.output_format == "github":
        for outcome in results:
            if outcome.get("status") == "failed":
                continue
            if outcome.get("url"):
                print(f"::notice file={outcome['file']}::{outcome['url']}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    else:
        argv = list(argv)
    if argv and argv[0] not in ("sync", "-h", "--help", "--version"):
        argv = ["sync", *argv]
    args = parser.parse_args(argv)
    if args.command == "sync":
        return run_sync(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
