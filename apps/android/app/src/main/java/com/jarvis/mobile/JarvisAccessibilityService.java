package com.jarvis.mobile;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Bitmap;
import android.graphics.Path;
import android.graphics.Rect;
import android.os.Build;
import android.os.Bundle;
import android.view.Display;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import java.io.ByteArrayOutputStream;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * The opt-in Android accessibility bridge.
 *
 * Precision follows the desktop Jarvis Set-of-Marks design: inspect the live
 * accessibility tree, return a numbered menu with exact screen bounds, then
 * execute against the chosen node itself. Raw coordinates remain a fallback
 * for apps that do not expose an accessibility tree.
 */
public final class JarvisAccessibilityService extends AccessibilityService {
    private static final int MAX_SCREENSHOT_BYTES = 7_000;
    private static final int MAX_UI_ELEMENTS = 60;
    private static final int MAX_VISITED_NODES = 900;
    private static final int MAX_TREE_DEPTH = 45;
    private static final int MAX_UI_SUMMARY_CHARS = 4_200;
    private static final long SNAPSHOT_MAX_AGE_MS = 120_000;

    private static volatile JarvisAccessibilityService instance;
    private static volatile UiSnapshot lastSnapshot;

    static final class ScreenshotCapture {
        final byte[] jpeg;
        final int width;
        final int height;
        final int previewWidth;
        final int previewHeight;
        final String error;

        private ScreenshotCapture(byte[] jpeg, int width, int height,
                                  int previewWidth, int previewHeight, String error) {
            this.jpeg = jpeg;
            this.width = width;
            this.height = height;
            this.previewWidth = previewWidth;
            this.previewHeight = previewHeight;
            this.error = error;
        }

        static ScreenshotCapture failure(String error) {
            return new ScreenshotCapture(null, 0, 0, 0, 0, error);
        }
    }

    static final class UiSnapshot {
        final String packageName;
        final int width;
        final int height;
        final List<UiElementRef> elements;
        final String summary;
        final String error;
        final long capturedAt;

        private UiSnapshot(String packageName, int width, int height,
                           List<UiElementRef> elements, String summary, String error) {
            this.packageName = packageName;
            this.width = width;
            this.height = height;
            this.elements = elements;
            this.summary = summary;
            this.error = error;
            this.capturedAt = System.currentTimeMillis();
        }

        static UiSnapshot failure(String error) {
            return new UiSnapshot("", 0, 0, new ArrayList<>(), "", error);
        }
    }

    static final class ElementActionResult {
        final boolean ok;
        final String message;

        private ElementActionResult(boolean ok, String message) {
            this.ok = ok;
            this.message = message;
        }

        static ElementActionResult success(String message) {
            return new ElementActionResult(true, message);
        }

        static ElementActionResult failure(String message) {
            return new ElementActionResult(false, message);
        }
    }

    private static final class UiElementRef {
        int id;
        final Rect bounds;
        final String viewId;
        final String text;
        final String description;
        final String className;
        final boolean clickable;
        final boolean longClickable;
        final boolean editable;
        final boolean scrollable;
        final boolean checkable;

        UiElementRef(Rect bounds, String viewId, String text, String description,
                     String className, boolean clickable, boolean longClickable,
                     boolean editable, boolean scrollable, boolean checkable) {
            this.bounds = new Rect(bounds);
            this.viewId = viewId;
            this.text = text;
            this.description = description;
            this.className = className;
            this.clickable = clickable;
            this.longClickable = longClickable;
            this.editable = editable;
            this.scrollable = scrollable;
            this.checkable = checkable;
        }

        int centerX() { return bounds.centerX(); }
        int centerY() { return bounds.centerY(); }

        int priority() {
            if (editable) return 6;
            if (clickable || checkable) return 5;
            if (longClickable) return 4;
            if (scrollable) return 3;
            return 1;
        }

