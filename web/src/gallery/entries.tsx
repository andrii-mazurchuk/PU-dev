import { useState, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Sheet, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { StatusIcon, type StatusKind } from "@/components/ui/status-icon"
import { TaskNode } from "@/components/ui/task-node"
import { TaskGraph } from "@/components/ui/task-graph"
import { SidePanel } from "@/components/ui/side-panel"
import { StateSwitch } from "@/components/ui/state-switch"
import { Panel, KeyValue } from "@/components/ui/panel"
import { StateBlock } from "@/components/ui/state-block"
import { PieChart, BarChart, AreaChart, YieldBar } from "@/components/ui/charts"
import { LiveGraph } from "@/components/ui/live-graph"
import { PanZoom } from "@/components/ui/pan-zoom"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { ViewTabs, StatusStrip, ScopeBar } from "@/components/ui/shell"
import * as F from "@/fixtures/pu"

export type Entry = {
  component: string      // contract name, e.g. "Button"
  variant: string        // one of the contract's variants, e.g. "destructive"
  state: string          // one of the contract's states, e.g. "loading"
  render: () => ReactNode
  note?: string          // e.g. "long label" for stress content
  width?: number         // the width of the container the element really lives in (px); gallery-check tests overflow in it
}

const LONG = "Описать окно сброса лимитов для недельного окна и проверить часовой пояс"
const graphMaps = (ids: string[]) => F.MAPS.filter(m => ids.includes(m.id)).map(m => ({ id: m.id, title: m.title, done: F.ticketsOf(m.id, true).filter(t => t.state === "completed").length, total: F.ticketsOf(m.id, true).length,
  tasks: F.ticketsOf(m.id).map(t => ({ id: t.id, title: t.title, status: (t.state === "open" ? "ready" : t.state) as StatusKind, deps: F.openDeps(t) })) }))

function PanelDemo() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button data-gallery-trigger="" variant="outline" onClick={() => setOpen(true)}>Open task panel</Button>
      <SidePanel open={open} onOpenChange={setOpen} backLabel="Back to tasks" backHref="#/gallery" title="Tell the owner on transition" description="task · holonic-node.pu">
        <KeyValue rows={[["Map", "Account usage ceilings…"], ["Blocked by", "Gate before selection"]]} />
      </SidePanel>
    </>
  )
}

function ConfirmDemo() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button data-gallery-trigger="" variant="outline" onClick={() => setOpen(true)}>Widen grant</Button>
      <ConfirmDialog open={open} title="Widen this type's access?" body="Change grant from read to web? The session can then reach more." confirmLabel="Yes, change it" onConfirm={() => setOpen(false)} onCancel={() => setOpen(false)} />
    </>
  )
}
const field = (id: string, el: ReactNode, msg?: string) => <div className="flex flex-col gap-1"><Label htmlFor={id}>Purpose</Label>{el}{msg && <span id={`${id}-msg`} role="alert" className="text-xs text-muted-foreground">{msg}</span>}</div>

