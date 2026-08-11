package com.jarvis.mobile;

import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.net.Uri;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Executes the deliberately small command surface accepted by the phone agent. */
final class MobileCommandExecutor {
    static final class Result {
        final boolean ok;
        final String message;
        final JarvisAccessibilityService.ScreenshotCapture screenshot;
        Result(boolean ok, String message) { this(ok, message, null); }
        Result(boolean ok, String message, JarvisAccessibilityService.ScreenshotCapture screenshot) {
            this.ok = ok;
            this.message = message;
            this.screenshot = screenshot;
        }
    }

    static final String[] CAPABILITIES = {
            "open <app or URL>", "screenshot (returns exact element IDs)",
            "tap element <id>", "long press element <id>",
            "type element <id> <text>",
            "scroll element <id> <forward|backward>",
            "swipe element <id> <up|down|left|right>",
            "tap x y (fallback)", "swipe x1 y1 x2 y2 (fallback)",
            "type <text> (focused fallback)", "back", "home", "capabilities"
    };

    private static final Pattern TWO_NUMBERS = Pattern.compile("(-?\\d+)\\s+(-?\\d+)");
    private static final Pattern FOUR_NUMBERS = Pattern.compile("(-?\\d+)\\s+(-?\\d+)\\s+(-?\\d+)\\s+(-?\\d+)");
    private static final Pattern ELEMENT = Pattern.compile("(?i)^(?:element\\s+|#)(\\d+)$");
    private static final Pattern ELEMENT_WITH_TEXT = Pattern.compile(
            "(?is)^(?:element\\s+|#)(\\d+)\\s+(.+)$");
    private static final Pattern ELEMENT_WITH_DIRECTION = Pattern.compile(
            "(?i)^(?:element\\s+|#)(\\d+)\\s+(up|down|left|right|forward|backward)$");
    private static final Pattern PACKAGE_NAME = Pattern.compile("[A-Za-z][A-Za-z0-9_]*(?:\\.[A-Za-z0-9_]+)+");
    private static final Pattern WEB_ADDRESS = Pattern.compile(
            "(?i)(https?://[^\\s]+|www\\.[^\\s]+|(?:[a-z0-9-]+\\.)+(?:com|org|net|io|app|dev|co|in|me|tv)(?:/[^\\s]*)?)");

    private static final Map<String, String> APP_ALIASES = new LinkedHashMap<>();
    private static final Map<String, String> WEB_FALLBACKS = new LinkedHashMap<>();
    static {
        APP_ALIASES.put("youtube", "com.google.android.youtube");
        APP_ALIASES.put("chrome", "com.android.chrome");
        APP_ALIASES.put("google chrome", "com.android.chrome");
        APP_ALIASES.put("settings", "com.android.settings");
        APP_ALIASES.put("play store", "com.android.vending");
        APP_ALIASES.put("google play", "com.android.vending");
        APP_ALIASES.put("gmail", "com.google.android.gm");
        APP_ALIASES.put("google maps", "com.google.android.apps.maps");
        APP_ALIASES.put("maps", "com.google.android.apps.maps");
        APP_ALIASES.put("whatsapp", "com.whatsapp");
        APP_ALIASES.put("google photos", "com.google.android.apps.photos");
        APP_ALIASES.put("photos", "com.google.android.apps.photos");

        WEB_FALLBACKS.put("youtube", "https://youtube.com");
        WEB_FALLBACKS.put("chrome", "https://google.com");
        WEB_FALLBACKS.put("google chrome", "https://google.com");
        WEB_FALLBACKS.put("gmail", "https://mail.google.com");
        WEB_FALLBACKS.put("google maps", "https://maps.google.com");
        WEB_FALLBACKS.put("maps", "https://maps.google.com");
    }

    private final Context context;

    MobileCommandExecutor(Context context) { this.context = context.getApplicationContext(); }

