"""Converter tests: Markdown to Confluence storage format."""

from __future__ import annotations

import os
import tempfile

from mark.converter import RenderResult, render


def test_headings_and_paragraph():
    result = render("# Title\n\nSome *em* and **strong** text.\n")
    assert result.storage == (
        "<h1>Title</h1><p>Some <em>em</em> and <strong>strong</strong> text.</p>"
    )
    assert result.attachments == []


def test_setext_headings():
    result = render("Big\n===\n\nSmall\n---\n")
    assert result.storage == "<h1>Big</h1><h2>Small</h2>"


def test_drop_h1_atx_and_setext():
    assert render("# Gone\n\nKept\n", drop_h1=True).storage == "<p>Kept</p>"
    assert render("Gone\n===\n\nKept\n", drop_h1=True).storage == "<p>Kept</p>"
    # Only the first H1 is dropped.
    assert render("# One\n\n# Two\n", drop_h1=True).storage == "<h1>Two</h1>"


def test_inline_code_strike_and_escapes():
    result = render("Use `a < b` and ~~gone~~ and \\*literal\\*.\n")
    assert result.storage == (
        "<p>Use <code>a &lt; b</code> and <s>gone</s> and *literal*.</p>"
    )


def test_entities_preserved():
    assert render("fish &amp; chips\n").storage == "<p>fish &amp; chips</p>"


def test_fenced_code_macro():
    result = render("```python\nprint(1 < 2)\n```\n")
    assert result.storage == (
        '<ac:structured-macro ac:name="code">'
        '<ac:parameter ac:name="language">python</ac:parameter>'
        "<ac:plain-text-body><![CDATA[print(1 < 2)]]></ac:plain-text-body>"
        "</ac:structured-macro>"
    )


def test_fenced_code_tilde_no_language():
    result = render("~~~\nplain\n~~~\n")
    assert '<ac:parameter ac:name="language"></ac:parameter>' in result.storage
    assert "plain" in result.storage


def test_table():
    result = render("| a | b |\n|---|---|\n| 1 | 2 |\n| 3 |\n")
    assert result.storage == (
        "<table><thead><tr><th>a</th><th>b</th></tr></thead>"
        "<tbody><tr><td>1</td><td>2</td></tr>"
        "<tr><td>3</td><td></td></tr></tbody></table>"
    )


def test_lists_nested_ordered_and_tasks():
    result = render(
        "- one\n  - nested\n- two\n\n1. first\n2. second\n\n- [x] done\n- [ ] todo\n"
    )
    assert "<ul><li>one<ul><li>nested</li></ul></li><li>two</li></ul>" in result.storage
    assert "<ol><li>first</li><li>second</li></ol>" in result.storage
    assert "<ac:task-status>complete</ac:task-status>" in result.storage
    assert "<ac:task-status>incomplete</ac:task-status>" in result.storage


def test_blockquote_and_hr():
    result = render("> quoted **bold**\n\n---\n")
    assert result.storage == (
        "<blockquote><p>quoted <strong>bold</strong></p></blockquote><hr />"
    )


def test_links_and_autolinks():
    result = render("[label](https://example.com) and <https://x.test/y>\n")
    assert '<a href="https://example.com">label</a>' in result.storage
    assert '<a href="https://x.test/y">https://x.test/y</a>' in result.storage


def test_local_image_becomes_attachment():
    result = render("![alt text](img/pic.png)\n")
    assert result.storage == (
        '<p><ac:image><ri:attachment ri:filename="pic.png" />'
        "<ac:caption>alt text</ac:caption></ac:image></p>"
    )
    assert result.attachments == ["img/pic.png"]


def test_image_align_and_remote_passthrough():
    result = render("![](a.png)\n\n![](https://cdn/x.png)\n", image_align="center")
    assert '<ac:image ac:align="center">' in result.storage
    assert '<img src="https://cdn/x.png"' in result.storage
    assert result.attachments == ["a.png"]


def test_invalid_image_align_ignored():
    result = render("![](a.png)\n", image_align="top")
    assert "<ac:image>" in result.storage


def _write_svg(tmp_dir: str, name: str, width: int, height: int) -> None:
    with open(os.path.join(tmp_dir, name), "w", encoding="utf-8") as f:
        f.write(
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{width}" height="{height}"><rect /></svg>'
        )


def test_oversized_svg_is_scaled_down_proportionally():
    with tempfile.TemporaryDirectory() as tmp:
        _write_svg(tmp, "big.svg", 1000, 500)
        result = render(
            "![](big.svg)\n",
            image_max_width="400",
            image_max_height="400",
            image_base_dir=tmp,
        )
        # 1000x500 constrained to 400x400 -> width is the binding dimension.
        assert '<ac:image ac:width="400" ac:height="200">' in result.storage
        assert result.attachments == ["big.svg"]


def test_undersized_svg_is_left_alone():
    with tempfile.TemporaryDirectory() as tmp:
        _write_svg(tmp, "small.svg", 100, 50)
        result = render(
            "![](small.svg)\n",
            image_max_width="400",
            image_max_height="400",
            image_base_dir=tmp,
        )
        assert result.storage == (
            "<p><ac:image><ri:attachment ri:filename=\"small.svg\" /></ac:image></p>"
        )


def test_svg_with_only_viewbox_is_scaled():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "vb.svg"), "w", encoding="utf-8") as f:
            f.write(
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'viewBox="0 0 800 400"><rect /></svg>'
            )
        result = render("![](vb.svg)\n", image_max_width="400", image_base_dir=tmp)
        assert '<ac:image ac:width="400" ac:height="200">' in result.storage


def test_unreadable_or_non_svg_image_is_left_alone():
    result = render(
        "![](missing.svg)\n", image_max_width="400", image_base_dir="/nonexistent"
    )
    assert "<ac:image>" in result.storage
    result2 = render("![](a.png)\n", image_max_width="400", image_base_dir="/tmp")
    assert "<ac:image>" in result2.storage


def test_attach_referenced_links():
    result = render("[spec](docs/spec.pdf)\n", attach_referenced=True)
    assert '<ri:attachment ri:filename="spec.pdf" />' in result.storage
    assert "<ac:plain-text-link-body><![CDATA[spec]]></ac:plain-text-link-body>" in result.storage
    assert result.attachments == ["docs/spec.pdf"]


def test_attach_referenced_skips_remote():
    result = render("[web](https://example.com/f.pdf)\n", attach_referenced=True)
    assert result.storage == '<p><a href="https://example.com/f.pdf">web</a></p>'
    assert result.attachments == []


def test_raw_html_passthrough():
    macro = '<ac:structured-macro ac:name="toc" />'
    assert render(macro + "\n").storage == macro


def test_strip_linebreaks_is_accepted_noop():
    assert isinstance(
        render("# T\n", strip_linebreaks=True), RenderResult
    )


def test_url_encoded_attachment_filename():
    result = render("![](my%20pic.png)\n")
    assert 'ri:filename="my pic.png"' in result.storage
