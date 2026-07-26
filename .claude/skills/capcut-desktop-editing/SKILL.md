---
name: capcut-desktop-editing
description: >-
  Edit videos professionally in the CapCut Desktop app (Windows/Mac) by driving
  its GUI with desktop automation (screenshot -> click/type/keyboard -> verify).
  Use for ANY request to edit, cut, assemble, trim, color-grade, caption, add
  music/effects/text/transitions, or export video in CapCut Desktop, across any
  genre: vlogs, cinematic/travel, gaming montages, tutorials/how-tos, ads &
  product/marketing, music videos, talking-head, podcasts/interviews, and social
  shorts (TikTok/Reels/Shorts). Encodes a professional editing pipeline, not just
  button clicks.
---

# CapCut Desktop — Professional Video Editing

CapCut Desktop has **no API, CLI, or scripting**. You edit by **driving the GUI**:
take a screenshot, decide the next action, click / type / press keys, then
**screenshot again to confirm it worked**. This skill gives you (a) a reliable
way to control the app, and (b) the editing judgment to make the result look
professional in any genre.

Load reference files only when you need them:
- **`references/shortcuts.md`** — keyboard shortcuts + the automation cheat sheet. Read this first; shortcuts are your most reliable lever.
- **`references/features.md`** — step-by-step recipes for every CapCut tool (split/trim, speed curves, keyframes, chroma key, masks, color/LUT, captions, TTS, audio ducking, beat sync, text, transitions, effects, export).
- **`references/genre-playbooks.md`** — how a pro cuts each genre (pacing, structure, which tools, what to avoid). Pick the one matching the request.

---

## 1. The control model (read every time)

You are not a human clicking — you are automating a GUI. Follow this loop for
**every** operation:

1. **Screenshot** the current state.
2. **Locate** the target on screen (panel, button, clip, playhead). CapCut's
   layout is stable — see the map below.
3. **Act** — prefer a keyboard shortcut; fall back to clicking the coordinates
   you saw in the screenshot.
4. **Verify** — screenshot again and confirm the state actually changed (clip
   split, panel opened, value applied). If it didn't, don't repeat blindly:
   re-read the screen and adjust (the button may have moved, a dialog may be
   open, the timeline may not have had focus).

**Reliability rules — these prevent 90% of automation failures:**
- **Shortcuts over clicks.** `Ctrl+B` to split is 100% reliable; hunting for the
  split button is not. Timeline shortcuts only work when the **timeline has
  focus** — click once in an empty area of the timeline first.
- **Click a target once, then verify.** Never spam-click. Double-clicks and
  repeated clicks open editors or deselect things unexpectedly.
- **The playhead is your cursor.** Almost every edit happens *at the playhead*.
  Position it precisely (arrow keys nudge frame-by-frame) before splitting or
  inserting.
- **Drag = press, move, release.** To trim, grab a clip's edge handle and drag.
  To move a clip, grab its middle. Zoom the timeline in first so small drags are
  accurate.
- **One change at a time on complex ops.** Apply, verify, move on. Batching blind
  actions compounds errors.
- **If a shortcut misbehaves,** open `Help / the menu` and view the in-app
  keyboard-shortcut list to confirm the binding for this version, then continue.

