import { useState, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Panel, KeyValue } from "@/components/ui/panel"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { SchedulePicker } from "@/components/ui/schedule-picker"
import type { ApiTrigger, ApiType } from "@/fixtures/pu"
import { claimant, sameKind, type Claim } from "@/lib/claims"
import { NEW_SCHEDULE } from "@/lib/schedule"

// The session-type and trigger edit forms. They start from pu's record (ApiType / ApiTrigger), validate in place,
// and hand pu a patch of only the fields that changed (pu's edit door keeps every other field). Save and dry run are
// callbacks, so the same forms run on sample data here and on pu's live doors in pu's web/.
export type Wrap = (p: { empty: string; children: ReactNode }) => ReactNode
const link = "text-foreground underline underline-offset-4"
const NAME = /^[a-z0-9][a-z0-9._-]{0,63}$/
const KINDS = ["execution", "research", "task", "prototype", "grilling", "map"]
const OPS = [">=", ">", "==", "!=", "contains", "matches"]
const GRANTS = ["none", "read", "web", "repo"]
const FIELDS = ["count_since_fire", "age_since_last_seen", "title", "link", "summary"]
const UNITS = [["minutes", 60], ["hours", 3600], ["days", 86400]] as const

/** A labelled field with its message (aria-describedby; aria-invalid when the message is an error). */
export function Field({ id, label, error, hint, children }: { id: string; label: string; error?: string; hint?: ReactNode; children: ReactNode }) {
  return <div className="flex flex-col gap-1"><Label htmlFor={id} className="text-sm text-foreground">{label}</Label>{children}{(error || hint) && <span id={`${id}-msg`} role={error ? "alert" : undefined} className="text-xs text-muted-foreground">{error ?? hint}</span>}</div>
}
/** A button that answers in place (role=status), for actions that ask pu (dry run). */
export function Answer({ label, answer, disabled }: { label: string; answer: () => Promise<string>; disabled?: boolean }) {
  const [said, setSaid] = useState("")
  return <span className="flex flex-col gap-1"><Button variant="outline" size="sm" className="self-start" disabled={disabled} onClick={async () => setSaid(await answer())}>{label}</Button><span role="status" className="text-xs text-muted-foreground">{said}</span></span>
}
/** One choice out of a few, as pressed buttons (grant, role, kind, priority). */
function Choice({ id, label, options, value, disabled, onPick }: { id: string; label: string; options: string[]; value?: string; disabled?: boolean; onPick: (v: string) => void }) {
  return <div id={id} role="group" aria-label={label} className="flex flex-wrap gap-1">{options.map(o => <Button key={o} size="sm" variant={value === o ? "default" : "outline"} aria-pressed={value === o} disabled={disabled} onClick={() => o !== value && onPick(o)}>{o}</Button>)}</div>
}
/** Removable values (tags, MCP servers): each with its own remove button. */
function Chips({ items, what, disabled, onRemove }: { items: string[]; what: string; disabled?: boolean; onRemove: (v: string) => void }) {
  if (!items.length) return <span className="text-sm text-muted-foreground">none</span>
  return <span className="flex flex-wrap gap-1">{items.map(v => <span key={v} className="inline-flex items-center gap-1 rounded-sm border border-border py-0.5 pl-2 text-sm text-foreground">{v}<Button variant="ghost" size="sm" className="h-6 px-2" aria-label={`Remove ${what} ${v}`} disabled={disabled} onClick={() => onRemove(v)}>×</Button></span>)}</span>
}
function TagAdder({ id, label, disabled, onAdd }: { id: string; label: string; disabled?: boolean; onAdd: (v: string) => void }) {
  const [v, setV] = useState(""), add = () => { const t = v.trim(); if (t) { onAdd(t); setV("") } }
  return <span className="flex gap-1"><Input id={id} aria-label={label} placeholder="add a tag" className="max-w-40" value={v} disabled={disabled} onChange={e => setV(e.target.value)} onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); add() } }} /><Button variant="outline" size="sm" disabled={disabled || !v.trim()} onClick={add}>Add</Button></span>
}
/** The top-level fields that differ: pu's edit door changes only what it is sent. */
function patchOf<T extends object>(before: T, after: T): Partial<T> {
  return Object.fromEntries(Object.entries(after).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify((before as Record<string, unknown>)[k]))) as Partial<T>
}
const withTag = (tags: string[] | undefined, t: string) => (tags ?? []).includes(t) ? tags ?? [] : [...(tags ?? []), t]

