import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react"
import { useScreenState, type ScreenState } from "@/lib/screen-state"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Panel, KeyValue } from "@/components/ui/panel"
import { StateBlock, type BlockState } from "@/components/ui/state-block"
import { StateSwitch } from "@/components/ui/state-switch"
import { SidePanel } from "@/components/ui/side-panel"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { TaskGraph, type GraphMap } from "@/components/ui/task-graph"
import { LiveGraph } from "@/components/ui/live-graph"
import { PieChart, BarChart, AreaChart, YieldBar } from "@/components/ui/charts"
import { StatusIcon, type StatusKind } from "@/components/ui/status-icon"
import { ViewTabs, StatusStrip, ScopeBar } from "@/components/ui/shell"
import * as F from "@/lib/live"
import { useViewState, setViewState, cycle, scopeQuery } from "@/lib/store"
import { useScopeTasks, useRuns, allTasks, useBody, useRunPrompt } from "@/lib/live"

// The final pages (stage 6) of the chosen layouts: Tasks A, Sessions A, the standard right panel, run log in
// Analytics. Built on sample data; every button acts on it. ?state=… renders each state of the brief: empty and
// loading replace a block's data; error, stale, partial and offline keep the data and add a note.

const q = (k: string) => new URLSearchParams(location.hash.split("?")[1] ?? "").get(k)
const go = (h: string) => { location.hash = h }
const retry = () => { const [path, query = ""] = location.hash.split("?"); const p = new URLSearchParams(query); p.delete("state"); go(`${path}${p.size ? `?${p}` : ""}`) }
/** Inside the node frame (or offline) pu can't take a write: every write shows why it is off. */
const writeBlock = (state: ScreenState) => (window.self !== window.top || state === "permission" ? "writes are not open inside the node yet" : state === "offline" ? "pu is not reachable; nothing can be saved" : undefined)
/** The switches are the writes pu declares in GET /actions, so they work inside the node frame too. */
const switchBlock = (state: ScreenState) => (state === "permission" ? "switching is not open here" : state === "offline" || F.CONN.mode !== "live" ? "pu is not reachable; nothing can be switched" : undefined)

const since = () => (F.CONN.lastGood ? `${Math.max(1, Math.round((Date.now() - F.CONN.lastGood) / 60000))} min ago` : "a while ago")
const NOTE: Partial<Record<ScreenState, string>> = {
  get error() { return `pu could not be read just now. Showing the last good data, from ${since()}.` },
  get stale() { return `Updated ${since()} · pu has not answered the last ${F.CONN.failures === 1 ? "poll" : `${F.CONN.failures} polls`}; retrying.` },
  partial: "Part of this view could not be read; the rest is current.",
  offline: "pu is not reachable · showing sample data.",
}
/** A state lives in the block it affects: empty/loading replace the data; the rest keep it and add a note. */
function Stateful({ state, empty, children }: { state: ScreenState; empty?: string; children?: ReactNode }) {
  if (state === "loading") return <StateBlock state="loading" message="" />
  if (state === "empty") return <StateBlock state="empty" message={empty ?? "Nothing here yet."} />
  const note = NOTE[state]
  if (!note) return <>{children}</>
  return <><StateBlock state={state as BlockState} message={note} lastGood={state === "error" && F.CONN.lastGood ? new Date(F.CONN.lastGood).toLocaleTimeString() : undefined} onRetry={retry} />{children}</>
}

function Page({ id, title, view, children, status = true, before }: { id: string; title: ReactNode; view: string; children: ReactNode; status?: boolean; before?: ReactNode }) {
  const state = useScreenState()
  return (
    <main data-screen={id} className="mx-auto flex max-w-[1440px] flex-col gap-4 p-4 sm:p-6">
      {before}
      <h1 data-block="title" className="m-0 text-xl font-semibold text-foreground">{title}</h1>
      <div data-block="views"><ViewTabs current={view} /></div>
      {status && <div data-block="status"><StatusStrip status={F.STATUS} state={state === "loading" ? "loading" : state === "stale" ? "stale" : state === "error" ? "error" : "default"} /></div>}
      {children}
    </main>
  )
}

const Divider = ({ label }: { label: string }) => <div className="flex items-center gap-3 text-xs font-medium uppercase tracking-wider text-muted-foreground"><span>{label}</span><span className="h-px flex-1 bg-border" /></div>
const typeStatus = (on: F.TypeOn): StatusKind => on === "on" ? "running" : on === "needs re-approval" ? "needs-you" : on === "never activated" ? "ready" : "blocked"
const sessionColumns = () => [
  { title: "Triggers", nodes: F.TRIGGERS.map(t => ({ id: `t:${t.name}`, label: t.name, sub: t.health.split(" · ")[0], href: `#/trigger?name=${t.name}`, status: (t.health.startsWith("fetch failing") ? "expired" : typeStatus(t.on)) as StatusKind })) },
  { title: "Session types", nodes: F.TYPES.map(t => ({ id: `s:${t.name}`, label: t.name, sub: `${t.on} · ${t.model}`, href: `#/type?name=${t.name}`, status: typeStatus(t.on) })) },
  { title: "Latest runs", nodes: F.TYPES.filter(t => t.lastRun).map(t => { const r = F.RUNS.find(x => x.type === t.name); return { id: `r:${t.name}`, label: t.last.split(" · ")[0], sub: t.last.split(" · ").slice(1).join(" · "), href: `#/run?id=${r?.id ?? ""}&from=sessions`, status: (t.last.startsWith("failed") ? "expired" : t.last.startsWith("partial") ? "needs-you" : "completed") as StatusKind } }) },
]
const sessionEdges = (): [string, string][] => [...F.TRIGGERS.filter(t => t.type).map(t => [`t:${t.name}`, `s:${t.type}`] as [string, string]), ...F.TYPES.filter(t => t.lastRun).map(t => [`s:${t.name}`, `r:${t.name}`] as [string, string])]
const status = (t: F.Task): StatusKind => (t.state === "open" ? "ready" : t.state)
const link = "text-foreground underline underline-offset-4"

