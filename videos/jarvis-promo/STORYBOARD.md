---
format: 1920x1080
duration: 36s
message: "A tiny local model can drive your whole desktop, because it never has to guess a pixel."
arc: Hook → Mechanism → Proof → Constraint → Lockup
audience: developers and technically-minded desktop users who are sceptical of AI-agent claims
music: "restrained minimal electronic underscore, low sustained drone, sparse pulsing percussion, cold and purposeful, no melodic uplift, no orchestral swell, steady tension that stays under a voice"
mode: collaborative
---

## Video direction

**Concept — "Set of Marks".** The video is narrated from Jarvis's own point of view:
we watch the screen the way it watches the screen. Every screen beat is a **real
capture** of the running product, and every mark is a real UI-Automation control at
its real coordinates, read from `capture/assets/observe-ui-home-clean.json`. Nothing
is invented; the mechanism is the hook.

**Value before evidence (reverse iceberg).** The hook speaks the viewer's language —
what the machine now does — and the value claim lands by beat 2. The mechanism
(Set-of-Marks) is *evidence*, not the spine. Self-check: delete the mechanism and
proof beats and the remaining beats still state the value; delete the value beats and
the video would be a feature tour.

**Register.** Precise, terminal-flavoured, confident, zero mysticism. No orb, no
neural-network glow, no floating chat bubbles — this is a tool, and it looks like one.
The two deliberate inversions: the model is described as *small* and *bad at pixels*
rather than as powerful, and the constraint (4 GB VRAM) is stated as the achievement
rather than apologised for.

**Palette system.** `frame.md` (Broadside, remixed) is the colour truth: near-black
`#02070d` ground, cyan `#00f0ff` accent, 1px hairlines, sharp corners, no shadow.
Roles: **ground** = the real plate; **ink** = mono chrome and element text; **accent**
= exactly one thing per frame (the landed target, the seated mark, the hero numeral,
the wordmark). Orbitron display lowercase, **JetBrains Mono** for all chrome and any
element-list text (the concept lives in the mono layer), Outfit body. One register per
frame.

**The plate spine (geometry, not decoration).** One asset is the ground of all five
frames: `capture/assets/som-ui-home-clean-raw.png`, a real 1920×935 crop of the running
Jarvis interface (`source_region` (0,145)–(1920,1080) of a 1920×1080 screen, so element
coordinates from the observation JSON map **1:1 into plate space**). It sits at x=0,
y=0, width 1920, height 935, **scale 1.000, position static for the whole video** — the
screen never moves; only Jarvis's reading of it changes. The 145px of slack below the
plate is exactly where the caption band sits (bottom 17% = y>896), so the plate's own
content never fights the captions.

**The marks are drawn live, never baked.** The two annotated captures
(`som-ui-home-clean.png`, `som-ui-home.png`) are deliberately **not used** in any frame:
the marks are drawn in HTML from `observe-ui-home-clean.json` so the plate is
pixel-identical at every cut. Frame 1 and Frame 2 are where the annotation IS the
subject and it must be freshly drawn. From Frame 3 on the annotation is settled and
rides the dim ramp as texture. Rationale: swapping to a pre-annotated plate at a cut
would pop 41 marks into existence (or duplicate them).

**Dim ramp = the only change across cuts.** The plate *and the seated badge field* are
one layer and share one opacity ramp, stated numerically in each `handoff_*` block:
`1.00 → 0.88 → 0.55 → 0.28 → 0.22`. **The value at every cut is the outgoing frame's
value** — the incoming frame eases to its own value over the first 0.4–0.6s, so no cut
ever steps.

**Motion grammar + reveal model.** Long-tail settles (`power3`; `expo.out` on a fast
arrival). Never bouncy, never overshoot. Every reveal is a *time-coded* window paced to
the voiceover: at t=0 only what the VO is saying then is on screen, and each further
piece arrives on its spoken cue, biased to the back ~50%. Camera is essentially locked
(one virtual camera; the only moves are element-level). **Idle budget: near zero** — at
most a low-amplitude jitter (`sine-wave-loop`, low register) on one held element per
frame; no breathing cards, no lazy drift.

