import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { cn } from "@/lib/utils"

/** The box a chart is given: charts draw at its real pixel size, so they fill their panel instead of letterboxing. */
function useSize() {
  const ref = useRef<HTMLDivElement>(null), [size, setSize] = useState<{ w: number; h: number }>()
  useLayoutEffect(() => {
    const el = ref.current!, ro = new ResizeObserver(([e]) => setSize({ w: Math.round(e.contentRect.width), h: Math.round(e.contentRect.height) }))
    ro.observe(el); return () => ro.disconnect()
  }, [])
  return [ref, size] as const
}

// Charts on the node's palette (J04): outcome slices in the status colours (done, nothing-to-do, partial, failed), bars in brand.
const SHADES = ["fill-status-ok", "fill-subtle", "fill-status-warn", "fill-status-down"]

/** Outcomes as a ring (R06's function status): share per outcome, total in the middle, legend with counts. */
function PieChart({ slices, total, label = "runs" }: { slices: [string, number][]; total: number; label?: string }) {
  const R = 70, C = 90; let a0 = -Math.PI / 2
  return (
    <figure className="m-0 flex flex-wrap items-center gap-6">
      <svg viewBox="0 0 180 180" className="size-[180px] shrink-0" role="img" aria-label={slices.map(([k, v]) => `${k} ${v}`).join(", ")}>
        {slices.map(([k, v], i) => {
          if (total && v === total) return <circle key={k} cx={C} cy={C} r={R} className={SHADES[i % SHADES.length]} />
          const a1 = a0 + (total ? v / total : 0) * 2 * Math.PI, large = a1 - a0 > Math.PI ? 1 : 0
          const d = `M${C} ${C} L${C + R * Math.cos(a0)} ${C + R * Math.sin(a0)} A${R} ${R} 0 ${large} 1 ${C + R * Math.cos(a1)} ${C + R * Math.sin(a1)} Z`
          a0 = a1
          return <path key={k} d={d} className={cn(SHADES[i % SHADES.length], "stroke-card")} strokeWidth="2" />
        })}
        <circle cx={C} cy={C} r="42" className="fill-card" />
        <text x={C} y={C} textAnchor="middle" className="fill-foreground text-xl">{total}</text>
        <text x={C} y={C + 18} textAnchor="middle" className="fill-muted-foreground text-xs">{label}</text>
      </svg>
      <figcaption className="grid grid-cols-[max-content_max-content] gap-x-4 gap-y-1 text-sm">
        {slices.map(([k, v], i) => (
          <div key={k} className="contents">
            <span className="flex items-center gap-2 text-foreground"><svg className="size-3" aria-hidden="true"><rect width="12" height="12" className={SHADES[i % SHADES.length]} /></svg>{k}</span>
            <span className="tabular-nums text-foreground">{v} · {total ? Math.round((v / total) * 100) : 0}%</span>
          </div>
        ))}
      </figcaption>
    </figure>
  )
}

/** Vertical bars on one shared frame (same height and axis inset), so two charts side by side line up on x and y. */
function BarChart({ bars, max, format, label }: { bars: [string, number][]; max: number; format: (v: number) => string; label: string }) {
  const [ref, size] = useSize()
  const W = size?.w ?? 560, H = (size?.h ?? 182) - 22, L = 44, B = 22, bw = (W - L) / Math.max(bars.length, 1), pad = Math.min(24, bw * 0.18)
  return (
    <div ref={ref} className="min-h-[200px] w-full flex-1">{size && <svg width={W} height={H + B} className="block" role="img" aria-label={`${label}: ${bars.map(([k, v]) => `${k} ${format(v)}`).join(", ")}`}>
      <line x1={L} y1="0" x2={L} y2={H} fill="none" className="stroke-border" />
      <line x1={L} y1={H} x2={W} y2={H} fill="none" className="stroke-border" />
      <text x={L - 6} y="12" textAnchor="end" className="fill-muted-foreground text-[11px]">{format(max)}</text>
      <text x={L - 6} y={H} textAnchor="end" className="fill-muted-foreground text-[11px]">0</text>
      {bars.map(([k, v], i) => { const h = max ? (v / max) * (H - 20) : 0, x = L + i * bw; return (
        <g key={k} fill="none">
          <rect x={x + pad} y={H - h} width={Math.max(0, bw - 2 * pad)} height={h} className="fill-primary/20" />
          <line x1={x + pad} x2={x + bw - pad} y1={H - h} y2={H - h} fill="none" className="stroke-primary" strokeWidth="2" />
          <text x={x + bw / 2} y={H - h - 4} textAnchor="middle" className="fill-muted-foreground text-[11px] tabular-nums">{format(v)}</text>
          <text x={x + bw / 2} y={H + 16} textAnchor="middle" className="fill-muted-foreground text-[11px]">{k}</text>
        </g>
      ) })}
    </svg>}</div>
  )
}

