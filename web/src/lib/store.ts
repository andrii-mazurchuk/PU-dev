import { useSyncExternalStore } from "react"

// View state that must survive moving between a view and its panels (#/tasks ↔ #/task?id=…): the scope in focus,
// archive, filters, switch states the reader set, and each board's pan/zoom. Lives for the page visit; pu never sees it.
export type BoardView = { x: number; y: number; k: number }
export type ViewState = {
  root: string; sub: string; archive: boolean
  typeFilter: Record<"role" | "grant" | "model", string>
  runFilter: Record<"type" | "outcome", string>
  window: string
  switches: Record<string, { on: boolean; pending?: boolean }>
  paused: boolean
}
// Scope and archive live in pu's own hash (#/tasks?root=…&sub=…&archive=1), so a reload or Back keeps them.
const hashQuery = () => new URLSearchParams(location.hash.split("?")[1] ?? "")
const fromHash = () => { const q = hashQuery(); return { root: q.get("root") ?? "holonic-node", sub: q.get("sub") ?? "all", archive: q.get("archive") === "1" } }
/** The query that keeps the scope when moving between Tasks and its panels. */
export const scopeQuery = (s: Pick<ViewState, "root" | "sub" | "archive">) => `root=${encodeURIComponent(s.root)}&sub=${encodeURIComponent(s.sub)}${s.archive ? "&archive=1" : ""}`
let state: ViewState = {
  ...fromHash(),
  typeFilter: { role: "all", grant: "all", model: "all" }, runFilter: { type: "all", outcome: "all" },
  window: "7 days", switches: {}, paused: false,
}
const listeners = new Set<() => void>()
export const setViewState = (patch: Partial<ViewState> | ((s: ViewState) => Partial<ViewState>)) => {
  const p = typeof patch === "function" ? patch(state) : patch
  state = { ...state, ...p }
  if (("root" in p || "sub" in p || "archive" in p) && location.hash.startsWith("#/tasks")) {
    const q = hashQuery(); new URLSearchParams(scopeQuery(state)).forEach((v, k) => q.set(k, v)); if (!state.archive) q.delete("archive")
    history.replaceState(null, "", `#/tasks?${q}`)
  }
  listeners.forEach(f => f())
}
export const useViewState = () => useSyncExternalStore(f => { listeners.add(f); return () => { listeners.delete(f) } }, () => state)

// Board views are written on every pointer move: kept outside React so panning never re-renders the page.
const boards = new Map<string, BoardView>()
export const boardView = (key?: string) => (key ? boards.get(key) : undefined)
export const rememberBoard = (key: string | undefined, v: BoardView) => { if (key) boards.set(key, v) }

/** Cycle a filter chip through its values ("all" first). */
export const cycle = (values: string[], current: string) => values[(values.indexOf(current) + 1) % values.length]
