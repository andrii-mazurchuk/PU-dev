# Handoff — 2026-09-25 — pu's dashboard: done work, theme, refresh, nested scopes

> Previous handoff: `.claude/handoff/2026-09-11-dashboard-page-tier-and-ask.md`
> — that session built the page tier and the dependency graph. **Everything
> here is that same page**, five commits later. Read that one first: its
> section on the layout algorithm's three guarantees is still current and
> still the thing most likely to be "simplified" into a bug.

**2026-09-11 → 2026-09-22.** Five commits, `b43863e..4255881`, all on
`master`, all pushed, suites green. **Deployed and verified live** at
`4255881cc343b8a016d83b948d047e6a9597ec64`; the running node serves it
now.

Every change here started as Andrey using the page and finding it
wrong. None came from reading the code.

---

## Session summary

The page shipped working and then failed in five ways nobody predicted
from the source. Completed tickets were absent from the graph — not a
gap in the picture but a lie about it, since the page could not tell
"finished" from "never existed". It was pinned to one of the console's
ten palettes. Clicking a ticket threw away your zoom. Data went stale
until reloaded by hand. And fifteen flat project tabs hid the fact that
ten of them were one tree.

All five are fixed and live. The through-line worth keeping: **four of
the five were found by a person looking at the screen, and four of the
five were caused by code that read as obviously correct.**

---

## What's done

- **Done tickets are drawn** (`b43863e`). Two reads, merged — the store
  filters one status at a time. Done blockers stop counting as
  blockers, so the frontier advances when you close something.
- **The colour contract** (`fb99c30`). `<meta name="holonic-tokens">`,
  palette renamed to the node's names, state colours derived from
  `--ok`/`--warn`/`--down`/`--dim`. The node injects its resolved
  tokens; we implement nothing at runtime.
- **Selecting a ticket keeps your view** (`c23655f`). `openDock` no
  longer calls `fit()`.
- **The page refreshes itself** (`ddd8f34`). Ten-second poll, the eight
  rules in the node's `UNIT_STANDARDS.md` §"Staying current".
- **Nested project scopes** (`4255881`). Two-row strip: roots with
  subtree totals, then the selected root's scopes led by "all".

## Current state / open threads

Working tree clean, nothing unpushed, nothing broken.

- **Horizontal packing — deferred, and deliberately visible.** A layer
  of independent tickets stacks into one tall column, so a big scope
  draws narrow and tall with empty margins either side. `job-search-agent`
  at 40 tickets shows it. The gateway (standards owner) ruled: leave it,
  a legible-bad canvas tells Andrey the view outgrew the layout instead
  of hiding it behind a scroll. **Before building it, check `mu-spec`'s
  graph-layout revisit ticket — it may be the same job.**
- **One dead line**: `:root.dark { color-scheme: dark }` in
  `dashboard.html`. The node injects `color-scheme` itself now. Harmless;
  sweep it with the next change to that file.