        String describe() {
            String role = className;
            int dot = role.lastIndexOf('.');
            if (dot >= 0 && dot + 1 < role.length()) role = role.substring(dot + 1);
            if (role.isBlank()) role = "Element";
            String label = !text.isBlank() ? text : description;
            if (label.isBlank() && !viewId.isBlank()) {
                int slash = viewId.lastIndexOf('/');
                label = slash >= 0 ? viewId.substring(slash + 1) : viewId;
            }
            label = compact(label, 72);
            StringBuilder actions = new StringBuilder();
            if (clickable || checkable) actions.append("click,");
            if (longClickable) actions.append("long-press,");
            if (editable) actions.append("type,");
            if (scrollable) actions.append("scroll,");
            if (actions.length() > 0) actions.setLength(actions.length() - 1);
            return String.format(Locale.ROOT,
                    "[%d] %s \"%s\" bounds=(%d,%d)-(%d,%d) center=(%d,%d)%s",
                    id, role, label.isBlank() ? "unlabeled" : label,
                    bounds.left, bounds.top, bounds.right, bounds.bottom,
                    centerX(), centerY(),
                    actions.length() == 0 ? "" : " actions=" + actions);
        }
    }

    private static final class BestNode {
        AccessibilityNodeInfo node;
        int score = Integer.MIN_VALUE;
        final Rect bounds = new Rect();
    }

    private static final class ResolvedElement {
        final AccessibilityNodeInfo node;
        final UiElementRef reference;
        final Rect bounds;

        ResolvedElement(AccessibilityNodeInfo node, UiElementRef reference, Rect bounds) {
            this.node = node;
            this.reference = reference;
            this.bounds = bounds;
        }
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        // No event history is retained. Commands inspect the live tree on demand.
    }

    @Override
    public void onInterrupt() {
        // Android interrupted feedback. The foreground relay remains active.
    }

    @Override
    protected void onServiceConnected() {
        instance = this;
        lastSnapshot = null;
    }

    @Override
    public void onDestroy() {
        if (instance == this) instance = null;
        lastSnapshot = null;
        super.onDestroy();
    }

    static boolean isAvailable() { return instance != null; }

    static boolean canTakeScreenshot() {
        return Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && instance != null;
    }

    static void invalidateSnapshot() { lastSnapshot = null; }

    static ScreenshotCapture screenshot() {
        JarvisAccessibilityService service = instance;
        if (service == null) {
            return ScreenshotCapture.failure("Android accessibility control is not enabled.");
        }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            return ScreenshotCapture.failure("Mobile screenshots require Android 11 or newer.");
        }

        CountDownLatch done = new CountDownLatch(1);
        Bitmap[] captured = {null};
        int[] failureCode = {-1};
        service.takeScreenshot(Display.DEFAULT_DISPLAY, service.getMainExecutor(),
                new TakeScreenshotCallback() {
                    @Override public void onSuccess(ScreenshotResult result) {
                        try {
                            Bitmap hardware = Bitmap.wrapHardwareBuffer(
                                    result.getHardwareBuffer(), result.getColorSpace());
                            if (hardware != null) {
                                captured[0] = hardware.copy(Bitmap.Config.ARGB_8888, false);
                            }
                        } finally {
                            result.getHardwareBuffer().close();
                            done.countDown();
                        }
                    }

                    @Override public void onFailure(int errorCode) {
                        failureCode[0] = errorCode;
                        done.countDown();
                    }
                });
        try {
            if (!done.await(6, TimeUnit.SECONDS)) {
                return ScreenshotCapture.failure("Android timed out while capturing the screen.");
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return ScreenshotCapture.failure("Screenshot capture was interrupted.");
        }
        Bitmap source = captured[0];
        if (source == null) {
            return ScreenshotCapture.failure("Android rejected the screenshot (error "
                    + failureCode[0] + "). Unlock the phone and keep accessibility enabled.");
        }
        try {
            return compressScreenshot(source);
        } finally {
            source.recycle();
        }
    }

