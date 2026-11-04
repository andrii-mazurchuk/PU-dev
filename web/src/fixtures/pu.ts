// Mock fixtures for every pu screen: the stage-3 wireframe data (pu's own dashboard sample plus PRD figures).
// Run figures are a fixture of what pu shows once the 5 recurring sessions run; no live task text from the node.

export type TaskState = "ready" | "blocked" | "running" | "needs-you" | "expired" | "completed"
export type Task = { id: string; title: string; kind: string; project: string; map: string | null; deps: string[]; state: TaskState | "open"; afk?: boolean; yours?: boolean }

const T: Task[] = [
  { id: "m1", title: "Dashboard routing across units", kind: "map", project: "holonic-node.dashboards", map: null, deps: [], state: "open" },
  { id: "a1", title: "How does a unit declare a dashboard?", kind: "research", project: "holonic-node.dashboards", map: "m1", deps: [], state: "completed", afk: true },
  { id: "a2", title: "Decide the panel vocabulary", kind: "grilling", project: "holonic-node.dashboards", map: "m1", deps: ["a1"], state: "ready", yours: true },
  { id: "a3", title: "Proxy or publish ports?", kind: "grilling", project: "holonic-node.dashboards", map: "m1", deps: ["a1"], state: "ready", yours: true },
  { id: "a4", title: "Build the spec renderer", kind: "execution", project: "holonic-node.dashboards", map: "m1", deps: ["a2", "a3"], state: "blocked", afk: true },
  { id: "a5", title: "Vendor a real fixture", kind: "execution", project: "holonic-node.dashboards", map: "m1", deps: ["a4"], state: "blocked", afk: true },
  { id: "m2", title: "Give pu a dashboard worth opening", kind: "map", project: "holonic-node.pu", map: null, deps: [], state: "open" },
  { id: "b1", title: "What can the spec tier express?", kind: "research", project: "holonic-node.pu", map: "m2", deps: [], state: "completed", afk: true },
  { id: "b2", title: "Which tier for a graph canvas?", kind: "grilling", project: "holonic-node.pu", map: "m2", deps: ["b1"], state: "ready", yours: true },
  { id: "b3", title: "Lay out dependencies without crossings", kind: "prototype", project: "holonic-node.pu", map: "m2", deps: ["b2"], state: "blocked", yours: true },
  { id: "b4", title: "Ship the page", kind: "execution", project: "holonic-node.pu", map: "m2", deps: ["b3"], state: "blocked", afk: true },
  { id: "b5", title: "Persist the run-report index", kind: "execution", project: "holonic-node.pu", map: "m2", deps: ["b2"], state: "running", afk: true },
  { id: "m3", title: "Account usage ceilings across both rolling windows and the daily cost cap", kind: "map", project: "holonic-node.pu", map: null, deps: [], state: "open" },
  { id: "c1", title: "Read the two rolling windows", kind: "execution", project: "holonic-node.pu", map: "m3", deps: [], state: "ready", afk: true },
  { id: "c2", title: "Gate before selection", kind: "execution", project: "holonic-node.pu", map: "m3", deps: ["c1"], state: "blocked", afk: true },
  { id: "c3", title: "Tell the owner on transition", kind: "task", project: "holonic-node.pu", map: "m3", deps: ["c2"], state: "needs-you", yours: true },
  { id: "c4", title: "Описать окно сброса лимитов для недельного окна и проверить часовой пояс", kind: "task", project: "holonic-node.pu", map: "m3", deps: ["c2"], state: "expired" },
]
const G = ["Registry schema", "Role POST door", "Role DELETE door", "Validate role on type write", "Cache roles in pu", "Refresh cache on 404", "Show role in Sessions", "Role filter", "Gateway tests", "pu tests", "Docs: Agent roles", "Migrate 9 types", "Audit unknown roles", "Grilling: who owns roles?"]
G.forEach((title, i) => T.push({ id: `g${i + 1}`, title, kind: i === 13 ? "grilling" : i % 4 === 3 ? "research" : "execution", project: "holonic-node.gateway", map: "m4", deps: i === 0 || i === 13 ? [] : i === 2 ? ["g1"] : i === 9 ? ["g4"] : i === 12 ? ["g11", "g10"] : [`g${i}`], state: i === 13 || i === 0 ? "ready" : "blocked", yours: i === 13, afk: i !== 13 }))
T.push({ id: "m4", title: "Agent-role registry in the gateway", kind: "map", project: "holonic-node.gateway", map: null, deps: [], state: "open" })
// A map whose two tickets wait on each other (C04: a cycle is drawn and counted), and a task with no map (C03).
T.push({ id: "m5", title: "Retry policy for failed fetches", kind: "map", project: "holonic-node.pu", map: null, deps: [], state: "open" })
T.push({ id: "x1", title: "Decide backoff ceiling", kind: "grilling", project: "holonic-node.pu", map: "m5", deps: ["x2"], state: "blocked", yours: true })
T.push({ id: "x2", title: "Measure feed outage lengths", kind: "research", project: "holonic-node.pu", map: "m5", deps: ["x1"], state: "blocked", afk: true })
T.push({ id: "n1", title: "Loose note: check node clock drift", kind: "task", project: "holonic-node.pu", map: null, deps: [], state: "ready" })
export const BODY: Record<string, string> = {
  c3: "When either rolling window crosses its ceiling, tell the owner once on the transition, not on every tick.\nInclude the window name, utilisation and resets_at.\n<script>alert(1)</script> in a body is shown as text.",
  a2: "Question: which panel kinds does the node draw for units?\nDecisions so far: kpis, table, record, bars, rows, meters, text.",
  m4: "The gateway owns the agent-role registry; pu validates every type write against it.",
}
export const PRIORITY: Record<string, string> = { c1: "high", c3: "medium", a2: "medium", g1: "medium" }
export const TASKS = T
export const byId = new Map(T.map(t => [t.id, t]))
export const openDeps = (t: Task) => t.deps.filter(d => byId.get(d)!.state !== "completed")
export const MAPS = T.filter(t => t.kind === "map")
export const ticketsOf = (map: string, withDone = false) => T.filter(t => t.map === map && (withDone || t.state !== "completed"))

