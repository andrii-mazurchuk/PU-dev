// How tasks are read from pu (PRD "Data loading today", pu-b5 2026-10-31):
//  - only the scope in focus: GET tasks?project=<scope>&status=pending (prefix match covers sub-scopes; relative URL,
//    no leading slash, so it works at /dashboard and framed at /dashboard/pu/);
//  - the archive only on request: GET tasks?project=<scope>&status=completed, then …&status=deleted;
//  - merged by uuid / run_id: unchanged records keep their object identity, so React keeps their DOM.
// pu has no change feed yet (ticketed): the diff is client-side. The poll itself lives in ./live.

export type Fetch = (url: string) => Promise<Response>
/** One scoped read: never the whole store. pu answers {tasks: [...]}. */
export async function fetchScope<T>(fetcher: Fetch, scope: string, status: "pending" | "completed" | "deleted" = "pending"): Promise<T[]> {
  const r = await fetcher(`tasks?project=${encodeURIComponent(scope)}&status=${status}`)
  if (!r.ok) throw new Error(`tasks ${status}: HTTP ${r.status}`)
  return ((await r.json()) as { tasks: T[] }).tasks
}
/** Merge a fresh read into what is on screen: same id + same content keeps the old object (no re-render). */
export function mergeById<T extends object>(prev: Map<string, T>, next: T[], id: (x: T) => string): Map<string, T> {
  const out = new Map<string, T>()
  for (const x of next) { const k = id(x), old = prev.get(k); out.set(k, old && JSON.stringify(old) === JSON.stringify(x) ? old : x) }
  return out
}

export const inScope = (project: string, scope: string) => project === scope || project.startsWith(scope + ".")
