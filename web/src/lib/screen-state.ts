import { useEffect } from "react"
import { CONN } from "./live"

// A page's state comes from the poll: no good read yet → loading, then offline (sample data shown) if pu never
// answers; failures after a good read → stale, which keeps the last good data. The other states (empty, error,
// partial, permission) are each block's own. The hook also marks the document ready after the first paint, so
// checks never measure a half-rendered page.
export type ScreenState = "default" | "empty" | "loading" | "error" | "stale" | "partial" | "permission" | "offline"

export const liveState = (): ScreenState =>
  CONN.mode === "live" ? (CONN.failures ? "stale" : "default") : CONN.failures ? "offline" : "loading"

export function useScreenState(): ScreenState {
  useEffect(() => { requestAnimationFrame(() => document.documentElement.setAttribute("data-ready", "")) }, [])
  return liveState()
}