export const ROOTS: [string, number][] = [["holonic-node", 72], ["job-search", 26], ["t-finance", 23], ["paperclip", 20], ["personal", 10], ["dark", 9], ["pjatk", 9], ["main-route", 6], ["portfolio", 1], ["context-service", 1], ["agents", 0], ["dev-pipeline", 0]]
export const SUBS: [string, number][] = [["pu", 14], ["gateway", 15], ["dashboards", 6], ["mu-logs", 9], ["mu-spec", 11]]

export const STATUS = { needYou: 39, gate: "clear", gateReason: "nothing is holding work back", running: 1, paused: false, today: 0.31, updated: "8 s ago" }
export const ATTENTION = {
  decisions: [
    ["grilling", "Decide the panel vocabulary", "holonic-node.dashboards", "a2"], ["grilling", "Proxy or publish ports?", "holonic-node.dashboards", "a3"],
    ["prototype", "Lay out dependencies without crossings", "holonic-node.pu", "b3"], ["grilling", "What closing a map means", "holonic-node.pu", "b2"],
    ["grilling", "Who owns agent roles?", "holonic-node.gateway", "g14"], ["grilling", "The analytical tool set beyond stats", "holonic-node.pu", "b2"],
  ] as [string, string, string, string][],
  decisionsTotal: 28, handed: [["arXiv cs.AI digest", "failed twice"]] as [string, string][],
  neverActivated: [["type", "job-watch", "Watch job boards daily"], ["trigger", "remoteok-jobs", "Fetch RemoteOK daily"]] as [string, string, string][], neverTotal: 10, faults: 0,
}

