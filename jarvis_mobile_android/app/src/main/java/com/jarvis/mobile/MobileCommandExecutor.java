package com.jarvis.mobile;

import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;

import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The first mobile task dialect: a small, auditable command surface emitted by
 * the computer Jarvis in a normal remote_task. Examples are shown in the app.
 */
final class MobileCommandExecutor {
    static final class Result {
        final boolean ok;
        final String message;
        Result(boolean ok, String message) { this.ok = ok; this.message = message; }
    }

    private static final Pattern TWO_NUMBERS = Pattern.compile("(-?\\d+)\\s+(-?\\d+)");
    private static final Pattern FOUR_NUMBERS = Pattern.compile("(-?\\d+)\\s+(-?\\d+)\\s+(-?\\d+)\\s+(-?\\d+)");
    private final Context context;

    MobileCommandExecutor(Context context) { this.context = context.getApplicationContext(); }

    Result execute(String task) {
        String command = task == null ? "" : task.trim();
        if (command.toLowerCase(Locale.ROOT).startsWith("mobile:")) command = command.substring(7).trim();
        String lower = command.toLowerCase(Locale.ROOT);
        if (lower.startsWith("open ") || lower.startsWith("launch ")) return open(command.substring(command.indexOf(' ') + 1).trim());
        if (lower.startsWith("tap ")) return tap(command.substring(4));
        if (lower.startsWith("swipe ")) return swipe(command.substring(6));
        if (lower.startsWith("type ")) return type(command.substring(5));
        if (lower.equals("back")) return action(JarvisAccessibilityService.back(), "Back pressed");
        if (lower.equals("home")) return action(JarvisAccessibilityService.home(), "Home opened");
        return new Result(false, "Unsupported mobile task. Use: open <package>, tap x y, swipe x1 y1 x2 y2, type <text>, back, or home.");
    }

    private Result open(String packageName) {
        if (packageName.isBlank()) return new Result(false, "An Android package name is required, for example com.android.settings.");
        Intent launch = context.getPackageManager().getLaunchIntentForPackage(packageName);
        if (launch == null) return new Result(false, "No launchable installed app named " + packageName + ".");
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            context.startActivity(launch);
            return new Result(true, "Opened " + packageName + ".");
        } catch (Exception e) {
            return new Result(false, "Could not open " + packageName + ": " + e.getMessage());
        }
    }

    private Result tap(String source) {
        Matcher match = TWO_NUMBERS.matcher(source.trim());
        if (!match.matches()) return new Result(false, "Tap format is: tap <x> <y>.");
        return action(JarvisAccessibilityService.tap(number(match, 1), number(match, 2)), "Tapped.");
    }

    private Result swipe(String source) {
        Matcher match = FOUR_NUMBERS.matcher(source.trim());
        if (!match.matches()) return new Result(false, "Swipe format is: swipe <x1> <y1> <x2> <y2>.");
        return action(JarvisAccessibilityService.swipe(number(match, 1), number(match, 2), number(match, 3), number(match, 4)), "Swiped.");
    }

    private Result type(String text) {
        if (text.isBlank()) return new Result(false, "Type needs text.");
        return action(JarvisAccessibilityService.type(text), "Typed text into the focused field.");
    }

    private Result action(boolean ok, String success) {
        return ok ? new Result(true, success) : new Result(false, "Android accessibility control is not enabled or the target action was rejected.");
    }

    private static int number(Matcher match, int group) { return Integer.parseInt(match.group(group)); }
}
