import { expect, test } from "bun:test"
import { monotone } from "./charts"

test("the smoothed spend line passes through every day and never overshoots between two days", () => {
  const ys = [0.33, 0.32, 0.46, 0.3, 0.38, 0, 0, 0.31]
  const f = monotone(ys)
  ys.forEach((y, i) => expect(f(i)).toBeCloseTo(y, 9))
  for (let t = 0; t < ys.length - 1; t += 0.01) {
    const i = Math.floor(t), lo = Math.min(ys[i], ys[i + 1]), hi = Math.max(ys[i], ys[i + 1])
    expect(f(t)).toBeGreaterThanOrEqual(lo - 1e-9); expect(f(t)).toBeLessThanOrEqual(hi + 1e-9)
  }
})
