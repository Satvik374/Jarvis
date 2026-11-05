package com.jarvis.mobile;

import android.Manifest;
import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.AlertDialog;
import android.content.BroadcastReceiver;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.view.View;
import android.view.accessibility.AccessibilityManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;

import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.annotation.Nullable;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.ContextCompat;

import com.google.android.material.tabs.TabLayout;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Pair a phone once, then use its cloud-backed Jarvis directly from the Mobile Jarvis tab. */
public final class MainActivity extends AppCompatActivity {
    private static final String SETTINGS = "jarvis_mobile_settings";
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final JSONArray chatHistory = new JSONArray();

    private EditText relayUrl;
    private EditText deviceName;
    private EditText pairingCode;
    private EditText fingerprint;
    private TextView status;
    private TextView accessibilityStatus;
    private Button accessibilityButton;
    private Button restrictedSettingsButton;
    private View setupPane;
    private View assistantPane;
    private TextView assistantStatus;
    private TextView assistantTranscript;
    private ScrollView chatScroll;
    private EditText assistantInput;
    private Switch assistantActions;
    private VoiceWaveView voiceWave;
    private Button stopVoiceButton;
    private boolean voiceReceiverRegistered;
    private String pendingVoiceAction = "";

    private final ActivityResultLauncher<String> notificationPermission = registerForActivityResult(
            new ActivityResultContracts.RequestPermission(), ignored -> { });
    private final ActivityResultLauncher<String> microphonePermission = registerForActivityResult(
            new ActivityResultContracts.RequestPermission(), granted -> {
                if (granted && !pendingVoiceAction.isBlank()) {
                    startVoiceService(pendingVoiceAction);
                } else if (!granted) {
                    setAssistantStatus("Microphone permission was not granted.");
                }
                pendingVoiceAction = "";
            });