**Rhythm / held-frame allocation.** F1 reveals late and holds ~2.7s of its 6.3s
read. F3 is the busiest frame (status theatre) and is followed by **F4's held numerals**
— the deliberate breather. **F5 is a single held read** (2.99s): the wordmark assembles
and stops, and the frame cuts on a held mark, never a fade into nothing.

**Sound policy — no SFX.** The register is austere and this mix is voice + one
restrained bed; there is also no offline SFX provider in this run (HeyGen is
credit-blocked at $0). `sfx: none` on every frame is deliberate, not an omission.

**Negative list.** Never: orb / neural glow / purple-blue "AI" gradients, floating chat
bubbles, generic decorative shapes standing in for real UI, browser chrome or a fake
cursor rendered as a real pointer, stock footage, more than one accent colour per
frame. And both motion failure modes: **slideshow** (everything dumped in the first
~25%, then frozen) and **screensaver** (elements floating independently with no cue).
Any element that is not arriving on a spoken cue is still.

**Legibility.** A 16:9 embed is often watched at phone width, so any text carrying the
argument is set large; the mono element list is a decorative texture and must never be
the only carrier of a claim.

**Corrections carried from capture (verified against the real observation).** The
clicked control in F1 is element **`[3]`**, not `[20]` — `"Open command palette"` is
`[3] Button @ (1497,33)`; `[20]` is `Text "SYSTEM" @ (126,196)`. The board has 41
controls, ids `0–40`, so "forty-one" is the *count*, which is how F1's title now reads.

**Duration.** The brief asked 45s. Narration measured 36.011s and duration sync is
mechanical, so the cut lands at **36.0s** (`6.315 + 11.413 + 6.4 + 8.896 + 2.987`).
Every frame stays inside its blueprint's duration range, so the drift serves the piece —
reported, not silently absorbed.

---

## Frame 1 — Element three of forty-one

- scene: A cursor lands dead-centre on a real control; the badge that named it is already there
- duration: 6.315s
- poster: 4s
- transition_in: cut
- status: animated
- blueprint: cursor-ui-demo (Adapt)
- focal: catalog component `simulated-cursor` (click pulse) — installed and restyled to the brand accent; searched the live catalog for the look, and `simulated-cursor` / `press-ripple` / `oversized-cursor` are the fitting blocks
- asset_candidates: assets/som-ui-home-clean-raw.png — the real 1920×935 plate, the spine ground of all five frames; assets/observe-ui-home-clean.json — the true geometry of all 41 controls, drives the one badge; assets/elements-ui-home-clean.txt — the mono annotation type
- roles: som-ui-home-clean-raw.png = background (full-bleed plate, opacity 1.00, static) · observe-ui-home-clean.json = the mark geometry (drives the one badge) · elements-ui-home-clean.txt = supporting (the annotation's mono type)
- sfx: none
- voiceover: "It moves the mouse, opens the app, types the thing — on a model small enough to run on this laptop."
- src: compositions/frames/01-element-three-of-forty-one.html

Cold open on the real Jarvis interface, unmarked. A cursor enters and travels to one
control — its path deliberate, not drifting — and lands dead-centre. The mono badge
`[3] Button "Open command palette" @ (1497,33)` snaps in at the moment of landing,
naming what it just touched. The claim: the click was not guessed. This frame carries
the outcome in the viewer's language before any mechanism is explained.

**Adapt:** keep the blueprint's signature (a cursor is the actor; the click emits a
ripple; the surface alone carries the shot). Change: the surface is a **real captured
plate**, not a reconstructed UI, the camera is **locked** (the static-stage pole), and
there is **one** interaction, not 2–4 beats.