// ---------- session type ----------
export type TypeFormProps = {
  record: ApiType; isNew: boolean; block?: string; claims: Claim[]; roles: string[]; registry: string[]; Wrap: Wrap
  onSave: (patch: Partial<ApiType>) => Promise<string>; onDryRun: (draft: ApiType) => Promise<string>; onCancel: () => void
  unit: string; between?: ReactNode
}
export function TypeForm({ record, isNew, block, claims, roles, registry, Wrap, onSave, onDryRun, onCancel, unit, between }: TypeFormProps) {
  const [f, setF] = useState<ApiType>(() => structuredClone(record))
  const [errors, setErrors] = useState<Record<string, string>>({}), [said, setSaid] = useState(""), [confirm, setConfirm] = useState<{ title: string; body: string; apply: () => void } | null>(null)
  const ask = (title: string, body: string, apply: () => void) => setConfirm({ title, body, apply })
  const kind = f.claims?.kind, others = sameKind(claims, record.name, kind)
  const save = async () => {
    const e: Record<string, string> = {}
    if (isNew && !NAME.test(f.name)) e.name = "Use lowercase letters, digits, . _ - (up to 64), starting with a letter or digit."
    if (!f.purpose?.trim()) e.purpose = "Say in one line what this type is for."
    if (!f.prompt?.trim()) e.prompt = "A type needs its instructions."
    setErrors(e)
    if (Object.keys(e).length) return setSaid("")
    const patch = isNew ? f : patchOf(record, f)
    setSaid(Object.keys(patch).length ? await onSave(patch) : "Nothing changed.")
  }
  return (
    <>
      <Panel data-block="definition" title="Definition">
        <Wrap empty="Fill the name, purpose, prompt and grant; the dry run needs them.">
          <Field id="t-name" label="Name" error={errors.name} hint={isNew ? "fixed once saved" : "fixed"}><Input id="t-name" value={f.name} disabled={!isNew || !!block} aria-invalid={!!errors.name || undefined} aria-describedby="t-name-msg" onChange={e => setF({ ...f, name: e.target.value })} /></Field>
          <Field id="t-unit" label="Unit" hint="not available in pu yet: reset to the source's definition"><span className="flex items-center gap-2 text-sm">{unit}<Button id="t-unit" variant="outline" size="sm" disabled>Reset to source</Button></span></Field>
          <Field id="t-purpose" label="Purpose" error={errors.purpose}><Input id="t-purpose" value={f.purpose ?? ""} disabled={!!block} aria-invalid={!!errors.purpose || undefined} aria-describedby="t-purpose-msg" onChange={e => setF({ ...f, purpose: e.target.value })} /></Field>
          <Field id="t-model" label="Model"><Input id="t-model" value={f.model ?? ""} suggestions={["haiku", "sonnet", "opus"]} disabled={!!block} onChange={e => setF({ ...f, model: e.target.value })} /></Field>
          <Field id="t-prompt" label={`Prompt · ${(f.prompt ?? "").length.toLocaleString()} chars`} error={errors.prompt}><Textarea id="t-prompt" rows={5} value={f.prompt ?? ""} disabled={!!block} aria-invalid={!!errors.prompt || undefined} aria-describedby="t-prompt-msg" onChange={e => setF({ ...f, prompt: e.target.value })} /></Field>
        </Wrap>
      </Panel>
      <Panel data-block="access" title="Effective access">
        <Field id="t-grant" label="Grant" hint="widening asks first">
          <Choice id="t-grant" label="Grant" options={GRANTS} value={f.grant} disabled={!!block} onPick={g => GRANTS.indexOf(g) > GRANTS.indexOf(f.grant) ? ask("Widen this type's access?", `Change grant from ${f.grant} to ${g}? The session can then reach more.`, () => setF(x => ({ ...x, grant: g }))) : setF({ ...f, grant: g })} />
        </Field>
        <Field id="t-role" label="Agent role" hint="changing it asks first">
          <Choice id="t-role" label="Agent role" options={roles} value={f.agent_role} disabled={!!block} onPick={r => ask("Change the agent role?", `Change agent role from ${f.agent_role ?? "none"} to ${r}?`, () => setF(x => ({ ...x, agent_role: r })))} />
        </Field>
        <Field id="t-mcp" label="MCP servers" hint="adding one asks first; removing one narrows access and asks nothing">
          <span id="t-mcp" className="flex flex-wrap items-center gap-2">
            <Chips items={f.mcp_servers ?? []} what="MCP server" disabled={!!block} onRemove={s => setF({ ...f, mcp_servers: (f.mcp_servers ?? []).filter(x => x !== s) })} />
            {registry.filter(s => !(f.mcp_servers ?? []).includes(s)).map(s => <Button key={s} variant="outline" size="sm" disabled={!!block} onClick={() => ask("Widen this type's access?", `Add MCP server "${s}"? The session can then call its tools.`, () => setF(x => ({ ...x, mcp_servers: [...(x.mcp_servers ?? []), s] })))}>Add {s}</Button>)}
          </span>
        </Field>
        <Field id="t-claims" label="Claims: the tasks this type picks up" hint={kind ? (others.length ? <>Also claiming {kind}: {others.map((c, i) => <span key={c.type}>{i ? ", " : ""}<a href={`#/type?name=${c.type}`} className={link}>{c.type}</a>{c.tags?.length ? ` (${c.tags.join(", ")})` : " (no tags)"}</span>)} · the most specific claim wins</> : `No other type claims ${kind}.`) : "Claims nothing: no trigger's tasks reach it."}>
          <span id="t-claims" className="flex flex-col gap-2">
            <Choice id="t-claims-kind" label="Claimed kind" options={["none", ...KINDS]} value={kind ?? "none"} disabled={!!block} onPick={k => ask("Change what this type picks up?", `Change claimed kind from ${kind ?? "none"} to ${k}? Tasks of that kind start reaching this type.`, () => setF(x => ({ ...x, claims: k === "none" ? {} : { kind: k, tags: x.claims?.tags ?? [] } })))} />
            {kind && <span className="flex flex-wrap items-center gap-2"><Chips items={f.claims?.tags ?? []} what="claimed tag" disabled={!!block} onRemove={t => setF({ ...f, claims: { ...f.claims, tags: (f.claims?.tags ?? []).filter(x => x !== t) } })} /><TagAdder id="t-claims-tag" label="Add a claimed tag" disabled={!!block} onAdd={t => setF({ ...f, claims: { ...f.claims, tags: withTag(f.claims?.tags, t) } })} /></span>}
          </span>
        </Field>
        <KeyValue rows={[["Plugins", `${(f.plugins ?? []).join(", ") || "none"} · per-type plugins are not available in pu yet`], ["Also reaches", "every peer tool via mcp-bridge (every grant does today)"]]} />
      </Panel>
      <Panel data-block="dry-run" title="Dry run"><Answer label="Run dry run" disabled={!!block || (isNew && !f.name)} answer={() => onDryRun(f)} /></Panel>
      {between}
      <div data-block="save" className="flex flex-wrap items-center gap-2"><Button size="sm" disabled={!!block} onClick={save}>Save the type</Button><Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button><span role="status" className="text-xs text-muted-foreground">{block ?? said}</span></div>
      <ConfirmDialog open={!!confirm} title={confirm?.title ?? ""} body={confirm?.body} confirmLabel="Yes, change it" onConfirm={() => { confirm?.apply(); setConfirm(null) }} onCancel={() => setConfirm(null)} />
    </>
  )
}

