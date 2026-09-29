"""Configuration for mark: file, environment and CLI merging.

Precedence is CLI flag > ``MARK_*`` environment variable > config file,
matching the Go implementation's ``GLOBAL OPTIONS`` (run ``mark --help``
upstream; every option lists its ``$MARK_*`` variable).

Config file format is TOML (default ``~/.config/mark.toml``)::

    username = "your-email"
    password = "api-token"
    base-url = "https://tenant.atlassian.net/wiki"
    title-from-h1 = true

``password-command`` names a helper whose first stdout line is the token; it
is mutually exclusive with ``password``. ``password = "-"`` (or flag ``-p -``)
reads the token from stdin.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None  # type: ignore[assignment]

DEFAULT_CONFIG_PATH = str(Path.home() / ".config" / "mark.toml")

ENV_PREFIX = "MARK_"


class ConfigError(ValueError):
    """Configuration is missing, unreadable or contradictory."""


@dataclass
class Config:
    base_url: str = ""
    username: str = ""
    password: str = ""
    password_command: str = ""
    target_url: str = ""
    space: str = ""
    files: list[str] = field(default_factory=list)
    parents: list[str] = field(default_factory=list)
    parents_delimiter: str = "/"
    parents_from_path: bool = False
    parents_from_path_root: str = ""
    title_from_h1: bool = False
    title_from_filename: bool = False
    title_append_hash: bool = False
    drop_h1: bool = False
    strip_linebreaks: bool = False
    compile_only: bool = False
    dry_run: bool = False
    minor_edit: bool = False
    version_message: str = ""
    edit_lock: bool = False
    changes_only: bool = False
    ci: bool = False
    continue_on_error: bool = False
    insecure_skip_tls_verify: bool = False
    image_align: str = ""
    image_width: str = ""
    image_height: str = ""
    content_appearance: str = ""
    append_labels: bool = False
    attach_referenced: bool = False
    include_path: str = ""
    log_level: str = "info"
    color: str = "auto"
    output_format: str = "url"
    check_links: list[str] = field(default_factory=list)
    check_links_warn_only: bool = False
    global_properties: str = ""
    no_overwrite: bool = False
    track_pages: bool = False
    manifest_page: str = ""
    manifest_prefix: str = "mark.manifest"
    on_orphan: str = "report"
    orphan_under: str = ""
    preserve_comments: bool = False
    mermaid_scale: float = 1.0
    mermaid_engine: str = "chrome"
    mermaid_output: str = "png"
    mermaid_bundle: bool = False
    math_format: str = "png"
    math_scale: float = 2.0
    d2_output: str = "png"
    d2_bundle_remote: bool = False
    d2_scale: float = 1.0
    features: list[str] = field(default_factory=list)

    def resolve_password(self, stdin_text: str | None = None) -> str:
        """Return the usable token, honouring ``-`` and ``password-command``."""
        if self.password and self.password_command:
            raise ConfigError(
                "password and password-command are mutually exclusive; "
                "remove one of them"
            )
        if self.password_command:
            return run_password_command(self.password_command)
        if self.password == "-":
            if stdin_text is None:
                import sys

                stdin_text = sys.stdin.read()
            return stdin_text.splitlines()[0] if stdin_text.splitlines() else ""
        return self.password


def run_password_command(command: str) -> str:
    """Run the password helper (no shell, no stdin) and take its first line."""
    if os.name == "nt":
        # POSIX splitting would eat backslashes in Windows paths, so split
        # plainly and strip one layer of surrounding quotes per argument.
        argv = [
            arg[1:-1]
            if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in ("'", '"')
            else arg
            for arg in shlex.split(command, posix=False)
        ]
    else:
        argv = shlex.split(command, posix=True)
    if not argv:
        raise ConfigError("password-command is empty")
    try:
        proc = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ConfigError(f"password-command failed to start: {exc}") from exc
    if proc.returncode != 0:
        raise ConfigError(
            f"password-command exited with status {proc.returncode}: "
            f"{proc.stderr.strip()}"
        )
    lines = proc.stdout.splitlines()
    if not lines or not lines[0].strip():
        raise ConfigError("password-command printed no token on its first line")
    return lines[0].strip()


# ---------------------------------------------------------------------------
# TOML file loading
# ---------------------------------------------------------------------------

def _fallback_toml_loads(text: str) -> dict[str, Any]:
    """Bare-minimum TOML for flat string/bool/number/list config files."""
    import re

    result: dict[str, Any] = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            raise ConfigError(
                f"{lineno}: tables are not supported by the fallback TOML reader"
            )
        key, sep, rest = line.partition("=")
        if not sep:
            raise ConfigError(f"{lineno}: expected 'key = value', got {raw!r}")
        key = key.strip()
        value = rest.strip()
        if "#" in value and not (
            value.startswith(('"', "'")) and value.endswith(('"', "'"))
        ):
            value = value.split("#", 1)[0].strip()
        result[key] = _fallback_toml_value(value, lineno)
    return result


def _fallback_toml_value(value: str, lineno: int) -> Any:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        items = []
        for part in inner.split(","):
            part = part.strip()
            if len(part) >= 2 and part[0] == part[-1] and part[0] in ("'", '"'):
                items.append(part[1:-1])
            else:
                items.append(part)
        return items
    try:
        return int(value, 10)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    raise ConfigError(f"{lineno}: cannot parse TOML value {value!r}")


def load_config_file(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read a TOML config file into a plain dict (kebab-case keys kept)."""
    expanded = os.path.expanduser(os.path.expandvars(str(path)))
    with open(expanded, "rb") as handle:
        raw = handle.read()
    if tomllib is not None:
        data = tomllib.loads(raw.decode("utf-8"))
    else:
        data = _fallback_toml_loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"config file {path} must hold a TOML table")
    return data


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------

