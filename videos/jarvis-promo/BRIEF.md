---
workflow: product-launch-video
flow: automation
storyboard: yes
message: "A tiny local model can drive your whole desktop, because it never has to guess a pixel."
destination: youtube
aspect: 1920x1080
language: en
length: 45s
angle: mechanism-as-hook
audience: developers and technically-minded desktop users who are sceptical of AI-agent claims
narration: yes
---

## Intent

A promo for **Jarvis**, a local, agentic Windows desktop assistant. The locked
concept is **"Set of Marks"** — the video is narrated from Jarvis's own point of
view: we watch the screen the way it watches the screen. Every frame is a real
desktop wearing numbered Set-of-Marks badges over genuine UI Automation controls,
with the cursor arriving at an exact pixel centre.

The angle is **mechanism-as-hook**: the mechanism is the hook, not a slide about
the mechanism. The one thing the video must communicate — "a tiny local model can
drive your whole desktop, because it never has to guess a pixel" — is the same
sentence as the product's architecture. Rooted in the README's own positioning:
grounding is done with classical tools (UI Automation + OCR), the model only
answers *"click element 0"*, which is what makes accurate desktop control possible
on a modest machine built for an RTX 2050 / 4 GB VRAM / 8 GB RAM.

Sell, don't tour: this is a promo with a thesis, not a feature walkthrough.
Tone: precise, terminal-flavoured, confident, zero mysticism.

Opening hook (the accepted pitch's): a cursor lands dead-centre on a button nobody
could have guessed — because element 41 said so.

## Assets

- The user's own material is **the real Jarvis app, captured on this machine** —
  the HUD, the browser UI, the Set-of-Marks overlay, and the UI Automation element
  list. Real product screens are the video's primary assets.

## Customizations

- **Real app capture** — run the app and screenshot the real HUD / browser UI /
  numbered overlay; these become the featured `asset_candidates` rather than
  invented graphics.
- **Motion is reconstructed, not recorded.** Capture yields static plates; the
  cursor travel and badge landings are animated in HTML over those plates at
  measured positions. Every screen beat needs a measured overlay position.
- **Captions** built from the narration.
- **Design spec: workflow's choice.** The user left the look to the workflow's
  Step 2 preset decision; the brief names no preset.
- The proof beat ("this model is bad at pixels, so we never ask it for one —
  element 14, Button, Send") is available from the pitch round's tail sampling.

## Notes

- **No product site exists.** Jarvis is a local desktop app, so website capture
  does not apply: this runs the **no-capture path**, with the repo's README as the
  source of positioning and the real app screenshots as the asset inventory.
- **HeyGen credential is valid but unusable.** `HEYGEN_API_KEY` authenticates as
  satvik.gamerz309@gmail.com (wallet 0 USD); `POST /v3/voices/speech` returns
  `HTTP 402 insufficient_credit`. Voice and music therefore run on **local engines
  (Kokoro narration · MusicGen music)**. Re-test if credits are added.
- **Aspect is 1920x1080** because the product controls a desktop — the widescreen
  desktop is the product's native frame, and it is the strongest surface for
  showing the perceive→think→act loop.
- **Legibility constraint:** a 16:9 embed is often watched at phone width, so the
  small monospace badge labels must stay readable when downscaled.
- Watch for Pro-badged presets/effects/filters: a free plan watermarks the export.
