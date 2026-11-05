import { useId, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select } from "@/components/ui/select"
import { DAYS, DEFAULT_TZ, fromSource, readback, scheduleError, toSource, UNITS, ZONES, type Schedule, type Source, type Unit } from "@/lib/schedule"

type Mode = Schedule["mode"]
const MODES: Record<Mode, string> = { every: "Repeat every", daily: "Daily", weekly: "Weekly", monthly: "Monthly", advanced: "Advanced" }
const BLANK = { n: "1", unit: "hours" as Unit, time: "09:00", days: ["mon"], day: "1", cron: "", tz: DEFAULT_TZ }

/** When a schedule fires, in people's terms. The source reaches onChange only after a touch, so opening a saved
 *  schedule changes nothing; switching modes keeps what was entered in the others. */
function SchedulePicker({ value, disabled, onChange }: { value?: Source; disabled?: boolean; onChange?: (next: { source: Source; error?: string }) => void }) {
  const id = useId()
  const [d, setD] = useState(() => {
    const s = value?.cron !== undefined || value?.every_seconds !== undefined ? fromSource(value) : undefined
    return { ...BLANK, ...s, mode: (s?.mode ?? "daily") as Mode }
  })
  const current = (x: typeof d): Schedule => {
    if (x.mode === "every") return { mode: "every", n: x.n, unit: x.unit }
    if (x.mode === "weekly") return { mode: "weekly", days: x.days, time: x.time, tz: x.tz }
    if (x.mode === "monthly") return { mode: "monthly", day: x.day, time: x.time, tz: x.tz }
    if (x.mode === "advanced") return { mode: "advanced", cron: x.cron, tz: x.tz }
    return { mode: "daily", time: x.time, tz: x.tz }
  }
  const s = current(d), error = scheduleError(s), invalid = !!error || undefined
  const set = (patch: Partial<typeof d>) => {
    const next = { ...d, ...patch }
    // Advanced starts from the preset just left, so it can be adjusted rather than written from nothing.
    if (patch.mode === "advanced" && !d.cron && !scheduleError(s) && s.mode !== "every") next.cron = toSource(s).cron!
    setD(next)
    const n = current(next), e = scheduleError(n)
    onChange?.({ source: e ? {} : toSource(n), error: e })
  }
  const time = <div className="flex flex-col gap-1"><Label htmlFor={`${id}-time`} className="text-sm text-foreground">At</Label><Input id={`${id}-time`} type="time" className="w-32 dark:[color-scheme:dark]" value={d.time} disabled={disabled} aria-invalid={invalid} aria-describedby={`${id}-say`} onChange={e => set({ time: e.target.value })} /></div>
  return (
    <div className="flex flex-col gap-3">
      <div role="group" aria-label="When it fires" className="flex flex-wrap gap-1">{(Object.entries(MODES) as [Mode, string][]).map(([m, l]) => <Button key={m} size="sm" variant={d.mode === m ? "default" : "outline"} aria-pressed={d.mode === m} disabled={disabled} onClick={() => d.mode !== m && set({ mode: m })}>{l}</Button>)}</div>
      {d.mode === "every" && <div className="flex items-end gap-2">
        <div className="flex flex-col gap-1"><Label htmlFor={`${id}-n`} className="text-sm text-foreground">Every</Label><Input id={`${id}-n`} inputMode="numeric" className="w-24" value={d.n} disabled={disabled} aria-invalid={invalid} aria-describedby={`${id}-say`} onChange={e => set({ n: e.target.value })} /></div>
        <Select aria-label="Repeat unit" className="w-32" value={d.unit} disabled={disabled} onChange={e => set({ unit: e.target.value as Unit })}>{UNITS.map(u => <option key={u} value={u}>{u}</option>)}</Select>
      </div>}
      {d.mode === "weekly" && <div role="group" aria-label="Days" className="flex flex-wrap gap-1">{DAYS.map(x => { const on = d.days.includes(x); return <Button key={x} size="sm" variant={on ? "default" : "outline"} aria-pressed={on} disabled={disabled} onClick={() => set({ days: on ? d.days.filter(y => y !== x) : [...d.days, x] })}>{x[0].toUpperCase() + x.slice(1)}</Button> })}</div>}
      {(d.mode === "daily" || d.mode === "weekly") && time}
      {d.mode === "monthly" && <div className="flex items-end gap-2">
        <div className="flex flex-col gap-1"><Label htmlFor={`${id}-day`} className="text-sm text-foreground">On day</Label><Input id={`${id}-day`} inputMode="numeric" className="w-24" value={d.day} disabled={disabled} aria-invalid={invalid} aria-describedby={`${id}-say`} onChange={e => set({ day: e.target.value })} /></div>
        {time}
      </div>}
      {d.mode === "advanced" && <div className="flex flex-col gap-1"><Label htmlFor={`${id}-cron`} className="text-sm text-foreground">Cron (minute hour day month weekday)</Label><Input id={`${id}-cron`} placeholder="0 7 * * mon" value={d.cron} disabled={disabled} aria-invalid={invalid} aria-describedby={`${id}-say`} onChange={e => set({ cron: e.target.value })} /></div>}
      {d.mode !== "every" && <div className="flex flex-col gap-1"><Label htmlFor={`${id}-tz`} className="text-sm text-foreground">Time zone</Label><Input id={`${id}-tz`} className="max-w-64" value={d.tz} suggestions={ZONES} disabled={disabled} aria-invalid={invalid} aria-describedby={`${id}-say`} onChange={e => set({ tz: e.target.value })} /></div>}
      <p id={`${id}-say`} role={error ? "alert" : "status"} className="m-0 text-sm text-muted-foreground">{error ?? readback(s)}</p>
    </div>
  )
}

export { SchedulePicker }
