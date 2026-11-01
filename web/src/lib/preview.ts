// Standalone preview only: ?theme=dark|light&palette=<node palette> does what the node does inside its frame — toggles
// .dark and writes the palette's source variables on <html> (the colour contract). Inside the node the node does this
// itself and no query is present, so this never runs there. Palettes copied from holonic-node web/src/data.ts PALETTES.
const P: Record<string, Record<"light" | "dark", string[]>> = {
  // brand, brand-ink, ink, ground, panel, panel-alt, panel-3, rule, rule-soft
  instrument: { light: ["#0E6E77", "#FAFBFC", "#10141C", "#E4E7EC", "#FAFBFC", "#EFF1F5", "#E8EBF0", "#C7CDD8", "#D9DEE6"], dark: ["#4CC2CE", "#0C1016", "#E8ECF2", "#0C1016", "#141A22", "#1A212B", "#1F2732", "#262E3A", "#1E2530"] },
  graphite: { light: ["#3B5BDB", "#FCFCFD", "#14161A", "#E9EAEC", "#FCFCFD", "#F1F2F4", "#EAEBEE", "#CCCED3", "#DEDFE3"], dark: ["#8CA2FF", "#101114", "#E9EAED", "#101114", "#17181C", "#1D1F24", "#23262C", "#2A2C33", "#212329"] },
  signal: { light: ["#B4530F", "#FCFBFA", "#181310", "#EAE7E3", "#FCFBFA", "#F3F0EC", "#ECE8E3", "#D3CCC4", "#E2DCD5"], dark: ["#E8894A", "#13100E", "#EFEAE5", "#13100E", "#1B1714", "#221D19", "#292320", "#332B26", "#26201C"] },
  moss: { light: ["#3F6B3A", "#FAFBF8", "#12160F", "#E5E9E1", "#FAFBF8", "#EFF2EB", "#E8ECE3", "#C7CFBF", "#DAE0D2"], dark: ["#7FBF6E", "#0D110B", "#E8EDE4", "#0D110B", "#151A12", "#1B2117", "#212819", "#28301F", "#1E2417"] },
  oxide: { light: ["#6B4A8F", "#FBFAFC", "#161320", "#E7E5EC", "#FBFAFC", "#F1EFF5", "#EAE7F0", "#CBC5D6", "#DCD8E4"], dark: ["#B69AE0", "#0F0D14", "#EAE7F2", "#0F0D14", "#17141E", "#1D1926", "#241F2E", "#2B2537", "#211C2A"] },
}
const VARS = ["brand", "brand-ink", "ink", "ground", "panel", "panel-alt", "panel-3", "rule", "rule-soft"]

export function applyPreview() {
  const q = new URLSearchParams(location.search), theme = q.get("theme"), palette = q.get("palette")
  const root = document.documentElement
  if (theme) root.classList.toggle("dark", theme === "dark")
  const p = palette && P[palette]?.[root.classList.contains("dark") ? "dark" : "light"]
  if (p) { VARS.forEach((v, i) => root.style.setProperty(`--${v}`, p[i])); root.style.setProperty("--brand-soft", p[0] + "1A") }
}
