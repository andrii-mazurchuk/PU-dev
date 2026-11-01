import { expect, test } from "bun:test"
import { toTask, type ApiTask } from "./live"

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