export type TypeOn = "on" | "off" | "never activated" | "needs re-approval"
export type SessionType = { name: string; unit: string; on: TypeOn; role: string; grant: string; model: string; last: string; lastRun: boolean; runs: number; cost: number; costs: [number, number, number, number, number] }
export const TYPES: SessionType[] = [
  { name: "morning-brief", unit: "pu", on: "on", role: "system", grant: "web", model: "sonnet", last: "done · 07:02 · $0.31", lastRun: true, runs: 7, cost: 2.10, costs: [0.27, 0.30, 0.30, 0.33, 0.34] },
  { name: "hn-watch", unit: "pu", on: "on", role: "system", grant: "web", model: "haiku", last: "nothing-to-do · 06:10 · $0.02", lastRun: true, runs: 7, cost: 0.14, costs: [0.01, 0.02, 0.02, 0.03, 0.03] },
  { name: "job-watch", unit: "pu", on: "never activated", role: "system", grant: "web", model: "sonnet", last: "not run yet", lastRun: false, runs: 0, cost: 0, costs: [0, 0, 0, 0, 0] },
  { name: "pu-self-review", unit: "pu", on: "off", role: "system", grant: "read", model: "haiku", last: "partial · 10-27 09:00 · $0.12", lastRun: true, runs: 1, cost: 0.12, costs: [0.12, 0.12, 0.12, 0.12, 0.12] },
  { name: "ai-news", unit: "pu", on: "needs re-approval", role: "researcher", grant: "web", model: "haiku", last: "failed · 05:40 · $0.04", lastRun: true, runs: 2, cost: 0.08, costs: [0.04, 0.04, 0.04, 0.04, 0.04] },
  { name: "mu-spec.verify-layer", unit: "mu-spec", on: "off", role: "system", grant: "web", model: "sonnet", last: "done · 10-21 · $0.58", lastRun: true, runs: 0, cost: 0, costs: [0, 0, 0, 0, 0] },
  { name: "execution", unit: "pu (built-in)", on: "on", role: "system", grant: "repo", model: "sonnet", last: "resolved · 10-01 · $0.61", lastRun: true, runs: 0, cost: 0, costs: [0, 0, 0, 0, 0] },
  { name: "research", unit: "pu (built-in)", on: "on", role: "system", grant: "web", model: "sonnet", last: "resolved · 09-28 · $0.44", lastRun: true, runs: 0, cost: 0, costs: [0, 0, 0, 0, 0] },
  { name: "intake", unit: "pu (built-in)", on: "on", role: "system", grant: "read", model: "haiku", last: "not run yet", lastRun: false, runs: 0, cost: 0, costs: [0, 0, 0, 0, 0] },
]
export type Trigger = { name: string; src: string; kind: string; type: string; health: string; on: TypeOn; fires: number; expired: number }
export const TRIGGERS: Trigger[] = [
  { name: "morning-0700", src: "schedule · 0 7 * * * Europe/Warsaw", kind: "task", type: "morning-brief", health: "fired 07:00", on: "on", fires: 7, expired: 0 },
  { name: "hn-front-page", src: "http-json · every 86400 s", kind: "research", type: "hn-watch", health: "fetched 06:00 · 3 new", on: "on", fires: 7, expired: 0 },
  { name: "arxiv-cs-ai", src: "rss · every 86400 s", kind: "research", type: "ai-news", health: "fetch failing · backing off until 14:00", on: "on", fires: 2, expired: 0 },
  { name: "remoteok-jobs", src: "http-json · every 86400 s", kind: "research", type: "job-watch", health: "never fetched", on: "never activated", fires: 0, expired: 0 },
  { name: "weekly-review", src: "schedule · 0 9 * * mon Europe/Warsaw", kind: "task", type: "", health: "wired to no type", on: "off", fires: 1, expired: 0 },
]
export const EXTRA_YIELD: Pick<Trigger, "name" | "fires" | "expired">[] = [["github-releases", 5, 0], ["pjatk-calendar", 3, 1], ["status-page", 0, 0], ["hf-papers", 4, 2], ["polish-jobs", 2, 0]].map(([name, fires, expired]) => ({ name: name as string, fires: fires as number, expired: expired as number }))
export const fedBy = (t: SessionType) => TRIGGERS.find(x => x.type === t.name)?.name

