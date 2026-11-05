import { useEffect, useState } from "react"
import { screens } from "./pages/screens"

// The product: hash routes to the pages and nothing else (HANDOFF.md "What ships"). No gallery, no page index,
// no review entry is part of this app.

export function useRoute() {
  // The whole hash, not just the route: #/task?id=a → #/task?id=b is the same route and must still re-render.
  const [hash, setHash] = useState(() => location.hash)
  useEffect(() => {
    const on = () => setHash(location.hash)
    addEventListener("hashchange", on)
    return () => removeEventListener("hashchange", on)
  }, [])
  return hash.replace(/^#\/?/, "").split("?")[0]
}

/** #/<page-id>; an empty or unknown route opens Tasks, the opening view. The panels (task, map, type, trigger) open
 *  over their view: fade only when the view itself changes. */
export function App({ route }: { route: string }) {
  const id = (route in screens ? route : "tasks") as keyof typeof screens, Page = screens[id]
  const view = ({ task: "tasks", map: "tasks", type: "sessions", trigger: "sessions" } as Record<string, string>)[id] ?? id
  return <div key={view} className="animate-in fade-in-0 duration-(--duration-fast) ease-(--ease-out)"><Page /></div>
}
