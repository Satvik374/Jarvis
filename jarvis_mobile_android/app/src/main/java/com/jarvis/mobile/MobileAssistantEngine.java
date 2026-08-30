package com.jarvis.mobile;

import android.app.KeyguardManager;
import android.content.Context;

import org.json.JSONArray;

import java.util.Locale;

/** Standalone Mobile Jarvis cloud conversation and tightly-scoped phone actions. */
final class MobileAssistantEngine {
    private static final int MAX_AUTOMATION_STEPS = 8;

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
        RelayClient relay = new RelayClient(pairing.endpoint);
        if (!allowActions) {
            RelayClient.AssistantReply response = relay.askMobileAssistant(pairing, prompt, history);
            String actionResult = response.command.trim().isEmpty() ? ""
                    : "Turn on phone control to let Jarvis carry out this request.";
            return withSpeech(response.reply, actionResult, false, response);
        }

        if (isLocked(context)) {
            RelayClient.AssistantReply response = relay.askMobileAssistant(pairing, prompt, history);
            return withSpeech(response.reply,
                    "Your phone is locked. Unlock it before Jarvis can control the screen.",
                    false, response);
        }

        MobileCommandExecutor commands = new MobileCommandExecutor(context);
        StringBuilder actionLog = new StringBuilder();
        String previousAction = "";
        boolean actionRan = false;
        boolean completed = false;
        RelayClient.AssistantReply response = null;
        for (int step = 1; step <= MAX_AUTOMATION_STEPS; step++) {
            MobileCommandExecutor.AssistantObservation observation = commands.observeForAssistant(
                    previousAction, step, MAX_AUTOMATION_STEPS);
            response = relay.askMobileAssistant(pairing, prompt, history,
                    observation.context, observation.screenImage);
            String command = response.command.trim();
            if (command.isEmpty()) {
                completed = true;
                break;
            }

            if (!isAllowedCommand(command)) {
                appendAction(actionLog, step, command,
                        "Jarvis proposed an unsupported phone action, so it was not run.");
                break;
            }
            MobileCommandExecutor.Result executed = commands.execute(command);
            appendAction(actionLog, step, command, executed.message);
            previousAction = command + " → " + executed.message;
            actionRan |= executed.ok;
            if (!executed.ok) break;
            try {
                // Give Android time to publish the window resulting from a
                // navigation or gesture before inspecting the next step.
                Thread.sleep(450L);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                break;
            }
        }

        if (response == null) {
            throw new IllegalStateException("Mobile Jarvis did not return a control plan.");
        }
        if (actionLog.length() == 0 && !response.command.trim().isEmpty()) {
            actionLog.append("Jarvis stopped before a phone action could be completed.");
        } else if (actionLog.length() > 0 && !completed) {
            actionLog.append("\nControl stopped before the task was complete.");
        }
        return withSpeech(response.reply, actionLog.toString(), actionRan, response);
    }

    private static Result withSpeech(String reply, String actionResult, boolean actionRan,
                                     RelayClient.AssistantReply response) {
        long speechDurationMs = 0L;
        if (response.pcmAudio.length > 0) {
            speechDurationMs = (response.pcmAudio.length * 1_000L)
                    / Math.max(1, response.sampleRate * 2L);
            PcmAudioPlayer.playAsync(response.pcmAudio, response.sampleRate);
        }
        return new Result(reply, actionResult, actionRan, speechDurationMs);
    }

    private static void appendAction(StringBuilder log, int step, String command, String result) {
        if (log.length() > 0) log.append('\n');
        log.append("Step ").append(step).append(": ").append(command)
                .append(" — ").append(result);
    }

    private static boolean isLocked(Context context) {
        KeyguardManager keyguard = context.getSystemService(KeyguardManager.class);
        return keyguard != null && keyguard.isKeyguardLocked();
    }

    /** Only commands implemented by MobileCommandExecutor may reach Android. */
    static boolean isAllowedCommand(String command) {
        String lower = command.trim().toLowerCase(Locale.ROOT);
        return lower.startsWith("open ")
                || lower.startsWith("launch ")
                || lower.equals("screenshot") || lower.equals("inspect")
                || lower.equals("inspect screen") || lower.equals("capabilities")
                || lower.matches("(?:tap|click)\\s+element\\s+\\d+")
                || lower.matches("long[- ]press\\s+element\\s+\\d+")
                || lower.matches("type\\s+element\\s+\\d+\\s+.+")
                || lower.matches("scroll\\s+element\\s+\\d+\\s+(forward|backward)")
                || lower.matches("swipe\\s+element\\s+\\d+\\s+(up|down|left|right)")
                || lower.matches("(?:tap|click)\\s+-?\\d+\\s+-?\\d+")
                || lower.matches("swipe\\s+-?\\d+\\s+-?\\d+\\s+-?\\d+\\s+-?\\d+")
                || lower.matches("type\\s+.+")
                || lower.equals("back")
                || lower.equals("home");
    }
}
