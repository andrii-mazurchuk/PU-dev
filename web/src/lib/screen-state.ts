import { useEffect, useState } from "react"

// Step 6 pages render one spec state at a time from mock fixtures: #/orders?state=empty.
// The hook also marks the document ready after the first paint, so checks never measure a half-rendered page.
export type ScreenState = "default" | "empty" | "loading" | "error" | "stale" | "partial" | "permission" | "offline"

export function useScreenState(): ScreenState {
  const read = () => (new URLSearchParams(location.hash.split("?")[1] ?? "").get("state") ?? "default") as ScreenState
  const [state, setState] = useState(read)
  useEffect(() => {
    const on = () => setState(read())
    addEventListener("hashchange", on)
    requestAnimationFrame(() => document.documentElement.setAttribute("data-ready", ""))
    return () => removeEventListener("hashchange", on)
  }, [])
  return state
}
