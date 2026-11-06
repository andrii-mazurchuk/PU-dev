import { expect, test } from "bun:test"
import { fromSource, readback, scheduleError, toSource, type Schedule } from "./schedule"

test("a preset writes the cron and every_seconds pu expects, never both", () => {
  expect(toSource({ mode: "every", n: "2", unit: "hours" })).toEqual({ every_seconds: 7200 })
  expect(toSource({ mode: "weekly", days: ["wed", "mon"], time: "09:30", tz: "Europe/Warsaw" })).toEqual({ cron: "30 9 * * mon,wed", tz: "Europe/Warsaw" })
  expect(toSource({ mode: "daily", time: "07:05", tz: "" })).toEqual({ cron: "5 7 * * *", tz: "UTC" })
  expect(toSource({ mode: "monthly", day: "15", time: "00:00", tz: "UTC" })).toEqual({ cron: "0 0 15 * *", tz: "UTC" })
})

test("saved schedules open in the preset that reproduces them", () => {
  expect(fromSource({ every_seconds: 86400 })).toEqual({ mode: "every", n: "1", unit: "days" })
  expect(fromSource({ every_seconds: 90 })).toEqual({ mode: "every", n: "90", unit: "seconds" })
  expect(fromSource({ cron: "0 7 * * *", tz: "Europe/Warsaw" })).toEqual({ mode: "daily", time: "07:00", tz: "Europe/Warsaw" })
  expect(fromSource({ cron: "0 9 * * mon", tz: "Europe/Warsaw" })).toEqual({ mode: "weekly", days: ["mon"], time: "09:00", tz: "Europe/Warsaw" })
  expect(fromSource({ cron: "30 6 1 * *" })).toEqual({ mode: "monthly", day: "1", time: "06:30", tz: "UTC" })
})

test("a cron no preset writes back stays in Advanced exactly as typed", () => {
  for (const cron of ["*/15 9-17 * * 1-5", "0 9 * * 1-5", "0 9 * * 7", "0 9 * * 3,1", "0 9 * * wed,mon", "05 7 * * *", "0 7 1 1 *", "0 7 1 * mon", "not a cron", "0 25 * * *"])
    expect(fromSource({ cron, tz: "UTC" })).toEqual({ mode: "advanced", cron, tz: "UTC" })
})

test("numeric weekdays open as the days they name", () => {
  expect(fromSource({ cron: "0 8 * * 1" })).toEqual({ mode: "weekly", days: ["mon"], time: "08:00", tz: "UTC" })
  expect(fromSource({ cron: "0 8 * * 0" })).toEqual({ mode: "weekly", days: ["sun"], time: "08:00", tz: "UTC" })
  expect(fromSource({ cron: "0 8 * * 1,3" })).toEqual({ mode: "weekly", days: ["mon", "wed"], time: "08:00", tz: "UTC" })
})

test("every preset survives a save and reopen", () => {
  const all: Schedule[] = [{ mode: "every", n: "45", unit: "minutes" }, { mode: "daily", time: "23:59", tz: "UTC" }, { mode: "weekly", days: ["mon", "fri", "sun"], time: "00:01", tz: "Asia/Tokyo" }, { mode: "monthly", day: "31", time: "12:00", tz: "UTC" }]
  for (const s of all) expect(fromSource(toSource(s))).toEqual(s)
})

test("what pu would refuse is refused here, with the reason", () => {
  expect(scheduleError({ mode: "every", n: "20", unit: "seconds" })).toContain("at least 30 seconds")
  expect(scheduleError({ mode: "every", n: "30", unit: "seconds" })).toBeUndefined()
  expect(scheduleError({ mode: "every", n: "1.5", unit: "hours" })).toBe("Enter a whole number.")
  expect(scheduleError({ mode: "weekly", days: [], time: "09:00", tz: "UTC" })).toBe("Choose at least 1 day.")
  expect(scheduleError({ mode: "monthly", day: "32", time: "09:00", tz: "UTC" })).toContain("1 to 31")
  expect(scheduleError({ mode: "daily", time: "", tz: "UTC" })).toBe("Choose a time.")
  expect(scheduleError({ mode: "daily", time: "09:00", tz: "Mars/Base" })).toContain("not a time zone")
  expect(scheduleError({ mode: "advanced", cron: "@weekly", tz: "UTC" })).toContain("no @weekly")
  expect(scheduleError({ mode: "advanced", cron: "0 7 * * mon", tz: "Europe/Warsaw" })).toBeUndefined()
})

test("the readback says it in plain words, and when it first fires", () => {
  expect(readback({ mode: "every", n: "2", unit: "hours" })).toBe("Every 2 hours after the last fire · first fire when switched on")
  expect(readback({ mode: "every", n: "1", unit: "days" })).toStartWith("Every day after the last fire")
  expect(readback({ mode: "daily", time: "07:30", tz: "Europe/Warsaw" })).toBe("Every day at 07:30, Warsaw time · next fire at the next matching minute")
  expect(readback({ mode: "weekly", days: ["wed", "mon"], time: "09:30", tz: "Europe/Warsaw" })).toStartWith("Every Monday and Wednesday at 09:30, Warsaw time")
  expect(readback({ mode: "monthly", day: "31", time: "08:00", tz: "UTC" })).toStartWith("On the 31st of each month at 08:00, UTC, skipped in months without a day 31")
  expect(readback({ mode: "monthly", day: "12", time: "08:00", tz: "America/New_York" })).toStartWith("On the 12th of each month at 08:00, New York time ·")
})
