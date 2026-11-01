import { StrictMode, useEffect, useState } from "react"
import { createRoot } from "react-dom/client"
import "./index.css"
import { screens } from "./pages/screens"
import { applyPreview } from "./lib/preview"
import { useLive } from "./lib/live"

applyPreview()

// Tasks is the page: it opens with no hash, and any route this app does not know lands there too. Every other view
// is reached from Tasks' own tabs and links, as #/<view> in pu's own hash.
type Route = keyof typeof screens
function App() {
  useLive() // one subscription: a poll that changed something re-renders; merged records keep their DOM
  const current = () => location.hash.replace(/^#\/?/, "").split("?")[0]
  const [route, setRoute] = useState(current)
  useEffect(() => {
    const on = () => setRoute(current())
    addEventListener("hashchange", on)
    return () => removeEventListener("hashchange", on)
  }, [])
  const id: Route = route in screens ? (route as Route) : "tasks"
  const Page = screens[id]
  // The panels (task, map, type, trigger) open over their view: fade only when the view itself changes.
  const view = ({ task: "tasks", map: "tasks", type: "sessions", trigger: "sessions" } as Record<string, string>)[id] ?? id
  return <div key={view} className="animate-in fade-in-0 duration-(--duration-fast) ease-(--ease-out)"><Page /></div>
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>)
