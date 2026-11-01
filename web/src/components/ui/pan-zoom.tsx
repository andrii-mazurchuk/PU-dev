import { useCallback, useEffect, useRef, useState, type ReactNode } from "react"
import { cn } from "@/lib/utils"
import { boardView, rememberBoard } from "@/lib/store"

// The original pu canvas, kept (pu/dashboard.html "pan + zoom"): drag the empty board to pan in any direction,
// the wheel zooms around the pointer (0.25×–2.2×), +/−/fit in the corner, no scrollbars. A drag that starts on a node
// ([data-node]) is the node's, not the board's. Arrow keys pan and +/− zoom when the board has focus.
const MIN = 0.25, MAX = 2.2

function PanZoom({ width, height, frameHeight = 560, label, children, className, viewKey }: {
  width: number; height: number; frameHeight?: number; label: string; children: ReactNode; className?: string; viewKey?: string
}) {
  const frame = useRef<HTMLDivElement>(null)
  const saved = boardView(viewKey)
  const [view, setView] = useState(saved ?? { x: 0, y: 0, k: 1 })
  const fitted = useRef(!!saved)
  useEffect(() => { rememberBoard(viewKey, view) }, [viewKey, view])
  const [panning, setPanning] = useState(false)
  const fit = useCallback(() => {
    const c = frame.current; if (!c || !width || !height) return
    const k = Math.min(1.15, Math.max(0.7, Math.min((c.clientWidth - 80) / width, (c.clientHeight - 80) / height)))
    // As the original: centred across, anchored to the top unless the drawing fills the height.
    const drawn = height * k
    setView({ k, x: width * k > c.clientWidth - 32 ? 16 : (c.clientWidth - width * k) / 2, y: drawn > c.clientHeight - 120 ? Math.max(16, (c.clientHeight - drawn) / 2) : 28 })
  }, [width, height])
  // Fit once, on first open. New data never refits: the reader's pan and zoom are theirs (the original's rule).
  useEffect(() => { if (!fitted.current && width && height) { fitted.current = true; fit() } }, [fit, width, height])
  const zoomAt = (mx: number, my: number, factor: number) => setView(v => {
    const k = Math.min(MAX, Math.max(MIN, v.k * factor))
    return { k, x: mx - (mx - v.x) * (k / v.k), y: my - (my - v.y) * (k / v.k) }
  })
  useEffect(() => {
    const c = frame.current; if (!c) return
    const wheel = (e: WheelEvent) => { if (!e.deltaY) return; e.preventDefault(); const r = c.getBoundingClientRect(); zoomAt(e.clientX - r.left, e.clientY - r.top, e.deltaY < 0 ? 1.12 : 0.89) }
    c.addEventListener("wheel", wheel, { passive: false })
    return () => c.removeEventListener("wheel", wheel)
  }, [])
  const grab = useRef<{ x: number; y: number } | null>(null)
  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || (e.target as HTMLElement).closest("[data-node], button")) return
    e.currentTarget.setPointerCapture(e.pointerId); grab.current = { x: e.clientX - view.x, y: e.clientY - view.y }; setPanning(true)
  }
  const onPointerMove = (e: React.PointerEvent) => { const g = grab.current; if (g) setView(v => ({ ...v, x: e.clientX - g.x, y: e.clientY - g.y })) }
  const endPan = () => { grab.current = null; setPanning(false) }
  // Tabbing to a box outside the frame pans it into view (the frame itself never scrolls: overflow clip).
  const onFocus = (e: React.FocusEvent<HTMLDivElement>) => {
    const t = e.target.closest("[data-node]"); const c = frame.current; if (!t || !c) return
    const r = t.getBoundingClientRect(), f = c.getBoundingClientRect(), m = 24
    const dx = r.left < f.left + m ? f.left + m - r.left : r.right > f.right - m ? f.right - m - r.right : 0
    const dy = r.top < f.top + m ? f.top + m - r.top : r.bottom > f.bottom - m ? f.bottom - m - r.bottom : 0
    if (dx || dy) setView(v => ({ ...v, x: v.x + dx, y: v.y + dy }))
  }
  const onKeyDown = (e: React.KeyboardEvent) => {
    const step = 48, c = frame.current
    const keys: Record<string, () => void> = {
      ArrowLeft: () => setView(v => ({ ...v, x: v.x + step })), ArrowRight: () => setView(v => ({ ...v, x: v.x - step })),
      ArrowUp: () => setView(v => ({ ...v, y: v.y + step })), ArrowDown: () => setView(v => ({ ...v, y: v.y - step })),
      "=": () => c && zoomAt(c.clientWidth / 2, c.clientHeight / 2, 1.2), "+": () => c && zoomAt(c.clientWidth / 2, c.clientHeight / 2, 1.2), "-": () => c && zoomAt(c.clientWidth / 2, c.clientHeight / 2, 1 / 1.2), "0": fit,
    }
    if (keys[e.key] && e.target === e.currentTarget) { e.preventDefault(); keys[e.key]() }
  }
  const btn = "flex size-8 items-center justify-center rounded-md border border-border bg-card text-sm text-foreground hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring outline-none"
  return (
    <div className="flex flex-col gap-1">
    <div ref={frame} data-viewport="" role="region" aria-label={`${label}: drag to pan, scroll to zoom, arrow keys pan`} tabIndex={0}
      onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={endPan} onPointerCancel={endPan} onFocus={onFocus} onKeyDown={onKeyDown} data-panning={panning || undefined}
      className={cn("relative touch-none select-none overflow-clip overscroll-contain rounded-lg border border-border bg-muted outline-none focus-visible:ring-2 focus-visible:ring-ring",
        panning ? "cursor-grabbing" : "cursor-grab", className)}
      style={{ height: frameHeight }}>
      <div className="absolute left-0 top-0 origin-top-left" style={{ width, height, transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }}>{children}</div>
      <div className="absolute right-2 top-2 z-10 flex gap-1">
        <button type="button" className={btn} aria-label="Zoom in" onClick={() => { const c = frame.current; if (c) zoomAt(c.clientWidth / 2, c.clientHeight / 2, 1.2) }}>+</button>
        <button type="button" className={btn} aria-label="Zoom out" onClick={() => { const c = frame.current; if (c) zoomAt(c.clientWidth / 2, c.clientHeight / 2, 1 / 1.2) }}>−</button>
        <button type="button" className={btn} aria-label="Fit to view" onClick={fit}>⤢</button>
      </div>
    </div>
    {/* below the board, never over it: the board's content can reach any edge */}
    <p className="m-0 text-xs text-muted-foreground">drag to pan · scroll to zoom · arrow keys pan</p>
    </div>
  )
}