    Result execute(String task) {
        String command = task == null ? "" : task.trim();
        if (command.toLowerCase(Locale.ROOT).startsWith("mobile:")) command = command.substring(7).trim();
        String lower = command.toLowerCase(Locale.ROOT);
        if (lower.startsWith("open ") || lower.startsWith("launch ")) {
            return open(command.substring(command.indexOf(' ') + 1).trim());
        }
        if (lower.startsWith("tap ") || lower.startsWith("click ")) {
            return tap(command.substring(command.indexOf(' ') + 1));
        }
        if (lower.startsWith("long press ")) return longPress(command.substring(11));
        if (lower.startsWith("long-press ")) return longPress(command.substring(11));
        if (lower.startsWith("scroll ")) return scroll(command.substring(7));
        if (lower.startsWith("swipe ")) return swipe(command.substring(6));
        if (lower.startsWith("type ")) return type(command.substring(5));
        if (lower.equals("back")) return action(JarvisAccessibilityService.back(), "Back pressed");
        if (lower.equals("home")) return action(JarvisAccessibilityService.home(), "Home opened");
        if (lower.equals("capabilities") || lower.equals("list capabilities")
                || lower.equals("help")) return capabilities();
        if (lower.equals("screenshot") || lower.equals("inspect")
                || lower.equals("inspect screen") || lower.equals("take screenshot")
                || lower.equals("take a screenshot") || lower.equals("get screenshot")
                || lower.equals("show screen") || lower.equals("show me the screen")) {
            return screenshot();
        }
        return new Result(false, "Unsupported mobile task. Use only: "
                + String.join(", ", CAPABILITIES) + ".");
    }

    private Result capabilities() {
        return new Result(true, "Supported mobile commands: " + String.join(", ", CAPABILITIES)
                + ". Accessibility ready: " + JarvisAccessibilityService.isAvailable()
                + ". Screenshot ready: " + JarvisAccessibilityService.canTakeScreenshot() + ".");
    }

    private Result screenshot() {
        JarvisAccessibilityService.ScreenshotCapture capture =
                JarvisAccessibilityService.screenshot();
        if (capture.error != null) return new Result(false, capture.error);
        JarvisAccessibilityService.UiSnapshot snapshot =
                JarvisAccessibilityService.inspectScreen(capture.width, capture.height);
        String grounding = snapshot.error == null ? snapshot.summary
                : "\nExact element grounding unavailable: " + snapshot.error;
        return new Result(true, "Captured the mobile screen at " + capture.width + "x"
                + capture.height + " (secure preview " + capture.previewWidth + "x"
                + capture.previewHeight + ")." + grounding, capture);
    }

    private Result open(String rawTarget) {
        String target = cleanTarget(rawTarget);
        if (target.isBlank()) return new Result(false, "Open needs an app name, package name, or web address.");

        String url = extractWebAddress(target);
        if (url != null) return openUrl(url, null);

        String normalized = normalizeName(target);
        if (normalized.equals("browser") || normalized.equals("web browser") || normalized.equals("internet")) {
            return openBrowserHome(null);
        }

        PackageManager packages = context.getPackageManager();
        if (PACKAGE_NAME.matcher(target).matches()) {
            Result exactPackage = launchPackage(packages, target, target);
            if (exactPackage.ok) return exactPackage;
        }

        String knownPackage = APP_ALIASES.get(normalized);
        if (knownPackage != null) {
            Result known = launchPackage(packages, knownPackage, displayName(normalized));
            if (known.ok) return known;
            String fallback = WEB_FALLBACKS.get(normalized);
            if (fallback != null) {
                Result web = openUrl(fallback, normalized.contains("chrome") ? "Chrome is not installed; " : displayName(normalized) + " is not installed; ");
                if (web.ok) return web;
            }
        }

        ResolveInfo matched = findLauncherByLabel(packages, normalized);
        if (matched != null && matched.activityInfo != null) {
            Intent launch = packages.getLaunchIntentForPackage(matched.activityInfo.packageName);
            if (launch == null) {
                launch = new Intent(Intent.ACTION_MAIN)
                        .addCategory(Intent.CATEGORY_LAUNCHER)
                        .setClassName(matched.activityInfo.packageName, matched.activityInfo.name);
            }
            return start(launch, "Opened " + matched.loadLabel(packages) + ".", "Could not open " + target);
        }

        return new Result(false, "No launchable installed app matches '" + target
                + "'. Try its visible app name, Android package name, or a URL such as https://youtube.com.");
    }