### CapCut Desktop layout map
```
┌──────────────────────────────────────────────────────────────┐
│ Top tabs: Media | Audio | Text | Stickers | Effects |         │
│           Transitions | Filters | Adjustment | Captions ...    │
├───────────────────────────┬──────────────────────────────────┤
│  MEDIA / TOOL PANEL        │        PLAYER / PREVIEW          │
│  (top-left) import & browse│  (top-right) shows playhead frame│
│  the selected tab's assets │  + Export button (top-right)     │
├───────────────────────────┴──────────────────────────────────┤
│  INSPECTOR / function panel (right side when a clip is        │
│  selected): Basic, Speed, Animation, Adjustment, Audio,       │
│  Video/Cutout(Chroma key, mask), etc.                         │
├──────────────────────────────────────────────────────────────┤
│  TIMELINE (bottom): multi-track. Playhead is the vertical     │
│  line. Toolbar above it: split, delete, freeze, mirror,       │
│  crop, zoom slider. Tracks stack — higher track = on top.     │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Pre-flight — gather before you touch the app

Confirm (ask the user only if genuinely unknown — don't stall on defaults):
- **Source files** — paths to the footage/audio/images. Where are they?
- **Genre & goal** — which playbook applies, and what's the video *for*?
- **Target platform → aspect ratio** — 9:16 (TikTok/Reels/Shorts), 16:9
  (YouTube), 1:1 (feed). **Set aspect ratio on the project BEFORE adding clips**
  (Modify/project settings), not at export.
- **Length target** and whether **captions** are wanted (yes for most social).
- **Output path** for the export, and whether overwriting an existing file is OK.

Then: open CapCut (`open_app` / desktop automation), **New Project**, set aspect
ratio/resolution/frame rate, and import the source media (Media tab → Import).
Leave resolution/frame-rate on defaults unless the footage or platform demands
otherwise.

---

## 3. The professional editing pipeline

Genre changes the *flavor*; this **order** stays constant. Editing out of order
(e.g. color-grading before you've locked the cut) wastes work.

1. **Assembly** — drop all keeper clips on the timeline in story order. Don't
   polish yet. Get the spine of the video down.
2. **Rough cut** — tighten. Remove dead air, bad takes, "um"s. On talking-head,
   split at each pause and delete the gap (`Ctrl+B` then `Delete`, or `Q`/`W` to
   trim to the playhead). Cut on action. Get to roughly target length.
3. **Fine cut / pacing** — refine every cut point. Use **J-cuts and L-cuts**
   (lead audio in before / carry it out after a picture cut) so dialogue flows.
   Cover jump cuts with **B-roll** on the track above. Sync cuts to the music
   **beat** where energy matters.
4. **Motion & speed** — **speed ramps** (Curve, not flat) for emphasis;
   **keyframes** for zoom/pan/reframe; freeze frames for punctuation. Restraint:
   motion should serve a beat, not decorate every clip.
5. **Color** — one **filter/LUT applied consistently** across clips (30–60%
   intensity), then per-clip **Adjustment** (exposure, contrast, saturation,
   temperature) to match shots. Use *Apply to all* for a base grade.
6. **Audio** — this is half of "professional." Level **dialogue to peak around
   −6 to −3 dB**; sit **music ~−18 to −20 dB under voice** (enable **auto
   ducking** / manually keyframe music down under speech). **Noise reduction** on
   dialogue. Add SFX (whooshes/impacts) on transitions and hits. Beat-sync.
7. **Titles & graphics** — hook text, lower-thirds, captions. For social,
   **auto-captions** are near-mandatory; correct the 5–10% it mishears. Keep
   fonts to 1–2; keep text on screen long enough to read.
8. **Polish & export** — watch it through once at full speed. Check the **first 3
   seconds hook** hardest. Then Export at the right settings (§5).

---

## 4. Pick a genre playbook

Route to the matching section of **`references/genre-playbooks.md`**:
Talking-head/Vlog · Cinematic/Travel · Gaming montage · Tutorial/How-to ·
Ad/Product/Marketing · Music video · Podcast/Interview · Social short/UGC.

Each playbook gives the pacing, structure, must-use tools, and what to avoid.
When unsure, default to the **Social short** playbook for vertical content and
**Talking-head** for horizontal.

---

## 5. Export settings (defaults that survive platform recompression)

| Target | Aspect | Resolution | FPS | Notes |
|---|---|---|---|---|
| TikTok / Reels / Shorts | 9:16 | 1080p | 30 | 1080p handles upload recompression cleanly; 4K gives no visible gain here |
| YouTube (standard) | 16:9 | 1080p or 4K | 24 (narrative) / 30 | Use 4K only if source is 4K |
| Sports / gaming / action | 16:9 or 9:16 | 1080p (4K if source) | 60 | Preserve fast motion |
| Feed / square | 1:1 | 1080p | 30 | |

Bitrate: **Recommended/High** is fine; raise only if you see compression
artifacts. Format: **MP4 (H.264)** unless the user needs otherwise. After export,
confirm the file exists at the agreed path.

---

## 6. What makes it "professional" (the quality bar)

- **Hook in ≤3s.** Lead with the most compelling moment, not a slow intro.
- **Every cut earns its place.** No clip runs longer than it holds attention.
- **Motivated transitions only.** 90% hard cuts. A whoosh/zoom transition is for
  a genuine scene/energy change — not every clip. Overusing flashy transitions is
  the #1 tell of an amateur edit.
- **Consistent color and consistent audio loudness** across the whole video.
- **Audio is intentional** — leveled dialogue, ducked music, SFX on hits, no
  clipping, no dead silence.
- **Text is readable** — safe from platform UI (keep captions clear of the bottom
  ~15% on vertical), on screen long enough, high contrast.
- **It ends deliberately** — a button, a CTA, or a clean cut to black; never a
  fade that trails into nothing.

---

## 7. Safety & limits

- **Pro watermark:** Templates/effects/filters with a **Pro** badge stamp a
  watermark on free-plan exports. Prefer free assets; if the user wants a Pro
  asset, warn that export will carry a watermark unless they have Pro.
- **Account/login:** Some AI features (background removal, some captions) need an
  account and have free monthly caps. If the app shows a login/upgrade wall,
  surface it to the user rather than entering any credentials yourself.
- **Don't overwrite blindly.** If the export path already has a file, confirm
  before overwriting. Editing is destructive to the timeline but CapCut
  autosaves the *project*; the source media is never modified.
- **Verify, don't assume.** "Applied a filter" is only true if the screenshot
  shows it. Report what you actually confirmed on screen, including anything you
  couldn't complete.
