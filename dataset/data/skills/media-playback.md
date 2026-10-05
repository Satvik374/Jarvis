---
name: media-playback
description: Play, pause and control media, using the system media controls rather
  than hunting for pixels on a video.
when_to_use: play, pause, music, song, video, volume, mute, next track, media, spotify,
  youtube
tools: media, media_intel, open_app, open_url, click, observe, press, notify
version: '1.0'
created: '2026-09-26T21:39:29'
updated: '2026-09-26T21:39:29'
---

# Controlling media

## Steps
1. **Find what is playing first.** `media_intel` names the current session and its
   state, so "pause it" acts on the thing actually playing instead of guessing an
   app.
2. **Prefer the media action** over clicking a player window: `media` sends the
   system play/pause/next/previous/volume command and works even when the player
   is minimised or behind another window.
3. **To start something new,** `open_url` the page (a video or a track) or
   `open_app` the player, `wait_for` the window, then use the keyboard (space for
   play/pause) rather than aiming at a small control.
4. **Confirm the state changed** - the media session's state is the evidence, not
   the fact that you pressed a key.
5. **Report** what is playing and the volume when the user asked for a change.

## Rules
- Do not raise the volume sharply; a sudden blare is worse than a wrong track.
- Do not add to a playlist, buy, or download media the user did not request.
