# This project was developed with assistance from AI tools.
"""Static checks on the page's explicit light/dark theme and the links that carry it between dashboards."""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "src" / "eval-dashboard" / "eval_dashboard" / "web" / "static"
PALETTE_VARS = ("--surface", "--page", "--border", "--text", "--series-1", "--series-2", "--series-3")


def read(name):
    return (STATIC / name).read_text()


def theme_block(selector):
    """Body of the stylesheet rule whose selector list is exactly `selector`."""
    match = re.search(rf"(?m)^{re.escape(selector)}\s*\{{([^}}]*)\}}", read("styles.css"))
    assert match, f"no rule for {selector}"
    return match.group(1)


@pytest.mark.parametrize("selector", [':root, [data-theme="light"]', '[data-theme="dark"]'])
@pytest.mark.parametrize("variable", PALETTE_VARS)
def test_each_theme_block_defines_the_palette(selector, variable):
    """Both explicit theme blocks define every palette variable the page and charts use."""
    assert re.search(rf"{re.escape(variable)}\s*:", theme_block(selector))


def test_light_is_the_default_theme():
    """The light palette is also the :root palette, so a page with no data-theme is light."""
    assert "color-scheme: light" in theme_block(':root, [data-theme="light"]')
    assert "color-scheme: dark" in theme_block('[data-theme="dark"]')


@pytest.mark.parametrize("name", ["styles.css", "dashboard.js", "index.html"])
def test_theme_does_not_follow_the_os_preference(name):
    """No static file themes the page from prefers-color-scheme."""
    assert "prefers-color-scheme" not in read(name)


def test_theme_is_set_before_the_stylesheet_and_page_script_load():
    """An inline head script sets data-theme, defaulting to light, ahead of styles.css and dashboard.js."""
    html = read("index.html")
    head = html[: html.index("</head>")]
    assert "data-theme" in head
    assert re.search(r"===\s*'dark'\s*\?\s*'dark'\s*:\s*'light'", head)
    assert head.index("data-theme") < head.index("styles.css")
    assert "dashboard.js" not in head


def test_theme_query_parameter_wins_and_is_saved():
    """The head script reads ?theme=, accepts only light or dark, and stores it under the key dashboard.js uses."""
    html = read("index.html")
    head = html[: html.index("</head>")]
    assert re.search(r"URLSearchParams\(location\.search\)\.get\('theme'\)", head)
    assert "t === 'light' || t === 'dark'" in head
    assert "localStorage.setItem('eval-theme'" in head
    assert "const THEME_KEY = 'eval-theme';" in read("dashboard.js")


def test_toggle_markup():
    """The header has a two-button theme toggle with a light and a dark button."""
    html = read("index.html")
    header = html[html.index("<header>") : html.index("</header>")]
    assert 'id="theme-toggle"' in header
    assert re.search(r'<button[^>]*id="theme-light"', header)
    assert re.search(r'<button[^>]*id="theme-dark"', header)


def test_toggle_is_wired_and_shows_its_state():
    """dashboard.js binds both buttons, marks the active one and saves the choice."""
    script = read("dashboard.js")
    for theme in ("light", "dark"):
        assert f"getElementById('theme-{theme}').addEventListener('click'" in script
    assert "classList.toggle('active'" in script
    assert "localStorage.setItem(THEME_KEY" in script
    assert ".theme-toggle button.active" in read("styles.css")


def test_header_links_carry_the_theme():
    """Header links get theme=<current> through searchParams, which keeps any query already on the URL."""
    script = read("dashboard.js")
    assert "searchParams.set('theme', currentTheme())" in script
    assert "link.href = withTheme(url);" in script


def test_applied_theme_parameter_leaves_the_address():
    """The theme parameter is dropped from the address once applied, so a reload keeps a later toggle."""
    script = read("dashboard.js")
    assert "searchParams.delete('theme')" in script
    assert "history.replaceState" in script


def test_theme_change_redraws_the_charts():
    """Chart colours are resolved from CSS variables at render time, so a theme change re-renders."""
    script = read("dashboard.js")
    body = script[script.index("function setTheme(") : script.index("function initTheme(")]
    assert "renderViews(lastStats)" in body


def test_charts_have_no_hard_coded_colours():
    """dashboard.js holds no colour literal: series, text and axis colours all come from the stylesheet."""
    script = read("dashboard.js")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b(?![0-9a-zA-Z;])", re.sub(r"&#\d+;", "", script))
    assert not re.search(r"\brgba?\(", script)


def test_back_link_is_labelled_and_starts_hidden():
    """The back link names the flywheel dashboard, carries no address of its own and is hidden until one is set."""
    html = read("index.html")
    tag = re.search(r'<a[^>]*id="backlink"[^>]*>(.*?)</a>', html, re.S)
    assert tag
    assert "Flywheel dashboard" in tag.group(1)
    assert "&larr;" in tag.group(1)
    assert " hidden" in tag.group(0)
    assert "href" not in tag.group(0)


def test_sibling_link_starts_hidden_and_takes_its_label_from_the_api():
    """The sibling-view link is empty and hidden in the markup; dashboard.js gives it the configured label."""
    tag = re.search(r'<a[^>]*id="other-view-link"[^>]*>(.*?)</a>', read("index.html"), re.S)
    assert tag
    assert tag.group(1).strip() == ""
    assert " hidden" in tag.group(0)
    assert "setHeaderLink('other-view-link', otherView ? stats.other_view_url : '', stats.other_view_label)" in read("dashboard.js")
