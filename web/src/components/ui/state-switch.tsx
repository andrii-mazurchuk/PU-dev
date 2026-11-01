import { useId } from "react"
import { cn } from "@/lib/utils"
import { Switch } from "@/components/ui/switch"

// On/off for a session type or trigger. "never activated" and "needs re-approval" are off with a reason in words;
// inside the node frame (or while pu can't take the write) the switch is disabled and says why. Switching off never
// stops a running session, so the off direction says so.
export type SwitchState = "on" | "off" | "never activated" | "needs re-approval"

const SAY: Record<SwitchState, string> = {
  on: "on",
  off: "off",
  "never activated": "never activated",
  "needs re-approval": "needs re-approval",
}

function StateSwitch({ name, state, pending = false, disabledReason, error, onChange }: {
  name: string; state: SwitchState; pending?: boolean; disabledReason?: string; error?: string; onChange?: (on: boolean) => void
}) {
  const id = useId(), on = state === "on"
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <Switch
          id={id}
          checked={on}
          disabled={!!disabledReason}
          aria-disabled={pending || undefined}
          aria-busy={pending || undefined}
          aria-invalid={error ? true : undefined}
          aria-describedby={error || disabledReason ? `${id}-msg` : undefined}
          aria-label={`${name}: ${state}`}
          onCheckedChange={v => { if (!pending) onChange?.(v) }}
        />
        <label htmlFor={id} className={cn("text-sm text-foreground", state === "needs re-approval" && "font-semibold")}>
          {pending ? "saving…" : SAY[state]}
        </label>
      </div>
      {(error || disabledReason) && (
        <p id={`${id}-msg`} role={error ? "alert" : undefined} className="text-xs text-muted-foreground">{error ?? disabledReason}</p>
      )}
    </div>
  )
}

export { StateSwitch }