    private final BroadcastReceiver voiceReceiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            String state = intent.getStringExtra(WakeWordService.EXTRA_STATE);
            String text = intent.getStringExtra(WakeWordService.EXTRA_TEXT);
            handleVoiceState(state == null ? "" : state, text == null ? "" : text);
        }
    };

    @Override protected void onCreate(@Nullable Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        bindViews();
        setupTabs();
        setupActions();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS);
        }
        refreshStatus();
        refreshAccessibilityStatus();
    }

    @Override protected void onStart() {
        super.onStart();
        if (!voiceReceiverRegistered) {
            ContextCompat.registerReceiver(this, voiceReceiver,
                    new IntentFilter(WakeWordService.ACTION_STATE), ContextCompat.RECEIVER_NOT_EXPORTED);
            voiceReceiverRegistered = true;
        }
    }

    @Override protected void onStop() {
        if (voiceReceiverRegistered) {
            unregisterReceiver(voiceReceiver);
            voiceReceiverRegistered = false;
        }
        super.onStop();
    }

    @Override protected void onResume() {
        super.onResume();
        refreshAccessibilityStatus();
    }

    private void bindViews() {
        relayUrl = findViewById(R.id.relayUrlInput);
        deviceName = findViewById(R.id.deviceNameInput);
        pairingCode = findViewById(R.id.pairingCodeInput);
        fingerprint = findViewById(R.id.fingerprintInput);
        status = findViewById(R.id.statusText);
        accessibilityStatus = findViewById(R.id.accessibilityStatusText);
        accessibilityButton = findViewById(R.id.accessibilityButton);
        restrictedSettingsButton = findViewById(R.id.restrictedSettingsButton);
        setupPane = findViewById(R.id.setupPane);
        assistantPane = findViewById(R.id.assistantPane);
        assistantStatus = findViewById(R.id.assistantStatusText);
        assistantTranscript = findViewById(R.id.assistantChatTranscript);
        chatScroll = findViewById(R.id.chatScroll);
        assistantInput = findViewById(R.id.assistantInput);
        assistantActions = findViewById(R.id.assistantActionsSwitch);
        voiceWave = findViewById(R.id.voiceWave);
        stopVoiceButton = findViewById(R.id.stopVoiceButton);
        deviceName.setText(Build.MANUFACTURER + " " + Build.MODEL);
        SharedPreferences settings = getSharedPreferences(SETTINGS, MODE_PRIVATE);
        assistantActions.setChecked(settings.getBoolean("assistant_actions", true));
    }

    private void setupTabs() {
        TabLayout tabs = findViewById(R.id.mainTabs);
        tabs.addTab(tabs.newTab().setText("Mobile Jarvis"));
        tabs.addTab(tabs.newTab().setText("Phone setup"));
        tabs.selectTab(tabs.getTabAt(0));
        showAssistantPane(true);
        tabs.addOnTabSelectedListener(new TabLayout.OnTabSelectedListener() {
            @Override public void onTabSelected(TabLayout.Tab tab) { showAssistantPane(tab.getPosition() == 0); }
            @Override public void onTabUnselected(TabLayout.Tab tab) { }
            @Override public void onTabReselected(TabLayout.Tab tab) { }
        });
    }

    private void setupActions() {
        findViewById(R.id.pairButton).setOnClickListener(view -> pair());
        findViewById(R.id.trustButton).setOnClickListener(view -> trust());
        restrictedSettingsButton.setOnClickListener(view -> openAppInfo());
        accessibilityButton.setOnClickListener(view -> openAccessibilitySetup());
        findViewById(R.id.startButton).setOnClickListener(view -> startAgent());
        findViewById(R.id.stopButton).setOnClickListener(view -> stopAgent());

        findViewById(R.id.assistantSendButton).setOnClickListener(view -> submitTypedChat());
        findViewById(R.id.assistantMicButton).setOnClickListener(
                view -> requestVoiceService(WakeWordService.ACTION_START_CAPTURE));
        findViewById(R.id.wakeButton).setOnClickListener(
                view -> requestVoiceService(WakeWordService.ACTION_START_WAKE));
        findViewById(R.id.wakeStopButton).setOnClickListener(view -> stopWakeWord());
        stopVoiceButton.setOnClickListener(view -> stopVoiceCapture());
        assistantActions.setOnCheckedChangeListener((button, checked) ->
                getSharedPreferences(SETTINGS, MODE_PRIVATE).edit()
                        .putBoolean("assistant_actions", checked).apply());
        assistantInput.setOnEditorActionListener((view, actionId, event) -> {
            submitTypedChat();
            return true;
        });
    }

    private void showAssistantPane(boolean showAssistant) {
        assistantPane.setVisibility(showAssistant ? View.VISIBLE : View.GONE);
        setupPane.setVisibility(showAssistant ? View.GONE : View.VISIBLE);
    }

    private void submitTypedChat() {
        String prompt = assistantInput.getText().toString().trim();
        if (prompt.isBlank()) return;
        assistantInput.setText("");
        appendChat("You", prompt);
        setAssistantStatus("Jarvis is thinking…");
        boolean allowActions = assistantActions.isChecked();
        JSONArray history = historySnapshot();
        io.execute(() -> {
            try {
                MobileAssistantEngine.Result answer = MobileAssistantEngine.ask(this, prompt, history, allowActions);
                appendHistory("user", prompt);
                appendHistory("assistant", answer.reply);
                runOnUiThread(() -> {
                    appendChat("Jarvis", answer.reply);
                    if (!answer.actionResult.isBlank()) appendChat("Phone action", answer.actionResult);
                    setAssistantStatus(answer.actionRan ? "Action completed." : "Ready for your next request.");
                });
            } catch (Exception e) {
                setAssistantStatus("Mobile Jarvis is unavailable: " + message(e));
            }
        });
    }

    private void requestVoiceService(String action) {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            pendingVoiceAction = action;
            microphonePermission.launch(Manifest.permission.RECORD_AUDIO);
            return;
        }
        startVoiceService(action);
    }

    private void startVoiceService(String action) {
        Intent service = new Intent(this, WakeWordService.class).setAction(action);
        ContextCompat.startForegroundService(this, service);
        if (WakeWordService.ACTION_START_CAPTURE.equals(action)) {
            setAssistantStatus("Starting voice capture…");
        } else {
            setAssistantStatus("Wake word listening is enabled. It can continue while the screen is off.");
        }
    }

    private void stopVoiceCapture() {
        startService(new Intent(this, WakeWordService.class).setAction(WakeWordService.ACTION_STOP_CAPTURE));
        setAssistantStatus("Finishing voice capture…");
    }

    private void stopWakeWord() {
        startService(new Intent(this, WakeWordService.class).setAction(WakeWordService.ACTION_STOP));
        hideVoiceCapture();
        setAssistantStatus("Wake word listening stopped.");
    }

    private void handleVoiceState(String state, String text) {
        if (WakeWordService.STATE_CAPTURING.equals(state)) {
            showAssistantPane(true);
            voiceWave.setVisibility(View.VISIBLE);
            voiceWave.startFlow();
            stopVoiceButton.setVisibility(View.VISIBLE);
            setAssistantStatus(text);
        } else if (WakeWordService.STATE_PROCESSING.equals(state)) {
            hideVoiceCapture();
            appendChat("You", text);
            setAssistantStatus("Jarvis is working on your voice request…");
        } else if (WakeWordService.STATE_REPLY.equals(state)) {
            hideVoiceCapture();
            appendChat("Jarvis", text);
            setAssistantStatus("Ready for your next request.");
        } else if (WakeWordService.STATE_ERROR.equals(state)) {
            hideVoiceCapture();
            setAssistantStatus(text);
        } else if (WakeWordService.STATE_WAKE.equals(state)) {
            hideVoiceCapture();
            setAssistantStatus(text);
        }
    }

    private void hideVoiceCapture() {
        voiceWave.stopFlow();
        voiceWave.setVisibility(View.GONE);
        stopVoiceButton.setVisibility(View.GONE);
    }

    private synchronized JSONArray historySnapshot() {
        JSONArray copy = new JSONArray();
        for (int i = 0; i < chatHistory.length(); i++) {
            try { copy.put(chatHistory.getJSONObject(i)); } catch (Exception ignored) { }
        }
        return copy;
    }

    private synchronized void appendHistory(String role, String content) {
        try {
            chatHistory.put(new JSONObject().put("role", role).put("content", content));
        } catch (Exception ignored) {
            return;
        }
        while (chatHistory.length() > 12) chatHistory.remove(0);
    }

    private void appendChat(String speaker, String message) {
        runOnUiThread(() -> {
            String current = assistantTranscript.getText().toString();
            assistantTranscript.setText(current + "\n\n" + speaker + ": " + message.trim());
            chatScroll.post(() -> chatScroll.fullScroll(View.FOCUS_DOWN));
        });
    }

    private void pair() {
        String endpoint = relayUrl.getText().toString().trim();
        String name = deviceName.getText().toString().trim();
        String code = pairingCode.getText().toString().trim();
        if (endpoint.isBlank() || name.isBlank() || code.length() != 8) {
            setStatus("Enter the relay URL, a phone name, and the 8-character pairing code.");
            return;
        }
        setStatus("Pairing with the Jarvis relay…");
        io.execute(() -> {
            try {
                PairingRecord paired = new RelayClient(endpoint).claim(code, name);
                new PairingStore(this).save(paired);
                runOnUiThread(() -> {
                    relayUrl.setText(paired.endpoint);
                    setStatus("Paired with " + paired.peerName + ". Compare this fingerprint on the computer, then trust it on both devices:\n"
                            + safeFingerprint(paired));
                });
            } catch (Exception e) {
                setStatus("Pairing failed: " + message(e));
            }
        });
    }

    private void trust() {
        String supplied = fingerprint.getText().toString().replace("-", "").trim().toUpperCase(Locale.ROOT);
        io.execute(() -> {
            try {
                PairingStore store = new PairingStore(this);
                PairingRecord paired = store.load();
                if (paired == null) throw new IllegalStateException("Pair this phone first.");
                String expected = paired.fingerprint().replace("-", "");
                if (!expected.equals(supplied)) throw new SecurityException("Fingerprint does not match. Do not trust this pairing.");
                paired.trusted = true;
                store.save(paired);
                setStatus("Trusted " + paired.peerName + ". Mobile Jarvis is ready in its own tab.");
            } catch (Exception e) {
                setStatus("Trust failed: " + message(e));
            }
        });
    }

    private void startAgent() {
        io.execute(() -> {
            try {
                PairingRecord paired = new PairingStore(this).load();
                if (paired == null || !paired.trusted) throw new IllegalStateException("Pair and trust the computer first.");
                Intent intent = new Intent(this, RemoteAgentService.class).setAction(RemoteAgentService.ACTION_START);
                ContextCompat.startForegroundService(this, intent);
                setStatus("Phone agent started. It will execute remote phone commands from " + paired.peerName + "."
                        + (isAccessibilityEnabled() ? " Accessibility control is ready."
                        : " App and URL launching works now; enable Accessibility for tap, swipe, type, and navigation."));
            } catch (Exception e) {
                setStatus("Could not start agent: " + message(e));
            }
        });
    }

    private void stopAgent() {
        startService(new Intent(this, RemoteAgentService.class).setAction(RemoteAgentService.ACTION_STOP));
        setStatus("Phone agent stopped.");
    }

    private void refreshStatus() {
        io.execute(() -> {
            try {
                PairingRecord paired = new PairingStore(this).load();
                if (paired == null) {
                    setStatus("Not paired. On the computer, create a pairing code with: python run.py --remote-pair \"My Phone\"");
                } else {
                    runOnUiThread(() -> relayUrl.setText(paired.endpoint));
                    String mobile = paired.mobileAssistantToken.isBlank()
                            ? " Re-pair this phone to enable standalone Mobile Jarvis."
                            : " Standalone Mobile Jarvis is available.";
                    setStatus((paired.trusted ? "Trusted" : "Paired — not trusted") + " computer: " + paired.peerName
                            + "\nFingerprint: " + safeFingerprint(paired) + mobile);
                }
            } catch (Exception e) {
                setStatus("Could not read secure pairing state: " + message(e));
            }
        });
    }

    private void openAccessibilitySetup() {
        if (isAccessibilityEnabled()) {
            setStatus("Android accessibility control is already enabled.");
            return;
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            new AlertDialog.Builder(this)
                    .setTitle("Allow Jarvis Mobile control")
                    .setMessage("Android may block a sideloaded app with ‘Restricted setting’ or call Accessibility dangerous. "
                            + "This is Android’s protection, not an app permission that Jarvis can bypass.\n\n"
                            + "1. Open App info.\n2. Tap the three-dot menu (⋮).\n3. Choose ‘Allow restricted settings’.\n"
                            + "4. Return here and open Accessibility.\n\nOnly continue if you trust this APK.")
                    .setPositiveButton("OPEN APP INFO", (dialog, which) -> openAppInfo())
                    .setNeutralButton("OPEN ACCESSIBILITY", (dialog, which) -> openAccessibilitySettings())
                    .setNegativeButton("CANCEL", null)
                    .show();
            return;
        }
        openAccessibilitySettings();
    }

    private void openAppInfo() {
        startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.parse("package:" + getPackageName())));
    }

    private void openAccessibilitySettings() {
        startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
    }

    private boolean isAccessibilityEnabled() {
        if (JarvisAccessibilityService.isAvailable()) return true;
        AccessibilityManager manager = (AccessibilityManager) getSystemService(ACCESSIBILITY_SERVICE);
        if (manager == null) return false;
        ComponentName wanted = new ComponentName(this, JarvisAccessibilityService.class);
        for (AccessibilityServiceInfo info : manager.getEnabledAccessibilityServiceList(
                AccessibilityServiceInfo.FEEDBACK_ALL_MASK)) {
            if (info.getResolveInfo() == null || info.getResolveInfo().serviceInfo == null) continue;
            ComponentName enabled = new ComponentName(info.getResolveInfo().serviceInfo.packageName,
                    info.getResolveInfo().serviceInfo.name);
            if (wanted.equals(enabled)) return true;
        }
        return false;
    }

    private void refreshAccessibilityStatus() {
        boolean enabled = isAccessibilityEnabled();
        accessibilityStatus.setText(enabled
                ? "Accessibility control: ENABLED — tap, swipe, type, back, and home are ready."
                : "Accessibility control: OFF — app and URL launching still works, but screen control needs this permission.");
        accessibilityButton.setText(enabled ? "Android accessibility control enabled" : "2. Enable Android accessibility control");
        accessibilityButton.setEnabled(!enabled);
        restrictedSettingsButton.setVisibility(Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && !enabled
                ? View.VISIBLE : View.GONE);
    }

    private String safeFingerprint(PairingRecord pairing) {
        try { return pairing.fingerprint(); } catch (Exception e) { return "unavailable"; }
    }

    private void setStatus(String text) { runOnUiThread(() -> status.setText(text)); }
    private void setAssistantStatus(String text) { runOnUiThread(() -> assistantStatus.setText(text)); }
    private static String message(Exception e) {
        return e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
    }

    @Override protected void onDestroy() {
        io.shutdownNow();
        super.onDestroy();
    }
}
