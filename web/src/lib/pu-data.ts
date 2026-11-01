import { useEffect, useState } from "react"
import * as F from "@/fixtures/pu"

// What the pages read, and how it is fetched from pu (PRD "Data loading today", pu-b5 2026-10-31):
//  - only the scope in focus: GET tasks?project=<scope>&status=pending (prefix match covers sub-scopes; relative URL,
//    no leading slash, so it works at /dashboard and framed at /dashboard/pu/);
//  - the archive only on request: GET tasks?project=<scope>&status=completed, then …&status=deleted;
//  - polled every 10 s (backing off to 80 s, paused while the tab is hidden) and merged by uuid / run_id: unchanged
//    records keep their object identity, so React keeps their DOM; nothing refits or relayouts the reader's view.
// pu has no change feed yet (ticketed by pu-b5): the diff is client-side. Without a reachable pu the source is the
// fixture (shown as "sample data"); ?simulate=1 adds one task and one run per poll to watch the merge work.
const POLL_MS = 10_000, MAX_MS = 80_000

export type Fetch = (url: string) => Promise<Response>
/** One scoped read: never the whole store. */
export async function fetchScope(fetcher: Fetch, scope: string, status: "pending" | "completed" | "deleted" = "pending"): Promise<F.Task[]> {
  const r = await fetcher(`tasks?project=${encodeURIComponent(scope)}&status=${status}`)
  if (!r.ok) throw new Error(`tasks ${status}: HTTP ${r.status}`)
  return (await r.json()) as F.Task[]
}
/** Merge a fresh read into what is on screen: same id + same content keeps the old object (no re-render). */
export function mergeById<T extends object>(prev: Map<string, T>, next: T[], id: (x: T) => string): Map<string, T> {
  const out = new Map<string, T>()
  for (const x of next) { const k = id(x), old = prev.get(k); out.set(k, old && JSON.stringify(old) === JSON.stringify(x) ? old : x) }
  return out
}

const simulate = () => new URLSearchParams(location.search).has("simulate")
let tasks = new Map(F.TASKS.map(t => [t.id, t]))
let runs = new Map(F.RUNS.map(r => [r.id!, r]))
let tick = 0
const listeners = new Set<() => void>()

function poll() {
  if (!simulate()) return
  tick++
  const nextTasks = [...tasks.values(), { id: `sim${tick}`, title: `New ticket from a trigger #${tick}`, kind: "execution", project: "holonic-node.gateway", map: "m4", deps: [`g${Math.min(14, tick)}`], state: "blocked" as const, afk: true }]
  tasks = mergeById(tasks, nextTasks, t => t.id)
  runs = mergeById(runs, [{ id: `sim-r${tick}`, at: `11-01 0${tick % 10}:00`, type: "hn-watch", task: `HN: ${tick} new items`, taskId: "c1", outcome: "done", needsYou: "none", cost: "$0.02", duration: "0m 20s", tokens: "3.0k" }, ...runs.values()], r => r.id!)
  listeners.forEach(f => f())
}
let timer: number | undefined, delay = POLL_MS
function schedule() {
  clearTimeout(timer)
  timer = window.setTimeout(() => { if (!document.hidden) { try { poll(); delay = POLL_MS } catch { delay = Math.min(delay * 2, MAX_MS) } } schedule() }, delay)
}
function useSource() {
  const [, force] = useState(0)
  useEffect(() => {
    const f = () => force(n => n + 1)
    listeners.add(f); if (listeners.size === 1) schedule()
    const vis = () => { if (!document.hidden) poll() }
    document.addEventListener("visibilitychange", vis)
    return () => { listeners.delete(f); if (!listeners.size) clearTimeout(timer); document.removeEventListener("visibilitychange", vis) }
  }, [])
}

export const inScope = (project: string, scope: string) => project === scope || project.startsWith(scope + ".")
/** Tasks of the scope in focus: open work by default; with archive, done and cancelled too (requested only then). */
export function useScopeTasks(root: string, sub: string, archive: boolean): F.Task[] {
  useSource()
  const scope = sub === "all" ? root : `${root}.${sub}`
  return [...tasks.values()].filter(t => inScope(t.project, scope) && (archive || t.state !== "completed"))
}
export function useRuns(): F.Run[] { useSource(); return [...runs.values()] }
export const allTasks = () => [...tasks.values()]