Scene 1 (0.0–1.2s): the plate at full opacity, static, nothing else on screen but one
dim mono line seating lower-left above the caption band — `observe · 41 controls`
(type-on with caret). Layered depth plate-only; no marks yet. VO: "It moves the mouse,"
Scene 2 (1.2–2.1s): the cursor enters from the left edge and decel-travels rightward
along the toolbar row, level with the top bar — deliberate, straight, no wobble.
Camera locked. VO: "opens the app,"
Scene 3 (2.1–3.0s): the cursor lands dead-centre on the control at **(1497,33)**; it
presses (a small compress) and emits one expanding ripple that fades in place. Nothing
else has appeared. VO: "types the thing —"
Scene 4 (3.0–3.6s): the badge snaps in on the cue — accent hairline brackets around the
real bbox **(1454,17)–(1540,50)** with a short leader line, and beside it the mono
annotation `[3] Button "Open command palette" @ (1497,33)`, seating **below the toolbar
row, right side**, clear of the clicked control and clear of the caption band.
VO: "on a model small enough to run on this laptop."
Scene 5 (3.6–6.315s): everything resolves and holds **still** — the badge seated, the
cursor at rest on the control, the mono line steady. No drift, no breathing. The held
read is the point: the click is a fact, not a flourish.
- handoff_out: element = plate + badge field · asset som-ui-home-clean-raw.png ·
  x=0, y=0, w=1920, h=935 · scale 1.000 · opacity 1.00 · motion: none (static) ·
  additionally at the cut: badge `[3]` seated at bbox (1454,17)–(1540,50), opacity 1.00, static

## Frame 2 — Never guess a pixel

- scene: 41 real numbered marks cascade onto the real interface, one at a time, and hold
- duration: 11.413s
- poster: 8s
- transition_in: cut
- status: animated
- blueprint: grid-card-assemble (Adapt)
- focal: the badge field itself — hand-authored, because the catalogue search for a numbered-mark overlay returned nothing that fits (no generic block carries real UI-Automation coordinates); the frame's `signature` is the staggered self-assembly into a resolved array that holds
- asset_candidates: assets/som-ui-home-clean-raw.png — the spine plate; assets/observe-ui-home-clean.json — the true geometry of all 41 marks; assets/elements-ui-home-clean.txt — the mono labels for the marks and the left stream column
- roles: som-ui-home-clean-raw.png = background (full-bleed plate, easing 1.00 → 0.88) · observe-ui-home-clean.json = cutout (the true geometry of all 41 marks) · elements-ui-home-clean.txt = supporting (the left stream column)
- sfx: none
- voiceover: "The trick isn't a bigger model. It's that we never ask the model for a pixel. Windows already knows where every control is — so we number them, and the model only picks a number."
- src: compositions/frames/02-never-guess-a-pixel.html

The mechanism beat, and the video's argument. Over the unmarked plate, the 41 real
elements assemble as a staggered cascade — each badge arriving at its own true
coordinate from `observe-ui-home-clean.json`, the mono list streaming in beside them in
the app's own **left process-stream column** (x≈40–360, where the real stream lives) as
the texture of what the model actually reads. The line lands mid-cascade so the
"only picks a number" clause coincides with the final marks seating. Ends held on the
fully-numbered screen.

**Adapt:** keep the signature — many items self-assemble in a staggered cascade into a
resolved array and hold. Change: the "grid" is an **irregular field of real screen
coordinates**, and per the blueprint's own merge tension for dense arrays (its
`center-outward-expansion` backing caps at 3–8 items; 41 would mid-flight collide) every
badge slides a **short distance into its own slot** from partially-spread start
positions — never a centre burst.