// ---------- Tasks (key screen, option A) ----------
/** The height left in the window below this element (minus `reserve`), never under `min`: the board fills what you see. */
function useFillHeight(min: number, reserve: number) {
  const ref = useRef<HTMLDivElement>(null), [h, setH] = useState(min)
  useLayoutEffect(() => {
    const fit = () => { const top = ref.current ? ref.current.getBoundingClientRect().top + window.scrollY : 0; setH(Math.max(min, Math.round(window.innerHeight - top - reserve))) }
    fit(); window.addEventListener("resize", fit); return () => window.removeEventListener("resize", fit)
  }, [min, reserve])
  return [ref, h] as const
}
function TasksBody({ selected, behind = false }: { selected?: string; behind?: boolean }) {
  const screen = useScreenState(), state = behind ? "default" : screen, v = useViewState()
  const scoped = useScopeTasks(v.root, v.sub, v.archive), ids = new Set(scoped.map(t => t.id)), all = allTasks()
  const scopeName = v.sub === "all" ? v.root : `${v.root}.${v.sub}`, sq = scopeQuery(v)
  const mapOf = (id: string, title: string): GraphMap => {
    const mine = all.filter(t => t.map === id), shown = scoped.filter(t => t.map === id)
    return { id, title, done: mine.filter(t => t.state === "completed").length, total: mine.length, tasks: shown.map(t => ({ id: t.id, title: t.title, status: status(t), deps: t.deps.filter(d => ids.has(d)) })) }
  }
  const loose = scoped.filter(t => !t.map && t.kind !== "map")
  const maps: GraphMap[] = [...F.MAPS.filter(m => ids.has(m.id)).map(m => mapOf(m.id, m.title)), ...(loose.length ? [{ id: "nomap", title: "No map", done: 0, total: loose.length, tasks: loose.map(t => ({ id: t.id, title: t.title, status: status(t), deps: [] })) }] : [])]
  const yours = scoped.filter(t => t.yours && t.state !== "completed")
  const ready = scoped.filter(t => t.state === "ready" && t.kind !== "map").sort((a, b) => (F.PRIORITY[a.id] === "high" ? -1 : 0) - (F.PRIORITY[b.id] === "high" ? -1 : 0))
  const count = (s: string) => scoped.filter(t => t.state === s).length
  const [graphRef, frame] = useFillHeight(400, 84) // 84: the hint and counts lines under the board, and the page's bottom padding
  return (
    <Page id="tasks" title="Tasks" view="tasks">
      <div data-block="scope" className="flex flex-col gap-2">
        <ScopeBar roots={F.ROOTS} subs={F.subsOf(v.root)} root={v.root} sub={v.sub} onRoot={r => setViewState({ root: r, sub: "all" })} onSub={s => setViewState({ sub: s })} />
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
          <Button variant={v.archive ? "default" : "ghost"} size="sm" aria-pressed={v.archive} onClick={() => setViewState({ archive: !v.archive })}>Archive</Button>
          <span>{v.archive ? "done and cancelled tasks of this scope shown (loaded on request)" : "open work only; the archive loads only when you ask"}</span>
          <span className="flex flex-wrap items-center gap-3">{(["ready", "needs-you", "blocked", "running", "expired"] as StatusKind[]).map(s => <span key={s} className="flex items-center gap-1 whitespace-nowrap"><StatusIcon status={s} />{s === "ready" ? "can start" : s === "blocked" ? "waiting" : s.replace("-", " ")}</span>)}</span>
        </div>
      </div>
      <div className="flex flex-col gap-4 lg:flex-row">
        <section data-block="graph" aria-label="Dependency graph" className="flex min-w-0 flex-1 flex-col gap-2">
          <Stateful state={state} empty={`No open tasks in ${scopeName}.`}>
            {maps.length ? <div ref={graphRef}><TaskGraph maps={maps} selected={selected} viewKey="tasks" frameHeight={frame} completedShown={v.archive} taskHref={id => `#/task?id=${id}&${sq}`} mapHref={id => (id === "nomap" ? `#/tasks?${sq}` : `#/map?id=${id}&${sq}`)} /></div>
              : <StateBlock state="empty" message={`No open tasks in ${scopeName}. Other projects stay one click away above.`} />}
          </Stateful>
          {state === "default" && !maps.length && <p data-block="honesty" className="m-0 text-xs text-muted-foreground">graph not drawn · nothing open in this scope</p>}
        </section>
        <aside className="flex flex-col gap-4 lg:w-[300px] lg:shrink-0">
          <Panel data-block="queue" title="Next up (queue)" aside={<span className="text-xs text-muted-foreground">{ready.length} ready</span>}>
            <ul className="m-0 flex list-none flex-col gap-2 p-0 text-sm">{ready.slice(0, 8).map(t => <li key={t.id} className="flex items-center gap-2"><StatusIcon status={status(t)} /><a href={`#/task?id=${t.id}&${sq}`} className="truncate text-foreground underline-offset-4 hover:underline">{t.title}</a>{F.PRIORITY[t.id] === "high" && <Badge variant="outline">high</Badge>}</li>)}</ul>
            <p className="m-0 text-xs text-muted-foreground">Frontier: {count("ready")} ready · {count("blocked")} blocked · {count("running")} running</p>
          </Panel>
          <Panel data-block="yours" title={`Yours in ${scopeName} · ${yours.length}`} className="lg:min-h-0 lg:flex-1">
            <ul className="m-0 flex min-h-0 list-none flex-col gap-2 overflow-y-auto p-0 text-sm lg:flex-1">{yours.map(t => <li key={t.id} className="flex items-center gap-2"><StatusIcon status={status(t)} /><a href={`#/task?id=${t.id}&${sq}`} className="truncate text-foreground underline-offset-4 hover:underline">{t.title}</a></li>)}</ul>
            <a href="#/attention" className={link}>All {F.STATUS.needYou} that need you</a>
          </Panel>
        </aside>
      </div>
    </Page>
  )
}
const Tasks = () => <TasksBody />

// ---------- the standard right panel over its parent view ----------
function TaskPanel() {
  const t = F.byId.get(q("id") ?? "c3") ?? F.byId.get("c3") ?? F.TASKS[0], state = useScreenState(), v = useViewState(), sq = scopeQuery(v), body = useBody(t?.id ?? "")
  if (!t) return <TasksBody behind />
  const map = t.map ? F.byId.get(t.map) : undefined, blocks = F.TASKS.filter(x => x.deps.includes(t.id) && x.state !== "completed"), runs = F.RUNS.filter(r => r.taskId === t.id)
  const uuid = F.CONN.mode === "live" ? t.id : `3f2a9c1e-${t.id.padStart(4, "0")}-4c1d-9a7e-1b2c3d4e5f60`
  return (
    <>
      <TasksBody selected={t.id} behind />
      <SidePanel open onOpenChange={o => !o && go(`#/tasks?${sq}`)} backLabel="Back to tasks" backHref={`#/tasks?${sq}`} title={t.title}>
        {state === "empty" || state === "loading" ? <Stateful state={state} empty="This task could not be found in the scope in focus." /> : <>
        <div data-block="identity" className="flex flex-col gap-2 text-sm">
          <div className="flex flex-wrap items-center gap-2"><StatusIcon status={status(t)} /><Badge variant="outline">{t.kind}</Badge><Badge variant="outline">{status(t)}</Badge><Badge variant="outline">priority {F.PRIORITY[t.id] ?? "low"}</Badge>{t.afk && <Badge variant="secondary">afk</Badge>}{t.state === "needs-you" && <Badge variant="secondary">needs_human</Badge>}</div>
          <span>{map ? <a href={`#/map?id=${map.id}&${sq}`} className={link}>{map.title}</a> : "no map"} · <span className="text-muted-foreground">{t.project}</span></span>
          <span className="text-muted-foreground">uuid {uuid.slice(0, 8)}…</span>
        </div>
        <div data-block="actions" className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => navigator.clipboard?.writeText(uuid)}>Copy uuid</Button>
          <Button size="sm" disabled>Re-queue</Button><Button size="sm" disabled>Resolve</Button>
          <span className="text-xs text-muted-foreground">not available in pu yet{writeBlock(state) ? ` · ${writeBlock(state)}` : ""}</span>
        </div>
        <Panel data-block="blocking" title="Blocked by" framed={false}>
          {t.deps.length ? t.deps.map(d => <a key={d} href={`#/task?id=${d}&${sq}`} className={`${link} text-sm`}>{F.byId.get(d)?.title ?? `${d.slice(0, 8)}… (outside this scope)`}</a>) : <p className="m-0 text-sm text-muted-foreground">nothing</p>}
          <h3 className="m-0 text-sm font-medium">Blocks</h3>
          {blocks.length ? blocks.map(x => <a key={x.id} href={`#/task?id=${x.id}&${sq}`} className={`${link} text-sm`}>{x.title}</a>) : <p className="m-0 text-sm text-muted-foreground">nothing</p>}
        </Panel>
        <Panel data-block="runs" title="Runs on this task" framed={false}>
          {runs.map(r => <a key={r.id} href={`#/run?id=${r.id}&from=task`} className={`${link} text-sm`}>{r.at} · {r.outcome} · {r.cost}</a>)}
          {!runs.length && <p className="m-0 text-sm text-muted-foreground">not run yet</p>}
        </Panel>
        <Panel data-block="body" title="Body" framed={false}>
          <Stateful state={state} empty="This task has no body.">
            <pre className="m-0 whitespace-pre-wrap rounded-md bg-muted p-3 font-sans text-sm text-foreground">{body ?? "No body."}</pre>
          </Stateful>
        </Panel>
        </>}
      </SidePanel>
    </>
  )
}

function MapPanel() {
  const m = F.byId.get(q("id") ?? "m4") ?? F.byId.get("m4") ?? F.MAPS[0], state = useScreenState(), v = useViewState(), sq = scopeQuery(v), body = useBody(m?.id ?? "")
  if (!m) return <TasksBody behind />
  const all = F.ticketsOf(m.id, true), open = F.ticketsOf(m.id)
  return (
    <>
      <TasksBody behind />
      <SidePanel open onOpenChange={o => !o && go(`#/tasks?${sq}`)} backLabel="Back to tasks" backHref={`#/tasks?${sq}`} title={m.title} description={`${m.project} · ${open.length} open · ${all.length - open.length}/${all.length} done`}>
        {state === "empty" || state === "loading" ? <Stateful state={state} empty="No open tickets; the map can be closed." /> : <>
        <p data-block="map-identity" className="m-0 text-sm text-muted-foreground">A map with open tickets can't be closed.</p>
        <Panel data-block="tickets" title={`Open tickets · ${open.length}`} framed={false}>
          <Stateful state={state} empty="No open tickets; the map can be closed.">
            {open.map(t => <a key={t.id} href={`#/task?id=${t.id}&${sq}`} className="flex items-center gap-2 text-sm text-foreground underline-offset-4 hover:underline"><StatusIcon status={status(t)} />{t.title}</a>)}
          </Stateful>
        </Panel>
        <Panel data-block="map-body" title="Body" framed={false}><pre className="m-0 whitespace-pre-wrap rounded-md bg-muted p-3 font-sans text-sm text-foreground">{body ?? "No body."}</pre></Panel>
        </>}
      </SidePanel>
    </>
  )
}

