# Handoff — pu's dashboard: page tier, dependency graph, and `ask`

**2026-09-10/11.** Twelve commits, `f1a5f09..7a345c0`, all on `master`,
all pushed, CI green. Deployed and verified in a browser against the
live queue by the gateway session.

Read this before touching `pu/dashboard.html`, `pu/ask.py`, or anything
under `/dashboard`. Several things here look like mistakes and are not.

---

## What exists now

`GET /dashboard` serves **HTML** (`pu/dashboard.html`) — the **page
tier**. The node frames it below its own chrome.

The page is a **dependency graph**: wayfinder maps as containers, their
tickets laid out by blocking relations, one project at a time. Click a
task to inspect, hover to light its whole dependency chain both ways,
drag to pan, scroll to zoom. Plus a KPI strip, gate state, and a
right-hand dock.

Reads: `tasks`, `gate`, `stats`, `spend`, `projects`. All relative, no
leading slash.

---

## The decisions, and why reversing them is not free

### Page tier, not spec tier — and this needed an argument

pu served a **spec** first (`pu/dashboard.py`, now deleted): JSON
declaring panels, rendered by the node's own components. That is the
right default, costs a unit no frontend at all, and **every other unit
should still do it**.

pu moved because a graph canvas cannot be expressed in the node's closed
panel vocabulary (`kpis | table | record | bars | rows | meters | text |
ask`), and `UNIT_STANDARDS.md` §Dashboards names "a graph canvas"
verbatim as the page tier's reason to exist.

**I deliberately did not ask the node for a `graph` panel kind.** The
gateway session agreed and put it better than I did: *a node-link
renderer with layering and crossing reduction is not a panel, it is a
rendering engine wearing a panel's name.* Putting it in the vocabulary
for one unit would make every other unit's dashboard downstream of pu's
problem, and the closed vocabulary is the only thing between six units
and six visual languages.

**The cost is permanent and one-way: pu owns its own look and no longer
inherits improvements to the node's components.** Worth it here. It will
not usually be.

### The layout is the part worth reading

Hand-rolled Sugiyama in `dashboard.html`. `tests/layout.test.mjs`
(15 checks, `node tests/layout.test.mjs`, **not in CI** — pu's CI is
Python only) extracts the functions *from the page* rather than copying
them, because a drifted copy of a layout algorithm passes its tests
while the screen is wrong.

Three guarantees, each with a reason someone will otherwise "simplify":

1. **Layer = longest path from a root, not shortest.** With shortest, a
   task blocked by both a one-step and a five-step chain lands beside
   the short one and draws an edge leaping four layers backwards.
2. **Separation runs last.** Alignment pulls nodes toward neighbour
   medians; a hard separation pass then pushes anything too close apart.
   Reorder these and nodes overlap.
3. **Dummy nodes.** An edge spanning more than one layer is broken at
   every layer it crosses, and the dummy takes real space in that
   layer's ordering, so the layer reserves a gap for the edge. **This is
   the step people delete as pointless.** Deleting it is exactly why
   hand-rolled graphs get lines slicing through task boxes.

**Zero edge crossings is not promised and cannot be** — minimisation is
NP-hard and a non-planar graph cannot be drawn flat without one. The
page minimises, then *reports the count it could not remove*. Do not
replace that with a claim of zero. Dependency cycles are broken, drawn
differently, and counted; a cycle is work that can never start.

### Colour marks only the frontier

Grey is the default. Only **can start now** and **needs you** get a hue.

The first version painted every blocked task red. It looked informative
and said nothing: in a dependency chain *everything* downstream is
blocked, so the colour marked "is downstream" — a fact the edges already
carry, better. If you find yourself adding a colour, ask what decision
it changes.

### The `ask` chat box is disabled ON PURPOSE

`POST /ask` and `GET /ask/<id>` **exist, are gated, capped and tested**
(`pu/ask.py`, `session_types/ask/`, `tests/test_ask.py`). The panel in
the page is **layout only, and the controls are `disabled`**.

Why it is not wired: submitting is a POST, and the node's dashboard
proxy forwards GET and refuses everything else with 405. A page-tier
unit reaching for the bridge's `POST /route` directly is the "unit knows
node internals" coupling the standard forbids — the gateway session said
it would have refused it.

Why it is present at all: **Andrey asked for the chat to be visible as
layout**, having been told it would not function.

Why `disabled` rather than labelled: the gateway session objected that a
box accepting input and submitting nothing is the standard's own "an
empty dashboard is worse than an absent one" failure, with a keystroke
cost instead of a click — nobody reads the note before typing into the
box. Disabling it is what actually addresses that. **Do not re-enable it
without a decided write path, and do not delete it without asking
Andrey.**