export type Run = { id?: string; at: string; type: string; task: string; taskId: string; outcome: string; needsYou: string; cost: string; duration: string; tokens: string }
export const RUNS: Run[] = [
  { at: "10-31 07:02", type: "morning-brief", task: "Morning brief 2026-10-31", taskId: "c1", outcome: "done", needsYou: "none", cost: "$0.31", duration: "2m 14s", tokens: "41.2k" },
  { at: "10-31 06:10", type: "hn-watch", task: "HN: 3 new items", taskId: "c1", outcome: "nothing-to-do", needsYou: "none", cost: "$0.02", duration: "0m 21s", tokens: "3.1k" },
  { at: "10-31 05:40", type: "ai-news", task: "arXiv cs.AI digest", taskId: "c3", outcome: "failed (2 of 2)", needsYou: "feed unreachable twice; check the arXiv URL", cost: "$0.04", duration: "0m 48s", tokens: "5.9k" },
  { at: "10-31 05:20", type: "ai-news", task: "arXiv cs.AI digest", taskId: "c3", outcome: "failed (1 of 2)", needsYou: "none", cost: "$0.04", duration: "0m 44s", tokens: "5.7k" },
  { at: "10-30 07:01", type: "morning-brief", task: "Morning brief 2026-10-30", taskId: "c1", outcome: "done", needsYou: "none", cost: "$0.29", duration: "2m 02s", tokens: "39.8k" },
  { at: "10-27 09:00", type: "pu-self-review", task: "Weekly pu self-review", taskId: "c1", outcome: "partial", needsYou: "none", cost: "$0.12", duration: "3m 02s", tokens: "18.0k" },
  { at: "10-01 14:22", type: "execution", task: "Gate before selection", taskId: "c2", outcome: "resolved · no report", needsYou: "—", cost: "$0.61", duration: "6m 40s", tokens: "not recorded" },
  { at: "09-28 11:05", type: "research", task: "What can the spec tier express?", taskId: "b1", outcome: "resolved · no report", needsYou: "—", cost: "$0.44", duration: "4m 10s", tokens: "not recorded" },
]
RUNS.forEach((r, i) => { r.id = `r${i + 1}` })
// Run detail: a run with the six-field report, one whose report pu truncated, one from before the report contract.
export type RunDetail = { report?: { outcome: string; needsHuman: string; goal: string; actions: string; reasoning: string; evidence: string; text: string }; detail?: string; attempt: string; tokens?: number[] }
const LONG = "The brief covers 14 sources. ".repeat(560).slice(0, 16000 - 48) + "\n\n[report truncated by pu at 16,000 characters]"
export const RUN_DETAIL: Record<string, RunDetail> = {
  r3: { attempt: "2 of 2 · handed to you · failure_class agent", tokens: [4120, 610, 0, 1180, 0], report: { outcome: "failed", needsHuman: "feed unreachable twice; check the arXiv URL", goal: "Digest new cs.AI papers since yesterday", actions: "Fetched the feed twice; both 503", reasoning: "No items, so no digest; retrying would hit the same outage", evidence: "HTTP 503 at 05:39:58 and 05:40:31", text: "The feed at rss.arxiv.org returned 503 twice. Nothing to summarise." } },
  r1: { attempt: "1 of 1", tokens: [31200, 8400, 0, 1600, 0], report: { outcome: "done", needsHuman: "none", goal: "Write the 07:00 brief", actions: "Read the queue and 4 run reports; wrote the brief", reasoning: "Everything since yesterday fits one message", evidence: "Telegram message 18834 sent 07:02", text: LONG } },
  r7: { attempt: "1 of 1", detail: "resolved: gate now runs before selection; tests green (detail cut by pu at ~200 characters)" },
  r8: { attempt: "1 of 1", detail: "resolved: the spec tier expresses kpis, tables and bars, not graphs" },
}
export const RETIRED = ["weekly-digest-old"]
// pu's own figures per role and trigger (stats by_role, by_trigger): cost avg/p50/p95 and per success, never computed here.
export const BY_ROLE: [string, number, number, number, number, number, string][] = [["system", 15, 2.36, 0.16, 0.30, 0.33, "$0.16"], ["researcher", 2, 0.08, 0.04, 0.04, 0.04, "—"]]
export const BY_TRIGGER: [string, number, number, number, number, number, string][] = [["morning-0700", 7, 2.10, 0.30, 0.30, 0.33, "$0.30"], ["hn-front-page", 7, 0.14, 0.02, 0.02, 0.03, "—"], ["arxiv-cs-ai", 2, 0.08, 0.04, 0.04, 0.04, "—"]]
export const PER_SUCCESS: Record<string, string> = { "morning-brief": "$0.30", "hn-watch": "—", "pu-self-review": "—", "ai-news": "—" }
export const TOKEN_RUNS = { withTokens: 6, total: 8 }

