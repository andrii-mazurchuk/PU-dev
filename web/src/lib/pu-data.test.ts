import { expect, test } from "bun:test"
import { fetchScope, inScope, mergeById } from "./pu-data"

test("a refresh keeps unchanged records as the same objects and adds new ones", () => {
  const a = { id: "a", t: 1 }, b = { id: "b", t: 1 }
  const prev = new Map([["a", a], ["b", b]])
  const next = mergeById(prev, [{ id: "a", t: 1 }, { id: "b", t: 2 }, { id: "c", t: 1 }], x => x.id)
  expect(next.get("a")).toBe(a)          // unchanged: same object, React keeps its DOM
  expect(next.get("b")).not.toBe(b)      // changed: new object
  expect([...next.keys()]).toEqual(["a", "b", "c"])
})

test("only the scope in focus is requested, relative and with one status", async () => {
  const urls: string[] = []
  await fetchScope(async u => { urls.push(u); return new Response(JSON.stringify({ tasks: [] })) }, "holonic-node.pu")
  await fetchScope(async u => { urls.push(u); return new Response(JSON.stringify({ tasks: [] })) }, "holonic-node", "completed")
  expect(urls).toEqual(["tasks?project=holonic-node.pu&status=pending", "tasks?project=holonic-node&status=completed"])
  expect(urls.every(u => !u.startsWith("/"))).toBe(true)
})

test("a scope covers its sub-scopes but not a project that merely starts the same", () => {
  expect(inScope("holonic-node.pu", "holonic-node")).toBe(true)
  expect(inScope("holonic-node-v2", "holonic-node")).toBe(false)
})