Wiring it later is a change to one function (`ask()`), by design.

### The ask session gets NO tools

Not "read-only" — none. Sessions this unit spawns have no way back into
it; context is assembled going in and the result parsed coming out. It
answers from a trimmed context blob. It is absent from `ALLOWED_TOOLS`,
`REACHES_PEERS` and `SESSION_TYPE_FOR_KIND` — that last absence is what
makes it unreachable from the queue; the only path to one is `POST
/ask`. Andrey explicitly declined giving it session streams.

Four controls, because it is a money tap on an unauthenticated origin:
both gates refuse rather than queue, one question at a time unit-wide,
the question is capped and fenced *below* instructions it cannot
renegotiate, and `timeout_runner` gives up after 5 minutes. **The
timeout is load-bearing**: `subprocess_runner` has none, and a hung
session under the global lock would latch the panel shut for the life of
the process.

### `/trigger` is a declared tool (`run_tick`)

Decided on its own merits by Andrey after being told the cost: it makes a
money-spending call dispatchable by name by any peer or MCP client that
reaches the bridge. Not a side effect of wanting a button.

---

## Traps that already bit, so they are worth knowing

- **`/gate` is a separate read from `tick` on purpose.** A page polls it.
  If asking ran a tick, *looking at the dashboard would spend money.*
- **Two doors into markup.** Everything entering the DOM goes through
  `escapeText()` or `num()`, enforced mechanically by
  `test_untrusted_text_is_never_set_as_markup`. Task bodies are written
  by other agents and by anything that reached `POST /inbox`; an
  `onerror` quoted back into an HTML-rendering panel runs on the node's
  origin, which also serves unauthenticated `POST /route`.
- **`package-data` in `pyproject.toml` is load-bearing.** setuptools
  ships no non-`.py` file unless told to; without it a non-editable
  `pip install .` omits `dashboard.html` and `/dashboard` 404s, which
  the node renders as "this unit has no dashboard" rather than as a
  packaging error. It does not bite in the container because units are
  *cloned*, which is a property of the Dockerfile, not of pu. This is
  now the **sixth page-tier requirement** in `UNIT_STANDARDS.md`.
- **Tab counts are tickets, never records.** Counting maps made the tab
  disagree with the containers it opens — one map counted as an item
  *and* drawn as the box around the items.
- **`aria-pressed` must be derived from state on every change.** It was
  written once while building the buttons, so the strip claimed the
  mount-time project forever. The visual desync is the smaller half: a
  screen reader had no second channel to notice it from.
- **`path_for_binary` reads the drive letter as a string**, never via
  `Path().drive`, which is `"C:"` on Windows and `""` on POSIX. The old
  version answered differently depending on which machine ran it, and
  its test could only pass on one of them.

---

## Cross-repo state — do not break these

- The node vendors **`docs/prototypes/specimen-spec.json`**, a *frozen*
  copy of pu's old spec. It is **not tracking pu and must never be
  refreshed from it.** It exists to exercise seven standard features
  (`pages`, drill-down, `record`, `field`, `ceiling_field`, `percent`,
  sourced `text`) that no live unit uses. pu's drift test is gone.
- **The node pins pu by commit** in `docker/units.build.yaml`, currently
  `7a345c04d106fb41f4a6fe2d048ad241bdb19022`. Send full shas from
  `git rev-parse HEAD` — never a short sha, never one retyped. I sent a
  fabricated eighth character once; their build check caught it.
- The gateway session **will not deploy without Andrey's approval**,
  and does not act on a relayed "he approved". Ask him in his own
  session, not through me.
- `docker compose restart` does **not** rebuild, and a failed build is
  quiet — it prints a URL and starts the old image. Confirm
  `Image holonic-node:latest Built` before `up -d`.

---

## Open, deliberately

- **Canvas fill ~49%.** Reads as spacing, not absence. If it ever needs
  fixing the answer is packing maps into columns when there is
  horizontal room — real layout work, not a zoom constant. Do not
  inflate the cards.
- **The chat write path.** See above.
- Nothing else. No known defects.

## Verify before claiming anything

```
python -m pytest -q          # 208
node tests/layout.test.mjs   # 15, needs node, not in CI
python scripts/tag_sop_lookup.py --validate-all
```

The page opens standalone in a browser and falls back to a sample graph,
so the layout is inspectable with no node running. Four rounds of defects
here were found by driving a real browser and **reading the markup**, not
by reading source or looking at screenshots. Two of them were only
findable because an earlier fix was correct.
