# Skills shipped by this unit

Installed globally with `scripts/install_skill.py`, because there is one
shared queue and one set of efforts — a per-repo copy would be N copies of
one fact.

## Ours

| skill | what it is |
|---|---|
| `pu-tasks` | how to read and write the queue; also the tracker doc `wayfinder` looks for |
| `setup-project` | prepare a working directory to take part |

## Vendored unchanged

Copied verbatim from [mattpocock/skills](https://github.com/mattpocock/skills),
MIT licensed, © 2026 Matt Pocock. Full licence text below.

| skill | why unchanged |
|---|---|
| `wayfinder` | the effort model itself. Its body is the thing we adapt *around* — the tracker doc is the graft point, so this file never needs editing |
| `grilling` | pure conversation. Writes nothing, assumes nothing about the repo |

Keeping these byte-identical is deliberate: anything we change here is
something we have to re-merge every time upstream moves.

## Forked and adapted

Same licence and origin; each carries a note at the bottom saying exactly
what changed and why.

| skill | changed |
|---|---|
| `research` | findings resolve the ticket instead of becoming a file placed by repo convention — the processing unit cannot write files, so the resolution is the only form both it and a human session can produce |
| `prototype` | refuses to run without a repository to live in; the verdict resolves the ticket rather than being posted to a tracker-specific issue |
| `domain-modeling` | ADRs removed (a resolved ticket already carries the reasoning, and a second decision log drifts); multi-context layouts removed as unused; added why vocabulary discipline is load-bearing for a derived-claim specification |

---

MIT License

Copyright (c) 2026 Matt Pocock

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