// ---------- Needs attention ----------
function Attention() {
  const state = useScreenState()
  return (
    <Page id="attention" title={`Needs attention · ${F.STATUS.needYou}`} view="tasks" status={false}>
      <div className="flex flex-col gap-4">
        <Panel data-block="decisions" title={`Decisions waiting · ${F.ATTENTION.decisionsTotal}`} framed={false}>
          <Stateful state={state} empty="Nothing needs you. Last checked 8 s ago.">
            <ul className="m-0 flex list-none flex-col gap-2 p-0 text-sm">{F.ATTENTION.decisions.map(([k, t, p, id], i) => (
              <li key={i} className="flex flex-wrap items-center gap-2"><Badge variant="outline">{k}</Badge><a href={`#/task?id=${id}`} className={link}>{t}</a><span className="text-muted-foreground">{p}</span><Button variant="ghost" size="sm" onClick={() => navigator.clipboard?.writeText(id)}>Copy uuid</Button></li>))}</ul>
            <p className="m-0 text-sm text-muted-foreground">+ {F.ATTENTION.decisionsTotal - F.ATTENTION.decisions.length} more</p>
          </Stateful>
        </Panel>
        <div className="flex flex-col gap-4">
          <Panel data-block="handed" title={`Handed to you · ${F.ATTENTION.handed.length}`}>{F.ATTENTION.handed.map(([t, w]) => <a key={t} href="#/run?id=r3&from=attention" className={`${link} text-sm`}>{t} · {w} · open the run</a>)}</Panel>
          <Panel data-block="never-activated" title={`Never activated · ${F.ATTENTION.neverTotal}`}>{F.ATTENTION.neverActivated.map(([k, n, p]) => <a key={n} href={k === "type" ? `#/type?name=${n}` : `#/trigger?name=${n}`} className={`${link} text-sm`}>{k} · {n} · {p}</a>)}</Panel>
          <Panel data-block="faults" title="Faults · 0"><p className="m-0 text-sm">No stale claims. No placement faults.</p></Panel>
        </div>
      </div>
    </Page>
  )
}

// ---------- Sessions (key screen, option A) ----------
const filterValues = () => ({ role: ["all", ...new Set(F.TYPES.map(t => t.role))], grant: ["all", ...new Set(F.TYPES.map(t => t.grant))], model: ["all", ...new Set(F.TYPES.map(t => t.model))] })
/** A switch through pu's declared action: saving… while pu answers, then pu's own state, or pu's refusal in words. */
function Toggle({ kind, name, initial, block }: { kind: "type" | "trigger"; name: string; initial: F.TypeOn; block?: string }) {
  const [pending, setPending] = useState<boolean>(), [saved, setSaved] = useState<boolean>(), [error, setError] = useState<string>(), [offNote, setOffNote] = useState(false)
  const builtIn = kind === "type" && F.TYPES.find(t => t.name === name)?.unit.includes("built-in")
  // What pu accepted is shown until a poll brings pu's own record round to it; showing the older polled state in
  // between would make the next click send the same value again.
  const shown = pending ?? (saved !== undefined && saved !== (initial === "on") ? saved : undefined)
  const state: F.TypeOn = shown === undefined ? initial : shown ? "on" : "off"
  useEffect(() => { if (saved !== undefined && saved === (initial === "on")) setSaved(undefined) }, [initial, saved])
  const set = async (on: boolean) => {
    setError(undefined); setOffNote(false); setPending(on)
    try { await F.setEnabled(kind, name, on); setSaved(on); setOffNote(!on) } catch (e) { setError(`pu refused: ${(e as Error).message}`) }
    setPending(undefined)
  }
  return <div className="flex flex-col gap-1"><StateSwitch name={name} state={state} pending={pending !== undefined} disabledReason={builtIn ? "built-in: always on" : block} error={error} onChange={set} />{offNote && <span role="status" className="text-xs text-muted-foreground">switched off · running sessions are not stopped</span>}</div>
}
function PauseAll({ block }: { block?: string }) {
  const [ask, setAsk] = useState(false), [pending, setPending] = useState<boolean>(), [saved, setSaved] = useState<boolean>(), [error, setError] = useState<string>()
  const paused = pending ?? (saved !== undefined && saved !== F.STATUS.paused ? saved : F.STATUS.paused)
  useEffect(() => { if (saved !== undefined && saved === F.STATUS.paused) setSaved(undefined) }, [saved, F.STATUS.paused])
  const set = async (p: boolean) => {
    setAsk(false); setError(undefined); setPending(p)
    try { await F.setPaused(p); setSaved(p) } catch (e) { setError(`pu refused: ${(e as Error).message}`) }
    setPending(undefined)
  }
  return (
    <div className="flex items-center gap-3">
      <span className="whitespace-nowrap text-sm text-foreground">Pause all</span>
      <StateSwitch name="Pause all" state={paused ? "on" : "off"} pending={pending !== undefined} disabledReason={block} error={error} onChange={on => (on ? setAsk(true) : set(false))} />
      <ConfirmDialog open={ask} title="Pause every session?" body="pu stops starting new sessions. Sessions already running finish." confirmLabel="Pause all" onConfirm={() => set(true)} onCancel={() => setAsk(false)} />
    </div>
  )
}

// Max concurrent is read-only here (Andrey, 2026-11-01): it is set in the holonic node's settings, which the "?" leads to.
// ponytail: ticket 62f834ce-3379-40dc-a1a0-8d52c6151c59 (node settings page under holonic-node.gateway, calling pu POST /concurrency;
// it defines the URL). The link stays on Sessions until then (then
// open it with target="_top": the page lives in the node frame).
const NODE_SETTINGS_HREF = "#/sessions"
const MaxConcurrent = () => (
  <span className="flex items-center gap-2">{F.MAX_CONCURRENT}
    <a href={NODE_SETTINGS_HREF} aria-label="Where max concurrent is set: the holonic node's settings" title="Set in the holonic node's settings"
      className="flex size-5 items-center justify-center rounded-full border border-muted-foreground text-xs leading-none text-muted-foreground no-underline hover:border-foreground hover:text-foreground">?</a>
  </span>
)