    /** Build and cache a numbered element menu for the screenshot's native dimensions. */
    static UiSnapshot inspectScreen(int screenWidth, int screenHeight) {
        JarvisAccessibilityService service = instance;
        if (service == null) return UiSnapshot.failure("Android accessibility control is not enabled.");
        AccessibilityNodeInfo root = service.getRootInActiveWindow();
        if (root == null) {
            return UiSnapshot.failure("Android did not expose an active accessibility window. Unlock the phone and try again.");
        }
        try {
            Rect rootBounds = new Rect();
            root.getBoundsInScreen(rootBounds);
            int width = screenWidth > 0 ? screenWidth : Math.max(1, rootBounds.right);
            int height = screenHeight > 0 ? screenHeight : Math.max(1, rootBounds.bottom);
            String packageName = safe(root.getPackageName());
            List<UiElementRef> candidates = new ArrayList<>();
            int[] visited = {0};
            collectElements(root, 0, width, height, candidates, visited);

            candidates.sort(Comparator
                    .comparingInt(UiElementRef::priority).reversed()
                    .thenComparingInt(ref -> ref.bounds.top)
                    .thenComparingInt(ref -> ref.bounds.left)
                    .thenComparingInt(ref -> ref.bounds.width() * ref.bounds.height()));
            List<UiElementRef> elements = new ArrayList<>();
            Set<String> seen = new HashSet<>();
            for (UiElementRef candidate : candidates) {
                String key = candidate.className + "|" + candidate.bounds.flattenToString()
                        + "|" + candidate.text + "|" + candidate.description;
                if (!seen.add(key)) continue;
                candidate.id = elements.size();
                elements.add(candidate);
                if (elements.size() >= MAX_UI_ELEMENTS) break;
            }

            StringBuilder summary = new StringBuilder();
            summary.append("\nMOBILE UI ELEMENTS (native screen ")
                    .append(width).append('x').append(height)
                    .append(", package ").append(compact(packageName, 80)).append("):\n")
                    .append("Use these exact IDs; never estimate coordinates from the compressed preview. ")
                    .append("IDs expire after an action or screen change.\n");
            if (elements.isEmpty()) {
                summary.append("(No visible accessibility elements were exposed by this app.)");
            } else {
                for (UiElementRef element : elements) {
                    String line = element.describe() + "\n";
                    if (summary.length() + line.length() > MAX_UI_SUMMARY_CHARS) break;
                    summary.append(line);
                }
                summary.append("Preferred commands: tap element <id>; type element <id> <text>; ")
                        .append("scroll element <id> <forward|backward>; swipe element <id> <up|down|left|right>.");
            }
            UiSnapshot snapshot = new UiSnapshot(packageName, width, height,
                    elements, summary.toString(), null);
            lastSnapshot = snapshot;
            return snapshot;
        } finally {
            root.recycle();
        }
    }

    static ElementActionResult tapElement(int elementId) {
        ResolvedElement resolved = resolveElement(elementId);
        if (resolved == null) return resolutionFailure(elementId);
        try {
            boolean semantic = clickNodeOrAncestor(resolved.node);
            boolean ok = semantic || gesture(path(resolved.bounds.centerX(), resolved.bounds.centerY()), 90);
            if (!ok) return ElementActionResult.failure("Android rejected element " + elementId + ". Request a fresh screenshot.");
            invalidateSnapshot();
            String method = semantic ? "accessibility ACTION_CLICK" : "exact-center gesture fallback";
            return ElementActionResult.success("Clicked element " + elementId + " at exact native center ("
                    + resolved.bounds.centerX() + "," + resolved.bounds.centerY() + ") using " + method + ".");
        } finally {
            resolved.node.recycle();
        }
    }

    static ElementActionResult longPressElement(int elementId) {
        ResolvedElement resolved = resolveElement(elementId);
        if (resolved == null) return resolutionFailure(elementId);
        try {
            boolean ok = gesture(path(resolved.bounds.centerX(), resolved.bounds.centerY()), 650);
            if (!ok) return ElementActionResult.failure("Android rejected the long press on element " + elementId + ".");
            invalidateSnapshot();
            return ElementActionResult.success("Long-pressed element " + elementId + " at exact native center ("
                    + resolved.bounds.centerX() + "," + resolved.bounds.centerY() + ").");
        } finally {
            resolved.node.recycle();
        }
    }

    static ElementActionResult typeElement(int elementId, String text) {
        ResolvedElement resolved = resolveElement(elementId);
        if (resolved == null) return resolutionFailure(elementId);
        try {
            resolved.node.performAction(AccessibilityNodeInfo.ACTION_FOCUS);
            Bundle args = new Bundle();
            args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
            boolean ok = resolved.node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
            if (!ok) {
                clickNodeOrAncestor(resolved.node);
                ok = type(text);
            }
            if (!ok) return ElementActionResult.failure("Element " + elementId + " did not accept text. Request a fresh screenshot and choose an editable element.");
            invalidateSnapshot();
            return ElementActionResult.success("Set text on exact editable element " + elementId + " at ("
                    + resolved.bounds.centerX() + "," + resolved.bounds.centerY() + ").");
        } finally {
            resolved.node.recycle();
        }
    }

