# CapCut Desktop — Shortcuts & Automation Cheat Sheet

Shortcuts are your **most reliable automation lever** — they don't depend on
finding a button in a screenshot. **Timeline shortcuts only fire when the
timeline has focus:** click once in an empty timeline area first. If a binding
behaves differently in this build, open the in-app **keyboard-shortcut list**
(Help / menu) and confirm, then continue (self-heal — don't repeat a failing
key).

## Core editing
| Action | Windows | Mac |
|---|---|---|
| Play / pause | `Space` | `Space` |
| Split clip at playhead | `Ctrl+B` | `Cmd+B` |
| Delete selected clip | `Delete` / `Backspace` | `Delete` |
| Trim (delete) left of playhead | `Q` | `Q` |
| Trim (delete) right of playhead | `W` | `W` |
| Undo / Redo | `Ctrl+Z` / `Ctrl+Shift+Z` | `Cmd+Z` / `Cmd+Shift+Z` |
| Copy / Paste | `Ctrl+C` / `Ctrl+V` | `Cmd+C` / `Cmd+V` |
| Select all | `Ctrl+A` | `Cmd+A` |
| Group / Ungroup | `Ctrl+G` / `Ctrl+Shift+G` | `Cmd+G` |
| Save project | `Ctrl+S` | `Cmd+S` |
| Export | `Ctrl+E` | `Cmd+E` |

## Playhead & navigation
| Action | Key |
|---|---|
| Nudge one frame back / forward | `←` / `→` |
| Previous / next clip edge | `↑` / `↓` (or `Home`/`End` for start/end) |
| Shuttle: rewind / pause / forward | `J` / `K` / `L` (tap `J`/`L` again to speed up) |
| Zoom timeline in / out | `Ctrl+ =` / `Ctrl+ -` (or the timeline zoom slider) |
| Fit timeline to window | `Ctrl+Shift+ =` (or drag zoom slider fully left) |

## Markers & keyframes
| Action | Key |
|---|---|
| Add marker at playhead | `M` |
| Add keyframe | the diamond ◆ icon on the property in the Inspector (no universal hotkey — click it) |

> Exact bindings vary slightly by version/OS. The ones above are the stable,
> widely-documented set. When in doubt, drive the action from the on-screen
> button instead and verify by screenshot.

## Automation playbook (how you actually execute these)

**Split a clip at a point:**
1. Click empty timeline area (give it focus).
2. Position playhead: click the ruler at the spot, then `←`/`→` to fine-tune.
3. `Ctrl+B`. Screenshot → confirm two clips now exist at that seam.

**Trim dead air (talking-head cleanup):**
1. Playhead to start of the pause → `Ctrl+B`.
2. Playhead to end of the pause → `Ctrl+B`.
3. Select the middle segment → `Delete`. The gap closes (ripple). Screenshot to
   confirm no black gap remains; if a gap stays, select it and delete it.

**Move / reorder a clip:** drag its middle to the new slot. Zoom in first for
precision. Screenshot to confirm order.

**Trim an edge:** drag the clip's left/right handle inward/outward. The Player
shows the new in/out frame as you drag.

**Open a tool tab** (Text, Effects, Transitions, Filters, Adjustment, Audio,
Captions): click the tab in the **top toolbar**, then the asset appears in the
top-left panel — drag it onto the timeline (or onto a clip for transitions).

**Adjust a selected clip's properties:** select the clip → the **Inspector**
opens on the right → click the sub-tab (Basic / Speed / Animation / Adjustment /
Audio / Video·Cutout) → change the value → screenshot to confirm.

**Golden rule:** after any action, screenshot and verify the state changed before
issuing the next action.
