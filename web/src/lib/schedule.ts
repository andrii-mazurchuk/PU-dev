// A trigger's schedule in people's terms, mapped onto what pu accepts: every_seconds, or a 5-field cron with an IANA
// tz, never both. A cron opens as a preset only if that preset writes the same cron back, so anything else stays in
// Advanced as it was typed.
export type Unit = "seconds" | "minutes" | "hours" | "days"
export type Schedule =
  | { mode: "every"; n: string; unit: Unit }
  | { mode: "daily"; time: string; tz: string }
  | { mode: "weekly"; days: string[]; time: string; tz: string }
  | { mode: "monthly"; day: string; time: string; tz: string }
  | { mode: "advanced"; cron: string; tz: string }
export type Source = { every_seconds?: number; cron?: string; tz?: string }

export const UNITS: Unit[] = ["seconds", "minutes", "hours", "days"]
const SECONDS: Record<Unit, number> = { seconds: 1, minutes: 60, hours: 3600, days: 86400 }
export const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
const DAY_NAME: Record<string, string> = { mon: "Monday", tue: "Tuesday", wed: "Wednesday", thu: "Thursday", fri: "Friday", sat: "Saturday", sun: "Sunday" }
export const DEFAULT_TZ = "Europe/Warsaw"
/** Where a new schedule starts: Daily, 09:00, Warsaw. */
export const NEW_SCHEDULE = { cron: "0 9 * * *", tz: DEFAULT_TZ }
export const ZONES = ["Europe/Warsaw", "UTC", "Europe/London", "Europe/Berlin", "America/New_York", "America/Los_Angeles", "Asia/Tokyo"]

const hm = (time: string) => { const [h, m] = time.split(":").map(Number); return [m, h] }
const validTime = (t: string) => /^([01]\d|2[0-3]):[0-5]\d$/.test(t)
const cronOf = (s: Exclude<Schedule, { mode: "every" | "advanced" }>) => {
  const [m, h] = hm(s.time)
  return s.mode === "daily" ? `${m} ${h} * * *` : s.mode === "weekly" ? `${m} ${h} * * ${DAYS.filter(d => s.days.includes(d)).join(",")}` : `${m} ${h} ${s.day} * *`
}
const pad = (n: string) => n.padStart(2, "0")

/** The preset that writes exactly this cron back, or Advanced. */
export function fromSource(src: Source): Schedule {
  if (src.every_seconds !== undefined && !src.cron) {
    const unit = [...UNITS].reverse().find(u => src.every_seconds! % SECONDS[u] === 0) ?? "seconds"
    return { mode: "every", n: String(src.every_seconds / SECONDS[unit]), unit }
  }
  const cron = (src.cron ?? "").trim(), tz = src.tz ?? "UTC", f = cron.split(/\s+/)
  const advanced: Schedule = { mode: "advanced", cron: src.cron ?? "", tz }
  if (f.length !== 5 || !/^\d+$/.test(f[0]) || !/^\d+$/.test(f[1])) return advanced
  const time = `${pad(f[1])}:${pad(f[0])}`, tail = f.slice(2).join(" ")
  const preset: Schedule = tail === "* * *" ? { mode: "daily", time, tz }
    : /^\* \* [a-z,]+$/.test(tail) ? { mode: "weekly", days: f[4].split(","), time, tz }
    : /^\d+ \* \*$/.test(tail) ? { mode: "monthly", day: f[2], time, tz } : advanced
  if (preset.mode === "advanced" || !validTime(time)) return advanced
  return cronOf(preset) === cron ? preset : advanced
}

/** What the form sends: one or the other, never both. Only call on a schedule with no error. */
export function toSource(s: Schedule): Source {
  if (s.mode === "every") return { every_seconds: Number(s.n) * SECONDS[s.unit] }
  return { cron: s.mode === "advanced" ? s.cron.trim() : cronOf(s), tz: s.tz.trim() || "UTC" }
}

export function scheduleError(s: Schedule): string | undefined {
  if (s.mode === "every") {
    if (!/^[1-9]\d*$/.test(s.n)) return "Enter a whole number."
    return Number(s.n) * SECONDS[s.unit] < 30 ? "Repeat at least 30 seconds apart: pu ticks every 30 seconds." : undefined
  }
  try { new Intl.DateTimeFormat("en", { timeZone: s.tz.trim() || "UTC" }) } catch { return `"${s.tz}" is not a time zone pu knows; use an IANA name such as Europe/Warsaw.` }
  if (s.mode === "advanced") return s.cron.trim().startsWith("@") || s.cron.trim().split(/\s+/).length !== 5 ? "Five fields: minute hour day month weekday (no @weekly, L or #)." : undefined
  if (!validTime(s.time)) return "Choose a time."
  if (s.mode === "weekly" && !s.days.length) return "Choose at least 1 day."
  if (s.mode === "monthly" && !(/^\d+$/.test(s.day) && +s.day >= 1 && +s.day <= 31)) return "A day of the month, 1 to 31."
}

const list = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`)
const zone = (tz: string) => { const t = tz.trim() || "UTC"; return t === "UTC" ? "UTC" : `${t.split("/").pop()!.replaceAll("_", " ")} time` }
const ordinal = (d: string) => { const n = +d, t = n % 100; return `${n}${t >= 11 && t <= 13 ? "th" : ["th", "st", "nd", "rd"][n % 10] ?? "th"}` }

/** The schedule in plain words, with when it first fires. Assumes no scheduleError. */
export function readback(s: Schedule): string {
  if (s.mode === "every") return `Every ${s.n === "1" ? s.unit.slice(0, -1) : `${s.n} ${s.unit}`} after the last fire · first fire when switched on`
  const next = " · next fire at the next matching minute"
  if (s.mode === "advanced") return `Cron ${s.cron.trim()}, ${zone(s.tz)}${next}`
  const at = `at ${s.time}, ${zone(s.tz)}`
  if (s.mode === "daily") return `Every day ${at}${next}`
  if (s.mode === "weekly") return `Every ${list(DAYS.filter(d => s.days.includes(d)).map(d => DAY_NAME[d]))} ${at}${next}`
  return `On the ${ordinal(s.day)} of each month ${at}${+s.day > 28 ? `, skipped in months without a day ${s.day}` : ""}${next}`
}