export const TRIGGER_LOG: [string, string, string, string, string][] = [
  ["10-31 07:00", "morning-0700", "fired", "1 task · Morning brief 2026-10-31", ""],
  ["10-31 06:00", "hn-front-page", "fetched · 3 new · fired", "1 task · HN: 3 new items", ""],
  ["10-31 05:45", "hn-front-page", "fetched · 30 items · 0 new", "—", ""],
  ["10-31 05:30", "arxiv-cs-ai", "fetch failed", "—", "HTTP 503 · backing off until 14:00"],
  ["10-31 05:19", "arxiv-cs-ai", "fetched · 4 new · fired", "1 task · arXiv cs.AI digest", ""],
]
export const WEEK_RUNS = TYPES.reduce((n, t) => n + t.runs, 0)
export const WEEK_COST = TYPES.reduce((n, t) => n + t.cost, 0)
export const DAYS: [string, number][] = [["10-25", 0.33], ["10-26", 0.32], ["10-27", 0.46], ["10-28", 0.30], ["10-29", 0.38], ["10-30", 0.34], ["10-31", 0.31]]
/** Spend per window (sample): Today in 2-hour steps, 7 days, 30 days (31 points), All in weeks. */
const wave = (n: number, base: number, amp: number) => Array.from({ length: n }, (_, i) => +(base + amp * Math.sin(i * 1.7) * Math.cos(i * 0.6) + amp * 0.4 * Math.sin(i * 0.31)).toFixed(2))
export const SPEND: Record<string, [string, number][]> = {
  Today: ["00", "02", "04", "06", "08", "10", "12", "14", "16", "18", "20", "22", "24"].map((h, i) => [`${h}:00`, [0, 0, 0, 0.04, 0.19, 0.02, 0.03, 0, 0.01, 0.02, 0, 0, 0][i]]),
  "7 days": DAYS,
  "30 days": [...wave(24, 0.3, 0.12).map((v, i) => [`10-${String(i + 1).padStart(2, "0")}`, Math.max(v, 0.05)] as [string, number]), ...DAYS],
  All: wave(13, 2.1, 0.6).map((v, i) => [`w${32 + i}`, v] as [string, number]),
}
export const OUTCOMES: [string, number][] = [["done", 9], ["nothing-to-do", 5], ["partial", 1], ["failed", 2]]
export const DURATIONS: [string, number][] = [["<30s", 3], ["1m", 5], ["2m", 4], ["3m", 2], ["4m", 1], ["5m", 1], ["6m+", 1]]
export const usd = (n: number) => `$${n.toFixed(2)}`
export const FIXTURE_NOTE = "Run figures are a fixture: what pu shows once the 5 recurring sessions run (today all 9 recorded runs predate the report)."

