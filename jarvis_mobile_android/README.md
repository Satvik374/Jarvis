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

## First mobile command set

This first build executes concise, auditable mobile commands received from the
computer. Send one through the existing remote command path, for example:

```powershell
python run.py --remote-send "My Phone" "mobile: open com.android.settings"
python run.py --remote-send "My Phone" "mobile: tap 500 700"
python run.py --remote-send "My Phone" "mobile: type Hello from Jarvis"
```

Supported commands are `open <package>`, `tap <x> <y>`,
`swipe <x1> <y1> <x2> <y2>`, `type <text>`, `back`, and `home`. Touch, swipe,
typing, back, and home require the phone owner to have explicitly enabled the
Android Accessibility Service. The phone agent runs only after encrypted
pairing and matching-fingerprint trust.

## Build

With Android SDK 35 installed:

```powershell
.\gradlew.bat assembleDebug
```

The debug APK is created at:

```text
app\build\outputs\apk\debug\app-debug.apk
```
