/* Checks on the layered-DAG layout inside pu/dashboard.html.
 *
 *   node tests/layout.test.mjs
 *
 * NOT part of the pytest suite and not in CI: this unit's CI is
 * Python only, and adding a Node toolchain to run one file costs
 * more than it returns. Run it by hand when the layout changes --
 * which is rare, and obvious when it happens.
 *
 * The functions are extracted from the page rather than copied
 * here. A copy would drift, and a drifted copy of a layout
 * algorithm passes its tests while the thing on screen is wrong.
 */
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const PAGE = new URL("../pu/dashboard.html", import.meta.url)
const source = readFileSync(PAGE, "utf-8")

/* The page marks its own sections. Taking the slice between the
   markers keeps this independent of everything below it, which
   touches `document` and would not run here. */
const start = source.indexOf("const NODE_GAP")
const end = source.indexOf("const NODE_W =")
assert.ok(start > 0 && end > start, "could not find the layout section in dashboard.html")

const NODE_W = 176 // read by layout(); defined below the slice
const { layout, countCrossings, breakCycles, assignLayers } = new Function(
  `const NODE_W = ${NODE_W};
   ${source.slice(start, end)}
   return { layout, countCrossings, breakCycles, assignLayers };`,
)()

const HEIGHT = 36
const sizeOf = () => HEIGHT
const nodes = (...ids) => ids.map((id) => ({ id }))
const edge = (from, to) => ({ from, to })

let passed = 0
const check = (name, fn) => {
  try {
    fn()
    console.log(`  ok   ${name}`)
    passed++
  } catch (e) {
    console.log(`  FAIL ${name}\n       ${e.message}`)
    process.exitCode = 1
  }
}

console.log("\nguarantee 1 — a task is drawn after everything blocking it")

check("a blocker is always in an earlier layer", () => {
  const g = layout(nodes("a", "b", "c", "d"),
    [edge("a", "b"), edge("b", "c"), edge("a", "d")], sizeOf)
  const at = new Map(g.nodes.map((n) => [n.id, n.layer]))
  assert.ok(at.get("a") < at.get("b"))
  assert.ok(at.get("b") < at.get("c"))
  assert.ok(at.get("a") < at.get("d"))
})

check("longest path, so no edge leaps backwards over a long chain", () => {
  // d is blocked by a 1-step chain and a 3-step one. Shortest-path
  // layering puts d at layer 1 and draws a backwards edge from c.
  const layers = assignLayers(nodes("a", "b", "c", "d"),
    [edge("a", "b"), edge("b", "c"), edge("c", "d"), edge("a", "d")])
  assert.equal(layers.get("d"), 3)
})

console.log("\nguarantee 2 — no node ever overlaps another")

check("a wide fan-out keeps a real gap between every pair", () => {
  const ids = Array.from({ length: 14 }, (_, i) => `t${i}`)
  const g = layout(nodes("root", ...ids), ids.map((id) => edge("root", id)), sizeOf)
  const byLayer = new Map()
  for (const n of g.nodes) byLayer.set(n.layer, [...(byLayer.get(n.layer) ?? []), n.y])
  for (const [, ys] of byLayer) {
    const sorted = [...ys].sort((a, b) => a - b)
    for (let i = 1; i < sorted.length; i++)
      assert.ok(sorted[i] - sorted[i - 1] >= HEIGHT,
        `overlap: ${(sorted[i] - sorted[i - 1]).toFixed(1)}px < ${HEIGHT}`)
  }
})

check("a deep graph with skip edges still has no overlaps", () => {
  const ids = Array.from({ length: 30 }, (_, i) => `n${i}`)
  const edges = []
  for (let i = 0; i < 30; i++) {
    if (i > 0) edges.push(edge(`n${i - 1}`, `n${i}`))
    if (i > 3) edges.push(edge(`n${i - 4}`, `n${i}`))
  }
  const g = layout(nodes(...ids), edges, sizeOf)
  const seen = new Map()
  for (const n of g.nodes) {
    for (const other of seen.get(n.layer) ?? [])
      assert.ok(Math.abs(other - n.y) >= HEIGHT, `two nodes overlap at layer ${n.layer}`)
    seen.set(n.layer, [...(seen.get(n.layer) ?? []), n.y])
  }
})

