---
name: gui-app-automation
description: Operate a desktop application precisely by acting on named controls,
  not guessed pixels.
when_to_use: click, type into, fill in, open the app, navigate the window, use the
  settings, press
tools: focus_window, observe, click, type, press, key_sequence, scroll, wait
version: '1.0'
created: '2026-09-18T14:52:34'
updated: '2026-09-18T14:52:34'
---

# Driving an app precisely

## The one rule that matters
Act on **element ids from the current observation**, never on coordinates you
worked out in your head. A plausible-looking pixel is a coin flip; an element id
resolves to that control's real centre. If you do not have a fresh observation,
get one first.

## Steps
1. `focus_window` the target app. Reading and clicking only work on the window in
   the foreground, so this is not optional when the app is behind something.
2. `observe` and read the real labels. Do not assume a control's wording from
   memory of a similar app - "Save as", "Save As" and "Save a copy" are three
   different controls.
3. Act, one step at a time: `click` a control, `type` into a field, `press` a
   hotkey. After anything that can change the screen (a click, a scroll, a new
   window, a dialog), `observe` again - the ids you had are now stale.
4. Prefer the keyboard when a shortcut exists. A hotkey is one action and cannot
   land on the wrong control.

## Verify, then claim
After acting, confirm the effect from a fresh observation before telling the user
it worked. "Clicked Save" is a claim about the button; "the title bar no longer
shows an asterisk" is evidence. If the observation contradicts the claim, say
what you saw instead.

## When a control is not found
- The name may be worded differently: re-read the labels and try the exact text
  you see.
- The window may not be focused, or a modal dialog may be on top of it.
- If the tool offers several candidates, **read them to the user and let them
  choose**. Never pick one of several plausible controls and hope - that is the
  single fastest way to do something destructive by accident.
- Do not repeat the same failing action with the same arguments. A retry that
  changes nothing tells you nothing.