// The records as pu holds them (GET session-types / triggers), which the edit forms start from. Shapes match pu's
// API (pu web/src/lib/live.ts ApiType / ApiTrigger), so the forms port with only their save and dry-run calls swapped.
export type ApiType = { name: string; purpose?: string; prompt?: string; mcp_servers?: string[]; agent_role?: string; grant: string; model?: string; claims?: { kind?: string; tags?: string[] }; plugins?: string[] }
export type ApiTrigger = { name: string; purpose?: string; source: { kind: string; url?: string; every_seconds?: number; cron?: string; tz?: string; items_path?: string; id_path?: string }; emits?: { kind?: string; tags?: string[]; priority?: string; title?: string; afk?: boolean; stale_after_seconds?: number }; predicate?: [string, string, unknown][] }
const CLAIMS: Record<string, [string, string[]]> = { "morning-brief": ["task", ["brief"]], "hn-watch": ["research", ["hn"]], "job-watch": ["research", ["jobs"]], "pu-self-review": ["task", ["review"]], "ai-news": ["research", ["news"]], "mu-spec.verify-layer": ["execution", ["verify"]], execution: ["execution", []], research: ["research", []], intake: ["task", ["intake"]] }
export const TYPE_RECORDS: Record<string, ApiType> = Object.fromEntries(TYPES.map(t => [t.name, {
  name: t.name, grant: t.grant, model: t.model, agent_role: t.role, mcp_servers: t.name === "morning-brief" ? ["telegram-push"] : [], plugins: [],
  purpose: t.name === "morning-brief" ? "Write the 07:00 brief from yesterday's runs and the queue" : `Handle ${t.name} tasks`,
  prompt: t.name === "morning-brief" ? "You write Andrey's morning brief. Read pu's queue, yesterday's runs and their reports…" : `You handle ${t.name} tasks.`,
  claims: { kind: CLAIMS[t.name]?.[0], tags: CLAIMS[t.name]?.[1] ?? [] },
}]))
export const TRIGGER_RECORDS: Record<string, ApiTrigger> = {
  "morning-0700": { name: "morning-0700", source: { kind: "schedule", cron: "0 7 * * *", tz: "Europe/Warsaw" }, emits: { kind: "task", tags: ["brief"], priority: "medium", title: "Morning brief {date}", afk: true } },
  "hn-front-page": { name: "hn-front-page", source: { kind: "http-json", url: "https://hn.algolia.com/api/v1/search?tags=front_page", every_seconds: 86400, items_path: "hits", id_path: "objectID" }, predicate: [["count_since_fire", ">=", 1]], emits: { kind: "research", tags: ["hn"], priority: "medium", title: "HN: {count} new items", afk: true } },
  "arxiv-cs-ai": { name: "arxiv-cs-ai", source: { kind: "rss", url: "https://rss.arxiv.org/rss/cs.AI", every_seconds: 86400 }, predicate: [["title", "matches", "(agent|LLM|model)"], ["count_since_fire", ">=", 1]], emits: { kind: "research", tags: ["news"], priority: "high", title: "arXiv cs.AI digest: {count} new", afk: true } },
  "remoteok-jobs": { name: "remoteok-jobs", source: { kind: "http-json", url: "https://remoteok.com/api", every_seconds: 86400 }, predicate: [["item.tags", "contains", "python"]], emits: { kind: "research", tags: ["jobs"], priority: "low", title: "Jobs: {count} new", afk: false } },
  "weekly-review": { name: "weekly-review", source: { kind: "schedule", cron: "0 9 * * mon", tz: "Europe/Warsaw" }, emits: { kind: "task", tags: ["weekly"], priority: "medium", title: "Weekly review", afk: false } },
}
export const ROLES = ["system", "researcher", "cu"]
export const MCP_REGISTRY = ["telegram-push", "github"]