    static ElementActionResult scrollElement(int elementId, boolean forward) {
        ResolvedElement resolved = resolveElement(elementId);
        if (resolved == null) return resolutionFailure(elementId);
        try {
            int action = forward ? AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
                    : AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD;
            boolean semantic = performOnNodeOrAncestor(resolved.node, action, true);
            boolean ok = semantic || swipeWithin(resolved.bounds, forward ? "up" : "down");
            if (!ok) return ElementActionResult.failure("Element " + elementId + " could not scroll in that direction.");
            invalidateSnapshot();
            return ElementActionResult.success("Scrolled element " + elementId + " "
                    + (forward ? "forward" : "backward") + " using "
                    + (semantic ? "its accessibility scroll action" : "a bounded gesture fallback") + ".");
        } finally {
            resolved.node.recycle();
        }
    }

    static ElementActionResult swipeElement(int elementId, String direction) {
        ResolvedElement resolved = resolveElement(elementId);
        if (resolved == null) return resolutionFailure(elementId);
        try {
            boolean ok = swipeWithin(resolved.bounds, direction);
            if (!ok) return ElementActionResult.failure("Android rejected the " + direction + " swipe on element " + elementId + ".");
            invalidateSnapshot();
            return ElementActionResult.success("Swiped " + direction + " inside exact bounds of element "
                    + elementId + " " + resolved.bounds.flattenToString() + ".");
        } finally {
            resolved.node.recycle();
        }
    }

    static boolean tap(int x, int y) {
        boolean ok = gesture(path(x, y), 90);
        if (ok) invalidateSnapshot();
        return ok;
    }

    static boolean swipe(int x1, int y1, int x2, int y2) {
        Path path = new Path();
        path.moveTo(x1, y1);
        path.lineTo(x2, y2);
        boolean ok = gesture(path, 320);
        if (ok) invalidateSnapshot();
        return ok;
    }

    static boolean back() {
        JarvisAccessibilityService service = instance;
        boolean ok = service != null && service.performGlobalAction(GLOBAL_ACTION_BACK);
        if (ok) invalidateSnapshot();
        return ok;
    }

    static boolean home() {
        JarvisAccessibilityService service = instance;
        boolean ok = service != null && service.performGlobalAction(GLOBAL_ACTION_HOME);
        if (ok) invalidateSnapshot();
        return ok;
    }

    static boolean type(String text) {
        JarvisAccessibilityService service = instance;
        if (service == null) return false;
        AccessibilityNodeInfo root = service.getRootInActiveWindow();
        if (root == null) return false;
        try {
            AccessibilityNodeInfo focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
            if (focused == null) return false;
            try {
                Bundle args = new Bundle();
                args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
                boolean ok = focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
                if (ok) invalidateSnapshot();
                return ok;
            } finally {
                focused.recycle();
            }
        } finally {
            root.recycle();
        }
    }

    private static void collectElements(AccessibilityNodeInfo node, int depth,
                                        int screenWidth, int screenHeight,
                                        List<UiElementRef> output, int[] visited) {
        if (node == null || depth > MAX_TREE_DEPTH || visited[0] >= MAX_VISITED_NODES) return;
        visited[0]++;
        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);
        boolean validBounds = bounds.width() > 3 && bounds.height() > 3
                && bounds.right > 0 && bounds.bottom > 0
                && bounds.left < screenWidth && bounds.top < screenHeight;
        boolean visible;
        try {
            visible = node.isVisibleToUser();
        } catch (Exception ignored) {
            visible = true;
        }
        String text = node.isPassword() ? "password field" : safe(node.getText());
        String description = node.isPassword() ? "" : safe(node.getContentDescription());
        String viewId = safe(node.getViewIdResourceName());
        String className = safe(node.getClassName());
        boolean editable = node.isEditable()
                || supportsAction(node, AccessibilityNodeInfo.ACTION_SET_TEXT);
        boolean actionable = node.isClickable() || node.isLongClickable() || editable
                || node.isScrollable() || node.isCheckable();
        boolean informative = !text.isBlank() || !description.isBlank() || !viewId.isBlank();
        if (visible && validBounds && (actionable || informative)) {
            Rect clipped = new Rect(
                    Math.max(0, bounds.left), Math.max(0, bounds.top),
                    Math.min(screenWidth, bounds.right), Math.min(screenHeight, bounds.bottom));
            if (clipped.width() > 3 && clipped.height() > 3) {
                output.add(new UiElementRef(clipped, viewId, text, description, className,
                        node.isClickable(), node.isLongClickable(), editable,
                        node.isScrollable(), node.isCheckable()));
            }
        }

