package com.jarvis.mobile;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Path;
import android.os.Bundle;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** The opt-in Android bridge used by MobileCommandExecutor for UI interaction. */
public final class JarvisAccessibilityService extends AccessibilityService {
    private static volatile JarvisAccessibilityService instance;

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        // Commands use the live accessibility tree on demand; no event history is retained.
    }

    @Override
    public void onInterrupt() {
        // Android interrupted feedback. The foreground relay service remains active.
    }

    @Override
    protected void onServiceConnected() {
        instance = this;
    }

    @Override
    public void onDestroy() {
        if (instance == this) instance = null;
        super.onDestroy();
    }

    static boolean isAvailable() { return instance != null; }

    static boolean tap(int x, int y) { return gesture(path(x, y), 90); }

    static boolean swipe(int x1, int y1, int x2, int y2) {
        Path path = new Path();
        path.moveTo(x1, y1);
        path.lineTo(x2, y2);
        return gesture(path, 320);
    }

    static boolean back() {
        JarvisAccessibilityService service = instance;
        return service != null && service.performGlobalAction(GLOBAL_ACTION_BACK);
    }

    static boolean home() {
        JarvisAccessibilityService service = instance;
        return service != null && service.performGlobalAction(GLOBAL_ACTION_HOME);
    }

    static boolean type(String text) {
        JarvisAccessibilityService service = instance;
        if (service == null) return false;
        AccessibilityNodeInfo root = service.getRootInActiveWindow();
        if (root == null) return false;
        try {
            AccessibilityNodeInfo focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
            if (focused == null) return false;
            Bundle args = new Bundle();
            args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
            return focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
        } finally {
            root.recycle();
        }
    }

    private static Path path(int x, int y) {
        Path path = new Path();
        path.moveTo(x, y);
        return path;
    }

    private static boolean gesture(Path path, long durationMs) {
        JarvisAccessibilityService service = instance;
        if (service == null) return false;
        CountDownLatch done = new CountDownLatch(1);
        boolean[] success = {false};
        GestureDescription description = new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(path, 0, durationMs))
                .build();
        boolean accepted = service.dispatchGesture(description, new GestureResultCallback() {
            @Override public void onCompleted(GestureDescription gestureDescription) {
                success[0] = true;
                done.countDown();
            }
            @Override public void onCancelled(GestureDescription gestureDescription) { done.countDown(); }
        }, null);
        if (!accepted) return false;
        try { done.await(2, TimeUnit.SECONDS); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        return success[0];
    }
}
