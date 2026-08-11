package com.jarvis.mobile;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.util.Base64;

import androidx.core.app.NotificationCompat;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Foreground service that long-polls only a trusted pairing and executes mobile commands. */
public final class RemoteAgentService extends Service {
    static final String ACTION_START = "com.jarvis.mobile.START";
    static final String ACTION_STOP = "com.jarvis.mobile.STOP";
    private static final int NOTIFICATION_ID = 41;
    private volatile boolean running;
    private ExecutorService executor;

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? ACTION_START : intent.getAction();
        if (ACTION_STOP.equals(action)) {
            stopAgent();
            return START_NOT_STICKY;
        }
        if (!running) {
            running = true;
            startForeground(NOTIFICATION_ID, notification("Listening for trusted computer tasks"));
            executor = Executors.newSingleThreadExecutor();
            executor.execute(this::pollLoop);
        }
        return START_STICKY;
    }

    @Override public IBinder onBind(Intent intent) { return null; }

    @Override public void onDestroy() {
        stopAgent();
        super.onDestroy();
    }

    private void pollLoop() {
        try {
            PairingStore store = new PairingStore(this);
            PairingRecord pairing = store.load();
            if (pairing == null || !pairing.trusted) return;
            RelayClient relay = new RelayClient(pairing.endpoint);
            MobileCommandExecutor commands = new MobileCommandExecutor(this);
            try {
                relay.send(pairing, withDeviceInfo(new JSONObject().put("type", "device_info")));
            } catch (Exception ignored) {
                // The same signed metadata is included with every later task response.
            }
            while (running) {
                try {
                    List<JSONObject> messages = relay.receive(pairing, 20);
                    store.save(pairing);
                    for (JSONObject message : messages) {
                        if (!"task".equals(message.optString("type"))) continue;
                        String taskId = message.optString("id");
                        String task = message.optString("task").trim();
                        if (taskId.isBlank() || task.isBlank()) continue;
                        relay.send(pairing, withDeviceInfo(new JSONObject()
                                .put("type", "task_started").put("task_id", taskId)));
                        MobileCommandExecutor.Result result = commands.execute(task);
                        JSONObject response = withDeviceInfo(new JSONObject()
                                .put("type", "task_result").put("task_id", taskId)
                                .put("ok", result.ok).put("result", result.message));
                        if (result.screenshot != null) {
                            JarvisAccessibilityService.ScreenshotCapture screenshot = result.screenshot;
                            response.put("attachment", new JSONObject()
                                    .put("mime_type", "image/jpeg")
                                    .put("width", screenshot.width)
                                    .put("height", screenshot.height)
                                    .put("preview_width", screenshot.previewWidth)
                                    .put("preview_height", screenshot.previewHeight)
                                    .put("data", Base64.encodeToString(screenshot.jpeg,
                                            Base64.URL_SAFE | Base64.NO_WRAP)));
                        }
                        relay.send(pairing, response);
                    }
                } catch (Exception e) {
                    // A transient relay failure must not end an owner-started agent.
                    try {
                        Thread.sleep(2_000);
                    } catch (InterruptedException interrupted) {
                        Thread.currentThread().interrupt();
                        break;
                    }
                }
            }
        } catch (Exception ignored) {
            // The activity exposes pairing problems before the service is started.
        } finally {
            stopSelf();
        }
    }

    private void stopAgent() {
        running = false;
        if (executor != null) executor.shutdownNow();
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    private JSONObject withDeviceInfo(JSONObject message) throws Exception {
        JSONArray capabilities = new JSONArray();
        for (String capability : MobileCommandExecutor.CAPABILITIES) {
            capabilities.put(capability);
        }
        JSONObject status = new JSONObject()
                .put("android_sdk", Build.VERSION.SDK_INT)
                .put("accessibility_ready", JarvisAccessibilityService.isAvailable())
                .put("screenshot_ready", JarvisAccessibilityService.canTakeScreenshot());
        return message.put("device_kind", "android")
                .put("capabilities", capabilities)
                .put("device_status", status);
    }

    private Notification notification(String text) {
        String channelId = "jarvis_remote_agent";
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel(channelId, getString(R.string.agent_channel_name),
                NotificationManager.IMPORTANCE_LOW));
        return new NotificationCompat.Builder(this, channelId)
                .setSmallIcon(R.drawable.ic_jarvis)
                .setContentTitle(getString(R.string.agent_notification_title))
                .setContentText(text).setOngoing(true).build();
    }
}
