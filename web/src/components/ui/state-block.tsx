import { Button } from "@/components/ui/button"

// What a block shows instead of its data: the state lives in the block it affects, never as a page-wide banner.
export type BlockState = "empty" | "loading" | "error" | "stale" | "partial" | "offline" | "permission"

const ROLE: Record<Exclude<BlockState, "loading">, "alert" | "status"> = {
  empty: "status",
  error: "alert",
  stale: "status",
  partial: "status",
  offline: "status",
  permission: "status",
}

function StateBlock({ state, message, lastGood, onRetry }: { state: BlockState; message: string; lastGood?: string; onRetry?: () => void }) {
  if (state === "loading")
    return (
      <div role="status" aria-busy="true" aria-label="loading" className="border-l-2 border-border py-1 pl-3">
        <div className="h-5 w-3/4 rounded-sm bg-muted" />
      </div>
    )
  return (
    <div role={ROLE[state]} className="flex flex-col gap-2 border-l-2 border-border py-1 pl-3 text-sm text-foreground">
      <p className="m-0">{message}</p>
      {lastGood && <p className="m-0 text-xs text-muted-foreground">last good data {lastGood}</p>}
      {onRetry && (state === "error" || state === "partial" || state === "stale") && (
        <Button variant="outline" size="sm" className="self-start" onClick={onRetry}>{state === "stale" ? "Refresh" : "Retry"}</Button>
      )}
    </div>
  )
}

export { StateBlock }
