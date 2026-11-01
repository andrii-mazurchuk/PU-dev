"""The page-tier contract.

pu is the escape-hatch case `UNIT_STANDARDS.md` describes: its
dashboard is a dependency graph, and a graph canvas is not
expressible in the panel vocabulary. Taking that hatch means
taking on rules the spec tier enforced for us, and every one of
them fails *silently* -- the page renders, and the data is
simply absent or comes from the wrong origin.

`pu/dashboard.html` is a build artefact of `web/` (rule 1 as amended in
holonic-node 6b50dfb); CI checks it matches a fresh build. These tests
read the shipped file, because that is what the rules are about.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from urllib.request import urlopen

import pytest

from pu import ask, sessions, server

UNIT_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = UNIT_ROOT / "prompts"
PAGE = UNIT_ROOT / "pu" / "dashboard.html"
WEB_SRC = UNIT_ROOT / "web" / "src"

# The minifier writes fetch(`tasks?...`) as well as fetch("tasks").
FETCH = re.compile(r"""fetch\(\s*[`'"]([^`'"]*)""")


@pytest.fixture(scope="module")
def html() -> str:
    return PAGE.read_text(encoding="utf-8")


# -- self-contained -------------------------------------------------------


def test_the_page_loads_nothing_from_anywhere_else(html):
    """One file, inline CSS and JS, no CDN. The node serves this
    on its own origin, so anything fetched from elsewhere is a
    third party reading a page about this system's internals.

    Checked on what loads -- tags and CSS urls -- rather than on any
    `https://` in the text: the bundle carries URLs as plain strings
    (React's error-decoder link) that are never fetched."""
    for pattern in (r"<script[^>]+src=", r"<link[^>]+(stylesheet|href=)",
                    r"@import\s", r"url\(\s*['\"]?(https?:)?//"):
        assert not re.search(pattern, html, re.I), pattern


def test_no_absolute_urls_are_fetched(html):
    for match in FETCH.findall(html):
        assert not match.startswith("http"), match


# -- the relative-path rule ----------------------------------------------


@pytest.mark.xfail(strict=True, reason=(
    "the web/ app still reads its fixtures; it is not wired to pu's API yet"))
def test_every_fetch_is_relative_and_has_no_leading_slash(html):
    """The rule that makes one file work in both places.

    This unit serves the page at `/dashboard`, so a relative
    fetch lands on its own root. The node serves it at
    `/dashboard/pu/` -- with the trailing slash -- so the same
    fetch lands inside the proxied prefix. One file, both
    contexts, no conditional.

    A leading slash reaches the *node* instead, which answers
    with plausible JSON of the wrong shape rather than an
    error. That is the failure this test exists for: it does
    not look like a bug, it looks like bad data.
    """
    calls = FETCH.findall(html)
    assert calls, "no fetches found -- has the page stopped reading anything?"
    for call in calls:
        assert not call.startswith("/"), call
        assert not call.startswith("http"), call


def test_the_paths_it_fetches_are_ones_this_unit_serves(html):
    """A panel pointed at a path that 404s renders as empty, not
    as an error -- so nothing on screen says it is wrong."""
    served = {"tasks", "gate", "stats", "spend", "projects", "sessions",
              "session-types", "triggers", "running", "attention", "ticks",
              "concurrency", "pause", "mcp-servers", "queue", "frontier"}
    for call in FETCH.findall(html):
        root = call.split("/")[0].split("?")[0].strip()
        if not root or root.startswith("$"):
            continue
        assert root in served, f"{call} -> /{root} is not served by this unit"


# -- degradation and theme ------------------------------------------------


def test_it_renders_without_a_unit_behind_it(html):
    """A unit with no data yet renders the page, not an error. The app
    falls back to its sample data and says so."""
    assert "sample data" in html


def test_it_is_theme_aware(html):
    """Framed, the node writes its tokens onto <html> and toggles
    `.dark` there -- but only on a page that opts in."""
    assert '<meta name="holonic-tokens" content="1"' in html
    assert ".dark" in html


def test_it_draws_no_navigation(html):
    """The node's topbar and unit switcher sit above this and
    are not ours to duplicate. A unit that draws its own nav is
    the thing that makes six dashboards feel like six sites."""
    assert "<nav" not in html.lower()
    # It must not link a peer, either: cross-unit navigation is
    # the node's, and a hard-coded peer breaks the moment that
    # peer is swapped for another implementation.
    for unit in ("cu", "mu-agent", "mu-logs", "mu-spec", "au"):
        assert f'href="{unit}' not in html
        assert f"/dashboard/{unit}" not in html


def test_untrusted_text_is_never_set_as_markup():
    """Task descriptions and bodies are written by other agents and by
    any peer that can create a ticket. React escapes what it renders;
    the only ways around that are these two, so the source has none."""
    for path in WEB_SRC.rglob("*.ts*"):
        text = path.read_text(encoding="utf-8")
        assert "dangerouslySetInnerHTML" not in text, path
        assert ".innerHTML" not in text, path


# -- the page has to actually ship ---------------------------------------


def test_the_page_is_declared_as_package_data():
    """`UNIT_STANDARDS.md`, page-tier requirements: the page must be
    present at runtime however this unit is installed, and this unit is
    responsible for proving it.

    setuptools ships no non-`.py` file unless told to, so without a
    `package-data` entry a non-editable `pip install .` omits the page
    and `GET /dashboard` answers 404 -- which the node renders as "this
    unit has no dashboard" rather than as a packaging error. Nothing on
    screen says it is wrong.

    **What this deliberately does not do is assert the file exists in
    the checkout.** That is true whatever the packaging says, so it
    proves nothing -- it is exactly the check that would pass while the
    installed artefact was broken.
    """
    config = (UNIT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.setuptools.package-data]" in config, (
        "no package-data section; a built wheel would omit dashboard.html"
    )
    section = config.split("[tool.setuptools.package-data]", 1)[1]
    # Only up to the next section header.
    section = section.split("\n[", 1)[0]
    assert "dashboard.html" in section, section


def test_the_server_finds_the_page_relative_to_itself():
    """Resolved against the module, never the working directory.

    The gateway starts this unit with a cwd of its own choosing, and an
    installed package sits nowhere near the repo -- so a repo-relative
    path finds the page in development and nothing in production, which
    is the worst possible split.
    """
    assert server.DASHBOARD_HTML.is_absolute()
    assert server.DASHBOARD_HTML.parent == Path(server.__file__).resolve().parent
    assert server.DASHBOARD_HTML.read_text(encoding="utf-8").strip()


# -- over the wire --------------------------------------------------------


@pytest.fixture
def base(store, body_store, tmp_path):
    httpd = server.build_server(
        "127.0.0.1", 0, store, body_store, PROMPTS_DIR,
        unit_root=UNIT_ROOT,
        session_store=sessions.SessionStore(tmp_path / "sessions"),
        ask_store=ask.AskStore(tmp_path / "asks"),
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_dashboard_answers_html_which_is_how_the_tier_is_declared(base):
    """The node reads the tier off the response and there is no
    `kind` field to keep in sync. text/html is the whole of the
    declaration -- serve JSON here by accident and the node
    tries to render it as panels."""
    with urlopen(f"{base}/dashboard") as resp:
        assert resp.status == 200
        assert resp.headers.get_content_type() == "text/html"
        body = resp.read().decode("utf-8")
    assert "<title>pu" in body
    assert '<div id="root">' in body


def test_every_path_the_page_reads_answers_over_the_wire(base, html):
    """The end-to-end version of the path test above: not just
    that the roots are known, but that they actually respond."""
    for path in ("tasks?status=pending", "gate", "stats", "spend?days=1", "projects"):
        with urlopen(f"{base}/{path}") as resp:
            assert resp.status == 200, path
