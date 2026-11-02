import { expect, test } from "bun:test"
import { claimant, sameKind } from "./claims"

const claims = [{ type: "research", kind: "research" }, { type: "ai-news", kind: "research", tags: ["news"] }, { type: "hn", kind: "research", tags: ["hn"] }]

test("the most specific claim wins; a kind nobody claims has no claimant", () => {
  expect(claimant(claims, "research", ["news"])).toEqual({ winner: "ai-news", tied: [] })
  expect(claimant(claims, "research", [])).toEqual({ winner: "research", tied: [] })
  expect(claimant(claims, "grilling", ["news"])).toEqual({ tied: [] })
})

test("two claims equally specific are reported as a tie, not settled", () => {
  expect(claimant(claims, "research", ["news", "hn"])).toEqual({ tied: ["ai-news", "hn"] })
})

test("the type form lists the other types claiming its kind", () => {
  expect(sameKind(claims, "ai-news", "research").map(c => c.type)).toEqual(["research", "hn"])
  expect(sameKind(claims, "ai-news", undefined)).toEqual([])
})
