"""CLI tests: argument parsing, helpers, sync_file and run_sync flows."""

from __future__ import annotations

import argparse
import json
import os

import pytest

from mark.cli import (
    SyncResult,
    apply_layout,
    build_parser,
    content_hash,
    find_includes,
    format_version_message,
    glob_root,
    main,
    parents_from_path,
    read_content_hash,
    resolve_files,
    resolve_image_align,
    run_root,
    run_sync,
    set_log_level,
    sync_file,
    warn_unsupported_options,
)
from mark.config import Config, ConfigError
from mark.converter import render
from mark.metadata import MetaError


def parse_cli(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(["sync", *argv])


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.setattr(
        "mark.config.DEFAULT_CONFIG_PATH", str(tmp_path / "none.toml")
    )
    set_log_level("info")
    return tmp_path


# -- parsing ---------------------------------------------------------------------

def test_all_major_flags_parse():
    args = parse_cli(
        "--files", "a.md", "b.md", "--space", "S", "--base-url", "https://x",
        "--username", "u", "--password", "p", "--parents", "P1/P2",
        "--parents-delimiter", "/", "--parents-from-path",
        "--title-from-h1", "--drop-h1", "--compile-only", "--dry-run",
        "--minor-edit", "--version-message", "v", "--changes-only",
        "--edit-lock", "--ci", "--continue-on-error",
        "--image-align", "center", "--content-appearance", "fixed",
        "--append-labels", "--attach-referenced", "--log-level", "debug",
        "--color", "never", "--output-format", "json",
        "--check-links", "internal", "--check-links", "all",
        "--check-links-warn-only", "--no-overwrite", "--track-pages",
        "--manifest-page", "M", "--manifest-prefix", "pfx",
        "--on-orphan", "archive", "--orphan-under", "OU",
        "--preserve-comments", "--mermaid-scale", "2",
        "--mermaid-engine", "merman", "--mermaid-output", "svg",
        "--mermaid-bundle", "--math-format", "svg", "--math-scale", "3",
        "--d2-output", "svg", "--d2-bundle-remote", "--d2-scale", "4",
        "--features", "mermaid,math",
    )
    assert args.files == ["a.md", "b.md"]
    assert args.space == "S"
    assert args.parents == "P1/P2"
    assert args.changes_only is True
    assert args.mermaid_scale == 2.0
    assert args.features == ["mermaid,math"]
    assert args.check_links == ["internal", "all"]


def test_sync_help_lists_mark_env_names(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["sync", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for name in ("$MARK_SPACE", "$MARK_FILES", "$MARK_BASE_URL", "$MARK_PARENTS",
                 "$MARK_OUTPUT_FORMAT", "$MARK_CHECK_LINKS", "$MARK_FEATURES"):
        assert name in out


def test_bare_args_alias_to_sync(monkeypatch):
    seen = []
    monkeypatch.setattr("mark.cli.run_sync", lambda args: seen.append(args) or 0)
    assert main(["--files", "x.md", "--space", "S"]) == 0
    assert main(["sync", "--files", "x.md"]) == 0
    assert [a.command for a in seen] == ["sync", "sync"]
    assert seen[0].files == ["x.md"]


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


# -- path helpers -------------------------------------------------------------------

def test_resolve_files_glob_dedupe_and_ci(tmp_path):
    a = tmp_path / "a.md"
    b = tmp_path / "sub" / "b.md"
    b.parent.mkdir()
    a.write_text("x", encoding="utf-8")
    b.write_text("y", encoding="utf-8")
    pattern = str(tmp_path / "**" / "*.md")
    assert resolve_files([pattern, str(a)], ci=False) == [str(a), str(b)]
    with pytest.raises(ConfigError, match="no files match"):
        resolve_files([str(tmp_path / "nope/*.md")], ci=False)
    assert resolve_files([str(tmp_path / "nope/*.md")], ci=True) == []


def test_glob_root():
    assert glob_root("docs/**/*.md") == "docs"
    assert glob_root("docs/*.md") == "docs"
    assert glob_root("a.md") == ""
    assert glob_root("") == ""


def test_run_root():
    assert run_root(["docs/**/*.md"], "") == "docs"
    assert run_root(["docs/**/*.md"], "override") == "override"
    assert run_root(["a.md"], "") == ""


def test_parents_from_path(tmp_path):
    root = tmp_path / "docs"
    leaf = root / "guide" / "intro"
    leaf.mkdir(parents=True)
    page = leaf / "page.md"
    page.write_text("x", encoding="utf-8")
    parents, is_index = parents_from_path(str(page), str(root))
    assert (parents, is_index) == (["Guide", "Intro"], False)
    index = leaf / "index.md"
    index.write_text("x", encoding="utf-8")
    parents, is_index = parents_from_path(str(index), str(root))
    assert (parents, is_index) == (["Guide"], True)
    # File outside the root contributes no parents.
    parents, _ = parents_from_path(str(page), str(tmp_path / "elsewhere"))
    assert parents == []


# -- pure helpers ---------------------------------------------------------------------

def test_apply_layout():
    assert apply_layout("<p>x</p>", "", "") == "<p>x</p>"
    wrapped = apply_layout("<p>x</p>", "article", "<p>side</p>")
    assert wrapped.startswith("<ac:layout>")
    assert "<p>x</p>" in wrapped and "<p>side</p>" in wrapped


def test_hash_roundtrip():
    digest = content_hash("<p>x</p>")
    assert len(digest) == 40
    assert read_content_hash(format_version_message("", digest)) == digest
    assert read_content_hash(format_version_message("hello", digest)) == digest
    assert read_content_hash(f"prefix [v{digest}]") == digest
    assert read_content_hash("no hash here") == ""


def test_resolve_image_align():
    assert resolve_image_align("center", "") == "center"
    assert resolve_image_align("center", "right") == "right"  # header wins
    assert resolve_image_align("", "") == ""
    with pytest.raises(MetaError, match="unknown image-align"):
        resolve_image_align("top", "")


def test_find_includes():
    assert find_includes("<!-- Include: a.md -->\n<!--Include: b/c.md-->") == [
        "a.md", "b/c.md",
    ]


def test_sync_result_is_str_with_info():
    result = SyncResult("created", {"url": "https://x"})
    assert result == "created"
    assert result.info["url"] == "https://x"


def test_warn_unsupported_options(capsys):
    config = Config(edit_lock=True, check_links=["all"], features=["math"],
                    mermaid_scale=2.0)
    warn_unsupported_options(config)
    err = capsys.readouterr().err
    assert "--edit-lock" in err and "--check-links" in err
    assert "math" in err and "diagram rendering" in err


# -- sync_file --------------------------------------------------------------------------

DOC = "<!-- Space: DOC -->\n<!-- Title: Hello -->\n\nSome body.\n"


class FakeClient:
    def __init__(self):
        self.base_url = "https://confluence.test"
        self.pages: dict[tuple, dict] = {}
        self.writes: list[tuple] = []
        self._next_id = 100

    def _page(self, space, title, page_type, body, parent_id=None):
        self._next_id += 1
        page = {
            "id": str(self._next_id), "type": page_type, "title": title,
            "space": space,
            "version": {"number": 1, "message": ""},
            "ancestors": [{"id": parent_id}] if parent_id else [],
            "_links": {"base": self.base_url, "webui": f"/pages/{self._next_id}"},
            "body": {"storage": {"value": body}},
        }
        self.pages[(space, title, page_type)] = page
        return dict(page)

    def find_page(self, space, title, page_type="page"):
        found = self.pages.get((space, title, page_type))
        return dict(found) if found else None

    def create_page(self, space, title, body, page_type="page", parent_id=None):
        self.writes.append(("create", title))
        return self._page(space, title, page_type, body, parent_id)

    def get_page_by_id(self, page_id, expand=""):
        for page in self.pages.values():
            if page["id"] == str(page_id):
                return dict(page)
        raise AssertionError(f"unknown page id {page_id}")

    def update_page(self, page, content, **kwargs):
        self.writes.append(("update", page["title"], content, kwargs))
        key = next(k for k, v in self.pages.items() if v["id"] == page["id"])
        stored = self.pages[key]
        stored["body"] = {"storage": {"value": content}}
        stored["version"] = {
            "number": stored["version"]["number"] + 1,
            "message": kwargs.get("version_message", ""),
        }
        return dict(stored)

    def ensure_attachment(self, page_id, local_path, comment=""):
        self.writes.append(("attach", page_id, local_path))
        return ({"id": "a1"}, True)

    def sync_labels(self, page_id, labels, append=False):
        self.writes.append(("labels", page_id, labels, append))


def write_doc(tmp_path, name="page.md", text=DOC):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_sync_file_compile_only(tmp_path, capsys):
    path = write_doc(tmp_path)
    result = sync_file(path, Config(compile_only=True), None)
    assert result == "compiled"
    assert result.info["title"] == "Hello"
    out = capsys.readouterr().out
    assert "Hello" in out and "Some body." in out


def test_sync_file_skipped_when_not_synchronized(tmp_path, capsys):
    text = ("<!-- Space: DOC -->\n<!-- Title: Skip -->\n"
            "<!-- Synchronized: false -->\n\nBody.\n")
    path = write_doc(tmp_path, text=text)
    result = sync_file(path, Config(compile_only=True), None)
    assert result == "skipped"


def test_sync_file_dry_run_is_read_only(tmp_path, capsys):
    path = write_doc(tmp_path)
    client = FakeClient()
    config = Config(space="DOC", dry_run=True, parents=["P1", "P2"])
    result = sync_file(path, config, client)
    assert result == "dry-run"
    assert client.writes == []  # no creates, updates, attachments or labels
    assert "dry-run" in capsys.readouterr().out


def test_sync_file_create_with_parents_attachments_labels(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "a.png").write_bytes(b"img")
    text = (
        "<!-- Space: DOC -->\n<!-- Title: Hello -->\n"
        "<!-- Label: l1 -->\n<!-- Label: l2 -->\n"
        "<!-- Attachment: assets/a.png -->\n\nBody ![](assets/a.png).\n"
    )
    path = write_doc(tmp_path, text=text)
    client = FakeClient()
    result = sync_file(path, Config(parents=["P1"]), client)
    assert result == "created"
    kinds = [w[0] for w in client.writes]
    assert kinds.count("create") == 2  # parent P1 + page
    assert ("labels", result.info["page_id"], ["l1", "l2"], False) in client.writes
    assert any(w[0] == "attach" for w in client.writes)
    assert result.info["url"].startswith("https://confluence.test/pages/")


def test_sync_file_update_unchanged_and_emoji(tmp_path):
    path = write_doc(tmp_path)
    client = FakeClient()
    config = Config()
    first = sync_file(path, config, client)
    assert first == "created"
    client.writes.clear()
    second = sync_file(path, config, client)
    assert second == "unchanged"
    assert [w for w in client.writes if w[0] == "update"] == []
    assert second.info["page_id"] == first.info["page_id"]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(DOC.replace("Some body.", "Changed.")
                     .replace("<!-- Title: Hello -->",
                              "<!-- Title: Hello -->\n<!-- Emoji: \U0001F680 -->"))
    assert sync_file(path, config, client) == "updated"
    update = next(w for w in client.writes if w[0] == "update")
    assert update[3]["emoji"] == "\U0001F680"


def test_sync_file_changes_only(tmp_path):
    path = write_doc(tmp_path)
    storage = render("Some body.\n").storage
    digest = content_hash(storage)
    client = FakeClient()
    config = Config(changes_only=True, version_message="ship")
    created = sync_file(path, config, client)
    assert created == "created"
    # Simulate a previous changes-only publish with a matching hash.
    key = ("DOC", "Hello", "page")
    client.pages[key]["body"] = {"storage": {"value": "STALE"}}
    client.pages[key]["version"] = {
        "number": 2, "message": format_version_message("ship", digest),
    }
    client.writes.clear()
    assert sync_file(path, config, client) == "unchanged"
    assert [w for w in client.writes if w[0] == "update"] == []
    # Mismatched hash updates and stamps the new digest.
    client.pages[key]["version"] = {"number": 3, "message": "[v0*****] old"}
    assert sync_file(path, config, client) == "updated"
    update = next(w for w in client.writes if w[0] == "update")
    assert update[3]["version_message"] == format_version_message("ship", digest)


def test_sync_file_missing_title_and_space(tmp_path):
    path = write_doc(tmp_path, text="No headers here.\n")
    with pytest.raises(MetaError, match="title is not set"):
        sync_file(path, Config(space="DOC"), FakeClient())
    path = write_doc(tmp_path, name="t.md", text="<!-- Title: T -->\n\nBody.\n")
    with pytest.raises(MetaError, match="space is not set"):
        sync_file(path, Config(), FakeClient())


def test_sync_file_missing_attachment_raises(tmp_path):
    text = ("<!-- Space: DOC -->\n<!-- Title: Hello -->\n"
            "<!-- Attachment: nope.png -->\n\nBody.\n")
    path = write_doc(tmp_path, text=text)
    with pytest.raises(ConfigError, match="does not exist"):
        sync_file(path, Config(), FakeClient())


def test_sync_file_missing_body_ref_warns(tmp_path, capsys):
    path = write_doc(tmp_path, text=DOC.replace("Some body.", "![](nope.png)"))
    result = sync_file(path, Config(), FakeClient())
    assert result == "created"
    assert "does not exist; skipped" in capsys.readouterr().err


def test_sync_file_target_url_mode(tmp_path):
    path = write_doc(tmp_path)
    client = FakeClient()
    seeded = client._page("DOC", "Remote", "page", "<p>old</p>")
    config = Config(target_url=f"https://confluence.test/wiki?pageId={seeded['id']}")
    result = sync_file(path, config, client, seeded["id"])
    assert result == "updated"
    assert result.info["page_id"] == seeded["id"]
    assert result.info["title"] == "Remote"  # remote title wins in target mode
    client.writes.clear()
    assert sync_file(path, config, client, seeded["id"]) == "unchanged"


def test_sync_file_no_label_sync_when_append_and_empty(tmp_path):
    path = write_doc(tmp_path)
    client = FakeClient()
    sync_file(path, Config(append_labels=True), client)
    assert [w for w in client.writes if w[0] == "labels"] == []


# -- run_sync -------------------------------------------------------------------------------

def test_run_sync_compile_only_json(tmp_path, capsys):
    path = write_doc(tmp_path)
    code = run_sync(parse_cli("--files", path, "--output-format", "json",
                              "--compile-only"))
    assert code == 0
    out = capsys.readouterr().out
    # Compile banners precede the JSON report on stdout; parse from the object.
    report = json.loads(out[out.index("{"):])
    assert report["errors"] == []
    assert report["pages"][0]["status"] == "compiled"


def test_run_sync_errors(tmp_path, capsys):
    assert run_sync(parse_cli()) == 1  # no files
    assert "no files to sync" in capsys.readouterr().err
    path = write_doc(tmp_path)
    assert run_sync(parse_cli("--files", path)) == 1  # no base url
    assert "base URL" in capsys.readouterr().err
    assert run_sync(parse_cli("--files", path, "--base-url", "https://x")) == 1
    assert "password" in capsys.readouterr().err
    assert run_sync(parse_cli("--files", path, "--target-url",
                              "https://x/wiki?x=1")) == 1
    assert "pageId" in capsys.readouterr().err


def test_run_sync_validation_error(tmp_path, capsys):
    path = write_doc(tmp_path)
    code = run_sync(parse_cli("--files", path, "--compile-only",
                              "--title-from-h1", "--title-from-filename"))
    assert code == 1
    assert "mutually exclusive" in capsys.readouterr().err


def test_run_sync_with_fake_client_and_continue(tmp_path, monkeypatch, capsys):
    good = write_doc(tmp_path, name="good.md")
    bad = write_doc(tmp_path, name="bad.md", text="<!-- Titel: Typo -->\n\nBody.\n")
    fake = FakeClient()
    monkeypatch.setattr("mark.cli.ConfluenceClient", lambda *a, **k: fake)
    args = parse_cli("--files", bad, good, "--space", "DOC",
                     "--base-url", "https://confluence.test",
                     "--password", "tok")
    assert run_sync(args) == 1  # stops at first failure
    assert len([w for w in fake.writes if w[0] == "create"]) == 0
    fake2 = FakeClient()
    monkeypatch.setattr("mark.cli.ConfluenceClient", lambda *a, **k: fake2)
    args = parse_cli("--files", bad, good, "--space", "DOC",
                     "--base-url", "https://confluence.test",
                     "--password", "tok", "--continue-on-error")
    assert run_sync(args) == 1
    assert any(w[0] == "create" and w[1] == "Hello" for w in fake2.writes)


def test_run_sync_github_format(tmp_path, monkeypatch, capsys):
    good = write_doc(tmp_path, name="good.md")
    bad = write_doc(tmp_path, name="bad.md", text="<!-- Titel: Typo -->\n\nBody.\n")
    monkeypatch.setattr("mark.cli.ConfluenceClient", lambda *a, **k: FakeClient())
    args = parse_cli("--files", bad, good, "--space", "DOC",
                     "--base-url", "https://x", "--password", "tok",
                     "--output-format", "github", "--continue-on-error")
    assert run_sync(args) == 1
    out = capsys.readouterr().out
    assert "::error file=" in out
    assert "::notice file=" in out


def test_run_sync_ci_empty_match(tmp_path, capsys):
    code = run_sync(parse_cli("--files", str(tmp_path / "nothing/*.md"),
                              "--ci", "--compile-only",
                              "--output-format", "json"))
    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"pages": [], "errors": []}
