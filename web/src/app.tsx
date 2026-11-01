import { useEffect, useState } from "react"
import { screens } from "./pages/screens"

// The product: hash routes to the pages and nothing else (HANDOFF.md "What ships"). No gallery, no page index,
// no review entry is part of this app.
export const currentRoute = () => location.hash.replace(/^#\/?/, "").split("?")[0]

export function useRoute() {
  const [route, setRoute] = useState(currentRoute)
  useEffect(() => {
    const on = () => setRoute(currentRoute())
    addEventListener("hashchange", on)
    return () => removeEventListener("hashchange", on)
  }, [])
  return route
}

/** #/<page-id>; an empty or unknown route opens Tasks, the opening view. The panels (task, map, type, trigger) open
 *  over their view: fade only when the view itself changes. */
export function App({ route }: { route: string }) {
  const id = (route in screens ? route : "tasks") as keyof typeof screens, Page = screens[id]
  const view = ({ task: "tasks", map: "tasks", type: "sessions", trigger: "sessions" } as Record<string, string>)[id] ?? id
  return <div key={view} className="animate-in fade-in-0 duration-(--duration-fast) ease-(--ease-out)"><Page /></div>
}
