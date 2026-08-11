# Jarvis Mobile for Android

An Android companion agent for this repository's Jarvis Remote relay. It uses
the same `jarvis-remote-v1` protocol as `jarvis/remote.py`: X25519 key exchange,
Ed25519 signatures, ChaCha20-Poly1305 envelopes, and the existing relay API.
Private phone keys are kept in Android Keystore-backed encrypted storage.

## Pair the phone to the computer

1. Build and install the APK, then open **Jarvis Mobile**.
2. On the computer, configure the same public relay URL and run:

   ```powershell
   python run.py --remote-pair "My Phone"
   ```

3. Copy the printed eight-character code into the Android app, along with the
   same relay URL. Tap **Pair this phone**.
4. Compare the identical fingerprint on both devices, then trust it on the
   computer and the phone. On the computer this is:

   ```powershell
   python run.py --remote-trust "My Phone" FINGERPRINT
   ```

5. In Android settings, enable **Jarvis Mobile control** under Accessibility,
   then tap **Start phone agent** in the app. The persistent notification means
   it is actively connected.

   On Android 13 or newer, a manually installed APK may show **Restricted
   setting** or warn that Accessibility is dangerous. Android intentionally
   blocks sideloaded apps from enabling this setting automatically. In Jarvis
   Mobile, tap **1. Open App info**, open the three-dot menu, choose **Allow
   restricted settings**, return to Jarvis Mobile, and tap **2. Enable Android
   accessibility control**. Only do this for an APK you built or otherwise
   trust.

If an old phone pairing returns `pairing not found`, list and remove the stale
local record before pairing again:

```powershell
python run.py --list-devices
python run.py --remove pair "My Phone"
```

## First mobile command set

This first build executes concise, auditable mobile commands received from the
computer. Send one through the existing remote command path, for example:

```powershell
python run.py --remote-send "My Phone" "mobile: open com.android.settings"
python run.py --remote-send "My Phone" "mobile: open YouTube"
python run.py --remote-send "My Phone" "mobile: open https://youtube.com"
python run.py --remote-send "My Phone" "mobile: open Chrome to https://youtube.com"
python run.py --remote-send "My Phone" "mobile: screenshot"
python run.py --remote-send "My Phone" "mobile: tap element 7"
python run.py --remote-send "My Phone" "mobile: type element 4 Hello from Jarvis"
python run.py --remote-send "My Phone" "mobile: scroll element 12 forward"
python run.py --remote-send "My Phone" "mobile: swipe element 12 up"
python run.py --remote-send "My Phone" "mobile: capabilities"
```

Supported commands are `open <app name, package, or URL>`, `screenshot`,
`tap element <id>`, `long press element <id>`, `type element <id> <text>`,
`scroll element <id> <forward|backward>`, `swipe element <id>
<up|down|left|right>`, `capabilities`, `back`, and `home`. Raw `tap <x> <y>`,
`swipe <x1> <y1> <x2> <y2>`, and focused-field `type <text>` remain available
only as fallbacks for apps that expose no accessibility elements. Touch, swipe,
typing, back, home, and screenshots require the phone owner to have explicitly
enabled the Android Accessibility Service; screenshots also require Android 11
or newer. Opening apps and web addresses does not require accessibility. Known
apps such as YouTube, Chrome, Gmail, Maps, and Settings are resolved by name,
and other installed launchers are matched by their visible label. If YouTube
is not installed, `open YouTube` falls back to youtube.com in the default
browser. The phone agent runs only after encrypted pairing and
matching-fingerprint trust.

Each screenshot now includes a desktop-style **MOBILE UI ELEMENTS** menu built
from Android's live Accessibility tree. Every visible control has an ID, label,
role, native-screen bounds, exact center, and supported actions. Jarvis clicks
the selected Android node with `ACTION_CLICK` (using its exact center only when
the app rejects semantic clicking), sets text directly on editable nodes, and
uses the chosen container's native bounds for scrolling and swiping. Element
IDs are tied to the current package, re-matched against the live tree, expire
after two minutes, and are invalidated after every screen-changing action.
Always request a new screenshot before the next interaction.

Screenshot results are scaled and compressed to a small JPEG before being sent
inside the existing signed, encrypted remote envelope. The computer validates
the media type and size, saves it below the configured remote state directory,
and attaches it to Jarvis's next vision turn. The phone also reports its exact
command capabilities and current accessibility/screenshot readiness; Jarvis
persists that signed contract and uses it to avoid unsupported remote tools.

## Build

With Android SDK 35 installed:

```powershell
.\gradlew.bat assembleDebug
```

The debug APK is created at:

```text
app\build\outputs\apk\debug\app-debug.apk
```
