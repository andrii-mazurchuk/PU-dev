import { entries } from "./entries"

// The living gallery (retro 2026-10-30): every element in every configuration, from stage 3 on, each at its own
// address #/gallery/<component>/<variant>/<state>; a shorter path shows everything under it.
// One cell per contract component × variant × state. tools/gallery-check.ts reads the data-* attributes:
//   data-component, data-variant, data-state on the cell; data-gallery-target on the element under test
//   (defaults to the cell's first child); data-gallery-trigger on whatever opens an overlay.
// The cell's first div is the element's container: entry.width gives it the real width it lives in.
export function Gallery({ path = [] }: { path?: string[] }) {
  const [c, v, s] = path
  const shown = entries.filter(e => (!c || e.component === c) && (!v || e.variant === v) && (!s || e.state === s))
  const byComponent = Map.groupBy(shown, e => e.component)
  const at = (...p: string[]) => "#/gallery/" + p.map(encodeURIComponent).join("/")
  return (
    <main className="p-8 flex flex-col gap-12">
      <div className="flex gap-2 text-sm">
        <button type="button" className="rounded-md border border-border px-2 py-1" onClick={() => document.getAnimations().forEach(a => { a.pause(); a.currentTime = Number(a.effect?.getTiming().duration ?? 0) / 2 })}>Freeze motion at 50%</button>
        <button type="button" className="rounded-md border border-border px-2 py-1" onClick={() => document.getAnimations().forEach(a => a.play())}>Replay</button>
      </div>
      {path.length > 0 && <a href="#/gallery">All elements</a>}
      {!shown.length && <p>No element at {at(...path)}.</p>}
      {[...byComponent].map(([component, list]) => (
        <section key={component} className="flex flex-col gap-4">
          <h2 className="text-foreground"><a href={at(component)}>{component}</a></h2>
          <div className="flex flex-wrap gap-6 items-start">
            {list.map(e => (
              <figure key={`${e.variant}-${e.state}`} className="m-0 flex max-w-full flex-col gap-2"
                data-gallery-item="" data-component={e.component} data-variant={e.variant} data-state={e.state}>
                <div style={e.width ? { width: e.width, maxWidth: "100%" } : undefined}>{e.render()}</div>
                <figcaption className="text-muted-foreground">
                  <a href={at(e.component, e.variant, e.state)}>{e.variant} · {e.state}</a>{e.note ? ` · ${e.note}` : ""}{e.width ? ` · ${e.width}px` : ""}
                </figcaption>
              </figure>
            ))}
          </div>
        </section>
      ))}
    </main>
  )
}