- **Not pu, but live:** `holonic-node`'s manifest is modified and
  uncommitted on purpose — the pin-history commit is Andrey's to write.
  mu-agent's dashboard is broken (406, then `TypeError … reading
  'length'`); `a8d3ceb8` was not the fix, and its ticket has the evidence.

## ▶ Next step

**Nothing is queued. The session closed clean and Andrey said he is
moving to a new topic.** If work resumes on this page, in order:

1. **Horizontal packing**, but only once it actually obstructs reading a
   map — and only after checking whether `mu-spec`'s layout ticket makes
   it one job rather than two. It touches `assignLayers`/separation in
   `dashboard.html`, which carries three written-down guarantees; the
   tests in `tests/layout.test.mjs` grow with it rather than staying
   green.
2. Delete the dead `color-scheme` line while you are in the file.

## Files & references to read

- `pu/dashboard.html` — the whole feature. ~1500 lines, one file, no
  build step. Section markers divide LAYOUT / DATA / RENDER / INSPECTOR /
  DOCK,CHAT,VIEW / BOOT.
- `tests/layout.test.mjs` — extracts the layout functions *from the page*
  between `const NODE_GAP` and `const NODE_W =`. Not in CI (pu's CI is
  Python only). Run: `node tests/layout.test.mjs`.
- `tests/test_dashboard_page.py` — the page-tier contract, plus the
  source-level guards added this session for refresh and scopes.
- `C:\agents\holonic-node\docs\UNIT_STANDARDS.md` — §"Staying current"
  (the eight refresh rules), §"The colour contract", and the eight
  page-tier requirements. **This is the authority, not this file.**
- Previous handoff — the layout guarantees and the `ask` box decisions.

## Gotchas / warnings

- **The gateway does not act on a relayed approval, in either
  direction.** I told it "Andrey approved" and it refused, correctly.
  He has to say it in *that* session. I had stated this rule myself
  earlier in the same session and then broke it — do not repeat it.
- **Verify a sha, never assert one.** Send the full output of
  `git rev-parse HEAD`, pasted. A fabricated character has been sent
  before; their check caught it. Separately, Andrey misremembered a
  shipped sha (`39292…`) that exists nowhere — the gateway checked
  rather than trusting either of us.
- **Taskwarrior `project:` prefix-matches, and it is a live hazard.**
  A bulk `modify` on `project:holonic-node` silently caught
  `holonic-node.mu-spec` this week. The page's own `inScope` requires
  the dot, which guards *this page only* — nothing that calls
  `project:` directly.
- **`fit()` on anything but a first draw is a bug.** It recomputes
  scale and position from the whole scene, discarding reader state. It
  shipped once through `openDock`, then was nearly reintroduced by the
  refresh path reusing `select()`. Both are pinned by tests. There is
  exactly one `requestAnimationFrame(fit)` left and a test asserts that
  count.
- **A skipped poll must still reschedule.** Written wrong first time:
  the refresh would have died at the first bad moment while the page
  went on claiming to be live. A timer that stops silently is worse
  than no timer.
- **`known` (the new-work highlight set) is global.** Switching scope
  makes every node unseen; marking them all turns the highlight into
  decoration. `markFresh(g, keepView)` fires only on a refresh.
- **Two numbers for one thing is this page's recurring failure.** It
  has happened three times: tab-vs-container counts, done tickets, and
  the "all" chip. Any new count must equal what the canvas draws.
- **Browser-verify, and read the markup.** Every defect this session was
  found by driving a real page, never by reading source. Scratch
  harnesses (stub unit + Playwright, ~50 checks) are gone with the
  scratchpad; rebuild rather than trusting the source-level tests alone.
- **A teal sweep lies under one palette.** `instrument` light's brand
  *is* `#0e6e77`, so a correct result and a hardcoded literal look
  identical there. Resolve the host token and compare.

## Decisions made this session

- **Done tickets are drawn, not hidden** — a chain with its finished
  links cut out reads as though that work never happened, and the
  frontier becomes unreadable.
- **Done is drawn by removing ink, not adding a hue** — a fourth colour
  would compete with the three that carry actionable state.
- **Tab counts include done tickets** — the tab and the boxes it opens
  must be one number; the open/closed split goes on the map's meta line.
- **Domain colours derive from contract tokens, never literals** — a
  hex leaves "can start now" teal inside a moss console while every
  neutral around it moves, which looks deliberate and is worse than a
  uniform mismatch.
- **A refresh never refits; it reveals** — the smallest pan that
  uncovers the selected node, scale untouched.
- **Compare before touching the DOM, split by section** — the strip's
  cost figure drifts on its own, so hashing it together with the tasks
  would relay out the whole graph over a fraction of a cent.
- **A vanished selection is admitted, never reassigned** — the dock
  describing one task while the reader believes they are reading
  another is the failure worth naming.
- **Roots collapse ten scopes into one tab; prefix match, dot
  required** — otherwise `job-search-agent` swallows
  `job-search-agent-v2`.
- **No filter for a root's own tickets** — gateway's ruling: one
  instance (`job-search-agent`, 31 own + 9 in `jobscope`) is an argument
  for a filter nobody asked for, and the honest fix is usually splitting
  the scope, not adding a view.
