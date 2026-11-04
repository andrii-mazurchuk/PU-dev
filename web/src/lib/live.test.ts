import { expect, test } from "bun:test"
import { toTask, triggerLog, type ApiEvent, type ApiTask } from "./live"

const raw = (over: Partial<ApiTask>): ApiTask => ({ uuid: "u1", description: "t", status: "pending", kind: "execution", project: "holonic-node.pu", parent: "m1", tags: [], depends: [], afk: true, active: false, ...over })

test("a pu record reads as one state, in the agreed precedence", () => {
  const state = (over: Partial<ApiTask>, pending = new Set<string>()) => toTask(raw(over), pending).state
  expect(state({ status: "completed", active: true })).toBe("completed")
  expect(state({ active: true, tags: ["expired"] })).toBe("running")
  expect(state({ tags: ["expired", "needs_human"] })).toBe("expired")
  expect(state({ tags: ["needs_human"], depends: ["d"] }, new Set(["d"]))).toBe("needs-you")
  expect(state({ depends: ["d"] }, new Set(["d"]))).toBe("blocked")
  expect(state({ depends: ["done-elsewhere"] })).toBe("ready") // a dependency not open in this scope does not block
  expect(state({ kind: "map" })).toBe("open")
})

test("yours follows pu's audience rule, not afk", () => {
  expect(toTask(raw({ kind: "grilling" }), new Set()).yours).toBe(true)
  expect(toTask(raw({ tags: ["needs_human"] }), new Set()).yours).toBe(true)
  expect(toTask(raw({ kind: "task", afk: false }), new Set()).yours).toBe(false)
})

test("the trigger log shows quiet fetches, fires and errors, newest first", () => {
  const ev = (over: Partial<ApiEvent>): ApiEvent => ({ at: "2026-11-04T06:00:00Z", trigger: "hn", status: 200, items: 3, fresh: 0, matched: 0, fired: false, task_uuid: null, error: null, backoff_until: null, ...over })
  const log = triggerLog([
    ev({ at: "2026-11-04T05:00:00Z" }),
    ev({ at: "2026-11-04T07:00:00Z", status: 503, items: 0, error: "HTTP 503", backoff_until: "2026-11-04T08:00:00Z" }),
    ev({ at: "2026-11-04T06:00:00Z", fresh: 2, matched: 1, fired: true, task_uuid: "t1" }),
    ev({ at: "2026-11-04T04:00:00Z", trigger: "morning", status: null, items: 0, fired: true, task_uuid: "t2" }),
  ])
  expect(log.map(r => r[2])).toEqual(["fetch failed", "fetched · 2 new · fired", "fetched · 3 items · 0 new", "fired"])
  expect(log[0][4]).toContain("HTTP 503 · backing off until")
  expect(log[1][3]).toBe("1 task · 1 items")
  expect(log[2][3]).toBe("—")
})