/** pu's own ordering (dashboard.html reduceCrossings): median heuristic (Eades & Wells), 12 alternating sweeps; a node
 *  takes the median position of its neighbours in the fixed adjacent column (mean of the two middle ones for an even
 *  count), a node without neighbours keeps its place; every sweep is scored and the best order kept; stops at 0. */
function orderColumns(cols: string[][], edges: [string, string][], sweeps = 12): { cols: string[][]; crossings: number } {
  let cur = cols.map(c => [...c]), best = cur.map(c => [...c]), bestScore = countCrossings(cur, edges)
  const median = (xs: number[]) => { const s = [...xs].sort((p, q) => p - q), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2 }
  const reorder = (c: number, side: -1 | 1) => {
    const fixed = new Map(cur[c + side].map((id, i) => [id, i])), nb = new Map<string, number[]>()
    for (const [a, b] of edges) {
      const [mine, other] = side === -1 ? [b, a] : [a, b]
      if (fixed.has(other)) nb.set(mine, [...(nb.get(mine) ?? []), fixed.get(other)!])
    }
    cur[c] = cur[c].map((id, i) => [id, nb.get(id)?.length ? median(nb.get(id)!) : i] as [string, number]).sort((p, q) => p[1] - q[1]).map(p => p[0])
  }
  for (let sw = 0; sw < sweeps && bestScore > 0; sw++) {
    if (sw % 2 === 0) for (let c = 1; c < cur.length; c++) reorder(c, -1)
    else for (let c = cur.length - 2; c >= 0; c--) reorder(c, 1)
    const score = countCrossings(cur, edges)
    if (score < bestScore) { bestScore = score; best = cur.map(c => [...c]) }
  }
  return { cols: best, crossings: bestScore }
}

function countCrossings(cols: string[][], edges: [string, string][]): number {
  const at = new Map(cols.flatMap((c, ci) => c.map((id, i) => [id, [ci, i]] as [string, [number, number]])))
  const es = edges.map(([a, b]) => [at.get(a), at.get(b)]).filter((e): e is [[number, number], [number, number]] => !!e[0] && !!e[1] && e[1][0] === e[0][0] + 1)
  let n = 0
  for (let i = 0; i < es.length; i++) for (let j = i + 1; j < es.length; j++) {
    const [[c1, a1], [, b1]] = es[i], [[c2, a2], [, b2]] = es[j]
    if (c1 === c2 && (a1 - a2) * (b1 - b2) < 0) n++
  }
  return n
}

export { PanZoom, orderColumns, countCrossings }
