import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

const FRAME = {
  framed: "rounded-lg border border-border bg-card p-4 text-card-foreground",
  plain: "",
}

// A titled block: every section of every screen sits in one, so headings and edges line up across the page.
function Panel({ title, aside, children, className, framed = true, ...rest }: {
  title: ReactNode; aside?: ReactNode; children: ReactNode; className?: string; framed?: boolean
} & Omit<React.ComponentProps<"section">, "title">) {
  return (
    <section className={cn("flex min-w-0 flex-col gap-3", FRAME[framed ? "framed" : "plain"], className)} {...rest}>
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-base font-medium text-foreground">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  )
}

function KeyValue({ rows, className }: { rows: [ReactNode, ReactNode][]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm", className)}>
      {rows.map(([k, v], i) => <div key={i} className="contents"><dt className="text-muted-foreground">{k}</dt><dd className="m-0 min-w-0 break-words text-foreground">{v}</dd></div>)}
    </dl>
  )
}

export { Panel, KeyValue }