function SessionsBody({ behind = false }: { behind?: boolean }) {
  const screen = useScreenState(), state = behind && screen !== "permission" ? "default" : screen, block = switchBlock(state), v = useViewState()
  const types = F.TYPES.filter(t => (["role", "grant", "model"] as const).every(k => v.typeFilter[k] === "all" || t[k] === v.typeFilter[k]))
  return (
    <Page id="sessions" title="Sessions" view="sessions">
      <div className="flex flex-col gap-4 lg:flex-row">
        <Panel data-block="live" title="Triggers → session types → runs" framed={false} className="flex-1" aside={<span className="text-xs text-muted-foreground">{F.RUNNING.length} running</span>}>
          <LiveGraph columns={sessionColumns()} edges={sessionEdges()} orderKey="pu.sessions.order" />
        </Panel>
        <div className="flex flex-col gap-4 lg:w-[320px] lg:shrink-0">
          <Panel data-block="controls" title="Controls">
            <PauseAll block={block} />
            <KeyValue rows={[["Max concurrent", <MaxConcurrent key="m" />], ["Running now", F.RUNNING.length ? <span key="r" className="flex flex-col">{F.RUNNING.map(r => <a key={r.taskId} href={`#/task?id=${r.taskId}`} className="underline underline-offset-4">{r.type} · {r.elapsed}</a>)}</span> : "nothing"]]} />
            <p className="m-0 text-xs text-muted-foreground">Pause stops new starts; running sessions finish.</p>
          </Panel>
          <Panel data-block="session-attention" title={`Needs a look · ${F.NEEDS_A_LOOK.length}`}>
            <ul className="m-0 flex list-none flex-col gap-2 p-0 text-sm">
              {(F.NEEDS_A_LOOK as [StatusKind, string, string, string][]).map(([st, n, why, href]) => (
                <li key={n} className="flex items-start gap-2"><StatusIcon status={st} /><span><a href={href} className={link}>{n}</a> <span className="text-muted-foreground">· {why}</span></span></li>))}
            </ul>
          </Panel>
        </div>
      </div>
      <Panel data-block="types" title={`Session types · ${types.length}`} aside={<Button variant="outline" size="sm" onClick={() => go("#/type?name=new")}>New session type</Button>}>
        <div data-block="filters" className="flex flex-wrap items-center gap-2">{(["role", "grant", "model"] as const).map(k => <Button key={k} variant={v.typeFilter[k] === "all" ? "outline" : "default"} size="sm" onClick={() => setViewState(x => ({ typeFilter: { ...x.typeFilter, [k]: cycle(filterValues()[k], x.typeFilter[k]) } }))}>{k[0].toUpperCase() + k.slice(1)}: {v.typeFilter[k]}</Button>)}<span className="text-xs text-muted-foreground">{types.length} of {F.TYPES.length} shown · click a filter to cycle</span></div>
        <Stateful state={state} empty="No session types of your own yet. Built-ins appear once they have run.">
          <Table><TableHeader className={HEAD}><TableRow><TableHead>Type</TableHead><TableHead>Switch</TableHead><TableHead>Unit</TableHead><TableHead>Role · grant · model</TableHead><TableHead>Fed by</TableHead><TableHead>Last run</TableHead></TableRow></TableHeader>
            <TableBody>{types.map(t => (
              <TableRow key={t.name} className={ROW}>
                <TableCell className={NAME}><a href={`#/type?name=${t.name}`} className={link}>{t.name}</a></TableCell>
                <TableCell><Toggle kind="type" name={t.name} initial={t.on} block={block} /></TableCell>
                <TableCell>{t.unit}</TableCell><TableCell>{t.role} · {t.grant} · {t.model}</TableCell>
                <TableCell>{F.fedBy(t) ? <a href={`#/trigger?name=${F.fedBy(t)}`} className={link}>{F.fedBy(t)}</a> : "—"}</TableCell>
                <TableCell>{t.lastRun ? <a href={`#/run?id=${F.RUNS.find(r => r.type === t.name)?.id ?? ""}&from=sessions`} className={link}>{t.last}</a> : t.last}</TableCell>
              </TableRow>))}
              {F.RETIRED.map(n => <TableRow key={n} className={ROW}><TableCell className={`${NAME} text-muted-foreground`}>{n}</TableCell><TableCell className="text-muted-foreground" colSpan={5}>retired · no switch</TableCell></TableRow>)}</TableBody></Table>
        </Stateful>
      </Panel>
      <Panel data-block="triggers" title={`Triggers · ${F.TRIGGERS.length}`} aside={<Button variant="outline" size="sm" onClick={() => go("#/trigger?name=new")}>New trigger</Button>}>
        <Table><TableHeader className={HEAD}><TableRow><TableHead>Trigger</TableHead><TableHead>Switch</TableHead><TableHead>Source</TableHead><TableHead>Creates</TableHead><TableHead>Health</TableHead></TableRow></TableHeader>
          <TableBody>{F.TRIGGERS.map(t => (
            <TableRow key={t.name} className={ROW}><TableCell className={NAME}><a href={`#/trigger?name=${t.name}`} className={link}>{t.name}</a></TableCell><TableCell><Toggle kind="trigger" name={t.name} initial={t.on} block={block} /></TableCell><TableCell>{t.src}</TableCell><TableCell>{t.kind} → {t.type || "no type claims it"}</TableCell><TableCell>{t.health}</TableCell></TableRow>))}</TableBody></Table>
      </Panel>
    </Page>
  )
}
const Sessions = () => <SessionsBody />

/** A labelled field with its message (aria-describedby; aria-invalid when the message is an error). */
function Field({ id, label, error, hint, children }: { id: string; label: string; error?: string; hint?: string; children: ReactNode }) {
  return <div className="flex flex-col gap-1"><Label htmlFor={id} className="text-sm text-foreground">{label}</Label>{children}{(error || hint) && <span id={`${id}-msg`} role={error ? "alert" : undefined} className="text-xs text-muted-foreground">{error ?? hint}</span>}</div>
}
const GRANTS = ["none", "read", "web", "repo"]
/** Edits go through pu's owner door (GET /actions), so they work inside the node frame: only sample data blocks them. */
const editBlock = (state: ScreenState) => switchBlock(state)
const refused = (e: unknown) => `pu refused: ${(e as Error).message}`
/** The --allowedTools value of a dry run's argv: what the session may actually call. */
const allowedOf = (argv: string[] = []) => { const i = argv.indexOf("--allowedTools"); return i >= 0 ? argv[i + 1] : "nothing" }

/** A panel opened before pu's first answer starts on sample data; when pu's record arrives the form is rebuilt from
 *  it once, so it never edits a sample. */
function TypePanel() {
  const name = q("name") ?? "morning-brief"
  return <TypeForm key={`${name}:${F.TYPE_RECORD[name] ? "pu" : "sample"}`} />
}