    private Result launchPackage(PackageManager packages, String packageName, String label) {
        Intent launch = packages.getLaunchIntentForPackage(packageName);
        if (launch == null) return new Result(false, "Package " + packageName + " is not launchable.");
        return start(launch, "Opened " + label + ".", "Could not open " + label);
    }

    private Result openUrl(String url, String prefix) {
        Intent view = new Intent(Intent.ACTION_VIEW, Uri.parse(url)).addCategory(Intent.CATEGORY_BROWSABLE);
        String success = (prefix == null ? "" : prefix) + "opened " + url + " in the browser.";
        return start(view, capitalize(success), "No browser could open " + url);
    }

    private Result openBrowserHome(String prefix) {
        Intent browser = Intent.makeMainSelectorActivity(Intent.ACTION_MAIN, Intent.CATEGORY_APP_BROWSER);
        String success = (prefix == null ? "" : prefix) + "Opened the default browser.";
        Result result = start(browser, success, "No launchable browser is installed");
        if (result.ok) return result;
        return openUrl("https://google.com", prefix);
    }

    private Result start(Intent intent, String success, String failure) {
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
        try {
            context.startActivity(intent);
            JarvisAccessibilityService.invalidateSnapshot();
            return new Result(true, success);
        } catch (ActivityNotFoundException e) {
            return new Result(false, failure + ".");
        } catch (Exception e) {
            return new Result(false, failure + ": " + safeMessage(e));
        }
    }

    private ResolveInfo findLauncherByLabel(PackageManager packages, String wanted) {
        Intent launcher = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
        List<ResolveInfo> candidates = packages.queryIntentActivities(launcher, PackageManager.MATCH_ALL);
        ResolveInfo best = null;
        int bestScore = 0;
        for (ResolveInfo candidate : candidates) {
            if (candidate.activityInfo == null) continue;
            String label = normalizeName(String.valueOf(candidate.loadLabel(packages)));
            String packageName = candidate.activityInfo.packageName.toLowerCase(Locale.ROOT);
            String packageTail = packageName.substring(packageName.lastIndexOf('.') + 1).replace('_', ' ');
            int score = label.equals(wanted) ? 4 : packageTail.equals(wanted) ? 3
                    : label.startsWith(wanted + " ") || wanted.startsWith(label + " ") ? 2
                    : label.contains(wanted) && wanted.length() >= 4 ? 1 : 0;
            if (score > bestScore) {
                best = candidate;
                bestScore = score;
            }
        }
        return best;
    }

    static String extractWebAddress(String source) {
        Matcher match = WEB_ADDRESS.matcher(source == null ? "" : source);
        if (!match.find()) return null;
        String value = match.group(1).replaceAll("[),.;!?]+$", "");
        if (value.regionMatches(true, 0, "www.", 0, 4)) value = "https://" + value;
        if (!value.toLowerCase(Locale.ROOT).startsWith("http://")
                && !value.toLowerCase(Locale.ROOT).startsWith("https://")) value = "https://" + value;
        return value;
    }

    static String normalizeName(String source) {
        String value = cleanTarget(source).toLowerCase(Locale.ROOT)
                .replaceAll("^(the\\s+)", "")
                .replaceAll("\\s+on\\s+(my|the)\\s+(phone|mobile|device)$", "")
                .replaceAll("\\s+(app|application)$", "")
                .replaceAll("[^a-z0-9]+", " ")
                .trim();
        return value.replaceAll("\\s+", " ");
    }