// One entry per component × variant (state "default"), plus one per declared state. Started at stage 3 in grey;
// later stages upgrade the same entries, never a copy. Give an element its real container width where it lives in one.
// Mark the element under test with data-gallery-target and overlay openers with data-gallery-trigger.
export const entries: Entry[] = [
  // shadcn bases
  ...(["default", "outline", "secondary", "ghost"] as const).map(v => ({ component: "Button", variant: v, state: "default", render: () => <Button variant={v}>Save the type</Button> })),
  { component: "Button", variant: "default", state: "hover", render: () => <Button>Save the type</Button> },
  { component: "Button", variant: "default", state: "focus", render: () => <Button>Save the type</Button> },
  { component: "Button", variant: "default", state: "disabled", render: () => <Button disabled>Re-queue</Button>, note: "pu can't do it yet" },
  ...(["outline", "secondary"] as const).map(v => ({ component: "Badge", variant: v, state: "default", render: () => <Badge variant={v}>grilling</Badge> })),
  { component: "Switch", variant: "default", state: "default", render: () => <Switch aria-label="demo switch" defaultChecked /> },
  { component: "Switch", variant: "default", state: "focus", render: () => <Switch aria-label="demo switch" /> },
  { component: "Switch", variant: "default", state: "disabled", render: () => <Switch aria-label="demo switch" disabled /> },
  { component: "Switch", variant: "default", state: "error", render: () => <span><Switch aria-label="demo switch" aria-invalid aria-describedby="sw-err" /><span id="sw-err" role="alert" className="ml-2 text-xs">pu refused: 409</span></span> },
  { component: "Table", variant: "default", state: "default", width: 900, render: () => (
    <Table><TableHeader><TableRow><TableHead>Outcome</TableHead><TableHead>When</TableHead><TableHead>Type</TableHead><TableHead className="text-right">Cost</TableHead></TableRow></TableHeader>
      <TableBody>{F.RUNS.slice(0, 3).map(r => <TableRow key={r.at}><TableCell>{r.outcome}</TableCell><TableCell>{r.at}</TableCell><TableCell>{r.type}</TableCell><TableCell className="text-right">{r.cost}</TableCell></TableRow>)}</TableBody></Table>) },
  { component: "Input", variant: "default", state: "default", width: 320, render: () => field("in1", <Input id="in1" defaultValue="Write the 07:00 brief" />) },
  { component: "Input", variant: "default", state: "focus", width: 320, render: () => field("in2", <Input id="in2" data-gallery-target="" defaultValue={LONG} />) },
  { component: "Input", variant: "default", state: "disabled", width: 320, render: () => field("in3", <Input id="in3" disabled defaultValue="morning-brief" />) },
  { component: "Input", variant: "default", state: "error", width: 320, render: () => field("in4", <Input id="in4" aria-invalid aria-describedby="in4-msg" defaultValue="" />, "Say in one line what this type is for.") },
  { component: "Textarea", variant: "default", state: "default", width: 320, render: () => field("ta1", <Textarea id="ta1" rows={4} defaultValue={LONG} />) },
  { component: "Textarea", variant: "default", state: "focus", width: 320, render: () => field("ta2", <Textarea id="ta2" data-gallery-target="" rows={4} defaultValue="You write the morning brief." />) },
  { component: "Textarea", variant: "default", state: "disabled", width: 320, render: () => field("ta3", <Textarea id="ta3" rows={4} disabled defaultValue="You write the morning brief." />) },
  { component: "Textarea", variant: "default", state: "error", width: 320, render: () => field("ta4", <Textarea id="ta4" rows={4} aria-invalid aria-describedby="ta4-msg" defaultValue="" />, "A type needs its instructions.") },
  { component: "Label", variant: "default", state: "default", render: () => <Label htmlFor="lb1">Every (seconds)</Label> },
  { component: "ConfirmDialog", variant: "default", state: "default", render: () => <ConfirmDemo /> },
  { component: "ConfirmDialog", variant: "default", state: "open", render: () => <ConfirmDemo /> },
  { component: "Sheet", variant: "right", state: "default", render: () => <Sheet><SheetTrigger asChild><Button variant="outline">Open sheet</Button></SheetTrigger><SheetContent><SheetTitle>Sheet</SheetTitle><SheetDescription>Base of SidePanel</SheetDescription></SheetContent></Sheet> },
  { component: "Sheet", variant: "right", state: "open", render: () => <Sheet><SheetTrigger asChild><Button data-gallery-trigger="" variant="outline">Open sheet</Button></SheetTrigger><SheetContent><SheetTitle>Sheet</SheetTitle><SheetDescription>Base of SidePanel</SheetDescription></SheetContent></Sheet> },

  // pu elements
  ...(["ready", "blocked", "running", "needs-you", "expired", "completed"] as const).map(s => ({ component: "StatusIcon", variant: s, state: "default", render: () => <StatusIcon status={s} /> })),
  ...(["ready", "blocked", "running", "needs-you", "expired"] as const).map(s => ({ component: "TaskNode", variant: "default", state: s === "ready" ? "default" : s, width: 200, render: () => <TaskNode title={F.TASKS.find(t => t.state === s)!.title} status={s} href="#/gallery" /> })),
  { component: "TaskNode", variant: "default", state: "hover", width: 200, render: () => <TaskNode title="Decide the panel vocabulary" status="ready" href="#/gallery" /> },
  { component: "TaskNode", variant: "default", state: "focus", width: 200, render: () => <TaskNode title="Decide the panel vocabulary" status="ready" href="#/gallery" /> },
  { component: "TaskNode", variant: "default", state: "disabled", width: 200, render: () => <TaskNode title="Decide the panel vocabulary" status="ready" href="#/gallery" disabled /> },
  { component: "TaskNode", variant: "default", state: "selected", width: 200, render: () => <TaskNode title="Decide the panel vocabulary" status="ready" href="#/gallery" selected /> },
  { component: "TaskNode", variant: "default", state: "long", width: 200, note: "140-char Cyrillic title, truncated", render: () => <TaskNode title={LONG} status="expired" href="#/gallery" /> },
  { component: "TaskGraph", variant: "default", state: "default", width: 1100, render: () => <TaskGraph maps={graphMaps(["m1", "m2", "m3"])} taskHref={id => `#/task?id=${id}`} mapHref={id => `#/map?id=${id}`} /> },
  { component: "TaskGraph", variant: "default", state: "cycle", width: 1100, note: "two tickets waiting on each other", render: () => <TaskGraph maps={graphMaps(["m5"])} taskHref={id => `#/task?id=${id}`} mapHref={id => `#/map?id=${id}`} /> },
  { component: "TaskGraph", variant: "default", state: "many", width: 1100, note: "14-ticket map, 8-step chain", render: () => <TaskGraph maps={graphMaps(["m4"])} taskHref={id => `#/task?id=${id}`} mapHref={id => `#/map?id=${id}`} /> },
  { component: "SidePanel", variant: "default", state: "default", render: () => <PanelDemo /> },
  { component: "SidePanel", variant: "default", state: "open", render: () => <PanelDemo /> },
  ...(["on", "off", "never activated", "needs re-approval"] as const).map(s => ({ component: "StateSwitch", variant: s, state: "default", width: 260, render: () => <StateSwitch name="morning-brief" state={s} /> })),
  { component: "StateSwitch", variant: "on", state: "focus", width: 260, render: () => <StateSwitch name="morning-brief" state="on" /> },
  { component: "StateSwitch", variant: "on", state: "disabled", width: 260, note: "inside the node frame", render: () => <StateSwitch name="morning-brief" state="on" disabledReason="writes are not open inside the node yet" /> },
  { component: "StateSwitch", variant: "on", state: "loading", width: 260, render: () => <StateSwitch name="morning-brief" state="on" pending /> },
  { component: "StateSwitch", variant: "off", state: "error", width: 260, render: () => <StateSwitch name="ai-news" state="off" error="pu refused the change: the record changed elsewhere; reloaded" /> },
  { component: "Panel", variant: "framed", state: "default", width: 440, render: () => <Panel title="Next up (queue)"><p className="m-0 text-sm">Read the two rolling windows</p></Panel> },
  { component: "Panel", variant: "plain", state: "default", width: 440, render: () => <Panel title="Blocked by" framed={false}><p className="m-0 text-sm">Gate before selection</p></Panel> },
  { component: "KeyValue", variant: "default", state: "default", width: 300, render: () => <KeyValue rows={[["input", "4,120"], ["output", "610"], ["cache read", "1,180"], ["Also reaches", "every peer tool via mcp-bridge (every grant does today)"]]} /> },
  ...(["empty", "error", "stale", "partial", "offline", "permission"] as const).map(s => ({ component: "StateBlock", variant: s, state: "default", width: 440, render: () => <StateBlock state={s} message={`${s}: what the block says instead of its data`} onRetry={() => {}} lastGood={s === "error" ? "10-31 07:12" : undefined} /> })),
  { component: "StateBlock", variant: "empty", state: "loading", width: 440, render: () => <StateBlock state="loading" message="" /> },
  { component: "PieChart", variant: "default", state: "default", width: 440, render: () => <PieChart slices={F.OUTCOMES} total={F.WEEK_RUNS} /> },
  { component: "PieChart", variant: "default", state: "empty", width: 440, render: () => <PieChart slices={[["done", 0], ["failed", 0]]} total={0} /> },
  { component: "AreaChart", variant: "default", state: "default", width: 800, render: () => <AreaChart label="Spend per day" points={F.DAYS} format={F.usd} /> },
  { component: "AreaChart", variant: "default", state: "zero", width: 800, note: "quiet days as $0.00", render: () => <AreaChart label="Spend per day" points={F.DAYS.map(([d]) => [d, 0])} format={F.usd} /> },
  { component: "AreaChart", variant: "default", state: "many", width: 800, note: "31 days", render: () => <AreaChart label="Spend per day" points={F.SPEND["30 days"]} format={F.usd} /> },
  { component: "BarChart", variant: "default", state: "default", width: 600, render: () => <BarChart label="Spend per day" bars={F.DAYS} max={0.5} format={F.usd} /> },
  { component: "BarChart", variant: "default", state: "zero", width: 600, note: "quiet days as $0.00", render: () => <BarChart label="Spend per day" bars={F.DAYS.map(([d]) => [d, 0])} max={0.5} format={F.usd} /> },
  { component: "YieldBar", variant: "default", state: "default", width: 420, render: () => <YieldBar name="hf-papers" href="#/trigger" fires={4} expired={2} /> },
  { component: "PanZoom", variant: "default", state: "default", width: 600, render: () => <PanZoom width={900} height={400} frameHeight={260} label="Board"><div className="absolute left-10 top-10 rounded-md border border-border bg-card p-2 text-sm">drag the board, scroll to zoom</div></PanZoom> },
  { component: "LiveGraph", variant: "default", state: "default", width: 1000, render: () => <LiveGraph frameHeight={360} columns={[{ title: "Triggers", nodes: [{ id: "t1", label: "morning-0700", sub: "fired 07:00", href: "#/trigger", status: "running" }, { id: "t2", label: "arxiv-cs-ai", sub: "fetch failing", href: "#/trigger", status: "expired" }] }, { title: "Session types", nodes: [{ id: "s1", label: "morning-brief", sub: "on · sonnet", href: "#/type", status: "running" }, { id: "s2", label: "ai-news", sub: "needs re-approval · haiku", href: "#/type", status: "needs-you" }] }, { title: "Latest runs", nodes: [{ id: "r1", label: "done", sub: "07:02 · $0.31", href: "#/run", status: "completed" }] }]} edges={[["t1", "s1"], ["t2", "s2"], ["s1", "r1"]]} /> },
  { component: "LiveGraph", variant: "default", state: "many", width: 1000, note: "30 triggers, 30 types (shown whole here; on the page the frame stays fixed and scrolls)", render: () => <LiveGraph frameHeight={1700} columns={[{ title: "Triggers", nodes: Array.from({ length: 30 }, (_, i) => ({ id: `t${i}`, label: `trigger-${i + 1}`, href: "#/trigger", status: "running" as const })) }, { title: "Session types", nodes: Array.from({ length: 30 }, (_, i) => ({ id: `s${i}`, label: `type-${i + 1}`, href: "#/type", status: "blocked" as const })) }]} edges={Array.from({ length: 30 }, (_, i) => [`t${i}`, `s${(i * 7) % 30}`] as [string, string])} /> },
  { component: "ViewTabs", variant: "default", state: "default", width: 600, render: () => <ViewTabs current="tasks" /> },
  { component: "StatusStrip", variant: "default", state: "default", width: 1100, render: () => <StatusStrip status={F.STATUS} /> },
  { component: "StatusStrip", variant: "default", state: "loading", width: 1100, render: () => <StatusStrip status={F.STATUS} state="loading" /> },
  { component: "StatusStrip", variant: "default", state: "stale", width: 1100, render: () => <StatusStrip status={F.STATUS} state="stale" /> },
  { component: "StatusStrip", variant: "default", state: "gate-unknown", width: 1100, render: () => <StatusStrip status={F.STATUS} state="error" /> },
  { component: "ScopeBar", variant: "default", state: "default", width: 1100, render: () => <ScopeBar roots={F.ROOTS} subs={F.SUBS} root="holonic-node" sub="all" /> },
  { component: "ScopeBar", variant: "default", state: "narrow", width: 360, note: "phone: rows scroll inside themselves", render: () => <ScopeBar roots={F.ROOTS} subs={F.SUBS} root="holonic-node" sub="pu" /> },
]
