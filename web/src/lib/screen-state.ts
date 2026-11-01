import { useEffect, useState } from "react"
import { CONN } from "./live"

// A page renders one spec state at a time. ?state=… is the preview override (#/sessions?state=empty); without it
// the state comes from the poll: no good read yet → loading, then offline (sample data shown) if pu never answers;
// failures after a good read → stale, which keeps the last good data.
// The hook also marks the document ready after the first paint, so checks never measure a half-rendered page.
export type ScreenState = "default" | "empty" | "loading" | "error" | "stale" | "partial" | "permission" | "offline"

export const liveState = (): ScreenState =>
  CONN.mode === "live" ? (CONN.failures ? "stale" : "default") : CONN.failures ? "offline" : "loading"

export function useScreenState(): ScreenState {
  const read = () => (new URLSearchParams(location.hash.split("?")[1] ?? "").get("state") ?? "") as ScreenState | ""
  const [state, setState] = useState(read)
  useEffect(() => {
    const on = () => setState(read())
    addEventListener("hashchange", on)
    requestAnimationFrame(() => document.documentElement.setAttribute("data-ready", ""))
    return () => removeEventListener("hashchange", on)
  }, [])
  return state || liveState()
}