// ---------- trigger ----------
type Clause = { field: string; op: string; value: string }
const toClauses = (p?: ApiTrigger["predicate"]): Clause[] => (p ?? []).map(([field, op, value]) => ({ field, op, value: String(value) }))
const fromClauses = (cs: Clause[]): [string, string, unknown][] => cs.map(c => [c.field.trim(), c.op, (c.op === ">=" || c.op === ">") && c.value.trim() !== "" && !isNaN(+c.value) ? +c.value : c.value])
const staleOf = (s?: number) => { if (!s) return { n: "", unit: 3600 }; const u = [...UNITS].reverse().find(([, k]) => s % k === 0)?.[1] ?? 60; return { n: String(s / u), unit: u } }

export type TriggerFormProps = {
  record?: ApiTrigger; name: string; block?: string; claims: Claim[]; Wrap: Wrap
  onSave: (patch: Partial<ApiTrigger>) => Promise<string>; onDryRun: (draft: ApiTrigger) => Promise<string>; onCancel: () => void
  health?: ReactNode
}
export function TriggerForm({ record, name, block, claims, Wrap, onSave, onDryRun, onCancel, health }: TriggerFormProps) {
  const isNew = !record
  const [src, setSrc] = useState<ApiTrigger["source"]>(() => structuredClone(record?.source ?? { kind: "" }))
  const [clauses, setClauses] = useState<Clause[]>(() => toClauses(record?.predicate))
  const [emits, setEmits] = useState<NonNullable<ApiTrigger["emits"]>>(() => structuredClone(record?.emits ?? { priority: "medium", tags: [], afk: false, title: "" }))
  const [newName, setNewName] = useState(""), [stale, setStale] = useState(() => staleOf(record?.emits?.stale_after_seconds))
  const [errors, setErrors] = useState<Record<string, string>>({}), [said, setSaid] = useState(""), [askAfk, setAskAfk] = useState(false), [whenError, setWhenError] = useState<string>()
  const schedule = src.kind === "schedule", title = emits.title ?? "", who = emits.kind ? claimant(claims, emits.kind, emits.tags ?? []) : undefined
  const draft = (): ApiTrigger => ({
    name: isNew ? newName : name, source: Object.fromEntries(Object.entries(src).filter(([, v]) => v !== "" && v !== undefined)) as ApiTrigger["source"],
    // pu refuses a schedule that keeps a predicate, and keeps any field it isn't sent: clearing one must be said out loud.
    ...(clauses.length && !schedule || record?.predicate?.length ? { predicate: schedule ? [] : fromClauses(clauses) } : {}),
    emits: { ...emits, ...(stale.n ? { stale_after_seconds: Math.round(+stale.n * stale.unit) } : { stale_after_seconds: undefined }) },
  })
  const save = async () => {
    const e: Record<string, string> = {}
    if (isNew && !NAME.test(newName)) e.name = "Use lowercase letters, digits, . _ - (up to 64)."
    if (!src.kind) e.kind = "Choose what it watches first."
    if (!schedule && src.kind && !/^https?:\/\//.test(src.url ?? "")) e.url = "A feed URL starting with http:// or https://."
    if (!schedule && src.every_seconds !== undefined && (!Number.isInteger(src.every_seconds) || src.every_seconds < 30)) e.every = "Every is in seconds, minimum 30 (the tick)."
    if (schedule && whenError) e.when = whenError // the picker shows it in place
    if (!schedule) clauses.forEach((c, i) => {
      if (!c.field.trim() || c.value.trim() === "") e[`c${i}`] = "A condition needs a field and a value."
      // pu matches with Python's re, whose leading inline flags ((?i) etc.) JS RegExp rejects: check the rest.
      else if (c.op === "matches") { try { new RegExp(c.value.replace(/^\(\?[aiLmsux]+\)/, "")) } catch { e[`c${i}`] = "not a valid regular expression" } }
      else if ((c.op === ">=" || c.op === ">") && isNaN(+c.value)) e[`c${i}`] = `${c.op} needs a number.`
    })
    if (!emits.kind) e.ekind = "Choose what kind of task it creates"
    if (stale.n && !(+stale.n > 0)) e.stale = "A positive number, or empty."
    setErrors(e)
    if (Object.keys(e).length) return setSaid("")
    const d = draft(), patch = isNew ? d : patchOf(record!, d)
    setSaid(Object.keys(patch).length ? await onSave(patch) : "Nothing changed.")
  }
  // pu takes every_seconds or cron + tz on a schedule and never a feed's fields, so changing kind drops the other's.
  const pickKind = (k: string) => {
    setWhenError(undefined)
    if (k === "schedule") setSrc({ kind: k, ...(record?.source.kind === "schedule" ? { every_seconds: record.source.every_seconds, cron: record.source.cron, tz: record.source.tz } : NEW_SCHEDULE) })
    else setSrc({ ...src, kind: k, cron: undefined, tz: undefined })
  }
  const setClause = (i: number, c: Partial<Clause>) => setClauses(cs => cs.map((x, j) => (j === i ? { ...x, ...c } : x)))
  return (
    <>
      <Panel data-block="source" title="Source">
        <Wrap empty="Choose the kind of source; its fields follow.">
          {isNew && <Field id="g-name" label="Name" error={errors.name}><Input id="g-name" value={newName} disabled={!!block} aria-invalid={!!errors.name || undefined} aria-describedby="g-name-msg" onChange={e => setNewName(e.target.value)} /></Field>}
          <Field id="g-kind" label="Kind" error={errors.kind}><Choice id="g-kind" label="Kind" options={["rss", "http-json", "schedule"]} value={src.kind} disabled={!!block} onPick={pickKind} /></Field>
          {src.kind && !schedule && <Field id="g-url" label="URL" error={errors.url}><Input id="g-url" value={src.url ?? ""} disabled={!!block} aria-invalid={!!errors.url || undefined} aria-describedby="g-url-msg" onChange={e => setSrc({ ...src, url: e.target.value })} /></Field>}
          {src.kind === "http-json" && <div className="grid gap-3 sm:grid-cols-2">
            <Field id="g-items" label="Items path" hint="dotted path to the list, e.g. data.items; empty: the top level"><Input id="g-items" value={src.items_path ?? ""} disabled={!!block} aria-describedby="g-items-msg" onChange={e => setSrc({ ...src, items_path: e.target.value })} /></Field>
            <Field id="g-id" label="Id path" hint="empty: guid, id, link or url is used"><Input id="g-id" value={src.id_path ?? ""} disabled={!!block} aria-describedby="g-id-msg" onChange={e => setSrc({ ...src, id_path: e.target.value })} /></Field>
          </div>}
          {src.kind && !schedule && <Field id="g-every" label="Every (seconds)" error={errors.every} hint="minimum 30"><Input id="g-every" inputMode="numeric" value={src.every_seconds ?? ""} disabled={!!block} aria-invalid={!!errors.every || undefined} aria-describedby="g-every-msg" onChange={e => setSrc({ ...src, every_seconds: e.target.value === "" ? undefined : Number(e.target.value) })} /></Field>}
          {schedule && <SchedulePicker value={src} disabled={!!block} onChange={({ source, error }) => { setWhenError(error); if (!error) setSrc({ kind: "schedule", ...source }) }} />}
        </Wrap>
      </Panel>
      {!schedule && <Panel data-block="predicate" title="Fires when (all of)">
        {clauses.length ? <ol className="m-0 flex list-none flex-col gap-2 p-0">{clauses.map((c, i) => (
          <li key={i} className="flex flex-col gap-1">
            <span className="grid grid-cols-[1fr_7rem_1fr_auto] items-center gap-1">
              <Input aria-label={`Condition ${i + 1} field`} value={c.field} suggestions={FIELDS} disabled={!!block} aria-invalid={!!errors[`c${i}`] || undefined} onChange={e => setClause(i, { field: e.target.value })} />
              <Select aria-label={`Condition ${i + 1} comparison`} value={c.op} disabled={!!block} onChange={e => setClause(i, { op: e.target.value })}>{OPS.map(o => <option key={o} value={o}>{o}</option>)}</Select>
              <Input aria-label={`Condition ${i + 1} value`} value={c.value} disabled={!!block} aria-invalid={!!errors[`c${i}`] || undefined} onChange={e => setClause(i, { value: e.target.value })} />
              <Button variant="ghost" size="sm" aria-label={`Remove condition ${i + 1}`} disabled={!!block} onClick={() => setClauses(cs => cs.filter((_, j) => j !== i))}>×</Button>
            </span>
            {errors[`c${i}`] && <span role="alert" className="text-xs text-muted-foreground">{errors[`c${i}`]}</span>}
          </li>))}</ol>
          : <p className="m-0 text-sm text-muted-foreground">No conditions: every new item fires it.</p>}
        <Button variant="outline" size="sm" className="self-start" disabled={!!block} onClick={() => setClauses(cs => [...cs, { field: "", op: ">=", value: "" }])}>Add condition</Button>
        <p className="m-0 text-xs text-muted-foreground">Fields: a dotted item path (title, item.tags), count_since_fire or age_since_last_seen.</p>
      </Panel>}
      <Panel data-block="creates" title="Creates">
        <Field id="g-ekind" label="Task kind" error={errors.ekind} hint={who ? (who.winner ? <>Claimed by <a href={`#/type?name=${who.winner}`} className={link}>{who.winner}</a></> : who.tied.length ? `${who.tied.join(" and ")} claim it equally; pu can't choose between them` : "no type claims it") : "decides which session type picks the task up"}>
          <Choice id="g-ekind" label="Task kind" options={KINDS} value={emits.kind} disabled={!!block} onPick={k => setEmits({ ...emits, kind: k })} />
        </Field>
        <Field id="g-tags" label="Tags" hint="a session type claims a kind plus tags"><span id="g-tags" className="flex flex-wrap items-center gap-2"><Chips items={emits.tags ?? []} what="tag" disabled={!!block} onRemove={t => setEmits({ ...emits, tags: (emits.tags ?? []).filter(x => x !== t) })} /><TagAdder id="g-tag-new" label="Add a tag" disabled={!!block} onAdd={t => setEmits({ ...emits, tags: withTag(emits.tags, t) })} /></span></Field>
        <Field id="g-title" label="Task title" hint={title.length > 140 ? `pu cuts this at 140 characters (now ${title.length})` : "pu cuts titles at 140 characters; {dotted.path} fills in from the item"}><Input id="g-title" value={title} disabled={!!block} aria-describedby="g-title-msg" onChange={e => setEmits({ ...emits, title: e.target.value })} /></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field id="g-priority" label="Priority"><Choice id="g-priority" label="Priority" options={["high", "medium", "low"]} value={emits.priority ?? "medium"} disabled={!!block} onPick={p => setEmits({ ...emits, priority: p })} /></Field>
          <Field id="g-stale" label="Goes stale after" error={errors.stale} hint={!stale.n && emits.priority === "high" ? "empty: high priority goes stale after 1 hour" : "empty: pu's default"}>
            <span className="flex gap-1"><Input id="g-stale" inputMode="decimal" className="max-w-24" value={stale.n} disabled={!!block} aria-invalid={!!errors.stale || undefined} aria-describedby="g-stale-msg" onChange={e => setStale({ ...stale, n: e.target.value })} />
              <Select aria-label="Stale after unit" className="max-w-32" value={stale.unit} disabled={!!block} onChange={e => setStale({ ...stale, unit: Number(e.target.value) })}>{UNITS.map(([l, k]) => <option key={l} value={k}>{l}</option>)}</Select></span>
          </Field>
        </div>
        <Field id="g-afk" label="Runs without you (afk)" hint={emits.afk ? "an agent runs these tasks alone, spending without asking" : "these tasks wait for you before an agent runs them"}>
          <Choice id="g-afk" label="Runs without you" options={["off", "on"]} value={emits.afk ? "on" : "off"} disabled={!!block} onPick={v => (v === "on" ? setAskAfk(true) : setEmits({ ...emits, afk: false }))} />
        </Field>
      </Panel>
      {health}
      <Panel data-block="dry-run" title="Dry run"><Answer label="Run dry run" disabled={!!block || schedule || !src.kind} answer={() => onDryRun(draft())} />{schedule && <span className="text-xs text-muted-foreground">schedules can't be previewed</span>}</Panel>
      <div data-block="save" className="flex flex-wrap items-center gap-2"><Button size="sm" disabled={!!block} onClick={save}>Save the trigger</Button><Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button><span role="status" className="text-xs text-muted-foreground">{block ?? said}</span></div>
      <ConfirmDialog open={askAfk} title="Let this trigger run alone?" body="With afk on, it starts sessions without asking you: each fire can spend money unattended." confirmLabel="Let it run alone" onConfirm={() => { setEmits(x => ({ ...x, afk: true })); setAskAfk(false) }} onCancel={() => setAskAfk(false)} />
    </>
  )
}
