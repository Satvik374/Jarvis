# Vendored browser assets

Served verbatim by `jarvis/browser.py` (see the `_STATIC` map), so the browser UI
stays offline-first: no CDN, no import map, and no runtime bundler.

## `three.min.js`

Pre-existing dependency of the HUD/visualisations.

## `fish-agent-client.esm.js`

`@fishaudio/agent-client` v0.3.0 for Fish Audio Agents - voice sessions,
transcripts, and client tools - bundled with its runtime dependencies
(`livekit-client`, `@fishaudio/agent-protocol`) into one self-contained ES
module. The published package is *not* self-contained (it imports those two as
bare specifiers), which is why the bundle exists.

`app.js` loads it lazily with `await import("/vendor/fish-agent-client.esm.js")`
and treats a missing file as "Fish voice is not installed" - the browser voice
mode then falls back to the `live_voice.ws_url` OpenAI-Realtime path.

Rebuild after bumping the SDK version (run it in a scratch directory so the
project keeps no `node_modules`):

```bash
mkdir -p /tmp/fish-sdk && cd /tmp/fish-sdk
npm init -y >/dev/null
npm i --no-audit --no-fund @fishaudio/agent-client@0.3.0 esbuild
printf 'export * from "@fishaudio/agent-client";\n' > entry.js
npx esbuild entry.js --bundle --format=esm --minify --target=es2020 \
  --outfile=fish-agent-client.esm.js
cp fish-agent-client.esm.js <repo>/jarvis/browser_ui/vendor/
cp node_modules/@fishaudio/agent-client/LICENSE \
   <repo>/jarvis/browser_ui/vendor/fish-agent-client.LICENSE
```

Licences: `@fishaudio/agent-client` and `@fishaudio/agent-protocol` are MIT
(`fish-agent-client.LICENSE`); `livekit-client` is Apache-2.0.
