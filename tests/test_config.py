"""Config tests: TOML/env/CLI merge, validation, target-url, passwords."""

from __future__ import annotations

import argparse

import pytest

from mark.cli import build_parser
from mark.config import (
    Config,
    ConfigError,
    apply_args,
    apply_dict,
    apply_env,
    load_config,
    load_config_file,
    parse_target_url,
    split_csv,
    split_parents,
    validate_config,
)


def parse_cli(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(["sync", *argv])


@pytest.fixture(autouse=True)
def no_default_config(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "mark.config.DEFAULT_CONFIG_PATH", str(tmp_path / "none.toml")
    )


# -- merging / precedence -----------------------------------------------------

def test_precedence_cli_over_env_over_file(tmp_path, monkeypatch):
    cfg = tmp_path / "mark.toml"
    cfg.write_text(
        'space = "FILE"\ntitle-from-h1 = true\nlog-level = "debug"\n',
        encoding="utf-8",
    )
    env = {"MARK_SPACE": "ENV", "MARK_LOG_LEVEL": "warning"}
    args = parse_cli("--config", str(cfg), "--space", "CLI")
    config, warnings = load_config(args, env)
    assert warnings == []
    assert config.space == "CLI"  # CLI wins over env and file
    assert config.title_from_h1 is True  # file value kept
    assert config.log_level == "warning"  # env wins over file


def test_env_only_and_file_only(tmp_path):
    cfg = tmp_path / "mark.toml"
    cfg.write_text('space = "FILE"\n', encoding="utf-8")
    config, _ = load_config(parse_cli("--config", str(cfg)), {})
    assert config.space == "FILE"
    config, _ = load_config(parse_cli(), {"MARK_SPACE": "ENV"})
    assert config.space == "ENV"


def test_unknown_keys_warned_not_fatal(tmp_path):
    cfg = tmp_path / "mark.toml"
    cfg.write_text('bogus-key = 1\n', encoding="utf-8")
    config, warnings = load_config(
        parse_cli("--config", str(cfg)), {"MARK_BOGUS": "x"}
    )
    assert any("bogus-key" in w for w in warnings)
    assert any("MARK_BOGUS" in w for w in warnings)
    assert config.space == ""


def test_explicit_missing_config_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(parse_cli("--config", str(tmp_path / "nope.toml")), {})


def test_missing_default_config_is_fine(monkeypatch, tmp_path):
    # Point the default path somewhere empty via HOME expansion is complex;
    # instead just verify a bare run with empty env works when no file exists.
    monkeypatch.setattr("mark.config.DEFAULT_CONFIG_PATH", str(tmp_path / "absent.toml"))
    config, warnings = load_config(parse_cli(), {})
    assert warnings == []
    assert config.space == ""


def test_load_config_file_toml_types(tmp_path):
    cfg = tmp_path / "mark.toml"
    cfg.write_text(
        'space = "DOC"\ntitle-from-h1 = true\n'
        'mermaid-scale = 2.5\nparents = ["a", "b"]\n',
        encoding="utf-8",
    )
    data = load_config_file(cfg)
    assert data == {
        "space": "DOC",
        "title-from-h1": True,
        "mermaid-scale": 2.5,
        "parents": ["a", "b"],
    }


def test_apply_dict_rejects_non_bool():
    with pytest.raises(ConfigError, match="must be true or false"):
        apply_dict(Config(), {"title-from-h1": "yes"}, "test")


def test_apply_dict_splits_csv_lists():
    config = Config()
    apply_dict(config, {"check-links": "internal, external", "features": ["mermaid"]}, "t")
    assert config.check_links == ["internal", "external"]
    assert config.features == ["mermaid"]


def test_apply_dict_rejects_bad_number():
    with pytest.raises(ConfigError, match="must be a number"):
        apply_dict(Config(), {"mermaid-scale": "huge"}, "t")


# -- environment ---------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "want"),
    [("1", True), ("true", True), ("Yes", True), ("ON", True),
     ("0", False), ("false", False), ("No", False), ("off", False)],
)
def test_env_bool_values(raw, want):
    config = Config()
    apply_env(config, {"MARK_DRY_RUN": raw})
    assert config.dry_run is want


def test_env_bool_invalid():
    with pytest.raises(ConfigError, match="boolean"):
        apply_env(Config(), {"MARK_DRY_RUN": "perhaps"})


def test_env_lists_and_floats():
    config = Config()
    apply_env(config, {"MARK_FILES": "a.md, b.md", "MARK_MERMAID_SCALE": "2"})
    assert config.files == ["a.md", "b.md"]
    assert config.mermaid_scale == 2.0


def test_env_config_key_ignored():
    config = Config()
    assert apply_env(config, {"MARK_CONFIG": "/tmp/x.toml"}) == []


# -- CLI args ------------------------------------------------------------------

def test_apply_args_only_when_given():
    config = Config(space="OLD", dry_run=False)
    apply_args(config, parse_cli("--space", "NEW"))
    assert config.space == "NEW"
    assert config.dry_run is False  # store_true default must not clobber


def test_apply_args_parents_split_on_delimiter():
    config = Config()
    apply_args(config, parse_cli("--parents", "a/b/c"))
    assert config.parents == ["a", "b", "c"]
    config = Config()
    apply_args(config, parse_cli("--parents", "a:b", "--parents-delimiter", ":"))
    assert config.parents == ["a", "b"]


def test_apply_args_scales_and_csv():
    config = Config()
    apply_args(
        config,
        parse_cli("--mermaid-scale", "3", "--check-links", "internal,all"),
    )
    assert config.mermaid_scale == 3.0
    assert config.check_links == ["internal", "all"]


