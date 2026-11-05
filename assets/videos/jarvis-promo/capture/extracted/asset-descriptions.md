# Asset inventory — Jarvis launch promo

**No website was captured.** Jarvis is a local Windows desktop app with no
product site, so this project runs the no-capture path. Instead, the assets below
are **real captures of the product running on this machine** — the app's own
interface, observed through its own UI-Automation pipeline and annotated with its
own Set-of-Marks renderer (`jarvis.perception.annotate`). These are the video's
primary visuals; nothing here is invented.

All paths are relative to `capture/assets/`.

---

## Hero plate — the app observing itself

**`som-ui-home-clean.png`** — 1920×935, annotated, *the signature frame.*
A real screenshot of the running Jarvis interface with 41 numbered Set-of-Marks
badges drawn over genuine UI-Automation controls. Verified by vision: no browser
toolbar, tab strip or address bar is present; the boxes sit on real controls and
are legible; accent is cyan. Near-black canvas (`#02070d`), cyan accent
(`#00f0ff`), the wordmark **JARVIS** with the sub-label **LOCAL INTELLIGENCE**,
a row of icon buttons (command palette, sessions sidebar, terminal transcript,
Live Voice Mode, audio output), tab items **STREAM · VITALS · CMDS · SKILLS ·
LOGS**, and a live process stream showing `Terminal backend connected`,
`Proactive Background Daemon started.` and registered global hotkeys.
Use for: the opening POV frame, the "this is what it sees" beat, the end card.

**`som-ui-home-clean-raw.png`** — 1920×935, the *same frame with no marks.*
This is the **base plate for animation**: the workers overlay animated badges and
a cursor on top of it rather than baking in the static marks, so the numbering can
land one at a time instead of all at once.

## Machine-readable element data — use this to place animated overlays

**`observe-ui-home-clean.json`** — the 41 real elements with `role`, `name`,
exact `bbox` and `center`, **already shifted into the clean plate's coordinate
space** (0,0 = top-left of `som-ui-home-clean-raw.png`), plus `source_region`
recording the crop. Any animated badge or cursor landing must read its target
position from this file, not from a guess, so every mark lands on its real
control.

**`elements-ui-home-clean.txt`** — the same 41 controls as the plain-text list
Jarvis actually shows its model, e.g.
`[20] Button "Open command palette" @ (1497,33)`.
Use for: typesetting the terminal/type content — the literal thing the model
reads before it answers "click element 20".

## Full-desktop variants — use only when a whole-screen read is wanted

**`som-ui-home.png`** — 1920×1080, annotated, 60 marks: the same capture
*including* the browser host chrome (title bar, tabs, address bar). Honest but
less clean — the first ~19 badges land on browser furniture. Use only for a
deliberate "entire desktop, all of it observed" beat.

**`som-ui-home-raw.png`** — 1920×1080, the unannotated full-desktop frame.

**`elements-ui-home.txt`** — all 60 controls including host chrome, as text.

---

## Not available — do not invent these

- **No screen recording / video of the app.** Capture yields *stills only*. Every
  movement beat (cursor travel, badge landing, panel opening) must be
  **reconstructed in HTML** over the still plates at measured positions — this is
  the plan recorded in `BRIEF.md` § Customizations.
- **No HUD-only capture.** The capture helper temporarily hides the floating
  JARVIS HUD overlay, so the plate never contains it. Do not fake a HUD.
- **No product site, no logo file, no brand PDF.** Brand colours and fonts come
  from `capture/extracted/tokens.json`, which was read from the app's own
  stylesheet (`jarvis/browser_ui/styles.css`) — those values are authoritative.
- **No stock imagery and no people.** The concept is deliberately screen-only:
  the video never shows a human.

## Brand tokens — authoritative, from the app's own stylesheet

`capture/extracted/tokens.json` holds the real palette and type stack:
`#02070d` base canvas · `#061018` elevated · `#00f0ff` accent (cyan) ·
`#b7efff` accent-bright · `#e5f8ff` text · `#8ba8b7` muted · `#647d8b` dim ·
`#00ff9d` success · `#ff4e45` danger.
Type: **Orbitron** (display) · **Outfit** (body) · **JetBrains Mono** (mono).
The mono face carries the concept — the element list is monospace.
