import { StrictMode, useEffect, useState } from "react"
import { createRoot } from "react-dom/client"
import "./index.css"
import { Gallery } from "./gallery/Gallery"
import { pages } from "./pages"
import { applyPreview } from "./lib/preview"
import { useLive } from "./lib/live"

applyPreview()

// Hash routes so the built app works from a static folder: #/gallery, #/gallery/<component>[/<variant>[/<state>]]
// (any element alone, by its address), #/<page-id>.
function App() {
  useLive() // one subscription: a poll that changed something re-renders; merged records keep their DOM
  const current = () => location.hash.replace(/^#\/?/, "").split("?")[0]
  const [route, setRoute] = useState(current)
  useEffect(() => {
    const on = () => setRoute(current())
    addEventListener("hashchange", on)
    return () => removeEventListener("hashchange", on)
  }, [])
  if (route === "gallery" || route.startsWith("gallery/")) return <Gallery path={route.split("/").slice(1).map(decodeURIComponent)} />
  const Page = pages[route.split("?")[0]]
  // The panels (task, map, type, trigger) open over their view: fade only when the view itself changes.
  const view = ({ task: "tasks", map: "tasks", type: "sessions", trigger: "sessions" } as Record<string, string>)[route] ?? route
  if (Page) return <div key={view} className="animate-in fade-in-0 duration-(--duration-fast) ease-(--ease-out)"><Page /></div>
  return (
    <main style={{ padding: 24 }}>
      <a href="#/gallery">Gallery</a>
      {Object.keys(pages).map(id => <div key={id}><a href={`#/${id}`}>{id}</a></div>)}
    </main>
  )
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>)
