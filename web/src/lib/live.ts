import { useEffect, useSyncExternalStore } from "react"
import * as S from "@/fixtures/pu"
import { fetchScope, inScope, mergeById, type Fetch } from "./pu-data"

// What the pages read: the same names as the fixtures, as live bindings. They start as the fixtures (SOURCE
// "sample", the offline fallback) and are replaced by pu's own data once a poll succeeds. Every list is merged by
// id or name, so unchanged records keep their object identity and React keeps their DOM (stage-6 rulings: only
// the scope in focus, archive on request, incremental updates, no refit).
export { usd } from "@/fixtures/pu"
export type { Task, Run, RunDetail, SessionType, Trigger, TypeOn } from "@/fixtures/pu"

type Pair = [string, number]
export type Conn = { mode: "sample" | "live"; failures: number; lastGood?: number; error?: string }
export let CONN: Conn = { mode: "sample", failures: 0 }

export let TASKS: S.Task[] = S.TASKS
export let byId = new Map(TASKS.map(t => [t.id, t]))
export let MAPS = TASKS.filter(t => t.kind === "map")
export const openDeps = (t: S.Task) => t.deps.filter(d => byId.get(d)?.state !== "completed")
export const ticketsOf = (map: string, withDone = false) => TASKS.filter(t => t.map === map && (withDone || t.state !== "completed"))
export let BODY: Record<string, string> = { ...S.BODY }
export let PRIORITY: Record<string, string> = S.PRIORITY
export let ROOTS: Pair[] = S.ROOTS
let subsByRoot: Record<string, Pair[]> = { "holonic-node": S.SUBS }
export const subsOf = (root: string) => subsByRoot[root] ?? []
export let STATUS = S.STATUS
export let ATTENTION = S.ATTENTION
export let TYPES: S.SessionType[] = S.TYPES
export let TRIGGERS: S.Trigger[] = S.TRIGGERS
export let EXTRA_YIELD = S.EXTRA_YIELD
export const fedBy = (t: S.SessionType) => TRIGGERS.find(x => x.type === t.name)?.name
export let RUNS: S.Run[] = S.RUNS
export let RUN_DETAIL: Record<string, RunMore> = S.RUN_DETAIL
export let RETIRED = S.RETIRED
/** The editable fields of each user-written type, for its panel (sample data has none, so the panel keeps its sample). */
export let TYPE_RECORD: Record<string, { purpose: string; prompt: string; mcp: string[]; claims: string }> = {}
export let BY_ROLE = S.BY_ROLE
export let BY_TRIGGER = S.BY_TRIGGER
export let PER_SUCCESS = S.PER_SUCCESS
export let TOKEN_RUNS = S.TOKEN_RUNS
export let TRIGGER_LOG = S.TRIGGER_LOG
export let WEEK_RUNS = S.WEEK_RUNS
export let WEEK_COST = S.WEEK_COST
export let DAYS = S.DAYS
export let SPEND = S.SPEND
export let OUTCOMES = S.OUTCOMES
export let DURATIONS = S.DURATIONS
export let FIXTURE_NOTE = S.FIXTURE_NOTE
export type RunMore = S.RunDetail & { tools?: string[]; models?: string[]; denials?: number; turns?: number; queued?: number | null; prompt?: string; task?: string; run?: string }
export type Running = { type: string; taskId: string; elapsed: string }
export let RUNNING: Running[] = [{ type: "morning-brief", taskId: "c1", elapsed: "2m 14s" }]
export let MAX_CONCURRENT = 1
export let PERFORMANCE = "Duration p50 1m 10s · p95 3m 02s · turns p50 4 · queue wait p95 40 s · 2.4 runs/day"
export let OUTCOME_NOTE = "failed 2 · both ai-news, handed to you"
export let NEEDS_A_LOOK: [string, string, string, string][] = [["needs-you", "ai-news", "needs re-approval", "#/type?name=ai-news"], ["expired", "arxiv-cs-ai", "fetch failing · backing off until 14:00", "#/trigger?name=arxiv-cs-ai"], ["blocked", "weekly-review", "wired to no type", "#/trigger?name=weekly-review"], ["ready", "job-watch", "never activated", "#/type?name=job-watch"], ["ready", "remoteok-jobs", "never activated", "#/trigger?name=remoteok-jobs"]]
export type Health = { gate: string; reason: string; paused: boolean; blockedToday: number; windows: [string, number, number][]; runway: number; ticksPerDay: string; silences: number; ticks: [string, string][]; mcp: string }
export let HEALTH: Health = { gate: "clear", reason: "Nothing is holding work back.", paused: false, blockedToday: 0, windows: [["5-hour", 0.41, 0.7], ["7-day", 0.12, 0.8]], runway: 0.15, ticksPerDay: "2,880", silences: 0, ticks: [["07:10", "nothing runnable ×38"], ["07:00", "launched morning-brief"], ["06:10", "launched hn-watch"]], mcp: "telegram-push, github" }

