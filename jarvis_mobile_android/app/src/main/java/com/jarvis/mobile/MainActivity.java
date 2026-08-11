package com.jarvis.mobile;

import android.Manifest;
import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.AlertDialog;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.view.View;
import android.view.accessibility.AccessibilityManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;

import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.annotation.Nullable;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.ContextCompat;

import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Setup screen for pairing a phone as a trusted Jarvis Remote agent. */
public final class MainActivity extends AppCompatActivity {
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private EditText relayUrl;
    private EditText deviceName;
    private EditText pairingCode;
    private EditText fingerprint;
    private TextView status;
    private TextView accessibilityStatus;
    private Button accessibilityButton;
    private Button restrictedSettingsButton;
    private final ActivityResultLauncher<String> notificationPermission = registerForActivityResult(
            new ActivityResultContracts.RequestPermission(), ignored -> { });

    @Override protected void onCreate(@Nullable Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        relayUrl = findViewById(R.id.relayUrlInput);
        deviceName = findViewById(R.id.deviceNameInput);
        pairingCode = findViewById(R.id.pairingCodeInput);
        fingerprint = findViewById(R.id.fingerprintInput);
        status = findViewById(R.id.statusText);
        accessibilityStatus = findViewById(R.id.accessibilityStatusText);
        accessibilityButton = findViewById(R.id.accessibilityButton);
        restrictedSettingsButton = findViewById(R.id.restrictedSettingsButton);
        deviceName.setText(Build.MANUFACTURER + " " + Build.MODEL);

        findViewById(R.id.pairButton).setOnClickListener(view -> pair());
        findViewById(R.id.trustButton).setOnClickListener(view -> trust());
        restrictedSettingsButton.setOnClickListener(view -> openAppInfo());
        accessibilityButton.setOnClickListener(view -> openAccessibilitySetup());
        findViewById(R.id.startButton).setOnClickListener(view -> startAgent());
        findViewById(R.id.stopButton).setOnClickListener(view -> stopAgent());
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS);
        }
        refreshStatus();
        refreshAccessibilityStatus();
    }

    @Override protected void onResume() {
        super.onResume();
        if (accessibilityStatus != null) refreshAccessibilityStatus();
    }

    private void pair() {
        String endpoint = relayUrl.getText().toString().trim();
        String name = deviceName.getText().toString().trim();
        String code = pairingCode.getText().toString().trim();
        if (endpoint.isBlank() || name.isBlank() || code.length() != 8) {
            setStatus("Enter the relay URL, a phone name, and the 8-character pairing code.");
            return;
        }
        setStatus("Pairing with the Jarvis computer…");
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
                setStatus("Trusted " + paired.peerName + ". You can now start the phone agent.");
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
                setStatus("Phone agent started. It will execute mobile commands from " + paired.peerName + "."
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
                    setStatus((paired.trusted ? "Trusted" : "Paired — not trusted") + " computer: " + paired.peerName
                            + "\nFingerprint: " + safeFingerprint(paired));
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
                            + "1. Open App info.\n"
                            + "2. Tap the three-dot menu (⋮).\n"
                            + "3. Choose ‘Allow restricted settings’.\n"
                            + "4. Return here and open Accessibility.\n\n"
                            + "Only continue if you trust this APK: Accessibility can read the screen and control taps for tasks from your trusted paired computer.")
                    .setPositiveButton("OPEN APP INFO", (dialog, which) -> openAppInfo())
                    .setNeutralButton("OPEN ACCESSIBILITY", (dialog, which) -> openAccessibilitySettings())
                    .setNegativeButton("CANCEL", null)
                    .show();
            return;
        }
        openAccessibilitySettings();
    }

    private void openAppInfo() {
        Intent details = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.parse("package:" + getPackageName()));
        startActivity(details);
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
            ComponentName enabled = new ComponentName(
                    info.getResolveInfo().serviceInfo.packageName,
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
        restrictedSettingsButton.setVisibility(
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && !enabled
                        ? View.VISIBLE : View.GONE);
    }

    private String safeFingerprint(PairingRecord pairing) {
        try { return pairing.fingerprint(); } catch (Exception e) { return "unavailable"; }
    }

    private void setStatus(String text) {
        runOnUiThread(() -> status.setText(text));
    }

    private static String message(Exception e) {
        return e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
    }

    @Override protected void onDestroy() {
        io.shutdownNow();
        super.onDestroy();
    }
}
