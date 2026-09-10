"""The page-tier contract.

pu is the escape-hatch case `UNIT_STANDARDS.md` describes: its
dashboard is a dependency graph, and a graph canvas is not
expressible in the panel vocabulary. Taking that hatch means
taking on rules the spec tier enforced for us, and every one of
them fails *silently* -- the page renders, and the data is
simply absent or comes from the wrong origin.

The layout algorithm itself is checked by `tests/layout.test.mjs`,
which runs under node against the very same file.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import pytest

from pu import ask, bodies, sessions, server

UNIT_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = UNIT_ROOT / "prompts"
PAGE = UNIT_ROOT / "pu" / "dashboard.html"


@pytest.fixture(scope="module")
def html() -> str:
    return PAGE.read_text(encoding="utf-8")


# -- self-contained -------------------------------------------------------


def test_the_page_loads_nothing_from_anywhere_else(html):
    """One file, inline CSS and JS, no CDN. The node serves this
    on its own origin, so anything fetched from elsewhere is a
    third party reading a page about this system's internals."""
    for pattern in (r"<script[^>]+src=", r"<link[^>]+stylesheet", r"@import"):
        assert not re.search(pattern, html, re.I), pattern
    assert "https://" not in html.split("<style>")[0]


def test_no_absolute_urls_are_fetched(html):
    for match in re.findall(r"""fetch\(\s*[`'"]([^`'"]*)""", html):
        assert not match.startswith("http"), match


# -- the relative-path rule ----------------------------------------------


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
    calls = re.findall(r"""fetch\(\s*[`'"]([^`'"]*)""", html)
    assert calls, "no fetches found -- has the page stopped reading anything?"
    for call in calls:
        assert not call.startswith("/"), call
        assert not call.startswith("http"), call


def test_the_paths_it_fetches_are_ones_this_unit_serves(html):
    """A panel pointed at a path that 404s renders as empty, not
    as an error -- so nothing on screen says it is wrong."""
    served = {"tasks", "gate", "stats", "spend", "projects", "sessions", "ask"}
    for call in re.findall(r"""fetch\(\s*[`'"]([^`'"]*)""", html):
        root = call.split("/")[0].split("?")[0].strip()
        if not root or root.startswith("$"):
            continue
        assert root in served, f"{call} -> /{root} is not served by this unit"


# -- read-only ------------------------------------------------------------


def test_the_page_never_writes(html):
    """The node's dashboard proxy forwards GET and refuses
    everything else with 405, so a write from inside the frame
    cannot arrive. The ask panel is deliberately layout-only
    until there is a submit path that goes through the bridge's
    audited write door rather than around it."""
    assert not re.search(r"method:\s*['\"]POST", html, re.I)


# -- degradation ----------------------------------------------------------


def test_it_renders_without_a_unit_behind_it(html):
    """A unit with no data yet renders the page, not an error --
    and this page goes further, falling back to a sample graph
    so the layout is inspectable without standing up a node."""
    assert "function sample(" in html
    assert "Not connected to a running pu" in html


def test_it_is_theme_aware(html):
    assert "prefers-color-scheme: dark" in html
    assert "color-scheme: light dark" in html


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


def test_untrusted_text_is_never_set_as_markup(html):
    """Task descriptions and bodies are written by other agents
    and by anything that reached POST /inbox. The one place a
    value could become markup is innerHTML, and the places that
    use it must only ever receive strings this file wrote."""
    for line in html.splitlines():
        if ".innerHTML" not in line or "innerHTML = ''" in line:
            continue
        # Every remaining assignment interpolates only through
        # escapeText() -- which round-trips via textContent -- or
        # num(), which cannot return anything but digits. Two
        # named doors, so the rule is absolute and checkable
        # rather than a judgement per interpolation.
        interpolations = re.findall(r"\$\{([^}]*)\}", line)
        for expr in interpolations:
            assert "escapeText(" in expr or "num(" in expr, (
                f"unescaped interpolation into innerHTML: {expr}"
            )


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
    assert "function layout(" in body


def test_every_path_the_page_reads_answers_over_the_wire(base, html):
    """The end-to-end version of the path test above: not just
    that the roots are known, but that they actually respond."""
    for path in ("tasks?status=pending", "gate", "stats", "spend?days=1", "projects"):
        with urlopen(f"{base}/{path}") as resp:
            assert resp.status == 200, path
