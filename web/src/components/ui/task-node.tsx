import { cn } from "@/lib/utils"
import { StatusIcon, type StatusKind } from "@/components/ui/status-icon"

// One task in the graph: status circle + title, a link that opens the task's side panel (GitHub-Actions-style pill).
const short = (s: string, n = 44) => (s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s)

function TaskNode({ title, status, href, selected = false, disabled = false, className, style }: {
  title: string; status: StatusKind; href: string; selected?: boolean; disabled?: boolean; className?: string; style?: React.CSSProperties
}) {
  return (
    <a
      data-node=""
      draggable={false}
      href={disabled ? undefined : href}
      aria-disabled={disabled || undefined}
      aria-current={selected || undefined}
      title={title}
      style={style}
      className={cn(
        "flex h-11 w-[200px] items-center gap-2 rounded-md border border-border bg-card px-2 text-xs text-card-foreground no-underline outline-none",
        "transition-colors duration-(--duration-fast) ease-(--ease-standard) hover:border-foreground hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring",
        selected && "border-foreground outline-1 outline-foreground",
        disabled && "pointer-events-none border-dashed text-muted-foreground",
        className,
      )}
    >
      <StatusIcon status={status} />
      <span className="leading-tight">{short(title)}</span>
    </a>
  )
}

export { TaskNode }