/** Monotone cubic interpolation (Fritsch–Carlson): a smooth line that never overshoots the data, so no false peaks. */
function monotone(ys: number[]) {
  const n = ys.length, d = ys.slice(1).map((y, i) => y - ys[i]), m = ys.map((_, i) => (i === 0 ? d[0] : i === n - 1 ? d[n - 2] : d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2) ?? 0)
  for (let i = 0; i < n - 1; i++) { if (!d[i]) { m[i] = m[i + 1] = 0; continue } const a = m[i] / d[i], b = m[i + 1] / d[i], h = a * a + b * b; if (h > 9) { const k = 3 / Math.sqrt(h); m[i] = k * a * d[i]; m[i + 1] = k * b * d[i] } }
  return (t: number) => {
    if (n < 2) return ys[0] ?? 0
    const i = Math.min(Math.floor(t), n - 2), f = t - i, f2 = f * f, f3 = f2 * f
    return (2 * f3 - 3 * f2 + 1) * ys[i] + (f3 - 2 * f2 + f) * m[i] + (-2 * f3 + 3 * f2) * ys[i + 1] + (f3 - f2) * m[i + 1]
  }
}

const N = 121 // samples per line: every series length used (7, 13, 31) lands on a sample, so the line keeps its peaks
/** A value over time as a filled line. On a new series (window switch, fresh data) the line morphs in place on
 *  --duration-overlay (CSS `d`), never redraws from zero; the highest point is named. */