// ---- the poll ------------------------------------------------------------------------------------------------
const POLL_MS = 10_000, MAX_MS = 80_000
const listeners = new Set<() => void>()
let version = 0
const notify = () => { version++; listeners.forEach(f => f()) }
/** One subscription at the App root: a poll that changed something re-renders, merged records keep their DOM. */
export const useLive = () => useSyncExternalStore(f => { listeners.add(f); start(); return () => { listeners.delete(f) } }, () => version)

const get: Fetch = url => fetch(url, { headers: { accept: "application/json" } })
async function json<T>(url: string): Promise<T> {
  const r = await get(url)
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`)
  return (await r.json()) as T
}

let want = { root: "holonic-node", sub: "all", archive: false }, loadedScope = ""
let taskMap = new Map(TASKS.map(t => [t.id, t]))
let timer: number | undefined, delay = POLL_MS, started = false, inflight = false, rerun = false
function start() {
  if (started || typeof window === "undefined") return
  started = true
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll() })
  poll()
}
function schedule() { clearTimeout(timer); timer = window.setTimeout(() => { if (!document.hidden) poll(); else schedule() }, delay) }

// /stats is the heavy read (it walks the task store and every run record): seconds, not milliseconds. It runs on
// its own slower cadence and never holds up the 10 s poll; the fast reads apply with the last metrics in hand.
const STATS_MS = 60_000
let metrics: ApiMetrics = {}, statsAt = 0, statsInflight = false, last: Omit<Input, "m"> | undefined
function pollStats() {
  if (statsInflight || Date.now() - statsAt < STATS_MS) return
  statsInflight = true
  json<{ metrics: ApiMetrics }>("stats")
    .then(s => { metrics = s.metrics; statsAt = Date.now(); if (last) { apply({ ...last, m: metrics }); notify() } })
    .catch(() => {})
    .finally(() => { statsInflight = false })
}

export async function poll() {
  // A scope change during a poll is not lost: the poll in flight read the old scope, so one more follows it.
  if (inflight) { rerun = true; return }
  inflight = true
  try {
    const scope = want.sub === "all" ? want.root : `${want.root}.${want.sub}`
    pollStats()
    const [projects, attention, types, triggers, runs, running, ticks, mcp, day, month30, week, tasks] = await Promise.all([
      json<{ projects: ApiProject[] }>("projects"), json<ApiAttention>("attention"),
      json<{ session_types: ApiType[] }>("session-types"), json<{ triggers: ApiTrigger[] }>("triggers"),
      json<{ sessions: ApiRun[] }>("sessions?limit=500"),
      json<{ running: ApiRunning[] }>("running"), json<{ ticks: ApiTick[] }>("ticks?limit=50"),
      json<{ mcp_servers: { name: string }[] }>("mcp-servers"),
      json<ApiSpend>("spend?by=day&from=" + isoDay(6)), json<ApiSpend>("spend?by=day&from=" + isoDay(29)), json<ApiSpend>("spend?by=week"),
      loadTasks(scope),
    ])
    last = { projects: projects.projects, attention, types: types.session_types, triggers: triggers.triggers, runs: runs.sessions, running: running.running, ticks: ticks.ticks, mcp: mcp.mcp_servers, day, month30, week, tasks, scope }
    apply({ ...last, m: metrics })
    CONN = { mode: "live", failures: 0, lastGood: Date.now() }
    delay = POLL_MS
  } catch (e) {
    CONN = { ...CONN, failures: CONN.failures + 1, error: String(e) }
    delay = Math.min(delay * 2, MAX_MS)
  } finally {
    inflight = false
    notify()
    if (rerun) { rerun = false; poll() } else schedule()
  }
}

/** The scope in focus only: open work, plus done and cancelled when the archive is asked for. */
async function loadTasks(scope: string): Promise<ApiTask[]> {
  const statuses = want.archive ? ["pending", "completed", "deleted"] as const : ["pending"] as const
  const lists = await Promise.all(statuses.map(s => fetchScope<ApiTask>(get, scope, s)))
  return lists.flat()
}

/** The Tasks view says which scope it shows; a new scope is fetched at once rather than at the next poll. */
export function useScopeTasks(root: string, sub: string, archive: boolean): S.Task[] {
  useEffect(() => {
    const changed = want.root !== root || want.sub !== sub || want.archive !== archive
    want = { root, sub, archive }
    if (changed) poll()
  }, [root, sub, archive])
  const scope = sub === "all" ? root : `${root}.${sub}`
  return TASKS.filter(t => inScope(t.project, scope) && (archive || t.state !== "completed"))
}
export const useRuns = (): S.Run[] => RUNS
export const allTasks = () => TASKS

/** A body is read when its panel opens, never for the whole scope. */
export function useBody(id: string) {
  useEffect(() => {
    if (CONN.mode !== "live" || id in BODY) return
    get(`tasks/${encodeURIComponent(id)}/body`).then(async r => { BODY = { ...BODY, [id]: r.ok ? await r.text() : "" }; notify() }).catch(() => {})
  }, [id])
  return BODY[id] || undefined
}
/** The prompt is the one large field of a run, read when its page opens. */
export function useRunPrompt(id: string) {
  const d = RUN_DETAIL[id]
  useEffect(() => {
    if (CONN.mode !== "live" || !d?.task || d.prompt !== undefined) return
    json<{ prompt?: string }>(`sessions/${d.task}/${d.run}`).then(r => { RUN_DETAIL = { ...RUN_DETAIL, [id]: { ...RUN_DETAIL[id], prompt: r.prompt ?? "" } }; notify() }).catch(() => {})
  }, [id, d])
  return d?.prompt
}

// ---- pu's shapes ----------------------------------------------------------------------------------------------
type ApiProject = { project: string; open_tasks: number }
export type ApiTask = { uuid: string; description: string; status: string; kind: string | null; project: string | null; parent: string | null; tags: string[]; depends: string[]; afk: boolean; active: boolean }
type ApiRow = { uuid: string; title: string; kind: string; project: string }
type ApiAttention = { waiting_on_you: { decisions: ApiRow[]; handed_to_you: ApiRow[]; never_activated: { kind: string; name: string; purpose: string }[] }; faults: { stale_run_claims: unknown[] } }
type ApiType = { name: string; purpose?: string; prompt?: string; mcp_servers?: string[]; agent_role?: string; grant: string; model?: string; claims?: { kind?: string; tags?: string[] }; enabled?: boolean; retired?: boolean; origin?: string }
type ApiTrigger = { name: string; purpose?: string; source: { kind: string; url?: string; every_seconds?: number; cron?: string; tz?: string }; emits?: { kind?: string; tags?: string[]; priority?: string }; enabled?: boolean; retired?: boolean }
type ApiRun = { run_id: string; task_uuid: string; session_type: string; recorded_at: string; outcome: string; detail?: string; cost_usd?: number; duration_ms?: number; num_turns?: number; tool_calls?: string[]; models_used?: string[]; permission_denials?: string[]; tokens?: Record<string, number>; report?: string; report_outcome?: string | null; report_structured?: boolean; needs_human?: string | null; failure_class?: string | null; queued_seconds?: number | null; trigger?: string | null; agent_role?: string }
type Window = { runs?: number; outcomes?: Record<string, number> }
type ApiMetrics = { gate?: { blocked?: boolean; paused?: boolean; reason?: string; cost?: { spent_today_usd?: number }; windows?: { name: string; utilization: number; ceiling: number }[]; concurrency?: { max?: number; runway_fraction?: number } }; triggers?: Record<string, { fires?: { d30?: number }; expired_d30?: { count?: number }; fetches?: { failing_now?: boolean }; backoff?: { until?: string | null }; freshness?: { last_fired_at?: string | null } }>; unit?: { blocked_seconds?: { today?: number }; liveness?: { ticks_per_day?: number; silences?: number } }; placement_faults?: Record<string, number>; sessions?: { d7?: Window } }
type ApiRunning = { task_uuid: string; session_type?: string; elapsed_seconds?: number | null }
type ApiTick = { at: string; ran: boolean; reason?: string; session_type?: string | null; emitted?: { trigger: string; task_uuid?: string; items?: number; error?: string; fired?: boolean }[] }
type ApiSpend = { periods: { period: string; cost_usd: number }[]; total_usd: number }

// ---- adapting them to what the pages read -----------------------------------------------------------------
const BUILTINS = ["ask", "execution", "intake", "research"]
const DEFAULT_FOR_KIND: Record<string, string> = { execution: "execution", research: "research" }
const isoDay = (back: number) => new Date(Date.now() - back * 864e5).toISOString().slice(0, 10)
const pad = (n: number) => String(n).padStart(2, "0")
const at = (iso?: string | null) => { if (!iso) return ""; const d = new Date(iso); return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}` }
const hm = (iso?: string | null) => (iso ? at(iso).slice(6) : "")
const dur = (ms?: number | null) => { if (ms == null) return "—"; const s = Math.round(ms / 1000); return `${Math.floor(s / 60)}m ${pad(s % 60)}s` }
const kilo = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n))
const q = (xs: number[], p: number) => { if (!xs.length) return 0; const s = [...xs].sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.floor(p * s.length))] }
const merge = <T extends object>(prev: T[], next: T[], id: (x: T) => string) => [...mergeById(new Map(prev.map(x => [id(x), x])), next, id).values()]
const TOKEN_KEYS = ["input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "thinking_tokens"]

export function toTask(t: ApiTask, pending: Set<string>): S.Task {
  const tags = t.tags ?? [], kind = t.kind ?? "task"
  const state: S.Task["state"] = t.status === "completed" || t.status === "deleted" ? "completed" : kind === "map" ? "open"
    : t.active ? "running" : tags.includes("expired") ? "expired" : tags.includes("needs_human") ? "needs-you"
    : (t.depends ?? []).some(d => pending.has(d)) ? "blocked" : "ready"
  // pu's audience rule (stats.py audience()): grillings, prototypes and handed-over work are yours.
  const yours = kind === "grilling" || kind === "prototype" || tags.includes("needs_human")
  return { id: t.uuid, title: t.description, kind, project: t.project ?? "", map: t.parent, deps: t.depends ?? [], state, afk: t.afk, yours }
}

function parseReport(text?: string) {
  if (!text) return undefined
  const field = (k: string) => text.match(new RegExp(`^\\s*\\**\\s*${k}\\s*\\**\\s*:\\s*\\**\\s*(.*)$`, "im"))?.[1]?.trim() ?? ""
  return { outcome: field("OUTCOME"), needsHuman: field("NEEDS HUMAN"), goal: field("GOAL"), actions: field("ACTIONS"), reasoning: field("REASONING"), evidence: field("EVIDENCE"), text }
}

type Input = { projects: ApiProject[]; attention: ApiAttention; types: ApiType[]; triggers: ApiTrigger[]; runs: ApiRun[]; m: ApiMetrics; running: ApiRunning[]; ticks: ApiTick[]; mcp: { name: string }[]; day: ApiSpend; month30: ApiSpend; week: ApiSpend; tasks: ApiTask[]; scope: string }
function apply(d: Input) {
  const now = Date.now(), week = d.runs.filter(r => now - Date.parse(r.recorded_at) < 7 * 864e5)

  // tasks: the scope in focus replaces the previous scope's records; unchanged ones keep their identity
  const pending = new Set(d.tasks.filter(t => t.status === "pending").map(t => t.uuid))
  if (loadedScope !== d.scope) taskMap = new Map()
  loadedScope = d.scope
  taskMap = mergeById(taskMap, d.tasks.map(t => toTask(t, pending)), t => t.id)
  TASKS = [...taskMap.values()]
  byId = taskMap
  MAPS = TASKS.filter(t => t.kind === "map")
  PRIORITY = {}

  const roots: Record<string, number> = {}, subs: Record<string, Record<string, number>> = {}
  for (const p of d.projects) {
    const [root, sub] = p.project.split(".")
    roots[root] = (roots[root] ?? 0) + p.open_tasks
    if (sub) (subs[root] ??= {})[sub] = (subs[root][sub] ?? 0) + p.open_tasks
  }
  ROOTS = Object.entries(roots).sort((a, b) => b[1] - a[1])
  subsByRoot = Object.fromEntries(Object.entries(subs).map(([r, s]) => [r, Object.entries(s).sort((a, b) => b[1] - a[1])]))

  // attention
  const w = d.attention.waiting_on_you, never = new Set(w.never_activated.map(n => n.name))
  const faults = d.attention.faults.stale_run_claims.length + Object.values(d.m.placement_faults ?? {}).reduce((a, b) => a + (Number(b) || 0), 0)
  ATTENTION = {
    decisions: w.decisions.map(x => [x.kind, x.title, x.project, x.uuid] as [string, string, string, string]), decisionsTotal: w.decisions.length,
    handed: w.handed_to_you.map(x => [x.title, "handed to you"] as [string, string]),
    neverActivated: w.never_activated.map(n => [n.kind === "session_type" ? "type" : "trigger", n.name, n.purpose] as [string, string, string]), neverTotal: w.never_activated.length, faults,
  }

  // runs
  const titleOf = (uuid: string, r: ApiRun) => byId.get(uuid)?.title ?? (r.trigger ? `${r.trigger} · ${uuid.slice(0, 8)}` : uuid.slice(0, 8))
  const detail: Record<string, RunMore> = {}
  RUNS = merge(RUNS === S.RUNS ? [] : RUNS, d.runs.map(r => {
    const tok = r.tokens ?? {}, total = TOKEN_KEYS.reduce((a, k) => a + (tok[k] ?? 0), 0)
    const report = r.report_structured ? parseReport(r.report) : undefined
    detail[r.run_id] = {
      report, detail: report ? undefined : (r.report || r.detail || ""), attempt: r.failure_class ? `failure_class ${r.failure_class}` : "—",
      tokens: total ? TOKEN_KEYS.map(k => tok[k] ?? 0) : undefined, tools: r.tool_calls ?? [], models: r.models_used ?? [],
      denials: (r.permission_denials ?? []).length, turns: r.num_turns, queued: r.queued_seconds, task: r.task_uuid, run: r.run_id,
      prompt: RUN_DETAIL[r.run_id]?.prompt,
    }
    return {
      id: r.run_id, at: at(r.recorded_at), type: r.session_type, task: titleOf(r.task_uuid, r), taskId: r.task_uuid,
      outcome: report?.outcome || r.report_outcome || `${r.outcome}${r.report_structured ? "" : " · no report"}`,
      needsYou: r.needs_human && r.needs_human !== "none" ? r.needs_human : r.report_structured ? "none" : "—",
      cost: `$${(r.cost_usd ?? 0).toFixed(2)}`, duration: dur(r.duration_ms), tokens: total ? kilo(total) : "not recorded",
    }
  }), r => r.id!)
  RUN_DETAIL = detail

  // session types: the records, plus the built-ins (directories, not records)
  const runsOf = (name: string) => week.filter(r => r.session_type === name)
  const typeOf = (name: string, unit: string, on: S.TypeOn, role: string, grant: string, model: string): S.SessionType => {
    const rs = runsOf(name), costs = rs.map(r => r.cost_usd ?? 0), last = d.runs.find(r => r.session_type === name)
    const lastOutcome = last && (last.report_outcome || last.outcome)
    return {
      name, unit, on, role, grant, model, runs: rs.length, cost: costs.reduce((a, b) => a + b, 0),
      costs: [Math.min(...costs, 0) || 0, costs.length ? costs.reduce((a, b) => a + b, 0) / costs.length : 0, q(costs, 0.5), q(costs, 0.95), Math.max(0, ...costs)],
      lastRun: !!last, last: last ? `${lastOutcome} · ${at(last.recorded_at)} · $${(last.cost_usd ?? 0).toFixed(2)}` : "not run yet",
    }
  }
  const live = d.types.filter(t => !t.retired)
  TYPES = merge(TYPES === S.TYPES ? [] : TYPES, [
    ...live.map(t => typeOf(t.name, t.name.includes(".") ? t.name.split(".")[0] : "pu", t.enabled === true ? "on" : never.has(t.name) ? "never activated" : "off", t.agent_role ?? "system", t.grant, t.model ?? "default")),
    ...BUILTINS.filter(n => !live.some(t => t.name === n)).map(n => typeOf(n, "pu (built-in)", "on", "system", n === "execution" ? "repo" : n === "research" ? "web" : "none", "default")),
  ], t => t.name)
  RETIRED = d.types.filter(t => t.retired).map(t => t.name)
  TYPE_RECORD = Object.fromEntries(live.map(t => [t.name, { purpose: t.purpose ?? "", prompt: t.prompt ?? "", mcp: t.mcp_servers ?? [], claims: t.claims?.kind ? `${t.claims.kind}${(t.claims.tags ?? []).length ? ` · tags ${t.claims.tags!.join(", ")}` : ""}` : "none" }]))

  // triggers: wired to the type whose claim matches what they emit, most specific first
  const claimant = (g: ApiTrigger) => {
    const kind = g.emits?.kind ?? "", tags = g.emits?.tags ?? []
    const fits = live.filter(t => t.claims?.kind === kind && (t.claims.tags ?? []).every(x => tags.includes(x)))
      .sort((a, b) => (b.claims?.tags?.length ?? 0) - (a.claims?.tags?.length ?? 0))
    return fits[0]?.name ?? DEFAULT_FOR_KIND[kind] ?? ""
  }
  const liveTriggers = d.triggers.filter(g => !g.retired)
  TRIGGERS = merge(TRIGGERS === S.TRIGGERS ? [] : TRIGGERS, liveTriggers.map(g => {
    const st = d.m.triggers?.[g.name] ?? {}, src = g.source, type = claimant(g)
    const fired = st.freshness?.last_fired_at
    const health = st.fetches?.failing_now ? `fetch failing${st.backoff?.until ? ` · backing off until ${hm(st.backoff.until)}` : ""}`
      : !type ? "wired to no type" : fired ? `fired ${at(fired)}` : "never fired"
    return {
      name: g.name, type, health, kind: g.emits?.kind ?? "—", fires: st.fires?.d30 ?? 0, expired: st.expired_d30?.count ?? 0,
      on: g.enabled === true ? "on" : never.has(g.name) ? "never activated" : "off",
      src: src.kind === "schedule" ? `schedule · ${src.cron ? `${src.cron} ${src.tz ?? "UTC"}` : `every ${src.every_seconds} s`}` : `${src.kind} · every ${src.every_seconds ?? 300} s`,
    } as S.Trigger
  }), g => g.name)
  EXTRA_YIELD = []

  // analytics, over the last 7 days of runs: pu's records, nothing invented
  const share = (rs: ApiRun[]) => {
    const c = rs.map(r => r.cost_usd ?? 0), total = c.reduce((a, b) => a + b, 0), ok = rs.filter(r => (r.report_outcome ?? r.outcome) === "done" || r.outcome === "resolved").length
    return [rs.length, total, rs.length ? total / rs.length : 0, q(c, 0.5), q(c, 0.95), ok ? `$${(total / ok).toFixed(2)}` : "—"] as const
  }
  const group = (key: (r: ApiRun) => string | null | undefined) => {
    const g: Record<string, ApiRun[]> = {}
    for (const r of week) { const k = key(r); if (k) (g[k] ??= []).push(r) }
    return Object.entries(g).map(([k, rs]) => [k, ...share(rs)] as [string, number, number, number, number, number, string])
  }
  BY_ROLE = group(r => r.agent_role ?? "system")
  BY_TRIGGER = group(r => r.trigger)
  PER_SUCCESS = Object.fromEntries(group(r => r.session_type).map(row => [row[0], row[6]]))
  TOKEN_RUNS = { withTokens: week.filter(r => TOKEN_KEYS.some(k => (r.tokens ?? {})[k])).length, total: week.length }
  WEEK_RUNS = week.length
  WEEK_COST = week.reduce((a, r) => a + (r.cost_usd ?? 0), 0)
  const outcomes: Record<string, number> = {}
  for (const r of week) { const o = r.report_outcome || r.outcome; outcomes[o] = (outcomes[o] ?? 0) + 1 }
  OUTCOMES = Object.entries(outcomes)
  const failed = week.filter(r => r.outcome === "failed" || r.report_outcome === "failed")
  OUTCOME_NOTE = failed.length ? `failed ${failed.length} · ${[...new Set(failed.map(r => r.session_type))].join(", ")}` : "nothing failed"
  const mins = week.map(r => (r.duration_ms ?? 0) / 60000)
  DURATIONS = (["<30s", "1m", "2m", "3m", "4m", "5m", "6m+"] as const).map((label, i) => [label, mins.filter(m => (i === 0 ? m < 0.5 : i === 6 ? m >= 5.5 : m >= i - 0.5 && m < i + 0.5)).length] as Pair)
  const ms = week.map(r => r.duration_ms ?? 0), turns = week.map(r => r.num_turns ?? 0), waits = week.map(r => r.queued_seconds ?? 0)
  PERFORMANCE = week.length ? `Duration p50 ${dur(q(ms, 0.5))} · p95 ${dur(q(ms, 0.95))} · turns p50 ${q(turns, 0.5)} · queue wait p95 ${Math.round(q(waits, 0.95))} s · ${(week.length / 7).toFixed(1)} runs/day` : "No runs in the last 7 days."
  FIXTURE_NOTE = `${d.runs.length} runs recorded; this list shows them newest first.`

  // spend: pu has no hourly series, so Today is built from today's run records, in 2-hour steps
  const today = new Date().toDateString()
  const hours = Array.from({ length: 13 }, (_, i) => [`${pad(i * 2)}:00`, 0] as Pair)
  for (const r of d.runs) { const t = new Date(r.recorded_at); if (t.toDateString() === today) hours[Math.min(12, Math.floor(t.getHours() / 2) + 1)][1] += r.cost_usd ?? 0 }
  const series = (s: ApiSpend, cut = 5) => s.periods.map(p => [p.period.slice(cut), +p.cost_usd.toFixed(2)] as Pair)
  DAYS = series(d.day)
  SPEND = { Today: hours.map(([h, v]) => [h, +v.toFixed(2)]), "7 days": DAYS, "30 days": series(d.month30), All: series(d.week, 0) }
  TRIGGER_LOG = d.ticks.flatMap(t => (t.emitted ?? []).map(e => [at(t.at), e.trigger, e.error ? "error" : "fired", e.task_uuid ? `1 task · ${e.items ?? 0} items` : "—", e.error ?? ""] as [string, string, string, string, string]))

  // status, controls and health
  const g = d.m.gate ?? {}
  STATUS = { needYou: w.decisions.length + w.handed_to_you.length + w.never_activated.length, gate: g.blocked ? "blocked" : "clear", gateReason: g.reason || "nothing is holding work back", running: d.running.length, paused: !!g.paused, today: g.cost?.spent_today_usd ?? 0, updated: `at ${new Date().toLocaleTimeString()}` }
  RUNNING = d.running.map(r => ({ type: r.session_type ?? "session", taskId: r.task_uuid, elapsed: r.elapsed_seconds == null ? "—" : dur(r.elapsed_seconds * 1000) }))
  MAX_CONCURRENT = g.concurrency?.max ?? 1
  NEEDS_A_LOOK = [
    ...TRIGGERS.filter(t => t.health.startsWith("fetch failing")).map(t => ["expired", t.name, t.health, `#/trigger?name=${t.name}`]),
    ...TRIGGERS.filter(t => !t.type).map(t => ["blocked", t.name, "wired to no type", `#/trigger?name=${t.name}`]),
    ...TYPES.filter(t => t.on === "never activated").map(t => ["ready", t.name, "never activated", `#/type?name=${t.name}`]),
    ...TRIGGERS.filter(t => t.on === "never activated").map(t => ["ready", t.name, "never activated", `#/trigger?name=${t.name}`]),
  ] as [string, string, string, string][]
  HEALTH = {
    gate: g.blocked ? "blocked" : "clear", reason: g.reason || "Nothing is holding work back.", paused: !!g.paused,
    blockedToday: d.m.unit?.blocked_seconds?.today ?? 0, windows: (g.windows ?? []).map(x => [x.name, x.utilization, x.ceiling] as [string, number, number]),
    runway: g.concurrency?.runway_fraction ?? 0.15, ticksPerDay: (d.m.unit?.liveness?.ticks_per_day ?? 0).toLocaleString(), silences: d.m.unit?.liveness?.silences ?? 0,
    ticks: d.ticks.slice(0, 3).map(t => [hm(t.at), t.ran ? `launched ${t.session_type ?? "a session"}` : t.reason ?? "—"] as [string, string]),
    mcp: d.mcp.map(x => x.name).join(", ") || "none",
  }
}
