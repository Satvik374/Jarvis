# CapCut Desktop — Feature Recipes

Each recipe is a click/keyboard path. **Screenshot before and after every step**
and verify. Panel names follow the layout map in `SKILL.md`. Menu labels can
shift slightly between versions — if a label isn't where described, screenshot,
read the current UI, and adapt.

---

## Cutting & timeline

**Split / trim / ripple-delete** → see `references/shortcuts.md` (Ctrl+B, Q, W,
Delete). This is the backbone of every edit.

**Freeze frame:** playhead on the frame → toolbar **Freeze** (snowflake) →
inserts a still you can extend by dragging its edge. Good for punctuation/reaction beats.

**Reverse:** select clip → Inspector → **Speed** → toggle **Reverse**.

**Group clips:** select multiple → `Ctrl+G` so B-roll + its SFX move together.

---

## Speed & motion

**Flat speed change:** select clip → Inspector → **Speed → Normal** → set
multiplier (0.1x–100x).

**Speed ramp (cinematic — use this, not flat):**
1. Select clip → Inspector → **Speed → Curve**.
2. Start from a preset (**Hero**, **Bullet**, **Montage**) then drag the control
   points: fast lead-in → **slowest point parked on the visual peak of the
   action** → accelerate out. The fast→slow→fast contrast is what reads as
   cinematic.
3. For 60fps→smooth slow-mo, enable **optical flow** if offered.

**Keyframe animation (zoom / pan / reframe / opacity):**
1. Select clip → Inspector → **Basic** (Scale/Position/Rotation/Opacity).
2. Playhead at start → click the **◆ keyframe diamond** next to the property to
   set keyframe A.
3. Move playhead forward → change the value (e.g. Scale 100→115%) → a keyframe B
   is added automatically. CapCut interpolates the motion between them.
4. Screenshot the timeline; keyframe dots should appear on the clip. Preview to
   confirm the move.
   *Uses:* slow push-in on a talking head, Ken Burns on stills, punch-in on a
   kill/highlight, animated lower-thirds.

---

## Color

**Filter / LUT (base look):** top tab **Filters** (or Inspector → **Filters**) →
pick one (cinematic/retro/social categories) → drag onto clip → set **intensity
30–60%**. Apply the **same** filter at the **same** intensity to every clip for
cohesion. Use **Apply to all** to push a base grade across the timeline.

**Manual grade (match shots):** select clip → Inspector → **Adjustment** →
Brightness/Contrast/Saturation/Highlights/Shadows/Temperature/HSL/Curves. Fix
exposure/white-balance first, then push style. Then **Apply to all** if shots
share conditions, and tweak outliers individually.

**Custom LUT import (desktop only):** Adjustment/Filters → **LUT → import** your
`.cube` → apply → dial intensity. Use the same LUT on adjacent clips for seamless
tone.

---

## Cutout (green screen & background removal)

**Chroma key (green screen):** select clip → Inspector → **Cutout → Chroma key**
→ eyedropper the background color → adjust **Intensity/Strength** and **Shadow**
until edges are clean → reduce green fringing with **Spill suppression** →
feather/soften edges. Place the new background on the track **below** the cutout.

**AI background removal (no green screen):** select clip → **Cutout → Auto
cutout** (a.k.a. remove background). Best with a single subject on a contrasting
background. Free plan is capped (~5/mo). Check edges (hair) on screenshot; clean
bleed-through with a mask if needed.

**Mask:** select clip → Inspector → **Mask** → choose shape (linear/mirror/
circle/rectangle) → drag to position, feather the edge. Combine with keyframes
for reveals or split-screen.

---

## Audio (half of "professional")

**Levels:** select audio → Inspector → **Basic/Volume**. Target **dialogue peaks
≈ −6 to −3 dB**; **music ≈ −18 to −20 dB under voice**. Enable **Normalize
loudness** to even clip-to-clip volume.

**Noise reduction:** select the dialogue clip → Inspector audio tools → enable
**Reduce noise** (removes hum/wind/room tone). Verify voice still sounds natural.

**Auto ducking:** with music + a voice/dialogue track present, enable **Auto
duck** so music dips automatically under speech. If unavailable, do it manually:
keyframe the music volume down (−18 dB) at the start of each speech segment and
back up between them.

**Beat sync (cut to music):**
1. Add music to an audio track.
2. Select it → toolbar **Beats** → CapCut auto-places beat markers on the
   waveform; toggle markers on/off.
3. Zoom the waveform until amplitude peaks are visible; **place your video cuts
   on the beat peaks.** For syncopated tracks, mark beats manually on the peaks.

**SFX:** top tab **Audio → Sound effects** — add whooshes on transitions,
impacts/booms on hits, risers before drops. Keep them low under the mix.

**Text-to-speech (AI voiceover):** select a text/caption element → **Text to
speech** → pick a voice → generates a voiceover clip. Usable for narration; not a
substitute for real emotional VO.

---

## Text & captions

**Auto-captions (near-mandatory for social):** top tab **Captions → Auto
captions** → pick language → CapCut transcribes to synced text clips (≈30s for a
60s clip; 90–95% accurate). **Click each caption block and fix mishears.** Style
the font/color/size once and apply to all. (Free plan caps ~10 min/video; some
animated styles are Pro.)

**Titles / lower-thirds:** top tab **Text → Add text** → drag to timeline →
Inspector for font/color/size → **Animation** tab for in/out (Typewriter, Fade,
Pop). Keep to 1–2 fonts. Keep text on screen long enough to read (~1s per 3–4
words). Keep vertical captions clear of the bottom ~15% (platform UI).

**Stickers/overlays:** top tab **Stickers** → drag onto its own track. Use
sparingly and on-brand.

---

## Transitions & effects

**Transition (between two adjacent clips):** top tab **Transitions** → drag one
onto the **seam** between clips → set duration. **Default to hard cuts;** reserve
a whoosh/zoom/glitch for a real scene or energy change. Overusing transitions is
the #1 amateur tell.

**Clip effect (camera shake, glitch, RGB split, zoom burst, VHS):** top tab
**Effects** → drag onto its own track above the clip → trim its duration to the
exact moment of emphasis. Sparingly, on beats/hits only.

---

## Auto reframe (repurpose aspect ratio)

To turn a 16:9 edit into 9:16 (or vice-versa): **Edit/menu → Auto reframe** →
choose the target ratio → CapCut tracks the subject and re-centers per shot.
Review each shot; nudge framing with keyframed Position where tracking drifts.

---

## Export

Click **Export** (top-right, or `Ctrl+E`) → set **resolution / frame rate /
format / bitrate** (see the table in `SKILL.md` §5) → set the **output
folder/filename** → Export. If any **Pro** asset was used on a free plan, warn
the user about the watermark first. After export, confirm the file exists at the
agreed path and report its location.
