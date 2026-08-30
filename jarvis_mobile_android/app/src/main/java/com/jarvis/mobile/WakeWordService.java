package com.jarvis.mobile;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;

import androidx.core.content.ContextCompat;
import androidx.core.app.NotificationCompat;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Owner-enabled foreground microphone service for the spoken wake word
 * "Jarvis". It can continue while the display is off as long as Android keeps
 * this foreground service alive; a foreground notification always makes that
 * active microphone use visible and lets the owner stop it.
 */
public final class WakeWordService extends Service {
    static final String ACTION_START_WAKE = "com.jarvis.mobile.START_WAKE";
    static final String ACTION_START_CAPTURE = "com.jarvis.mobile.START_CAPTURE";
    static final String ACTION_STOP_CAPTURE = "com.jarvis.mobile.STOP_CAPTURE";
    static final String ACTION_STOP = "com.jarvis.mobile.STOP_WAKE";
    static final String ACTION_STATE = "com.jarvis.mobile.VOICE_STATE";
    static final String EXTRA_STATE = "state";
    static final String EXTRA_TEXT = "text";
    static final String STATE_WAKE = "wake";
    static final String STATE_CAPTURING = "capturing";
    static final String STATE_PROCESSING = "processing";
    static final String STATE_REPLY = "reply";
    static final String STATE_ERROR = "error";