function AreaChart({ points, format, label }: { points: [string, number][]; format: (v: number) => string; label: string }) {
  const [ref, size] = useSize(), key = JSON.stringify(points), last = useRef(key), changed = last.current !== key
  useEffect(() => { last.current = key }, [key])
  const W = size?.w ?? 560, Hh = size?.h ?? 200, L = 48, R = 16, T = 28, B = 24, pw = W - L - R, ph = Hh - T - B, n = points.length
  const top = Math.max(...points.map(p => p[1]), 0) * 1.15 || 1, peak = points.reduce((a, p, i) => (p[1] > points[a][1] ? i : a), 0)
  const at = monotone(points.map(p => p[1])), [hover, setHover] = useState<number>()
  const xy = (j: number, v: number) => [L + (n < 2 ? 0 : (j / (n - 1)) * pw), T + (1 - v / top) * ph] as const
  const line = Array.from({ length: N }, (_, k) => { const t = (k / (N - 1)) * (n - 1); const [x, y] = xy(t, at(t)); return `${k ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}` }).join("")
  const area = `${line}L${L + pw} ${T + ph}L${L} ${T + ph}Z`, morph = { transitionProperty: changed ? "d" : "none", transitionDuration: "var(--duration-overlay)", transitionTimingFunction: "var(--ease-out)" } // only new data morphs; a resize just redraws
  const ticks = Array.from(new Set(Array.from({ length: Math.min(n, 7) }, (_, k) => Math.round((k / Math.max(Math.min(n, 7) - 1, 1)) * (n - 1)))))
  const [px, py] = xy(peak, points[peak]?.[1] ?? 0)
  return (
    <div ref={ref} className="min-h-[200px] w-full flex-1">{size && n > 0 && (
      <svg width={W} height={Hh} tabIndex={0} className="block rounded-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring" role="img"
        onPointerMove={e => { const x = e.clientX - e.currentTarget.getBoundingClientRect().left; setHover(Math.max(0, Math.min(n - 1, Math.round(((x - L) / pw) * (n - 1))))) }}
        onPointerLeave={() => setHover(undefined)} onBlur={() => setHover(undefined)}
        onKeyDown={e => { const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0; if (d) { e.preventDefault(); setHover(h => Math.max(0, Math.min(n - 1, (h ?? peak) + d))) } }} aria-label={`${label}: ${points.map(([k, v]) => `${k} ${format(v)}`).join(", ")}; highest ${points[peak][0]} ${format(points[peak][1])}`}>
        <line x1={L} y1={T} x2={L + pw} y2={T} fill="none" strokeDasharray="3 4" className="stroke-border" />
        <line x1={L} y1={T + ph / 2} x2={L + pw} y2={T + ph / 2} fill="none" strokeDasharray="3 4" className="stroke-border" />
        <line x1={L} y1={T + ph} x2={L + pw} y2={T + ph} fill="none" className="stroke-border" />
        <text x={L - 6} y={T + 4} textAnchor="end" className="fill-muted-foreground text-[11px] tabular-nums">{format(top)}</text>
        <text x={L - 6} y={T + ph / 2 + 4} textAnchor="end" className="fill-muted-foreground text-[11px] tabular-nums">{format(top / 2)}</text>
        <text x={L - 6} y={T + ph + 4} textAnchor="end" className="fill-muted-foreground text-[11px]">0</text>
        <path d={area} style={{ d: `path("${area}")`, ...morph }} className="fill-primary/15" />
        <path d={line} style={{ d: `path("${line}")`, ...morph }} fill="none" className="stroke-primary" strokeWidth="2" strokeLinejoin="round" />
        {ticks.map(j => { const [x] = xy(j, 0); return <text key={j} x={x} y={T + ph + 16} textAnchor={j === 0 ? "start" : j === n - 1 ? "end" : "middle"} className="fill-muted-foreground text-[11px]">{points[j][0]}</text> })}
        <circle cx={px} cy={py} r="4" className="fill-primary stroke-card" strokeWidth="2" />
        {hover === undefined && <text x={Math.min(Math.max(px, L + 60), L + pw - 60)} y={py - 10} textAnchor="middle" className="fill-foreground text-[11px] tabular-nums">highest · {points[peak][0]} · {format(points[peak][1])}</text>}
        {hover !== undefined && (() => { const [hx, hy] = xy(hover, points[hover][1]), tx = Math.min(Math.max(hx, L + 56), L + pw - 56); return (
          <g aria-hidden="true">
            <line x1={hx} y1={T} x2={hx} y2={T + ph} fill="none" className="stroke-muted-foreground" strokeDasharray="2 3" />
            <circle cx={hx} cy={hy} r="4" className="fill-primary stroke-card" strokeWidth="2" />
            <rect x={tx - 52} y={Math.max(hy - 34, 0)} width="104" height="22" rx="3" className="fill-card stroke-border" />
            <text x={tx} y={Math.max(hy - 34, 0) + 15} textAnchor="middle" className="fill-foreground text-[11px] tabular-nums">{points[hover][0]} · {format(points[hover][1])}</text>
          </g>) })()}
      </svg>)}</div>
  )
}

/** One trigger's yield: runs that went ahead vs expired, as a split bar with the counts in words. */
function YieldBar({ name, href, fires, expired, max = 10 }: { name: string; href: string; fires: number; expired: number; max?: number }) {
  return (
    <div className="flex flex-col gap-1 border-b border-border py-2 text-sm">
      <span><a href={href} className="text-foreground underline-offset-4 hover:underline">{name}</a> <span className="text-muted-foreground">· {fires} fires · {fires - expired} ran · {expired} expired</span></span>
      <span className="flex h-2.5" aria-hidden="true">
        <span className="border-t-2 border-primary bg-primary/20" style={{ width: `${((fires - expired) / Math.max(max, 1)) * 100}%` }} />
        <span className="bg-status-down" style={{ width: `${(expired / Math.max(max, 1)) * 100}%` }} />
      </span>
    </div>
  )
}

export { PieChart, BarChart, AreaChart, YieldBar, monotone }