_KEY_TO_ATTR = {
    "base-url": "base_url",
    "base_url": "base_url",
    "username": "username",
    "user": "username",
    "password": "password",
    "password-command": "password_command",
    "password_command": "password_command",
    "target-url": "target_url",
    "target_url": "target_url",
    "space": "space",
    "files": "files",
    "parents": "parents",
    "parents-delimiter": "parents_delimiter",
    "parents_delimiter": "parents_delimiter",
    "parents-from-path": "parents_from_path",
    "parents_from_path": "parents_from_path",
    "parents-from-path-root": "parents_from_path_root",
    "parents_from_path_root": "parents_from_path_root",
    "title-from-h1": "title_from_h1",
    "title_from_h1": "title_from_h1",
    "title-from-filename": "title_from_filename",
    "title_from_filename": "title_from_filename",
    "title-append-generated-hash": "title_append_hash",
    "title_append_generated_hash": "title_append_hash",
    "drop-h1": "drop_h1",
    "drop_h1": "drop_h1",
    "strip-linebreaks": "strip_linebreaks",
    "strip_linebreaks": "strip_linebreaks",
    "compile-only": "compile_only",
    "compile_only": "compile_only",
    "dry-run": "dry_run",
    "dry_run": "dry_run",
    "minor-edit": "minor_edit",
    "minor_edit": "minor_edit",
    "version-message": "version_message",
    "version_message": "version_message",
    "edit-lock": "edit_lock",
    "edit_lock": "edit_lock",
    "changes-only": "changes_only",
    "changes_only": "changes_only",
    "ci": "ci",
    "continue-on-error": "continue_on_error",
    "continue_on_error": "continue_on_error",
    "insecure-skip-tls-verify": "insecure_skip_tls_verify",
    "insecure_skip_tls_verify": "insecure_skip_tls_verify",
    "image-align": "image_align",
    "image_align": "image_align",
    "image-width": "image_width",
    "image_width": "image_width",
    "image-height": "image_height",
    "image_height": "image_height",
    "content-appearance": "content_appearance",
    "content_appearance": "content_appearance",
    "append-labels": "append_labels",
    "append_labels": "append_labels",
    "attach-referenced": "attach_referenced",
    "attach_referenced": "attach_referenced",
    "include-path": "include_path",
    "include_path": "include_path",
    "log-level": "log_level",
    "log_level": "log_level",
    "color": "color",
    "output-format": "output_format",
    "output_format": "output_format",
    "check-links": "check_links",
    "check_links": "check_links",
    "check-links-warn-only": "check_links_warn_only",
    "check_links_warn_only": "check_links_warn_only",
    "global-properties": "global_properties",
    "global_properties": "global_properties",
    "no-overwrite": "no_overwrite",
    "no_overwrite": "no_overwrite",
    "track-pages": "track_pages",
    "track_pages": "track_pages",
    "manifest-page": "manifest_page",
    "manifest_page": "manifest_page",
    "manifest-prefix": "manifest_prefix",
    "manifest_prefix": "manifest_prefix",
    "on-orphan": "on_orphan",
    "on_orphan": "on_orphan",
    "orphan-under": "orphan_under",
    "orphan_under": "orphan_under",
    "preserve-comments": "preserve_comments",
    "preserve_comments": "preserve_comments",
    "mermaid-scale": "mermaid_scale",
    "mermaid_scale": "mermaid_scale",
    "mermaid-engine": "mermaid_engine",
    "mermaid_engine": "mermaid_engine",
    "mermaid-output": "mermaid_output",
    "mermaid_output": "mermaid_output",
    "mermaid-bundle": "mermaid_bundle",
    "mermaid_bundle": "mermaid_bundle",
    "math-format": "math_format",
    "math_format": "math_format",
    "math-scale": "math_scale",
    "math_scale": "math_scale",
    "d2-output": "d2_output",
    "d2_output": "d2_output",
    "d2-bundle-remote": "d2_bundle_remote",
    "d2_bundle_remote": "d2_bundle_remote",
    "d2-scale": "d2_scale",
    "d2_scale": "d2_scale",
    "features": "features",
}

