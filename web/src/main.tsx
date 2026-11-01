import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "./index.css"
import { App, useRoute } from "./app"
import { useLive } from "./lib/live"

// The shipped entry (dashboard.html): pu's pages only.
function Root() {
  useLive() // one subscription: a poll that changed something re-renders; merged records keep their DOM
  return <App route={useRoute()} />
}

createRoot(document.getElementById("root")!).render(<StrictMode><Root /></StrictMode>)