        for (int i = 0; i < node.getChildCount()
                && visited[0] < MAX_VISITED_NODES; i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child == null) continue;
            try {
                collectElements(child, depth + 1, screenWidth, screenHeight, output, visited);
            } finally {
                child.recycle();
            }
        }
    }

    private static boolean supportsAction(AccessibilityNodeInfo node, int wanted) {
        for (AccessibilityNodeInfo.AccessibilityAction action : node.getActionList()) {
            if (action.getId() == wanted) return true;
        }
        return false;
    }

    private static ResolvedElement resolveElement(int elementId) {
        UiSnapshot snapshot = lastSnapshot;
        JarvisAccessibilityService service = instance;
        if (service == null || snapshot == null || elementId < 0
                || elementId >= snapshot.elements.size()) return null;
        if (System.currentTimeMillis() - snapshot.capturedAt > SNAPSHOT_MAX_AGE_MS) {
            lastSnapshot = null;
            return null;
        }
        AccessibilityNodeInfo root = service.getRootInActiveWindow();
        if (root == null) return null;
        try {
            if (!snapshot.packageName.equals(safe(root.getPackageName()))) {
                lastSnapshot = null;
                return null;
            }
            UiElementRef reference = snapshot.elements.get(elementId);
            BestNode best = new BestNode();
            int[] visited = {0};
            findBestNode(root, reference, 0, visited, best);
            if (best.node == null || best.score < 70) {
                if (best.node != null) best.node.recycle();
                return null;
            }
            return new ResolvedElement(best.node, reference, new Rect(best.bounds));
        } finally {
            root.recycle();
        }
    }

    @SuppressWarnings("deprecation")
    private static void findBestNode(AccessibilityNodeInfo node, UiElementRef reference,
                                     int depth, int[] visited, BestNode best) {
        if (node == null || depth > MAX_TREE_DEPTH || visited[0] >= MAX_VISITED_NODES) return;
        visited[0]++;
        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);
        int score = matchScore(node, bounds, reference);
        if (score > best.score) {
            if (best.node != null) best.node.recycle();
            best.node = AccessibilityNodeInfo.obtain(node);
            best.score = score;
            best.bounds.set(bounds);
        }
        for (int i = 0; i < node.getChildCount()
                && visited[0] < MAX_VISITED_NODES; i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child == null) continue;
            try {
                findBestNode(child, reference, depth + 1, visited, best);
            } finally {
                child.recycle();
            }
        }
    }

    private static int matchScore(AccessibilityNodeInfo node, Rect bounds, UiElementRef reference) {
        int score = 0;
        String currentViewId = safe(node.getViewIdResourceName());
        String currentText = node.isPassword() ? "password field" : safe(node.getText());
        String currentDescription = node.isPassword() ? "" : safe(node.getContentDescription());
        String currentClass = safe(node.getClassName());
        if (!reference.viewId.isBlank()) {
            score += reference.viewId.equals(currentViewId) ? 180 : -140;
        }
        if (!reference.text.isBlank()) {
            score += reference.text.equals(currentText) ? 70 : -80;
        }
        if (!reference.description.isBlank()) {
            score += reference.description.equals(currentDescription) ? 70 : -80;
        }
        if (!reference.className.isBlank() && reference.className.equals(currentClass)) score += 25;
        if (reference.bounds.equals(bounds)) {
            score += 110;
        } else {
            int dx = reference.centerX() - bounds.centerX();
            int dy = reference.centerY() - bounds.centerY();
            int distanceSquared = dx * dx + dy * dy;
            if (distanceSquared <= 16 * 16) score += 75;
            else if (distanceSquared <= 64 * 64) score += 35;
        }
        if (reference.clickable == node.isClickable()) score += 8;
        if (reference.editable == node.isEditable()) score += 8;
        if (reference.scrollable == node.isScrollable()) score += 8;
        return score;
    }

    private static boolean clickNodeOrAncestor(AccessibilityNodeInfo node) {
        return performOnNodeOrAncestor(node, AccessibilityNodeInfo.ACTION_CLICK, false);
    }

    @SuppressWarnings("deprecation")
    private static boolean performOnNodeOrAncestor(AccessibilityNodeInfo node, int action,
                                                   boolean requireScrollable) {
        AccessibilityNodeInfo current = AccessibilityNodeInfo.obtain(node);
        try {
            while (current != null) {
                if ((!requireScrollable || current.isScrollable())
                        && current.performAction(action)) return true;
                AccessibilityNodeInfo parent = current.getParent();
                current.recycle();
                current = parent;
            }
            return false;
        } finally {
            if (current != null) current.recycle();
        }
    }

    private static ElementActionResult resolutionFailure(int elementId) {
        return ElementActionResult.failure("Element " + elementId
                + " is missing, stale, or belongs to a different screen. Request 'screenshot' "
                + "again and use an ID from the new MOBILE UI ELEMENTS list; do not guess coordinates.");
    }

    private static boolean swipeWithin(Rect bounds, String direction) {
        if (bounds.width() < 20 || bounds.height() < 20) return false;
        int left = bounds.left + Math.max(4, bounds.width() / 5);
        int right = bounds.right - Math.max(4, bounds.width() / 5);
        int top = bounds.top + Math.max(4, bounds.height() / 5);
        int bottom = bounds.bottom - Math.max(4, bounds.height() / 5);
        int cx = bounds.centerX();
        int cy = bounds.centerY();
        Path path = new Path();
        switch (direction.toLowerCase(Locale.ROOT)) {
            case "up":
                path.moveTo(cx, bottom); path.lineTo(cx, top); break;
            case "down":
                path.moveTo(cx, top); path.lineTo(cx, bottom); break;
            case "left":
                path.moveTo(right, cy); path.lineTo(left, cy); break;
            case "right":
                path.moveTo(left, cy); path.lineTo(right, cy); break;
            default:
                return false;
        }
        return gesture(path, 360);
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
            @Override public void onCancelled(GestureDescription gestureDescription) {
                done.countDown();
            }
        }, null);
        if (!accepted) return false;
        try {
            done.await(2, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        return success[0];
    }

    private static ScreenshotCapture compressScreenshot(Bitmap source) {
        int sourceWidth = source.getWidth();
        int sourceHeight = source.getHeight();
        int[] maxDimensions = {960, 720, 540, 405, 300, 240, 180};
        int[] qualities = {62, 56, 50, 44, 36, 28, 20};
        for (int i = 0; i < maxDimensions.length; i++) {
            double scale = Math.min(1.0, maxDimensions[i]
                    / (double) Math.max(sourceWidth, sourceHeight));
            int width = Math.max(1, (int) Math.round(sourceWidth * scale));
            int height = Math.max(1, (int) Math.round(sourceHeight * scale));
            Bitmap preview = width == sourceWidth && height == sourceHeight
                    ? source : Bitmap.createScaledBitmap(source, width, height, true);
            try {
                ByteArrayOutputStream output = new ByteArrayOutputStream(MAX_SCREENSHOT_BYTES);
                if (preview.compress(Bitmap.CompressFormat.JPEG, qualities[i], output)) {
                    byte[] jpeg = output.toByteArray();
                    if (jpeg.length <= MAX_SCREENSHOT_BYTES) {
                        return new ScreenshotCapture(jpeg, sourceWidth, sourceHeight,
                                width, height, null);
                    }
                }
            } finally {
                if (preview != source) preview.recycle();
            }
        }
        return ScreenshotCapture.failure("The screen preview could not be reduced to the secure transfer limit.");
    }

    private static String safe(CharSequence value) {
        return value == null ? "" : value.toString().trim();
    }

    private static String compact(String value, int limit) {
        String clean = value == null ? "" : value.replaceAll("[\\r\\n\\t]+", " ")
                .replace('"', '\'').replaceAll("\\s+", " ").trim();
        return clean.length() <= limit ? clean : clean.substring(0, Math.max(0, limit - 3)) + "...";
    }
}