Scene 1 (0.0–1.9s): the plate eases 1.00 → 0.88 over the first 0.4s, holding the one
seated badge `[3]` from F1 exactly where it was. A mono line types on upper-left-third:
`a bigger model · not the trick` (caret blinks once, then holds). **No further marks.**
VO: "The trick isn't a bigger model."
Scene 2 (1.9–4.9s): the line backspaces-and-retypes to `never ask for a pixel`
(character-by-character delete then type — the thesis is re-stated in Jarvis's own
register, not the VO's). Simultaneously the first trickle of badges lands — the stream
column seeds its first rows at the left — and the plate holds at 0.88.
VO: "It's that we never ask the model for a pixel."
Scene 3 (4.9–7.1s): the cascade opens up: badges arrive in a staggered wave across the
plate at their true coordinates (per-item stagger, short-path into slot, no scatter),
the left stream column filling in lockstep row-by-row (each row whose control is being
marked). VO: "Windows already knows where every control is"
Scene 4 (7.1–8.6s): the wave accelerates through the remaining badges, occupying the
real toolbar, the tab row, the visual-effects cluster and the top-right controls —
35 of 41 have seated. VO: "— so we number them,"
Scene 5 (8.6–11.413s): the last badges seat, everything resolves, and the accent picks
out exactly one mark; a final mono line seats beneath the stream column:
`the model returns · one integer`, and the whole field holds **still**. VO: "and the
model only picks a number."
- handoff_in: element = plate + badge field · asset som-ui-home-clean-raw.png ·
  x=0, y=0, w=1920, h=935 · scale 1.000 · opacity **1.00 at the cut**, easing to 0.88 over
  the first 0.4s · badge `[3]` continues from F1 at bbox (1454,17)–(1540,50), opacity 1.00,
  static — do not re-animate it
- handoff_out: element = plate + badge field · x=0, y=0, w=1920, h=935 · scale 1.000 ·
  opacity 0.88 · motion: none (static) · all 41 badges seated at their true coordinates,
  each opacity 1.00, static · left stream column seated, each row opacity 1.00

## Frame 3 — Perceive, think, act

- scene: The loop runs as working-state theater — status lines swap, rows arrive and check off
- duration: 6.4s
- poster: 4.5s
- transition_in: cut
- status: animated
- blueprint: agent-progress-theater (Adapt)
- focal: catalog component `terminal-simulator` — a terminal that streams log output, matching this beat exactly (searched the live catalog; it is the fitting block). The content streamed is the **real** captured status lines
- asset_candidates: assets/som-ui-home-clean-raw.png — the spine plate; assets/elements-ui-home.txt — the real captured status and stream lines; assets/observe-ui-home-clean.json — the true status-line strings and their coordinates; assets/som-ui-home.png — UNUSED because it carries baked marks and the spine rule draws them live; assets/som-ui-home-clean.png — UNUSED for the same reason
- roles: som-ui-home-clean-raw.png = background (the spine plate, easing 0.88 → 0.55) · observe-ui-home-clean.json = supporting (the true status-line strings and coordinates) · elements-ui-home.txt = supporting (mono type) · som-ui-home.png **and som-ui-home-clean.png = unused in this frame** — both carry baked marks, and the spine rule draws the marks live so the ground is pixel-identical at every cut; swapping to an annotated plate here would pop 41 marks (or double them)
- sfx: none
- voiceover: "Perceive. Think. Act. Then look again. That loop is the whole product — and it runs until the job is done."
- src: compositions/frames/03-perceive-think-act.html

The proof beat: the loop as it actually appears, using the real captured status lines —
`[21] Text "Terminal backend connected" @ (180,210)`,
`[23] Text "⏰ Proactive Background Daemon started." @ (216,254)`, the five registered
global hotkeys `[24]–[28]`, and
`[29] Text "🚀 Floating Mini HUD & Global Hotkeys initialized." @ (211,525)`. The three
stage names hold as a fixed anchor while the stream beside them advances and rows check
off as they complete, then the whole thing resolves on the swing back to PERCEIVE.
Deliberately theatre-of-work, not a cursor clip: the state mutation is the demo.

**Adapt:** keep the signature — a trigger beat hands the frame to the machine, which
visibly works, then a receipt cascades and **changes state**. Change: no warm off-white
canvas and no white cards (the palette is the dark plate); the machine's working state
is carried by **real captured log lines** rather than manufactured status couplets; and
the receipt is hairline mono rows, not cards.

Scene 1 (0.0–1.0s): the plate eases 0.88 → 0.55; the 41 seated badges hold as faint
texture. `PERCEIVE` seats alone at centre-right (x≈1200, y≈520) in display lowercase
with wide tracking, on a hairline baseline. Nothing else. VO: "Perceive."
Scene 2 (1.0–2.0s): `THINK` seats to its right on the same baseline, and a thin accent
arc spinner ignites beside the pair (living SVG internals, one bounded loop).
VO: "Think."
Scene 3 (2.0–3.0s): `ACT` seats — the three anchors complete and lock. The spinner
holds. VO: "Act."
Scene 4 (3.0–4.3s): the loop turns — a hairline connector draws PERCEIVE → THINK → ACT
→ PERCEIVE in one pass (`svg-path-draw`), and as it closes the **left stream column**
begins receiving the real lines: `Terminal backend connected` then
`⏰ Proactive Background Daemon started.`, each row seated on its own beat.
VO: "Then look again. That loop is the whole product —"
Scene 5 (4.3–5.5s): the state mutation runs — each seated row's numbered badge flips to
a solid accent check and its label strikes through and dims
(`dynamic-content-sequencing` state machine + `svg-path-draw` check + `css-marker-patterns`
strike). Three hotkey rows arrive and check off in succession.
Scene 6 (5.5–6.4s): two more real rows cascade — `⌨️ Registered global hotkey: alt+v`
and `🚀 Floating Mini HUD & Global Hotkeys initialized.` — and the frame **cuts while the
last row is still mid-check** (a partially-drawn arc outline on it): the work is visibly
ongoing, which is the whole claim. VO: "and it runs until the job is done."
- handoff_in: element = plate + badge field · asset som-ui-home-clean-raw.png ·
  x=0, y=0, w=1920, h=935 · scale 1.000 · opacity **0.88 at the cut**, easing to 0.55 over
  the first 0.5s · all 41 badges continue at their exact F2-out coordinates **and ride the
  same ramp** (1.00 → 0.55, easing with the plate) · the left stream column continues in place
- handoff_out: element = plate + badge field + stream column · x=0, y=0, w=1920, h=935 ·
  scale 1.000 · opacity 0.55 · motion: none (static) · rows seated, five checked,
  one mid-check

## Frame 4 — Four gigabytes

- scene: The hardware constraint is set as the hero, not the apology
- duration: 8.896s
- poster: 6s
- transition_in: cut
- status: animated
- blueprint: dataviz-countup (Adapt)
- focal: catalog block `mk-progress-stat` — big numeral with count-up, label and a thin progress track filling to value/max (searched the live catalog; also `count-up` and `number-wheel` fit, this one carries the label + track the beat wants)
- asset_candidates: assets/som-ui-home-clean-raw.png — the spine plate behind the numerals; assets/observe-ui-home-clean.json — the true badge geometry that stays as dim texture
- roles: som-ui-home-clean-raw.png = background (full-bleed plate, easing 0.55 → 0.28) · observe-ui-home-clean.json = supporting (the true badge geometry that stays as dim texture behind the numerals)
- sfx: none
- voiceover: "Four gigabytes of VRAM. Eight gigabytes of RAM. No API key, no cloud round trip, nothing leaving this machine."
- src: compositions/frames/04-four-gigabytes.html

The constraint beat, and the credibility beat for a sceptical audience: the numbers
are the hero. A restrained count-up seats `4` and `8` as graphic primitives at
Broadside display scale, then the three negations land as a mono list beneath them —
scanning straight into the dimmed interface behind, so "nothing leaving this machine"
reads against the machine it stays on.

**Adapt:** keep the signature — a numeral that counts up while its scale grows to the
final type size, landing as one beat with its paired graphic. Change: two numerals
instead of an instrument chain, and **no camera push-through** — the frame is locked and
the numerals do the work (the blueprint's gauge-beat precedent: fully static, all
push-through element-level).

Scene 1 (0.0–2.2s): the plate eases 0.55 → 0.28. `4` counts 0 → 4 at the left third
(the numeral growing with the value), `GB VRAM` in mono beneath it, and a thin accent
track fills to match on the same ease. VO: "Four gigabytes of VRAM."
Scene 2 (2.2–4.4s): `8` counts 0 → 8 at the right two-thirds with its own track and
`GB RAM` beneath; the pair now reads as a comparison, `4` smaller than `8` by the same
ratio the real numbers are. VO: "Eight gigabytes of RAM."
Scene 3 (4.4–6.9s): the numerals hold and the three negations arrive **one per spoken
cue** as mono rows in the lower third, each with a small accent check seating after it:
`no API key` · `no cloud round trip` · `nothing leaves this machine`.
VO: "No API key, no cloud round trip, nothing leaving this machine."
Scene 4 (6.9–8.896s): a single accent hairline draws left-to-right beneath the two
numerals and locks the pair; everything holds **still** to the end. This is the video's
deliberate breather after F3's activity.
- handoff_in: element = plate + badge field · asset som-ui-home-clean-raw.png ·
  x=0, y=0, w=1920, h=935 · scale 1.000 · opacity **0.55 at the cut**, easing to 0.28 over
  the first 0.6s · badge field continues at its exact F3-out coordinates, riding the same ramp
- handoff_out: element = plate + badge field · x=0, y=0, w=1920, h=935 · scale 1.000 ·
  opacity 0.28 · motion: none (static)

## Frame 5 — Local intelligence

- scene: The wordmark comes to exist and holds with the honest CTA
- duration: 2.987s
- poster: 2.2s
- transition_in: cut
- status: animated
- blueprint: logo-assemble-lockup (Adapt)
- focal: catalog block `logo-outro` — piece-by-piece assembly into a lockup with tagline and URL pill (searched the live catalog; `bottom-up-letters`, `top-down-letters` and `tracking-in` are the alternatives, this one carries the assembly-plus-lockup shape)
- asset_candidates: assets/som-ui-home-clean-raw.png — the spine plate; assets/elements-ui-home-clean.txt — the mono fragments that become the wordmark; assets/som-ui-home-clean.png — UNUSED (baked marks)
- roles: som-ui-home-clean-raw.png = background (the spine plate, easing 0.28 → 0.22) · elements-ui-home-clean.txt = cutout (the mono fragments that become the wordmark) · som-ui-home-clean.png = unused (baked marks; the spine rule draws them live)
- sfx: none
- voiceover: "Jarvis. Local intelligence that has hands."
- src: compositions/frames/05-local-intelligence.html

The lockup. `JARVIS` assembles from the mono badge fragments that have carried the
whole video — the numbers regroup into the wordmark — over the dimmed real interface,
with `LOCAL INTELLIGENCE` seating beneath it and the real run command typed in mono.
Ends deliberately on a held mark, not a trailing fade.

**Adapt:** keep the signature — the mark is **built from arriving parts** and resolves
into a centred lockup that holds. Change: no glow bloom (this register has no glow), on
the dimmed real plate rather than a cleared stage, and the blueprint's URL-pill slot
becomes the **real** entry command — `python run.py --browser` — never an invented CTA.

Scene 1 (0.0–1.5s): the plate eases 0.28 → 0.22; a handful of the seated badge
fragments lift off the plate and converge toward centre (short decel paths), resolving
as they arrive into the display lowercase `JARVIS` — the numbers becoming the word.
VO: "Jarvis."
Scene 2 (1.5–2.987s): `LOCAL INTELLIGENCE` seats beneath the mark in mono as the
fragments' leader lines sweep out and fade; the real command
`python run.py --browser` types on under it. The lockup holds **dead still** for the
final beat and the frame cuts on the held mark.
VO: "Local intelligence that has hands."
- handoff_in: element = plate + badge field · asset som-ui-home-clean-raw.png (the
  F5 backdrop region matches it 1:1) · x=0, y=0, w=1920, h=935 · scale 1.000 ·
  opacity **0.28 at the cut**, easing to 0.22 over the first 0.4s · badge field continues
  at its exact F4-out coordinates, riding the same ramp
