// Which session type picks up a task, as pu wires it: a type claims {kind, tags}; it matches a task of that kind
// carrying all of its tags, and the most specific match (most tags) wins. Two matches equally specific are a tie
// pu can't settle, which the form says rather than guessing.
export type Claim = { type: string; kind?: string; tags?: string[] }

export function claimant(claims: Claim[], kind: string, tags: string[]): { winner?: string; tied: string[] } {
  const fits = claims.filter(c => c.kind === kind && (c.tags ?? []).every(t => tags.includes(t)))
  if (!fits.length) return { tied: [] }
  const most = Math.max(...fits.map(c => (c.tags ?? []).length)), top = fits.filter(c => (c.tags ?? []).length === most)
  return top.length === 1 ? { winner: top[0].type, tied: [] } : { tied: top.map(c => c.type) }
}

/** The other types claiming the same kind, for the type form's note. */
export const sameKind = (claims: Claim[], self: string, kind?: string) => (kind ? claims.filter(c => c.type !== self && c.kind === kind) : [])