    private static final int NOTIFICATION_ID = 42;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final JSONArray history = new JSONArray();
    private SpeechRecognizer recognizer;
    private ExecutorService cloudExecutor;
    private volatile boolean running;
    private boolean waitingForWake;
    private boolean capturingCommand;
    private boolean switchingRecognition;

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? ACTION_START_WAKE : intent.getAction();
        if (ACTION_STOP.equals(action)) {
            stopListeningService();
            return START_NOT_STICKY;
        }
        if (ACTION_STOP_CAPTURE.equals(action)) {
            main.post(this::completeCapture);
            return START_STICKY;
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            publish(STATE_ERROR, "Microphone permission is required before Jarvis can listen.");
            stopSelf();
            return START_NOT_STICKY;
        }
        if (!running) {
            running = true;
            cloudExecutor = Executors.newSingleThreadExecutor();
            startAsForeground("Listening for the wake word ‘Jarvis’");
        }
        main.post(() -> ensureRecognizer(ACTION_START_CAPTURE.equals(action)));
        return START_STICKY;
    }

    @Override public IBinder onBind(Intent intent) { return null; }

    @Override public void onDestroy() {
        stopListeningService();
        super.onDestroy();
    }

    private void ensureRecognizer(boolean captureNow) {
        if (!running) return;
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            publish(STATE_ERROR, "This phone has no available speech-recognition service.");
            return;
        }
        if (recognizer == null) {
            recognizer = SpeechRecognizer.createSpeechRecognizer(this);
            recognizer.setRecognitionListener(new Listener());
        }
        if (captureNow) beginCommandCapture(); else startWakeListening();
    }

    private void startWakeListening() {
        if (!running || recognizer == null) return;
        waitingForWake = true;
        capturingCommand = false;
        startRecognition();
        startAsForeground("Listening for the wake word ‘Jarvis’");
        publish(STATE_WAKE, "Say ‘Jarvis’ to speak a command.");
    }

    private void beginCommandCapture() {
        if (!running || recognizer == null) return;
        waitingForWake = false;
        capturingCommand = true;
        switchingRecognition = true;
        try { recognizer.cancel(); } catch (Exception ignored) { }
        startAsForeground("Jarvis is listening for your command");
        publish(STATE_CAPTURING, "Listening… tap the red stop button when you are finished.");
        // Cancelling the wake recognizer first prevents its partial result from
        // being treated as the task. A short hand-off keeps OEM recognizers stable.
        main.postDelayed(() -> {
            switchingRecognition = false;
            startRecognition();
        }, 250L);
    }

    private void startRecognition() {
        if (!running || recognizer == null) return;
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                        RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3)
                .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1_200L)
                .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 900L);
        try {
            recognizer.startListening(intent);
        } catch (Exception e) {
            scheduleWakeRetry("Speech recognition could not start: " + safeMessage(e));
        }
    }

    private void completeCapture() {
        if (!capturingCommand || recognizer == null) return;
        try { recognizer.stopListening(); } catch (Exception ignored) { }
    }

    private void receiveResults(Bundle results) {
        ArrayList<String> options = results == null ? null
                : results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        String transcript = options == null || options.isEmpty() ? "" : options.get(0).trim();
        if (waitingForWake) {
            if (containsWakeWord(transcript)) beginCommandCapture();
            else scheduleWakeRetry("");
            return;
        }
        if (!capturingCommand) return;
        capturingCommand = false;
        if (transcript.isBlank()) {
            scheduleWakeRetry("I did not catch a command.");
            return;
        }
        submitCommand(transcript);
    }

    private void submitCommand(String transcript) {
        startAsForeground("Jarvis is working on your request");
        publish(STATE_PROCESSING, transcript);
        cloudExecutor.execute(() -> {
            try {
                boolean allowActions = getSharedPreferences("jarvis_mobile_settings", MODE_PRIVATE)
                        .getBoolean("assistant_actions", false);
                MobileAssistantEngine.Result answer = MobileAssistantEngine.ask(
                        getApplicationContext(), transcript, history, allowActions);
                synchronized (history) {
                    history.put(new JSONObject().put("role", "user").put("content", transcript));
                    history.put(new JSONObject().put("role", "assistant").put("content", answer.reply));
                    while (history.length() > 12) history.remove(0);
                }
                String detail = answer.actionResult.isBlank() ? answer.reply
                        : answer.reply + "\n\n" + answer.actionResult;
                publish(STATE_REPLY, detail);
                long restartDelay = Math.max(600L, answer.speechDurationMs + 300L);
                main.postDelayed(this::startWakeListening, restartDelay);
            } catch (Exception e) {
                publish(STATE_ERROR, "Mobile Jarvis could not complete that request: " + safeMessage(e));
                main.postDelayed(this::startWakeListening, 600L);
            }
        });
    }

    private void scheduleWakeRetry(String message) {
        if (!running) return;
        if (!message.isBlank()) publish(STATE_WAKE, message);
        main.removeCallbacksAndMessages(null);
        main.postDelayed(this::startWakeListening, 900L);
    }

    private void stopListeningService() {
        running = false;
        waitingForWake = false;
        capturingCommand = false;
        main.removeCallbacksAndMessages(null);
        if (recognizer != null) {
            try { recognizer.cancel(); recognizer.destroy(); } catch (Exception ignored) { }
            recognizer = null;
        }
        PcmAudioPlayer.stop();
        if (cloudExecutor != null) cloudExecutor.shutdownNow();
        try { stopForeground(STOP_FOREGROUND_REMOVE); } catch (Exception ignored) { }
        stopSelf();
    }

    private void startAsForeground(String text) {
        String channel = "jarvis_wake_listener";
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel(channel,
                "Jarvis wake listener", NotificationManager.IMPORTANCE_LOW));
        Intent stop = new Intent(this, WakeWordService.class).setAction(ACTION_STOP);
        PendingIntent stopIntent = PendingIntent.getService(this, 3, stop,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent openIntent = PendingIntent.getActivity(this, 4, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification notification = new NotificationCompat.Builder(this, channel)
                .setSmallIcon(R.drawable.ic_jarvis)
                .setContentTitle("Jarvis Mobile is ready")
                .setContentText(text)
                .setContentIntent(openIntent)
                .addAction(0, "Stop listening", stopIntent)
                .setOngoing(true)
                .build();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    private void publish(String state, String text) {
        Intent event = new Intent(ACTION_STATE).setPackage(getPackageName())
                .putExtra(EXTRA_STATE, state).putExtra(EXTRA_TEXT, text);
        sendBroadcast(event);
    }

    private static boolean containsWakeWord(String text) {
        return text != null && text.toLowerCase(Locale.ROOT).matches(".*\\bjarvis\\b.*");
    }

    private static String safeMessage(Exception exception) {
        return exception.getMessage() == null ? exception.getClass().getSimpleName() : exception.getMessage();
    }

    private final class Listener implements RecognitionListener {
        @Override public void onReadyForSpeech(Bundle params) { }
        @Override public void onBeginningOfSpeech() { }
        @Override public void onRmsChanged(float rmsdB) { }
        @Override public void onBufferReceived(byte[] buffer) { }
        @Override public void onEndOfSpeech() { }
        @Override public void onError(int error) {
            if (!running) return;
            if (switchingRecognition) return;
            if (capturingCommand) {
                capturingCommand = false;
                scheduleWakeRetry("Voice capture ended before a command was recognized.");
            } else {
                scheduleWakeRetry("");
            }
        }
        @Override public void onResults(Bundle results) { receiveResults(results); }
        @Override public void onPartialResults(Bundle partialResults) {
            if (!waitingForWake) return;
            ArrayList<String> partial = partialResults == null ? null
                    : partialResults.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
            if (partial != null && !partial.isEmpty() && containsWakeWord(partial.get(0))) {
                beginCommandCapture();
            }
        }
        @Override public void onEvent(int eventType, Bundle params) { }
    }
}
