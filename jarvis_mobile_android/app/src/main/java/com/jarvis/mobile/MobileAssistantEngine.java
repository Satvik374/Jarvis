package com.jarvis.mobile;

import android.app.KeyguardManager;
import android.content.Context;

import org.json.JSONArray;

import java.util.Locale;

/** Standalone Mobile Jarvis cloud conversation and tightly-scoped phone actions. */
final class MobileAssistantEngine {
    static final class Result {
        final String reply;
        final String actionResult;
        final boolean actionRan;
        final long speechDurationMs;

        Result(String reply, String actionResult, boolean actionRan, long speechDurationMs) {
            this.reply = reply == null ? "" : reply;
            this.actionResult = actionResult == null ? "" : actionResult;
            this.actionRan = actionRan;
            this.speechDurationMs = Math.max(0L, speechDurationMs);
        }
    }

    private MobileAssistantEngine() { }

    static Result ask(Context context, String prompt, JSONArray history, boolean allowActions) throws Exception {
        PairingRecord pairing = new PairingStore(context).load();
        if (pairing == null || !pairing.trusted) {
            throw new IllegalStateException("Pair and trust this phone once before using standalone Mobile Jarvis.");
        }
        RelayClient.AssistantReply response = new RelayClient(pairing.endpoint)
                .askMobileAssistant(pairing, prompt, history);

        String actionResult = "";
        boolean actionRan = false;
        String command = response.command.trim();
        if (!command.isEmpty()) {
            if (!allowActions) {
                actionResult = "Jarvis suggested a phone action, but direct actions are turned off in this tab.";
            } else if (isLocked(context)) {
                // Android deliberately prevents an app from bypassing the
                // secure lock screen. Do not attempt gestures while locked.
                actionResult = "Your phone is locked. Unlock it before Jarvis can control the screen.";
            } else if (!isAllowedCommand(command)) {
                actionResult = "Jarvis proposed an unsupported mobile action, so it was not run.";
            } else {
                MobileCommandExecutor.Result executed = new MobileCommandExecutor(context).execute(command);
                actionResult = executed.message;
                actionRan = executed.ok;
            }
        }

        long speechDurationMs = 0L;
        if (response.pcmAudio.length > 0) {
            speechDurationMs = (response.pcmAudio.length * 1_000L)
                    / Math.max(1, response.sampleRate * 2L);
            PcmAudioPlayer.playAsync(response.pcmAudio, response.sampleRate);
        }
        return new Result(response.reply, actionResult, actionRan, speechDurationMs);
    }

    private static boolean isLocked(Context context) {
        KeyguardManager keyguard = context.getSystemService(KeyguardManager.class);
        return keyguard != null && keyguard.isKeyguardLocked();
    }

    /** Never allow raw coordinate gestures or arbitrary intents from an LLM. */
    static boolean isAllowedCommand(String command) {
        String lower = command.trim().toLowerCase(Locale.ROOT);
        return lower.startsWith("open ")
                || lower.equals("screenshot")
                || lower.matches("tap\\s+element\\s+\\d+")
                || lower.matches("long[- ]press\\s+element\\s+\\d+")
                || lower.matches("type\\s+element\\s+\\d+\\s+.+")
                || lower.matches("scroll\\s+element\\s+\\d+\\s+(forward|backward)")
                || lower.matches("swipe\\s+element\\s+\\d+\\s+(up|down|left|right)")
                || lower.equals("back")
                || lower.equals("home");
    }
}
