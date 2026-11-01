import { TaskNode } from "@/components/ui/task-node"
import { PanZoom, orderColumns } from "@/components/ui/pan-zoom"
import type { StatusKind } from "@/components/ui/status-icon"

// Maps as GitHub-Actions-style groups, tasks as nodes, edges left to right, on pu's pan/zoom board (no scrollbars).
// pu's layout guarantees, kept: layer = longest path from a root (a task is drawn after everything blocking it);
// an edge spanning several layers gets a dummy at every layer it crosses, so it reserves a gap and never cuts a task;
// rows are ordered by pu's median sweeps and the crossings left are reported, not hidden.
export type GraphTask = { id: string; title: string; status: StatusKind; deps: string[] }
export type GraphMap = { id: string; title: string; done: number; total: number; tasks: GraphTask[] }

const DX = 260, DY = 54, NODE_W = 200, NODE_H = 44, HEAD = 34, GAP = 16

function layoutMap(m: GraphMap) {
  const ids = new Set(m.tasks.map(t => t.id)), byId = new Map(m.tasks.map(t => [t.id, t]))
  // Cycles are real information (work that can never start): break each at a back edge, draw it apart, count it.
  const cut = new Set<string>(), seen = new Set<string>(), stack = new Set<string>()
  const dfs = (id: string) => { seen.add(id); stack.add(id); for (const d of byId.get(id)!.deps.filter(x => ids.has(x))) { if (stack.has(d)) cut.add(`${id}>${d}`); else if (!seen.has(d)) dfs(d) } stack.delete(id) }
  m.tasks.forEach(t => { if (!seen.has(t.id)) dfs(t.id) })
  const depsOf = (id: string) => byId.get(id)!.deps.filter(x => ids.has(x) && !cut.has(`${id}>${x}`))
  const memo = new Map<string, number>()
  const layer = (id: string): number => { if (memo.has(id)) return memo.get(id)!; const d = depsOf(id); const l = d.length ? 1 + Math.max(...d.map(layer)) : 0; memo.set(id, l); return l }
  const cols: string[][] = []
  const put = (id: string, l: number) => { (cols[l] ??= []).push(id) }
  m.tasks.forEach(t => put(t.id, layer(t.id)))
  const edges: [string, string][] = [], routes: string[][] = []
  for (const t of m.tasks) for (const d of depsOf(t.id)) {
    const chain = [d]
    for (let l = layer(d) + 1; l < layer(t.id); l++) { const dummy = `~${d}>${t.id}@${l}`; put(dummy, l); chain.push(dummy) }
    chain.push(t.id)
    for (let i = 1; i < chain.length; i++) edges.push([chain[i - 1], chain[i]])
    routes.push(chain)
  }
  const { cols: ordered, crossings } = orderColumns(cols.map(c => c ?? []), edges)
  return { cols: ordered, routes, crossings, cycles: [...cut].map(e => e.split(">") as [string, string]), rows: Math.max(1, ...ordered.map(c => c.length)), layers: ordered.length }
}

function TaskGraph({ maps, taskHref, mapHref, selected, frameHeight = 560, viewKey, completedShown = false }: {
  maps: GraphMap[]; taskHref: (id: string) => string; mapHref: (id: string) => string; selected?: string; frameHeight?: number; viewKey?: string; completedShown?: boolean
}) {
  let y = GAP, width = 0, crossings = 0, cycles: [string, string][] = []
  const pos = new Map<string, [number, number]>()
  const groups = maps.filter(m => m.tasks.length).map(m => {
    const L = layoutMap(m); crossings += L.crossings; cycles = [...cycles, ...L.cycles]
    L.cols.forEach((col, l) => col.forEach((id, r) => pos.set(id, [GAP + 8 + l * DX, y + HEAD + r * DY])))
    const g = { m, routes: L.routes, top: y, w: L.layers * DX - (DX - NODE_W), h: HEAD + L.rows * DY + 4 }
    width = Math.max(width, g.w + GAP * 3); y += g.h + GAP
    return g
  })
  const paths = groups.flatMap(g => g.routes.map(chain => {
    const pts = chain.map(id => pos.get(id)!)
    let d = `M${pts[0][0] + NODE_W} ${pts[0][1] + NODE_H / 2}`
    for (let i = 1; i < pts.length; i++) {
      const [px, py] = pts[i - 1], [x, yy] = pts[i], sx = i === 1 ? px + NODE_W : px + NODE_W / 2, ex = i === pts.length - 1 ? x : x + NODE_W / 2
      if (i > 1) d += ` L${sx} ${py + NODE_H / 2}`
      d += ` C${(sx + ex) / 2} ${py + NODE_H / 2} ${(sx + ex) / 2} ${yy + NODE_H / 2} ${ex} ${yy + NODE_H / 2}`
    }
    return <path key={chain.join(">")} d={d} fill="none" className="stroke-muted-foreground" />
  })).concat(cycles.filter(([a, b]) => pos.has(a) && pos.has(b)).map(([a, b]) => {
    // A cycle's broken edge: dashed, from the task back to the one it waits on.
    const [x1, y1] = pos.get(a)!, [x2, y2] = pos.get(b)!
    return <path key={`cycle-${a}-${b}`} d={`M${x1 + NODE_W / 2} ${y1} C${x1 + NODE_W / 2} ${Math.min(y1, y2) - 24} ${x2 + NODE_W / 2} ${Math.min(y1, y2) - 24} ${x2 + NODE_W / 2} ${y2}`} fill="none" strokeDasharray="4 3" className="stroke-status-down" />
  }))
  const tasks = groups.flatMap(g => g.m.tasks)
  return (
    <div className="flex flex-col gap-2">
      <PanZoom width={width} height={y} frameHeight={frameHeight} label="Dependency graph" viewKey={viewKey}>
        <svg width={width} height={y} className="absolute inset-0" aria-hidden="true">{paths}</svg>
        {groups.map(({ m, top, w, h }) => (
          <section key={m.id} aria-label={`Map ${m.title}`} className="absolute rounded-md border border-dashed border-border" style={{ left: GAP / 2, top, width: w + GAP * 2, height: h }}>
            <a data-node="" href={mapHref(m.id)} draggable={false} className="inline-block max-w-full truncate px-2 py-1 text-xs font-medium text-foreground hover:underline">
              {m.title} <span className="font-normal text-muted-foreground">· map · {m.done}/{m.total} done</span>
            </a>
          </section>
        ))}
        {tasks.map(t => { const [x, yy] = pos.get(t.id)!; return (
          <TaskNode key={t.id} title={t.title} status={t.status} href={taskHref(t.id)} selected={selected === t.id} className="absolute" style={{ left: x, top: yy }} />
        ) })}
      </PanZoom>
      <p data-block="honesty" className="m-0 text-xs text-muted-foreground">{crossings} crossing{crossings === 1 ? "" : "s"} · {cycles.length} cycle{cycles.length === 1 ? "" : "s"}{cycles.length ? " (dashed: work that can never start)" : ""} · {tasks.length} tasks in {groups.length} map{groups.length === 1 ? "" : "s"} · {completedShown ? "archive shown" : "completed hidden"}</p>
    </div>
  )
}

export { TaskGraph }
