import { useId, useMemo, useRef, useState } from "react"
import { cn } from "@/lib/utils"
import { StatusIcon, type StatusKind } from "@/components/ui/status-icon"
import { PanZoom, orderColumns, countCrossings } from "@/components/ui/pan-zoom"

// The Sessions relations graph (GitHub-Actions style): triggers → session types → latest runs, on pu's pan/zoom board
// (fixed frame, no scrollbars). Columns are ordered by pu's median sweeps so lines cross as little as possible; a node
// can be dragged up or down inside its own column, or moved with Alt+↑/↓, to reorder it (kept per viewer in this
// browser, never in pu). A saved order is merged with live data on every render: gone ids drop, new ids join.
export type FlowNode = { id: string; label: string; sub?: string; href: string; status: StatusKind }
export type FlowColumn = { title: string; nodes: FlowNode[] }

const COL_W = 240, GAP_X = 96, ROW_H = 54, HEAD = 36, PAD = 16, NODE_H = 44

const load = (key?: string): string[][] | null => {
  try { const v = key ? JSON.parse(localStorage.getItem(key) ?? "null") : null; return Array.isArray(v) && v.every(c => Array.isArray(c)) ? v : null } catch { return null }
}
const save = (key: string | undefined, v: string[][] | null) => {
  try { if (key) { if (v) localStorage.setItem(key, JSON.stringify(v)); else localStorage.removeItem(key) } } catch { /* private window: the order lasts this visit */ }
}
/** Keep the viewer's order for ids that still exist; ids pu added since join in the automatic order. */
const merge = (manual: string[][] | null, auto: string[][]) =>
  manual ? auto.map((col, i) => { const keep = (manual[i] ?? []).filter(id => col.includes(id)); return [...keep, ...col.filter(id => !keep.includes(id))] }) : auto

function LiveGraph({ columns, edges, frameHeight = 560, orderKey }: { columns: FlowColumn[]; edges: [string, string][]; frameHeight?: number; orderKey?: string }) {
  const auto = useMemo(() => orderColumns(columns.map(c => c.nodes.map(n => n.id)), edges).cols, [columns, edges])
  const [manual, setManual] = useState<string[][] | null>(() => load(orderKey))
  const order = useMemo(() => merge(manual, auto), [manual, auto])
  const [drag, setDrag] = useState<{ id: string; dy: number } | null>(null)
  const inner = useRef<HTMLDivElement>(null), press = useRef<{ id: string; y0: number; moved: boolean } | null>(null)
  const hint = useId()
  const byId = new Map(columns.flatMap(c => c.nodes.map(n => [n.id, n])))
  const pos = new Map<string, [number, number]>()
  order.forEach((col, ci) => col.forEach((id, ri) => pos.set(id, [PAD + ci * (COL_W + GAP_X), HEAD + PAD + ri * ROW_H])))
  const width = PAD * 2 + columns.length * COL_W + (columns.length - 1) * GAP_X
  const height = HEAD + PAD * 2 + Math.max(1, ...order.map(c => c.length)) * ROW_H
  const crossings = countCrossings(order, edges)

  const commit = (col: number, id: string, to: number) => {
    const list = order[col].filter(x => x !== id); list.splice(Math.max(0, Math.min(list.length, to)), 0, id)
    const next = order.map((c, i) => (i === col ? list : c)); setManual(next); save(orderKey, next)
  }
  const scale = () => (inner.current?.getBoundingClientRect().height ?? height) / height
  // Pointer capture keeps the drag on this box even outside the window; cancel ends it cleanly.
  const handlers = (col: number, id: string) => ({
    onPointerDown: (e: React.PointerEvent<HTMLAnchorElement>) => { if (e.button !== 0) return; e.currentTarget.setPointerCapture(e.pointerId); press.current = { id, y0: e.clientY, moved: false } },
    onPointerMove: (e: React.PointerEvent) => { const p = press.current; if (!p || p.id !== id) return; const dy = (e.clientY - p.y0) / scale(); if (!p.moved && Math.abs(dy) < 5) return; p.moved = true; setDrag({ id, dy }) },
    onPointerUp: (e: React.PointerEvent) => { const p = press.current; if (!p || p.id !== id) return; if (p.moved) commit(col, id, order[col].indexOf(id) + Math.round((e.clientY - p.y0) / scale() / ROW_H)); setDrag(null) },
    onPointerCancel: () => { press.current = null; setDrag(null) },
    onClick: (e: React.MouseEvent) => { if (press.current?.moved) e.preventDefault(); press.current = null },
    onKeyDown: (e: React.KeyboardEvent) => { if (!e.altKey || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return; e.preventDefault(); commit(col, id, order[col].indexOf(id) + (e.key === "ArrowUp" ? -1 : 1)) },
  })
  const y = (id: string) => pos.get(id)![1] + (drag?.id === id ? drag.dy : 0)
  return (
    <div className="flex flex-col gap-2">
      <PanZoom width={width} height={height} frameHeight={frameHeight} label="Triggers, session types and runs" viewKey={orderKey}>
        <div ref={inner} className="absolute inset-0">
          <svg width={width} height={height} className="absolute inset-0" aria-hidden="true">
            {edges.filter(([a, b]) => pos.has(a) && pos.has(b)).map(([a, b]) => {
              const s = pos.get(a)![0] + COL_W, x2 = pos.get(b)![0], m = (s + x2) / 2, y1 = y(a) + NODE_H / 2, y2 = y(b) + NODE_H / 2
              return <path key={`${a}-${b}`} d={`M${s} ${y1} C${m} ${y1} ${m} ${y2} ${x2} ${y2}`} fill="none" className="stroke-muted-foreground" />
            })}
          </svg>
          {columns.map((c, ci) => (
            <h3 key={c.title} className="absolute m-0 text-xs font-medium uppercase tracking-wider text-muted-foreground" style={{ left: PAD + ci * (COL_W + GAP_X), top: PAD }}>{c.title} · {c.nodes.length}</h3>
          ))}
          {order.flatMap((col, ci) => col.map(id => { const n = byId.get(id); if (!n) return []; const [x] = pos.get(id)!; return [(
            <a key={id} data-node="" draggable={false} href={n.href} title={n.sub ? `${n.label} · ${n.sub}` : n.label} aria-describedby={hint}
              {...handlers(ci, id)} style={{ left: x, top: y(id), width: COL_W, height: NODE_H }}
              className={cn("absolute flex cursor-grab touch-none items-center gap-2 rounded-md border border-border bg-card px-2 text-xs text-card-foreground no-underline outline-none transition-colors duration-(--duration-fast) ease-(--ease-standard) hover:border-foreground hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring",
                drag?.id === id && "z-10 cursor-grabbing border-foreground")}>
              <StatusIcon status={n.status} />
              <span className="flex min-w-0 flex-col leading-tight"><span className="font-medium">{n.label}</span>{n.sub && <span className="text-muted-foreground">{n.sub}</span>}</span>
            </a>
          )] }))}
        </div>
      </PanZoom>
      <p className="m-0 text-xs text-muted-foreground"><span id={hint}>{crossings} crossing{crossings === 1 ? "" : "s"} · drag a box up or down, or Alt+↑/↓, to reorder its column</span>{manual && <> · <button type="button" className="underline underline-offset-4" onClick={() => { setManual(null); save(orderKey, null) }}>reset order</button></>}</p>
    </div>
  )
}

export { LiveGraph }
