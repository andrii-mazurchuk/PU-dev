import { cn } from "@/lib/utils"

// The little circle Andrey kept from option A: one glyph per task state, readable without colour (shape carries it).
export type StatusKind = "ready" | "blocked" | "running" | "needs-you" | "expired" | "completed"
const LABEL: Record<StatusKind, string> = { ready: "can start", blocked: "waiting on a blocker", running: "running", "needs-you": "needs you", expired: "expired", completed: "done" }

// Colour only for the node's meanings (J04): ready = brand, needs you = warn, expired = down, done = ok; the rest grey.
const TONE: Record<StatusKind, string> = { ready: "text-primary", blocked: "text-muted-foreground", running: "text-foreground", "needs-you": "text-status-warn", expired: "text-status-down", completed: "text-status-ok" }

function StatusIcon({ status, className }: { status: StatusKind; className?: string }) {
  return (
    <svg viewBox="0 0 16 16" role="img" aria-label={LABEL[status]} data-status={status} className={cn("size-4 shrink-0", TONE[status], className)}>
      {status === "ready" && <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="1.5" />}
      {status === "blocked" && <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="2.5 2" />}
      {status === "running" && <><circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M8 2a6 6 0 0 1 0 12z" fill="currentColor" /></>}
      {status === "needs-you" && <><circle cx="8" cy="8" r="7" fill="currentColor" /><path d="M8 4v5M8 11v1.2" fill="none" className="stroke-background" strokeWidth="1.8" strokeLinecap="round" /></>}
      {status === "expired" && <><circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M5.5 5.5l5 5M10.5 5.5l-5 5" fill="none" stroke="currentColor" strokeWidth="1.5" /></>}
      {status === "completed" && <><circle cx="8" cy="8" r="7" fill="currentColor" /><path d="M5 8.2l2 2 4-4.4" fill="none" className="stroke-background" strokeWidth="1.8" /></>}
    </svg>
  )
}

export { StatusIcon, LABEL as STATUS_LABEL }