def test_file_env_parents_split_with_effective_delimiter(tmp_path):
    cfg = tmp_path / "mark.toml"
    cfg.write_text('parents = "a/b"\nparents-delimiter = "/"\n', encoding="utf-8")
    config, _ = load_config(parse_cli("--config", str(cfg)), {})
    assert config.parents == ["a", "b"]
    # Env raw value is split too.
    config, _ = load_config(parse_cli(), {"MARK_PARENTS": "x:y", "MARK_PARENTS_DELIMITER": ":"})
    assert config.parents == ["x", "y"]
    # CLI parents win outright.
    config, _ = load_config(
        parse_cli("--config", str(cfg), "--parents", "cli"),
        {"MARK_PARENTS": "env"},
    )
    assert config.parents == ["cli"]


def test_split_parents():
    assert split_parents("a/b//c", "/") == ["a", "b", "c"]
    assert split_parents("a:b", ":") == ["a", "b"]
    assert split_parents("a/b", "") == ["a", "b"]


def test_split_csv():
    assert split_csv(["a,b", "c", ""]) == ["a", "b", "c"]
    assert split_csv("x") == ["x"]


# -- target URL ------------------------------------------------------------------

def test_parse_target_url():
    base, page_id = parse_target_url(
        "https://tenant.atlassian.net/wiki/pages/viewpage.action?pageId=123"
    )
    assert base == "https://tenant.atlassian.net/wiki".replace("/wiki", "")
    assert page_id == "123"


def test_parse_target_url_port_and_missing_page_id():
    base, page_id = parse_target_url("http://host:8090/display/X?x=1")
    assert base == "http://host:8090"
    assert page_id == ""


def test_parse_target_url_invalid():
    with pytest.raises(ConfigError, match="as a URL"):
        parse_target_url("not-a-url")


# -- validation ------------------------------------------------------------------

def test_validate_defaults_ok():
    validate_config(Config())  # must not raise


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: setattr(c, "title_from_h1", True) or setattr(c, "title_from_filename", True),
        lambda c: setattr(c, "password", "x") or setattr(c, "password_command", "y"),
        lambda c: setattr(c, "content_appearance", "wide"),
        lambda c: setattr(c, "image_align", "top"),
        lambda c: setattr(c, "log_level", "verbose"),
        lambda c: setattr(c, "color", "always"),
        lambda c: setattr(c, "output_format", "yaml"),
        lambda c: setattr(c, "on_orphan", "move"),
        lambda c: setattr(c, "mermaid_output", "pdf"),
        lambda c: setattr(c, "d2_output", "pdf"),
        lambda c: setattr(c, "math_format", "pdf"),
        lambda c: setattr(c, "mermaid_engine", "firefox"),
        lambda c: setattr(c, "mermaid_scale", 0.0),
        lambda c: setattr(c, "math_scale", -1.0),
        lambda c: setattr(c, "d2_scale", float("nan")),
        lambda c: setattr(c, "d2_scale", float("inf")),
        lambda c: setattr(c, "features", ["wat"]),
        lambda c: setattr(c, "check_links", ["sometimes"]),
        lambda c: setattr(c, "check_links_warn_only", True),
        lambda c: setattr(c, "on_orphan", "delete"),
        lambda c: setattr(c, "no_overwrite", True),
        lambda c: setattr(c, "manifest_page", "Manifest"),
        lambda c: setattr(c, "manifest_prefix", "custom"),
        lambda c: (setattr(c, "track_pages", True), setattr(c, "manifest_prefix", "bad prefix!")),
    ],
)
def test_validate_rejects(mutate):
    config = Config()
    mutate(config)
    with pytest.raises(ConfigError):
        validate_config(config)


def test_validate_mermaid_bundle_needs_svg():
    config = Config(mermaid_bundle=True, mermaid_output="png")
    with pytest.raises(ConfigError, match="mermaid-bundle"):
        validate_config(config)
    config.mermaid_output = "svg"
    validate_config(config)


def test_validate_tracking_combos_ok():
    validate_config(Config(track_pages=True, on_orphan="archive", no_overwrite=True,
                           manifest_page="M", manifest_prefix="custom.prefix_1"))


# -- passwords -------------------------------------------------------------------

def test_resolve_password_plain():
    assert Config(password="tok").resolve_password() == "tok"


def test_resolve_password_mutually_exclusive():
    with pytest.raises(ConfigError, match="mutually exclusive"):
        Config(password="a", password_command="b").resolve_password()


def test_resolve_password_stdin_dash():
    assert Config(password="-").resolve_password("tok\nsecond\n") == "tok"
    assert Config(password="-").resolve_password("") == ""


def test_resolve_password_command(monkeypatch):
    monkeypatch.setattr(
        "mark.config.run_password_command", lambda cmd: "tok" if cmd == "helper" else "?"
    )
    assert Config(password_command="helper").resolve_password() == "tok"


def test_run_password_command_errors(monkeypatch):
    from mark.config import run_password_command

    with pytest.raises(ConfigError, match="empty"):
        run_password_command("")

    class FakeProc:
        returncode = 3
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr("mark.config.subprocess.run", lambda *a, **k: FakeProc())
    with pytest.raises(ConfigError, match="status 3"):
        run_password_command("helper")

    def missing(*a, **k):
        raise OSError("no such file")

    monkeypatch.setattr("mark.config.subprocess.run", missing)
    with pytest.raises(ConfigError, match="failed to start"):
        run_password_command("helper")