    private static String cleanTarget(String source) {
        String value = source == null ? "" : source.trim();
        if (value.length() >= 2 && ((value.startsWith("\"") && value.endsWith("\""))
                || (value.startsWith("'") && value.endsWith("'")))) {
            value = value.substring(1, value.length() - 1).trim();
        }
        return value;
    }

    private static String displayName(String normalized) {
        if (normalized.isBlank()) return "app";
        return capitalize(normalized);
    }

    private static String capitalize(String value) {
        if (value == null || value.isBlank()) return "";
        return Character.toUpperCase(value.charAt(0)) + value.substring(1);
    }

    private static String safeMessage(Exception exception) {
        return exception.getMessage() == null ? exception.getClass().getSimpleName() : exception.getMessage();
    }

    private Result tap(String source) {
        Matcher element = ELEMENT.matcher(source.trim());
        if (element.matches()) {
            return elementAction(JarvisAccessibilityService.tapElement(number(element, 1)));
        }
        Matcher match = TWO_NUMBERS.matcher(source.trim());
        if (!match.matches()) return new Result(false, "Tap format is: tap element <id>. "
                + "Request a screenshot to get exact IDs. Raw tap <x> <y> is fallback only.");
        return action(JarvisAccessibilityService.tap(number(match, 1), number(match, 2)),
                "Tapped raw coordinates as a fallback; request a screenshot and prefer an element ID next time.");
    }

    private Result longPress(String source) {
        Matcher element = ELEMENT.matcher(source.trim());
        if (!element.matches()) return new Result(false,
                "Long-press format is: long press element <id>.");
        return elementAction(JarvisAccessibilityService.longPressElement(number(element, 1)));
    }

    private Result scroll(String source) {
        Matcher match = ELEMENT_WITH_DIRECTION.matcher(source.trim());
        if (!match.matches()) return new Result(false,
                "Scroll format is: scroll element <id> <forward|backward>.");
        String direction = match.group(2).toLowerCase(Locale.ROOT);
        boolean forward = direction.equals("forward") || direction.equals("down");
        if (!(forward || direction.equals("backward") || direction.equals("up"))) {
            return new Result(false, "Scroll direction must be forward or backward.");
        }
        return elementAction(JarvisAccessibilityService.scrollElement(number(match, 1), forward));
    }

    private Result swipe(String source) {
        Matcher element = ELEMENT_WITH_DIRECTION.matcher(source.trim());
        if (element.matches()) {
            String direction = element.group(2).toLowerCase(Locale.ROOT);
            if (direction.equals("forward")) direction = "up";
            if (direction.equals("backward")) direction = "down";
            return elementAction(JarvisAccessibilityService.swipeElement(
                    number(element, 1), direction));
        }
        Matcher match = FOUR_NUMBERS.matcher(source.trim());
        if (!match.matches()) return new Result(false,
                "Swipe format is: swipe element <id> <up|down|left|right>. "
                        + "Raw swipe x1 y1 x2 y2 is fallback only.");
        return action(JarvisAccessibilityService.swipe(number(match, 1), number(match, 2),
                        number(match, 3), number(match, 4)),
                "Swiped raw coordinates as a fallback; prefer an element ID from a fresh screenshot.");
    }

    private Result type(String text) {
        Matcher element = ELEMENT_WITH_TEXT.matcher(text.trim());
        if (element.matches()) {
            return elementAction(JarvisAccessibilityService.typeElement(
                    number(element, 1), element.group(2)));
        }
        if (text.isBlank()) return new Result(false, "Type needs text.");
        return action(JarvisAccessibilityService.type(text),
                "Typed into the currently focused field as a fallback; prefer type element <id> <text>.");
    }

    private Result elementAction(JarvisAccessibilityService.ElementActionResult result) {
        return new Result(result.ok, result.message);
    }

    private Result action(boolean ok, String success) {
        return ok ? new Result(true, success) : new Result(false, "Android accessibility control is not enabled or the target action was rejected.");
    }

    private static int number(Matcher match, int group) { return Integer.parseInt(match.group(group)); }
}