function TypeForm() {
  const name = q("name") ?? "morning-brief", isNew = name === "new", t = F.TYPES.find(x => x.name === name) ?? F.TYPES[0]
  const builtIn = !isNew && t.unit.includes("built-in"), state = useScreenState(), block = editBlock(state) ?? (builtIn ? "pu can't edit built-in types yet" : undefined)
  const rec = F.TYPE_RECORD[t.name]
  const [f, setF] = useState(() => isNew ? { name: "", purpose: "", prompt: "", model: "haiku", role: "system", grant: "none", mcp: [] as string[] } : rec ? { name: t.name, purpose: rec.purpose, prompt: rec.prompt, model: t.model, role: t.role, grant: t.grant, mcp: rec.mcp } : { name: t.name, purpose: "Write the 07:00 brief from yesterday's runs and the queue", prompt: "You write Andrey's morning brief. Read pu's queue, yesterday's runs and their reports…", model: t.model, role: t.role, grant: t.grant, mcp: ["telegram-push"] })
  const [errors, setErrors] = useState<Record<string, string>>({}), [said, setSaid] = useState(""), [confirm, setConfirm] = useState<{ body: string; apply: () => void } | null>(null)
  const widen = (body: string, apply: () => void) => setConfirm({ body, apply })
  const fields = () => ({ purpose: f.purpose, prompt: f.prompt, model: f.model === "default" ? undefined : f.model, agent_role: f.role, grant: f.grant, mcp_servers: f.mcp })
  const save = async () => {
    const e: Record<string, string> = {}
    if (isNew && !/^[a-z0-9][a-z0-9._-]{0,63}$/.test(f.name)) e.name = "Use lowercase letters, digits, . _ - (up to 64), starting with a letter or digit."
    if (!f.purpose.trim()) e.purpose = "Say in one line what this type is for."
    if (!f.prompt.trim()) e.prompt = "A type needs its instructions."
    setErrors(e); setSaid("")
    if (Object.keys(e).length) return
    setSaid("saving…")
    try { await F.editType(f.name, fields()); setSaid(isNew ? "Saved to pu, switched off: switch it on when it is ready." : "Saved to pu.") } catch (err) { setSaid(refused(err)) }
  }
  const dryRun = async () => {
    const r = await F.dryRunType({ name: f.name, ...fields() })
    return `Would run on ${r.model ?? "the CLI default"} as ${r.agent_role}, grant ${r.grant}, in ${r.cwd}; allowed: ${allowedOf(r.argv)}. Nothing written, nothing launched.`
  }
  const back = "#/sessions"
  return (
    <>
      <SessionsBody behind />
      <SidePanel open onOpenChange={o => !o && go(back)} backLabel="Back to sessions" backHref={back} title={isNew ? "New session type" : t.name} description={isNew ? "pu writes origin and unit itself" : `${t.unit}${t.unit === "pu" ? " (no parent)" : ""} · ${t.role} · ${t.model}`}>
        <div data-block="panel-title">{isNew ? <span className="text-sm text-muted-foreground">off until you switch it on after saving</span> : <Toggle kind="type" name={t.name} initial={t.on} block={switchBlock(state)} />}</div>
        <Panel data-block="definition" title="Definition">
          <Stateful state={state} empty="Fill the name, purpose, prompt and grant; the dry run needs them.">
            <Field id="t-name" label="Name" error={errors.name} hint={isNew ? "fixed once saved" : "fixed"}><Input id="t-name" value={f.name} disabled={!isNew || !!block} aria-invalid={!!errors.name || undefined} aria-describedby="t-name-msg" onChange={e => setF({ ...f, name: e.target.value })} /></Field>
            <Field id="t-unit" label="Unit" hint="not available in pu yet: reset to the source's definition"><span className="flex items-center gap-2 text-sm">{isNew ? "pu" : t.unit}<Button id="t-unit" variant="outline" size="sm" disabled>Reset to source</Button></span></Field>
            <Field id="t-purpose" label="Purpose" error={errors.purpose}><Input id="t-purpose" value={f.purpose} disabled={!!block} aria-invalid={!!errors.purpose || undefined} aria-describedby="t-purpose-msg" onChange={e => setF({ ...f, purpose: e.target.value })} /></Field>
            <Field id="t-model" label="Model"><Input id="t-model" value={f.model} disabled={!!block} onChange={e => setF({ ...f, model: e.target.value })} /></Field>
            <Field id="t-prompt" label={`Prompt · ${f.prompt.length.toLocaleString()} chars`} error={errors.prompt}><Textarea id="t-prompt" rows={5} value={f.prompt} disabled={!!block} aria-invalid={!!errors.prompt || undefined} aria-describedby="t-prompt-msg" onChange={e => setF({ ...f, prompt: e.target.value })} /></Field>
          </Stateful>
        </Panel>
        <Panel data-block="access" title="Effective access">
          <Field id="t-grant" label="Grant" hint="widening asks first">
            <div id="t-grant" role="group" aria-label="Grant" className="flex gap-1">{GRANTS.map(g => <Button key={g} size="sm" variant={f.grant === g ? "default" : "outline"} aria-pressed={f.grant === g} disabled={!!block} onClick={() => GRANTS.indexOf(g) > GRANTS.indexOf(f.grant) ? widen(`Change grant from ${f.grant} to ${g}? The session can then reach more.`, () => setF(x => ({ ...x, grant: g }))) : setF({ ...f, grant: g })}>{g}</Button>)}</div>
          </Field>
          <Field id="t-role" label="Agent role" hint="changing it asks first">
            <div id="t-role" role="group" aria-label="Agent role" className="flex gap-1">{[...new Set([...F.ROLES, f.role])].map(r => <Button key={r} size="sm" variant={f.role === r ? "default" : "outline"} aria-pressed={f.role === r} disabled={!!block} onClick={() => r !== f.role && widen(`Change agent role from ${f.role} to ${r}?`, () => setF(x => ({ ...x, role: r })))}>{r}</Button>)}</div>
          </Field>
          <Field id="t-mcp" label="MCP servers">
            <span id="t-mcp" className="flex flex-wrap items-center gap-2 text-sm">{f.mcp.join(", ") || "none"}{F.MCP_REGISTRY.filter(s => !f.mcp.includes(s)).map(s => <Button key={s} variant="outline" size="sm" disabled={!!block} onClick={() => widen(`Add MCP server "${s}"? The session can then call its tools.`, () => setF(x => ({ ...x, mcp: [...x.mcp, s] })))}>Add {s}</Button>)}</span>
          </Field>
          <KeyValue rows={[["Plugins", "none"], ["Claims", isNew ? "none yet" : rec ? rec.claims : "task · tags brief"], ["Also reaches", "every peer tool via mcp-bridge (every grant does today)"]]} />
        </Panel>
        <Panel data-block="dry-run" title="Dry run"><Answer label="Run dry run" disabled={!!block || (isNew && !f.name)} run={dryRun} /></Panel>
        {!isNew && <Panel data-block="type-runs" title={`Recent runs · 7 days: ${t.runs} · ${F.usd(t.cost)}`} framed={false}>{F.RUNS.filter(r => r.type === t.name).map(r => <a key={r.id} href={`#/run?id=${r.id}&from=sessions`} className={`${link} text-sm`}>{r.at} · {r.outcome} · {r.cost}</a>)}</Panel>}
        <div data-block="save" className="flex flex-wrap items-center gap-2"><Button size="sm" disabled={!!block} onClick={save}>Save the type</Button><Button variant="ghost" size="sm" onClick={() => go(back)}>Cancel</Button><span role="status" className="text-xs text-muted-foreground">{block ?? said}</span></div>
        <ConfirmDialog open={!!confirm} title="Widen this type's access?" body={confirm?.body} confirmLabel="Yes, change it" onConfirm={() => { confirm?.apply(); setConfirm(null) }} onCancel={() => setConfirm(null)} />
      </SidePanel>
    </>
  )
}

function TriggerPanel() {
  const name = q("name") ?? "arxiv-cs-ai"
  return <TriggerForm key={`${name}:${F.TRIGGER_RECORD[name] ? "pu" : "sample"}`} />
}