_BOOL_ATTRS = frozenset(
    {
        "title_from_h1",
        "title_from_filename",
        "title_append_hash",
        "parents_from_path",
        "drop_h1",
        "strip_linebreaks",
        "compile_only",
        "dry_run",
        "minor_edit",
        "edit_lock",
        "changes_only",
        "ci",
        "continue_on_error",
        "insecure_skip_tls_verify",
        "append_labels",
        "attach_referenced",
        "check_links_warn_only",
        "no_overwrite",
        "track_pages",
        "preserve_comments",
        "mermaid_bundle",
        "d2_bundle_remote",
    }
)

_LIST_ATTRS = frozenset({"files", "parents", "check_links", "features"})

_FLOAT_ATTRS = frozenset({"mermaid_scale", "math_scale", "d2_scale"})

KNOWN_FEATURES = (
    "d2",
    "date",
    "emoji",
    "frontmatter",
    "inline-link-card",
    "math",
    "mention",
    "mermaid",
    "mkdocsadmonitions",
    "plantuml",
)

DEFAULT_FEATURES = ("mermaid", "mention")
DEFAULT_MANIFEST_PREFIX = "mark.manifest"


def _parse_bool_env(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in ("1", "t", "true", "y", "yes", "on"):
        return True
    if lowered in ("0", "f", "false", "n", "no", "off"):
        return False
    raise ConfigError(f"cannot parse boolean environment value {value!r}")


def _coerce_list(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    raise ConfigError(f"config key {key!r} must be a string or list of strings")


def _coerce_float(value: Any, key: str) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"config key {key!r} must be a number")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            pass
    raise ConfigError(f"config key {key!r} must be a number")


def split_csv(values: list[str] | tuple[str, ...] | str) -> list[str]:
    """Split repeatable/comma-separated flag values (check-links, features)."""
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    for entry in values:
        for part in str(entry).split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


def apply_dict(config: Config, data: Mapping[str, Any], source: str) -> list[str]:
    """Apply a config-file dict onto ``config``; returns warnings."""
    warnings: list[str] = []
    for key, value in data.items():
        attr = _KEY_TO_ATTR.get(str(key))
        if attr is None:
            warnings.append(f"{source}: unknown config key {key!r} was ignored")
            continue
        if attr in _BOOL_ATTRS:
            if not isinstance(value, bool):
                raise ConfigError(f"{source}: {key!r} must be true or false")
            setattr(config, attr, value)
        elif attr in _LIST_ATTRS:
            items = _coerce_list(value, str(key))
            if attr in ("check_links", "features"):
                items = split_csv(items)
            setattr(config, attr, items)
        elif attr in _FLOAT_ATTRS:
            setattr(config, attr, _coerce_float(value, str(key)))
        else:
            setattr(config, attr, "" if value is None else str(value))
    return warnings


def apply_env(
    config: Config, env: Mapping[str, str] | None = None
) -> list[str]:
    """Apply ``MARK_*`` variables onto ``config``; returns warnings."""
    if env is None:
        env = os.environ
    warnings: list[str] = []
    for key, raw in env.items():
        if not key.startswith(ENV_PREFIX):
            continue
        name = key[len(ENV_PREFIX):].lower()
        if name == "config":
            continue
        attr = _KEY_TO_ATTR.get(name) or _KEY_TO_ATTR.get(name.replace("_", "-"))
        if attr is None:
            warnings.append(f"environment: unknown variable {key!r} was ignored")
            continue
        if attr in _BOOL_ATTRS:
            setattr(config, attr, _parse_bool_env(raw))
        elif attr in _LIST_ATTRS:
            if attr == "parents":
                # Split on the parents delimiter below (load_config), once the
                # effective delimiter is known; keep the raw value for now.
                setattr(config, attr, [raw] if raw else [])
            else:
                setattr(config, attr, [p for p in (s.strip() for s in raw.split(",")) if p])
        elif attr in _FLOAT_ATTRS:
            setattr(config, attr, _coerce_float(raw, key))
        else:
            setattr(config, attr, raw)
    return warnings


def apply_args(config: Config, args: Any) -> None:
    """Apply parsed CLI args onto ``config`` (only options actually given)."""
    for attr in (
        "base_url",
        "username",
        "password",
        "password_command",
        "target_url",
        "space",
        "parents_delimiter",
        "parents_from_path_root",
        "version_message",
        "image_align",
        "image_width",
        "image_height",
        "content_appearance",
        "include_path",
        "log_level",
        "color",
        "output_format",
        "global_properties",
        "manifest_page",
        "manifest_prefix",
        "on_orphan",
        "orphan_under",
        "mermaid_engine",
        "mermaid_output",
        "math_format",
        "d2_output",
    ):
        value = getattr(args, attr, None)
        if value is not None and value != "":
            setattr(config, attr, value)
    for attr in ("mermaid_scale", "math_scale", "d2_scale"):
        value = getattr(args, attr, None)
        if value is not None:
            setattr(config, attr, float(value))
    files = getattr(args, "files", None)
    if files:
        config.files = list(files)
    parents = getattr(args, "parents", None)
    if parents:
        delimiter = config.parents_delimiter or "/"
        config.parents = [p for p in parents.split(delimiter) if p != ""]
    for attr in ("check_links", "features"):
        value = getattr(args, attr, None)
        if value:
            config.__dict__[attr] = split_csv(value)
    for attr in (
        "title_from_h1",
        "title_from_filename",
        "title_append_hash",
        "parents_from_path",
        "drop_h1",
        "strip_linebreaks",
        "compile_only",
        "dry_run",
        "minor_edit",
        "edit_lock",
        "changes_only",
        "ci",
        "continue_on_error",
        "insecure_skip_tls_verify",
        "append_labels",
        "attach_referenced",
        "check_links_warn_only",
        "no_overwrite",
        "track_pages",
        "preserve_comments",
        "mermaid_bundle",
        "d2_bundle_remote",
    ):
        if getattr(args, attr, False):
            setattr(config, attr, True)


def split_parents(value: str, delimiter: str) -> list[str]:
    """Split a ``--parents`` value on its delimiter, dropping empty fields."""
    if not delimiter:
        delimiter = "/"
    return [p for p in value.split(delimiter) if p != ""]


def parse_target_url(target_url: str) -> tuple[str, str]:
    """Split a ``--target-url`` into ``(base_url, page_id)``.

    The base URL is ``scheme://host`` (no path); the page id comes from the
    ``pageId`` query parameter and may be empty.
    """
    from urllib.parse import urlparse, parse_qs

    parsed = urlparse(target_url)
    if not parsed.scheme or not parsed.hostname:
        raise ConfigError(f"unable to parse {target_url!r} as a URL")
    base_url = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        base_url += f":{parsed.port}"
    page_id = parse_qs(parsed.query).get("pageId", [""])[0]
    return base_url, page_id


def validate_config(config: Config) -> None:
    """Reject contradictory or out-of-range option combinations."""
    import math
    import re

    if config.title_from_h1 and config.title_from_filename:
        raise ConfigError(
            "--title-from-h1 and --title-from-filename are mutually exclusive"
        )
    if config.password and config.password_command:
        raise ConfigError(
            "password and password-command are mutually exclusive; remove one"
        )
    appearance = config.content_appearance.strip()
    if appearance and appearance not in ("full-width", "fixed", "default"):
        raise ConfigError(
            f"invalid --content-appearance {appearance!r} "
            "(expected: full-width, fixed, or default)"
        )
    align = config.image_align.strip().lower()
    if align and align not in ("left", "center", "right"):
        raise ConfigError(
            f"invalid --image-align {config.image_align!r} "
            "(expected: left, center, or right)"
        )
    for flag, value in (
        ("--image-width", config.image_width),
        ("--image-height", config.image_height),
    ):
        value = value.strip()
        if value and not (value.isdigit() and int(value) > 0):
            raise ConfigError(
                f"invalid {flag} {value!r} (expected a positive number of pixels)"
            )
    if config.log_level.strip().lower() not in (
        "trace", "debug", "info", "warning", "error", "fatal",
    ):
        raise ConfigError(
            f"invalid --log-level {config.log_level!r} "
            "(expected: TRACE, DEBUG, INFO, WARNING, ERROR, FATAL)"
        )
    if config.color not in ("auto", "never"):
        raise ConfigError(
            f"invalid --color {config.color!r} (expected: auto or never)"
        )
    if config.output_format not in ("url", "json", "github"):
        raise ConfigError(
            f"invalid --output-format {config.output_format!r} "
            "(expected: url, json, or github)"
        )
    if config.on_orphan not in ("report", "archive", "delete"):
        raise ConfigError(
            f"invalid --on-orphan {config.on_orphan!r} "
            "(expected: report, archive, or delete)"
        )
    for name in ("mermaid_output", "d2_output"):
        value = getattr(config, name)
        if value not in ("png", "svg"):
            raise ConfigError(
                f"invalid --{name.replace('_', '-')} {value!r} "
                "(expected: png or svg)"
            )
    if config.math_format not in ("png", "svg"):
        raise ConfigError(
            f"invalid --math-format {config.math_format!r} (expected: png or svg)"
        )
    if config.mermaid_engine not in ("chrome", "merman"):
        raise ConfigError(
            f"invalid --mermaid-engine {config.mermaid_engine!r} "
            "(expected: chrome or merman)"
        )
    for name in ("mermaid_scale", "math_scale", "d2_scale"):
        scale = getattr(config, name)
        if not (scale > 0) or math.isinf(scale) or math.isnan(scale):
            raise ConfigError(
                f"invalid --{name.replace('_', '-')} {scale!r} "
                "(expected: a finite number greater than 0)"
            )
    if config.mermaid_bundle and config.mermaid_output != "svg":
        raise ConfigError(
            "--mermaid-bundle needs --mermaid-output=svg: "
            "there is nowhere in a PNG to keep the diagram's source"
        )
    for feature in config.features:
        if feature not in KNOWN_FEATURES:
            raise ConfigError(
                f"invalid --features value {feature!r} "
                f"(expected any of: {', '.join(KNOWN_FEATURES)})"
            )
    for link in config.check_links:
        if link not in ("internal", "confluence", "external", "all"):
            raise ConfigError(
                f"invalid --check-links value {link!r} "
                "(expected any of: internal, confluence, external, all)"
            )
    if config.check_links_warn_only and not config.check_links:
        raise ConfigError(
            "--check-links-warn-only requires --check-links: "
            "on its own no links are checked, so the silence means nothing"
        )
    if config.on_orphan != "report" and not config.track_pages:
        raise ConfigError(
            f"--on-orphan {config.on_orphan} requires --track-pages: "
            "only the page manifest knows which pages mark published"
        )
    if config.no_overwrite and not config.track_pages:
        raise ConfigError(
            "--no-overwrite requires --track-pages: the version mark last "
            "published is remembered in the page manifest"
        )
    if config.manifest_page and not config.track_pages:
        raise ConfigError(
            "--manifest-page requires --track-pages: it says where the page "
            "manifest is kept, and nothing else keeps one"
        )
    if config.manifest_prefix and config.manifest_prefix != DEFAULT_MANIFEST_PREFIX:
        if not config.track_pages:
            raise ConfigError(
                "--manifest-prefix requires --track-pages: it names the page "
                "manifest's properties, and nothing else keeps one"
            )
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", config.manifest_prefix):
            raise ConfigError(
                f"invalid --manifest-prefix {config.manifest_prefix!r} "
                "(expected letters, digits, '_', '-' and dots)"
            )


def load_config(args: Any, env: Mapping[str, str] | None = None) -> tuple[Config, list[str]]:
    """Build the effective Config from file + environment + CLI args."""
    if env is None:
        env = os.environ
    warnings: list[str] = []
    config = Config()

    config_path = getattr(args, "config", None) or env.get(ENV_PREFIX + "CONFIG")
    explicit = bool(getattr(args, "config", None) or env.get(ENV_PREFIX + "CONFIG"))
    path = config_path or DEFAULT_CONFIG_PATH
    expanded = os.path.expanduser(os.path.expandvars(path))
    if os.path.exists(expanded):
        warnings.extend(apply_dict(config, load_config_file(expanded), f"config file {path}"))
    elif explicit:
        raise ConfigError(f"config file {path} does not exist")

    warnings.extend(apply_env(config, env))
    cli_parents = getattr(args, "parents", None)
    apply_args(config, args)
    if not cli_parents and config.parents:
        # File/env parents arrive unsplit (like upstream's single string);
        # the CLI branch in apply_args already split its own value.
        delimiter = config.parents_delimiter or "/"
        split: list[str] = []
        for entry in config.parents:
            split.extend(p for p in entry.split(delimiter) if p != "")
        config.parents = split
    if not config.parents_delimiter:
        config.parents_delimiter = "/"
    if not config.log_level:
        config.log_level = "info"
    if not config.color:
        config.color = "auto"
    if not config.output_format:
        config.output_format = "url"
    if not config.on_orphan:
        config.on_orphan = "report"
    if not config.mermaid_engine:
        config.mermaid_engine = "chrome"
    if not config.mermaid_output:
        config.mermaid_output = "png"
    if not config.math_format:
        config.math_format = "png"
    if not config.d2_output:
        config.d2_output = "png"
    if not config.manifest_prefix:
        config.manifest_prefix = DEFAULT_MANIFEST_PREFIX
    return config, warnings