console.log("\nguarantee 3 — no edge is drawn across a node body")

check("a long edge bends once per layer it passes through", () => {
  const g = layout(nodes("a", "b", "c", "d"),
    [edge("a", "b"), edge("b", "c"), edge("c", "d"), edge("a", "d")], sizeOf)
  const long = g.routes.find((r) => r.edge.from === "a" && r.edge.to === "d")
  assert.equal(long.points.length, 4, "should bend at layers 1 and 2")
})

check("a one-layer edge stays a straight two-point line", () => {
  const g = layout(nodes("a", "b"), [edge("a", "b")], sizeOf)
  assert.equal(g.routes[0].points.length, 2)
})

check("the dummies actually reserve room, not just bend the line", () => {
  // A long edge passing a layer that also holds a real node must
  // not share that node's position.
  const g = layout(nodes("a", "b", "c", "d"),
    [edge("a", "d"), edge("a", "b"), edge("b", "c"), edge("c", "d")], sizeOf)
  const long = g.routes.find((r) => r.edge.from === "a" && r.edge.to === "d")
  const mid = long.points[1]
  const collides = g.nodes.some(
    (n) => n.layer === 1 && Math.abs(n.y - mid.y) < HEIGHT / 2)
  assert.ok(!collides, "the routed edge passes through a node body")
})

console.log("\ncrossings — minimised, counted honestly, never claimed to be zero")

check("counts a known crossing correctly", () => {
  const order = [["a", "b"], ["x", "y"]]
  const adjacency = new Map([["a", ["y"]], ["b", ["x"]], ["x", ["b"]], ["y", ["a"]]])
  assert.equal(countCrossings(order, adjacency), 1)
})

check("finds the zero-crossing ordering when one exists", () => {
  const g = layout(nodes("a1", "a2", "a3", "b1", "b2", "b3"),
    [edge("a1", "a2"), edge("a2", "a3"), edge("b1", "b2"), edge("b2", "b3")], sizeOf)
  assert.equal(g.crossings, 0)
})

check("admits the crossings it cannot remove", () => {
  // K3,3 is non-planar. No layout can draw it flat without a
  // crossing, so the honest answer is a number, not zero.
  const left = ["l1", "l2", "l3"], right = ["r1", "r2", "r3"]
  const g = layout(nodes(...left, ...right),
    left.flatMap((l) => right.map((r) => edge(l, r))), sizeOf)
  assert.ok(g.crossings > 0)
})

console.log("\ncycles — broken, flagged, never left to spin")

check("a dependency cycle is cut and reported", () => {
  const { reversed } = breakCycles(nodes("a", "b", "c"),
    [edge("a", "b"), edge("b", "c"), edge("c", "a")])
  assert.equal(reversed.length, 1)
})

check("a cyclic graph still lays out and terminates", () => {
  const g = layout(nodes("a", "b", "c"),
    [edge("a", "b"), edge("b", "c"), edge("c", "a")], sizeOf)
  assert.equal(g.nodes.length, 3)
  assert.equal(g.reversed.length, 1)
})

console.log("\ndegenerate shapes — a queue is empty far more often than it is full")

check("no nodes at all", () => {
  const g = layout([], [], sizeOf)
  assert.deepEqual(g.nodes, [])
  assert.equal(g.crossings, 0)
})

check("a single node", () => {
  const g = layout(nodes("only"), [], sizeOf)
  assert.equal(g.nodes.length, 1)
  assert.equal(g.nodes[0].layer, 0)
})

check("unrelated tasks share a layer without overlapping", () => {
  const g = layout(nodes("a", "b", "c"), [], sizeOf)
  assert.ok(g.nodes.every((n) => n.layer === 0))
  const ys = g.nodes.map((n) => n.y).sort((a, b) => a - b)
  assert.ok(ys[1] - ys[0] >= HEIGHT && ys[2] - ys[1] >= HEIGHT)
})

console.log(`\n${passed} checks passed\n`)