function TriggerForm() {
  const name = q("name") ?? "arxiv-cs-ai", isNew = name === "new", t = F.TRIGGERS.find(x => x.name === name) ?? F.TRIGGERS[2]
  const state = useScreenState(), block = editBlock(state), rec = isNew ? undefined : F.TRIGGER_RECORD[t.name]
  const [f, setF] = useState(() => isNew ? { name: "", kind: "", url: "", every: "86400", cron: "", tz: "UTC", title: "" }
    : rec ? { name: t.name, kind: rec.source.kind, url: rec.source.url ?? "", every: rec.source.every_seconds == null ? "" : String(rec.source.every_seconds), cron: rec.source.cron ?? "", tz: rec.source.tz ?? "UTC", title: rec.emits?.title ?? "" }
    : { name: t.name, kind: t.src.split(" · ")[0], url: t.src.startsWith("schedule") ? "" : `https://example.org/${t.name}.feed`, every: t.src.startsWith("schedule") ? "" : "86400", cron: t.src.startsWith("schedule") ? t.src.split(" · ")[1].split(" ").slice(0, 5).join(" ") : "", tz: "Europe/Warsaw", title: `${t.name}: {count} new items` })
  const [errors, setErrors] = useState<Record<string, string>>({}), [said, setSaid] = useState("")
  const schedule = f.kind === "schedule"
  // The form edits the source and the title; everything else on the record (predicate, what it creates) is kept.
  const draft = () => {
    const source: Record<string, unknown> = { ...(rec?.source ?? {}), kind: f.kind }
    if (schedule) { delete source.url; delete source.items_path; delete source.id_path; if (f.cron) { source.cron = f.cron.trim(); source.tz = f.tz; delete source.every_seconds } else { source.every_seconds = +f.every; delete source.cron; delete source.tz } }
    else { source.url = f.url; delete source.cron; delete source.tz; if (f.every) source.every_seconds = +f.every; else delete source.every_seconds }
    return { source, emits: { ...(rec?.emits ?? {}), title: f.title || undefined } }
  }
  const save = async () => {
    const e: Record<string, string> = {}
    if (isNew && !/^[a-z0-9][a-z0-9._-]{0,63}$/.test(f.name)) e.name = "Use lowercase letters, digits, . _ - (up to 64)."
    if (!f.kind) e.kind = "Choose what it watches first."
    if (!schedule && f.kind && !/^https?:\/\//.test(f.url)) e.url = "A feed URL starting with http:// or https://."
    if (f.every && (!/^\d+$/.test(f.every) || +f.every < 30)) e.every = "Every is in seconds, minimum 30 (the tick)."
    if (schedule && f.cron && (f.cron.startsWith("@") || f.cron.trim().split(/\s+/).length !== 5)) e.cron = "Five fields: minute hour day month weekday (no @weekly, L or #)."
    if (schedule && !f.cron && !f.every) e.cron = "A schedule needs a cron or an interval."
    setErrors(e); setSaid("")
    if (Object.keys(e).length) return
    if (isNew) { setSaid("This form cannot set what a new trigger creates yet (its task kind), so pu would refuse it; create it through pu's tools for now."); return }
    setSaid("saving…")
    try { await F.editTrigger(f.name, draft()); setSaid("Saved to pu.") } catch (err) { setSaid(refused(err)) }
  }
  const dryRun = async () => {
    const r = await F.dryRunTrigger({ name: f.name, ...draft(), predicate: rec?.predicate })
    if (!r.ok) return `The source did not answer: ${r.reason}. Nothing written.`
    return `Fetched ${r.fetched} item(s), ${r.fresh} new, ${r.matched_total} matching; it ${r.fired ? `would create 1 task: "${r.would_create?.title ?? f.title}"` : "would not fire"}. Nothing written.`
  }
  const predicate = rec ? (rec.predicate?.length ? rec.predicate.map(([k, op, v]) => [k, `${op} ${String(v)}`] as [string, string]) : [["—", "every new item"] as [string, string]]) : isNew ? [["—", "every new item"] as [string, string]] : [["new items", t.name === "arxiv-cs-ai" ? "title matches (agent|LLM|model)" : "any"] as [string, string], ["count_since_fire", ">= 1"] as [string, string]]
  const priority = rec ? `${rec.emits?.priority ?? "medium"}${rec.emits?.stale_after_seconds ? ` · stale after ${Math.round(rec.emits.stale_after_seconds / 3600)} h` : rec.emits?.priority === "high" ? " · stale after 1 h" : ""}` : "medium · stale after 24 h"
  const back = "#/sessions"
  return (
    <>
      <SessionsBody behind />
      <SidePanel open onOpenChange={o => !o && go(back)} backLabel="Back to sessions" backHref={back} title={isNew ? "New trigger" : t.name} description={isNew ? "choose what it watches first" : t.src}>
        <div data-block="panel-title">{isNew ? <span className="text-sm text-muted-foreground">off until you switch it on after saving</span> : <Toggle kind="trigger" name={t.name} initial={t.on} block={switchBlock(state)} />}</div>
        <Panel data-block="source" title="Source">
          <Stateful state={state} empty="Choose the kind of source; its fields follow.">
            {isNew && <Field id="g-name" label="Name" error={errors.name}><Input id="g-name" value={f.name} disabled={!!block} aria-invalid={!!errors.name || undefined} aria-describedby="g-name-msg" onChange={e => setF({ ...f, name: e.target.value })} /></Field>}
            <Field id="g-kind" label="Kind" error={errors.kind}><div id="g-kind" role="group" aria-label="Kind" className="flex gap-1">{["rss", "http-json", "schedule"].map(k => <Button key={k} size="sm" variant={f.kind === k ? "default" : "outline"} aria-pressed={f.kind === k} disabled={!!block} onClick={() => setF({ ...f, kind: k })}>{k}</Button>)}</div></Field>
            {f.kind && !schedule && <Field id="g-url" label="URL" error={errors.url}><Input id="g-url" value={f.url} disabled={!!block} aria-invalid={!!errors.url || undefined} aria-describedby="g-url-msg" onChange={e => setF({ ...f, url: e.target.value })} /></Field>}
            {f.kind && <Field id="g-every" label="Every (seconds)" error={errors.every} hint="minimum 30"><Input id="g-every" inputMode="numeric" value={f.every} disabled={!!block} aria-invalid={!!errors.every || undefined} aria-describedby="g-every-msg" onChange={e => setF({ ...f, every: e.target.value })} /></Field>}
            {schedule && <Field id="g-cron" label="Cron (5 fields)" error={errors.cron} hint={`time zone ${f.tz}`}><Input id="g-cron" value={f.cron} placeholder="0 7 * * mon" disabled={!!block} aria-invalid={!!errors.cron || undefined} aria-describedby="g-cron-msg" onChange={e => setF({ ...f, cron: e.target.value })} /></Field>}
          </Stateful>
        </Panel>
        {!schedule && <Panel data-block="predicate" title="Fires when (all of)"><KeyValue rows={predicate} /></Panel>}
        <Panel data-block="creates" title="Creates">
          <Field id="g-title" label="Task title" hint={f.title.length > 140 ? `pu cuts this at 140 characters (now ${f.title.length})` : "pu cuts titles at 140 characters"}><Input id="g-title" value={f.title} disabled={!!block} aria-describedby="g-title-msg" onChange={e => setF({ ...f, title: e.target.value })} /></Field>
          <KeyValue rows={[["Kind", isNew ? "choose after saving the source" : `${t.kind} → ${t.type || "no type claims it"}`], ["Priority", priority]]} />
        </Panel>
        {!isNew && <Panel data-block="health" title="Health"><KeyValue rows={[["Now", t.health], ["Fires 7 days", String(t.fires)], ["Expired 30 days", String(t.expired)]]} /></Panel>}
        <Panel data-block="dry-run" title="Dry run"><Answer label="Run dry run" disabled={!!block || schedule || !f.kind} run={dryRun} />{schedule && <span className="text-xs text-muted-foreground">schedules can't be previewed</span>}</Panel>
        <div data-block="save" className="flex flex-wrap items-center gap-2"><Button size="sm" disabled={!!block} onClick={save}>Save the trigger</Button><Button variant="ghost" size="sm" onClick={() => go(back)}>Cancel</Button><span role="status" className="text-xs text-muted-foreground">{block ?? said}</span></div>
      </SidePanel>
    </>
  )
}

/** A button that answers in place (aria-live), for actions that need pu (dry run). */
function Answer({ label, run, disabled }: { label: string; run: () => Promise<string>; disabled?: boolean }) {
  const [said, setSaid] = useState("")
  const go = async () => { setSaid("asking pu…"); try { setSaid(await run()) } catch (e) { setSaid(refused(e)) } }
  return <span className="flex flex-col gap-1"><Button variant="outline" size="sm" className="self-start" disabled={disabled} onClick={go}>{label}</Button><span role="status" className="text-xs text-muted-foreground">{said}</span></span>
}

// ---------- Run ----------
const FROM: Record<string, [string, string]> = { attention: ["Needs attention", "#/attention"], task: ["Tasks", "#/tasks"], sessions: ["Sessions", "#/sessions"], analytics: ["Analytics", "#/analytics"], glance: ["pu", "#/glance"] }
function Run() {
  const state = useScreenState(), runs = useRuns(), r = runs.find(x => x.id === (q("id") ?? "r3")) ?? runs.find(x => x.id === "r3") ?? runs[0], prompt = useRunPrompt(r?.id ?? "")
  if (!r) return <Page id="run" title="Run" view="analytics" status={false}><StateBlock state="empty" message="No runs recorded yet." /></Page>
  const d = state === "partial" ? F.RUN_DETAIL.r7 : F.RUN_DETAIL[r.id!] ?? { attempt: "1 of 1", detail: `${r.outcome}: no detail recorded` }, [fromLabel, fromHref] = FROM[q("from") ?? "attention"] ?? FROM.attention
  const tok = d.tokens
  return (
    <Page id="run" title={<>Run · <a href={`#/type?name=${r.type}`} className="underline underline-offset-4">{r.type}</a> · {r.outcome}</>} view="analytics" status={false}
      before={<p data-block="breadcrumb" className="m-0 text-sm"><a href={fromHref} className={link}>← {fromLabel}</a></p>}>
      <Panel data-block="outcome" title="Outcome"><KeyValue rows={d.report ? [["OUTCOME", <b key="o">{d.report.outcome}</b>], ["NEEDS HUMAN", <b key="n">{d.report.needsHuman}</b>], ["Attempt", d.attempt], ["Task", <a key="t" href={`#/task?id=${r.taskId}`} className="underline underline-offset-4">{r.task}</a>]] : [["Result", r.outcome], ["Attempt", d.attempt], ["Task", <a key="t" href={`#/task?id=${r.taskId}`} className="underline underline-offset-4">{r.task}</a>]]} /></Panel>
      <div className="flex flex-col gap-4 lg:flex-row">
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <Panel data-block="report" title="Report" framed={false}>
            <Stateful state={state === "partial" ? "default" : state}>
              {d.report ? <>
                <KeyValue rows={[["GOAL", d.report.goal], ["ACTIONS", d.report.actions], ["REASONING", d.report.reasoning], ["EVIDENCE", d.report.evidence]]} />
                <pre className="m-0 max-h-96 overflow-y-auto whitespace-pre-wrap rounded-md bg-muted p-3 font-sans text-sm text-foreground">{d.report.text}</pre>
              </> : <><p className="m-0 text-sm text-muted-foreground">No structured report (this run is from before the report contract).</p><pre className="m-0 whitespace-pre-wrap rounded-md bg-muted p-3 font-sans text-sm text-foreground">{d.detail}</pre></>}
            </Stateful>
          </Panel>
          <details data-block="behind" className="text-sm text-foreground"><summary>Tool calls · {d.tools?.length ?? 2} · refused permissions · {d.denials ?? 0} · models · {d.models?.join(", ") || "haiku"}</summary><p className="whitespace-pre-wrap">{d.tools ? d.tools.join(" · ") || "none" : "WebFetch rss.arxiv.org → 503 (twice)"}</p></details>
          <details data-block="prompt" className="text-sm text-foreground"><summary>Prompt · {d.run ? (prompt === undefined ? "loading…" : `${prompt.length.toLocaleString()} chars`) : "4,212 chars"}</summary><pre className="whitespace-pre-wrap font-sans">{d.run ? prompt ?? "" : "You read the arXiv cs.AI feed…"}</pre></details>
        </div>
        <Panel data-block="cost" title="Cost" className="lg:w-[300px] lg:self-start"><KeyValue rows={[["Cost", r.cost], ["Duration", r.duration], ["Turns", String(d.turns ?? 3)], ["Queued", d.queued == null ? (d.run ? "—" : "12 s") : `${Math.round(d.queued)} s`], ...(tok ? ["input", "output", "cache creation", "cache read", "thinking"].map((k, i) => [k, tok[i].toLocaleString()] as [string, string]) : [["tokens", "not recorded"] as [string, string]])]} /></Panel>
      </div>
    </Page>
  )
}

// ---------- Analytics (run log and trigger log at the bottom) ----------
// Colour in the lists carries the same meanings as the charts above (J04): done = ok, partial = warn, failed = down.
const TONE = (outcome: string) => (/fail|error/.test(outcome) ? "down" : /partial|needs|human/.test(outcome) ? "warn" : /^(done|resolved|fired)|fired$/.test(outcome) ? "ok" : "none")
const TINT = { ok: "even:bg-muted/30", warn: "bg-status-warn/10", down: "bg-status-down/10", none: "even:bg-muted/30" }
const DOT = { ok: "bg-status-ok", warn: "bg-status-warn", down: "bg-status-down", none: "bg-muted-foreground" }
/** A row tinted by its outcome when it needs a look (failed, partial), striped otherwise, so rows read at a glance. */
const tinted = (outcome: string) => TINT[TONE(outcome)]
const Dot = ({ tone }: { tone: keyof typeof DOT }) => <span aria-hidden="true" className={`size-2 shrink-0 rounded-full ${DOT[tone]}`} />
/** A number with its share of the column drawn behind it, like the charts above. */
const Bar = ({ v, max, children }: { v: number; max: number; children: ReactNode }) => (
  <span className="relative inline-flex min-w-20 justify-end px-1 tabular-nums"><span aria-hidden="true" className="absolute inset-y-0 right-0 rounded-sm bg-primary/15" style={{ width: `${max ? (v / max) * 100 : 0}%` }} /><span className="relative">{children}</span></span>
)
const HEAD = "bg-muted/60", ROW = "even:bg-muted/30", NAME = "font-mono text-sm"
const money = (s: string) => Number(s.replace(/[^0-9.]/g, "")) || 0

function Analytics() {
  const state = useScreenState(), v = useViewState(), runs = useRuns(), [by, setBy] = useState<"type" | "role" | "trigger">("type")
  const shown = runs.filter(r => (v.runFilter.type === "all" || r.type === v.runFilter.type) && (v.runFilter.outcome === "all" || r.outcome.startsWith(v.runFilter.outcome)))
  const yieldRows = [...F.TRIGGERS, ...F.EXTRA_YIELD]
  const breakdown: [string, number, number, number, number, number, string][] = by === "type" ? F.TYPES.filter(t => t.runs).map(t => [t.name, t.runs, t.cost, t.costs[1], t.costs[2], t.costs[3], F.PER_SUCCESS[t.name] ?? "—"]) : by === "role" ? F.BY_ROLE : F.BY_TRIGGER
  const spend = F.SPEND[v.window] ?? F.DAYS, spendTotal = spend.reduce((n, [, x]) => n + x, 0)
  const maxTotal = Math.max(0, ...breakdown.map(b => b[2])), maxCost = Math.max(0, ...shown.map(r => money(r.cost)))
  return (
    <Page id="analytics" title="Analytics" view="analytics">
      <div data-block="window" className="flex flex-wrap items-center gap-2">{["Today", "7 days", "30 days", "All"].map(w => <Button key={w} variant={w === v.window ? "default" : "ghost"} size="sm" aria-pressed={w === v.window} onClick={() => setViewState({ window: w })}>{w}</Button>)}<span className="text-xs text-muted-foreground">{F.CONN.mode === "live" ? "spend follows the window; the other figures show 7 days" : "sample data: spend follows the window; the other figures show 7 days"}</span></div>
      <Divider label="Overview" />
      <Stateful state={state} empty="Nothing has run yet: spend is $0.00 for every day of this window.">
        <div className="flex flex-col gap-4 lg:flex-row">
          <Panel data-block="outcomes" title="Outcomes · 7 days" className="lg:w-[440px]"><PieChart slices={F.OUTCOMES} total={F.WEEK_RUNS} /><p className="m-0 text-xs text-muted-foreground">{F.OUTCOME_NOTE}</p></Panel>
          <Panel data-block="spend" title={`Spend · ${v.window} · ${F.usd(spendTotal)}`} className="flex-1"><AreaChart label={`Spend per ${v.window === "Today" ? "2 hours" : v.window === "All" ? "week" : "day"}`} points={spend} format={F.usd} /></Panel>
        </div>
      </Stateful>
      <div className="flex flex-col gap-4 lg:flex-row">
        <Panel data-block="performance" title="Performance · 7 days" className="flex-1"><BarChart label="Run duration" bars={F.DURATIONS} max={6} format={v => `${v}`} /><p className="m-0 text-sm">{F.PERFORMANCE}</p></Panel>
        <Panel data-block="trigger-yield" title={`Trigger yield · 7 days · ${yieldRows.length} triggers`} className="lg:w-[440px]"><div className="max-h-[240px] overflow-y-auto">{yieldRows.map(t => <YieldBar key={t.name} name={t.name} href={`#/trigger?name=${t.name}`} fires={t.fires} expired={t.expired} max={Math.max(1, ...yieldRows.map(r => r.fires))} />)}</div></Panel>
      </div>
      <Divider label="Breakdown" />
      <Panel data-block="breakdown" title={`Cost by session ${by} · 7 days`} aside={<span role="group" aria-label="Break down by" className="flex gap-1">{(["type", "role", "trigger"] as const).map(b => <Button key={b} size="sm" variant={by === b ? "default" : "outline"} aria-pressed={by === b} onClick={() => setBy(b)}>By {b}</Button>)}</span>}>
        <Table><TableHeader className={HEAD}><TableRow><TableHead>{by[0].toUpperCase() + by.slice(1)}</TableHead>{["Runs", "Total", "avg", "p50", "p95", "per success"].map(h => <TableHead key={h} className="text-right">{h}</TableHead>)}</TableRow></TableHeader>
          <TableBody>{breakdown.map(([n, runsN, total, avg, p50, p95, ps]) => <TableRow key={n} className={ROW}><TableCell className={NAME}>{by === "type" ? <a href={`#/type?name=${n}`} className={link}>{n}</a> : n}</TableCell><TableCell className="text-right tabular-nums">{runsN}</TableCell><TableCell className="text-right"><Bar v={total} max={maxTotal}>{F.usd(total)}</Bar></TableCell><TableCell className="text-right tabular-nums">{F.usd(avg)}</TableCell><TableCell className="text-right tabular-nums">{F.usd(p50)}</TableCell><TableCell className="text-right tabular-nums">{F.usd(p95)}</TableCell><TableCell className="text-right font-medium tabular-nums">{ps}</TableCell></TableRow>)}
            <TableRow className="border-t-2 border-border"><TableCell className="font-semibold">All</TableCell><TableCell className="text-right font-semibold tabular-nums">{F.WEEK_RUNS}</TableCell><TableCell className="text-right font-semibold tabular-nums">{F.usd(F.WEEK_COST)}</TableCell><TableCell colSpan={4} /></TableRow></TableBody></Table>
        <p className="m-0 text-xs text-muted-foreground">All figures are pu's own; tokens averaged over {F.TOKEN_RUNS.withTokens} runs with tokens (of {F.TOKEN_RUNS.total}).</p>
      </Panel>
      <Divider label="Logs" />
      <Panel data-block="runs" title="Run log · sessions" aside={<span className="flex items-center gap-3 text-xs text-muted-foreground">{(["ok", "warn", "down"] as const).map((t, i) => <span key={t} className="flex items-center gap-1 whitespace-nowrap"><Dot tone={t} />{["done", "partial", "failed"][i]}</span>)}</span>}>
        <div data-block="filters" className="flex flex-wrap items-center gap-2">{(["type", "outcome"] as const).map(k => <Button key={k} variant={v.runFilter[k] === "all" ? "outline" : "default"} size="sm" onClick={() => setViewState(x => ({ runFilter: { ...x.runFilter, [k]: cycle(k === "type" ? ["all", ...new Set(runs.map(r => r.type))] : ["all", "done", "failed", "partial", "nothing-to-do", "resolved"], x.runFilter[k]) } }))}>{k === "type" ? "Type" : "Outcome"}: {v.runFilter[k]}</Button>)}<span className="text-xs text-muted-foreground">{shown.length} of {runs.length} runs</span></div>
        <Table><TableHeader className={HEAD}><TableRow>{["Outcome", "When", "Type", "Task", "Needs you"].map(h => <TableHead key={h}>{h}</TableHead>)}{["Cost", "Duration", "Tokens"].map(h => <TableHead key={h} className="text-right">{h}</TableHead>)}</TableRow></TableHeader>
          <TableBody>{shown.map(r => <TableRow key={r.id} className={tinted(r.outcome)}>
            <TableCell><span className="flex items-center gap-2"><Dot tone={TONE(r.outcome)} /><a href={`#/run?id=${r.id}&from=analytics`} className={link}>{r.outcome}</a></span></TableCell>
            <TableCell className="tabular-nums text-muted-foreground">{r.at}</TableCell><TableCell className={NAME}>{r.type}</TableCell>
            <TableCell><a href={`#/task?id=${r.taskId}`} className={link}>{r.task}</a></TableCell>
            <TableCell className="whitespace-normal">{r.needsYou === "none" || r.needsYou === "—" ? <span className="text-muted-foreground">none</span> : <span className="flex items-center gap-2"><Dot tone="warn" />{r.needsYou}</span>}</TableCell>
            <TableCell className="text-right"><Bar v={money(r.cost)} max={maxCost}>{r.cost}</Bar></TableCell><TableCell className="text-right tabular-nums">{r.duration}</TableCell><TableCell className="text-right tabular-nums">{r.tokens}</TableCell></TableRow>)}</TableBody></Table>
        <p className="m-0 text-xs text-muted-foreground">{F.FIXTURE_NOTE}</p>
      </Panel>
      <Panel data-block="trigger-log" title="Trigger log">
        <Table><TableHeader className={HEAD}><TableRow>{["When", "Trigger", "Event", "Created", "Error · backoff"].map(h => <TableHead key={h}>{h}</TableHead>)}</TableRow></TableHeader>
          <TableBody>{F.TRIGGER_LOG.map(([w, n, e, c, x]) => <TableRow key={w + n} className={tinted(x ? "failed" : e)}>
            <TableCell className="tabular-nums text-muted-foreground">{w}</TableCell><TableCell className={NAME}><a href={`#/trigger?name=${n}`} className={link}>{n}</a></TableCell>
            <TableCell><span className="flex items-center gap-2"><Dot tone={x ? "down" : TONE(e)} />{e}</span></TableCell><TableCell>{c}</TableCell>
            <TableCell className={x ? "font-medium text-foreground" : "text-muted-foreground"}>{x || "—"}</TableCell></TableRow>)}</TableBody></Table>
        <p className="m-0 text-xs text-muted-foreground">Today pu records only fires and errors; quiet fetches need pu's trigger event log (pu work).</p>
      </Panel>
    </Page>
  )
}

// ---------- Health (pending a grilling) ----------
function Health() {
  const state = useScreenState(), H = F.HEALTH
  return (
    <Page id="health" title="Unit health" view="health">
      <div className="flex flex-col gap-4">
        <Panel data-block="gate" title={`Gate · ${state === "error" ? "unknown" : H.gate}`}><Stateful state={state} empty="pu has not ticked yet."><p className="m-0 text-sm">{H.reason} Paused: {H.paused ? "yes" : "no"}. Blocked {Math.round(H.blockedToday)} s today.</p><a href="#/sessions" className={`${link} text-sm`}>Pause and concurrency live in Sessions</a></Stateful></Panel>
        <Panel data-block="usage" title="Account usage">
          {H.windows.length ? H.windows.map(([n, u, c]) => (
            <div key={n} className="flex flex-col gap-1 text-sm"><span>{n} · {u.toFixed(2)} of ceiling {c.toFixed(2)}</span><span className="h-2.5 rounded-sm bg-muted"><span className="block h-full rounded-sm bg-muted-foreground" style={{ width: `${Math.min(100, (u / (c || 1)) * 100)}%` }} /></span></div>))
            : <p className="m-0 text-sm text-muted-foreground">No reading yet: the figures come from a session that has run.</p>}
          <p className="m-0 text-xs text-muted-foreground">{Math.round(H.runway * 100)}% runway held back on cost and usage</p>
        </Panel>
      </div>
      <div className="flex flex-col gap-4">
        <Panel data-block="liveness" title="Liveness"><p className="m-0 text-sm">{H.ticksPerDay} ticks/day · silences: {H.silences}</p><KeyValue rows={H.ticks} /></Panel>
        <Panel data-block="registry" title="Registry"><KeyValue rows={[["MCP servers", H.mcp]]} /></Panel>
      </div>
    </Page>
  )
}

// ---------- Phone glance (information only, no switches) ----------
function Glance() {
  const state = useScreenState()
  return (
    <main data-screen="glance" className="mx-auto flex w-full max-w-[520px] flex-col gap-4 p-4">
      <h1 data-block="title" className="m-0 text-xl font-semibold text-foreground">pu</h1>
      <Panel data-block="system" title="System"><KeyValue rows={[["Need you", <a key="n" href="#/attention" className="font-semibold underline">{F.STATUS.needYou}</a>], ["Gate", state === "error" ? "gate unknown" : F.STATUS.gate], ["Running", F.RUNNING.length ? <a key="r" href={`#/task?id=${F.RUNNING[0].taskId}`} className="underline">{F.RUNNING.length} · {F.RUNNING.map(r => r.type).join(", ")}</a> : "0"], ["Paused", F.STATUS.paused ? "yes" : "no"]]} /><p className="m-0 text-xs text-muted-foreground">updated {F.STATUS.updated}</p></Panel>
      <Panel data-block="sessions" title={`Sessions · ${F.TYPES.length}`} framed={false}>
        <Stateful state={state} empty="No session types yet.">
          <ul className="m-0 flex list-none flex-col p-0">{F.TYPES.map(t => <li key={t.name} className="border-b border-border py-2 text-sm"><b>{t.name}</b> · {t.on}<br />{t.lastRun ? <a href={`#/run?id=${F.RUNS.find(r => r.type === t.name)?.id ?? ""}&from=glance`} className={link}>{t.last}</a> : <span className="text-muted-foreground">{t.last}</span>}</li>)}</ul>
        </Stateful>
      </Panel>
      <nav data-block="panels" aria-label="Panels" className="flex gap-2 border-t border-border bg-background py-2">{[["Attention", "attention"], ["Analytics", "analytics"], ["Health", "health"]].map(([l, h]) => <a key={h} href={`#/${h}`} className="rounded-md border border-border px-3 py-2 text-sm text-foreground no-underline">{l}</a>)}</nav>
    </main>
  )
}

export const screens = { tasks: Tasks, task: TaskPanel, map: MapPanel, attention: Attention, sessions: Sessions, type: TypePanel, trigger: TriggerPanel, run: Run, analytics: Analytics, health: Health, glance: Glance }
