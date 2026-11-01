import { useState } from "react"
import { cn } from "@/lib/utils"

// pu's own view switch (the node's chrome sits above; this is not navigation chrome, it is pu's hash route).
const VIEWS: [string, string][] = [["Tasks", "tasks"], ["Sessions", "sessions"], ["Analytics", "analytics"], ["Health", "health"]]

function ViewTabs({ current }: { current: string }) {
  return (
    <nav aria-label="pu views" className="flex gap-1 overflow-x-auto">
      {VIEWS.map(([label, id]) => (
        <a key={id} href={`#/${id}`} aria-current={id === current ? "page" : undefined}
          className={cn("whitespace-nowrap rounded-md border border-border px-3 py-1 text-sm text-foreground no-underline transition-colors duration-(--duration-fast) ease-(--ease-standard) hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring outline-none",
            id === current && "bg-secondary font-semibold")}>
          {label}
        </a>
      ))}
    </nav>
  )
}

export type Status = { needYou: number; gate: string; gateReason: string; running: number; paused: boolean; today: number; updated: string }

/** One-reading status: does anything need me, and is pu working. Each figure links to where it is explained. */
function StatusStrip({ status, state = "default" }: { status: Status; state?: "default" | "loading" | "stale" | "error" }) {
  const item = "flex items-baseline gap-1 text-sm text-foreground"
  if (state === "loading") return (
    <section role="status" aria-busy="true" aria-label="loading status" className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-border bg-card px-4 py-2">
      <span className={item}><b className="text-lg text-muted-foreground">—</b> need you</span><span className={item}>Gate <b className="text-muted-foreground">—</b></span><span className="text-xs text-muted-foreground">loading…</span>
    </section>
  )
  return (
    <section aria-label="pu status" className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-border bg-card px-4 py-2">
      <a href="#/attention" className={cn(item, "underline-offset-4 hover:underline")}><b className="text-lg tabular-nums">{status.needYou}</b> need you</a>
      <span className={item}>Gate <b>{state === "error" ? "unknown" : status.gate}</b></span>
      <a href="#/run" className={cn(item, "underline-offset-4 hover:underline")}>Running <b className="tabular-nums">{status.running}</b></a>
      <span className={item}>Paused <b>{status.paused ? "yes" : "no"}</b></span>
      <span className={item}>Today <b className="tabular-nums">${status.today.toFixed(2)}</b></span>
      <span className="text-xs text-muted-foreground">{state === "stale" ? `updated ${status.updated} · retrying` : state === "error" ? "status could not be read" : `updated ${status.updated}`}</span>
    </section>
  )
}

/** Today's scope architecture (kept by Andrey): root tabs by open count, the tail folded into "more", a quieter sub row. */
function ScopeBar({ roots, subs, root, sub, shown = 7, onRoot, onSub }: {
  roots: [string, number][]; subs: [string, number][]; root: string; sub: string; shown?: number; onRoot?: (r: string) => void; onSub?: (s: string) => void
}) {
  const [more, setMore] = useState(false), visible = more ? roots : roots.slice(0, shown)
  const btn = "whitespace-nowrap rounded-md border px-2.5 py-0.5 text-sm text-foreground hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring outline-none"
  return (
    <div className="flex flex-col gap-2">
      <div role="group" aria-label="Projects" className="flex gap-1 overflow-x-auto">
        {visible.map(([n, c]) => (
          <button key={n} type="button" aria-pressed={n === root} onClick={() => onRoot?.(n)} className={cn(btn, "border-border", n === root && "bg-secondary font-semibold")}>{n} <span className="tabular-nums text-muted-foreground">{c}</span></button>
        ))}
        {roots.length > shown && <button type="button" className={cn(btn, "border-border")} aria-expanded={more} onClick={() => setMore(m => !m)}>{more ? "fewer ▴" : `more · ${roots.length - shown} ▾`}</button>}
      </div>
      {subs.length > 0 && (
        <div role="group" aria-label={`Scopes of ${root}`} className="flex gap-1 overflow-x-auto">
          {[["all", 0] as [string, number], ...subs].map(([n, c]) => (
            <button key={n} type="button" aria-pressed={n === sub} onClick={() => onSub?.(n)} className={cn(btn, "border-transparent text-xs", n === sub && "border-border bg-muted")}>{n}{c ? <span className="ml-1 tabular-nums text-muted-foreground">{c}</span> : null}</button>
          ))}
        </div>
      )}
    </div>
  )
}

export { ViewTabs, StatusStrip, ScopeBar }
