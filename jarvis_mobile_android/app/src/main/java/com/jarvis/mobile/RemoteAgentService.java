package com.jarvis.mobile;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.os.IBinder;

import androidx.core.app.NotificationCompat;

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
            while (running) {
                try {
                    List<JSONObject> messages = relay.receive(pairing, 20);
                    store.save(pairing);
                    for (JSONObject message : messages) {
                        if (!"task".equals(message.optString("type"))) continue;
                        String taskId = message.optString("id");
                        String task = message.optString("task").trim();
                        if (taskId.isBlank() || task.isBlank()) continue;
                        relay.send(pairing, new JSONObject().put("type", "task_started").put("task_id", taskId));
                        MobileCommandExecutor.Result result = commands.execute(task);
                        relay.send(pairing, new JSONObject().put("type", "task_result").put("task_id", taskId)
                                .put("ok", result.ok).put("result", result.message));
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
