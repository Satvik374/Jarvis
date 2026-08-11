package com.jarvis.mobile;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class MobileCommandExecutorTest {
    @Test public void extractsExplicitHttpsUrlFromNaturalLanguage() {
        assertEquals("https://youtube.com/watch?v=abc",
                MobileCommandExecutor.extractWebAddress("Chrome to https://youtube.com/watch?v=abc"));
    }

    @Test public void normalizesBareWebAddressToHttps() {
        assertEquals("https://youtube.com",
                MobileCommandExecutor.extractWebAddress("youtube.com"));
    }

    @Test public void packageNameIsNotMistakenForAWebAddress() {
        assertNull(MobileCommandExecutor.extractWebAddress("com.android.settings"));
    }

    @Test public void normalizesVisibleAppNameAndDeviceSuffix() {
        assertEquals("youtube", MobileCommandExecutor.normalizeName("The YouTube app on my Mobile"));
    }

    @Test public void normalizesPunctuationAndSpacing() {
        assertEquals("google chrome", MobileCommandExecutor.normalizeName("  Google   Chrome  "));
    }

    @Test public void declaresScreenshotAndCapabilitiesCommands() {
        assertTrue(java.util.Arrays.stream(MobileCommandExecutor.CAPABILITIES)
                .anyMatch(command -> command.startsWith("screenshot")));
        assertTrue(java.util.Arrays.asList(MobileCommandExecutor.CAPABILITIES)
                .contains("capabilities"));
        assertTrue(java.util.Arrays.asList(MobileCommandExecutor.CAPABILITIES)
                .contains("tap element <id>"));
        assertTrue(java.util.Arrays.asList(MobileCommandExecutor.CAPABILITIES)
                .contains("type element <id> <text>"));
        assertTrue(java.util.Arrays.asList(MobileCommandExecutor.CAPABILITIES)
                .contains("scroll element <id> <forward|backward>"));
    }
}
