package com.jarvis.mobile;

import android.content.SharedPreferences;

import org.junit.Test;

import java.lang.reflect.Proxy;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

public final class PairingStoreTest {
    @Test public void replayHistorySurvivesLaterTasksAndNewStore() throws Exception {
        Map<String, Set<String>> disk = new HashMap<>();
        PairingStore store = new PairingStore(preferences(disk, true));
        assertFalse(store.alreadyProcessed("pair", "original"));
        for (int i = 0; i < 201; i++) {
            assertFalse(store.alreadyProcessed("pair", "later-" + i));
        }
        assertTrue(store.alreadyProcessed("pair", "original"));
        PairingStore restarted = new PairingStore(preferences(disk, true));
        assertTrue(restarted.alreadyProcessed("pair", "original"));
        assertFalse(restarted.alreadyProcessed("other-pair", "original"));
        assertTrue(restarted.alreadyProcessed("pair", " "));
    }

    @Test public void failedPersistenceDoesNotAuthorizeExecution() throws Exception {
        Map<String, Set<String>> disk = new HashMap<>();
        PairingStore store = new PairingStore(preferences(disk, false));
        try {
            store.alreadyProcessed("pair", "original");
            fail("A failed durable write must abort the task");
        } catch (IllegalStateException expected) {
            assertTrue(disk.isEmpty());
        }
    }

    @SuppressWarnings("unchecked")
    private static SharedPreferences preferences(Map<String, Set<String>> disk, boolean commitOk) {
        return (SharedPreferences) Proxy.newProxyInstance(
                SharedPreferences.class.getClassLoader(), new Class<?>[]{SharedPreferences.class},
                (proxy, method, args) -> {
                    if (method.getName().equals("getStringSet")) {
                        return new HashSet<>(disk.getOrDefault((String) args[0], (Set<String>) args[1]));
                    }
                    if (method.getName().equals("edit")) {
                        Map<String, Set<String>> pending = new HashMap<>();
                        return Proxy.newProxyInstance(SharedPreferences.Editor.class.getClassLoader(),
                                new Class<?>[]{SharedPreferences.Editor.class}, (editor, operation, values) -> {
                                    if (operation.getName().equals("putStringSet")) {
                                        pending.put((String) values[0], new HashSet<>((Set<String>) values[1]));
                                        return editor;
                                    }
                                    if (operation.getName().equals("commit")) {
                                        if (commitOk) disk.putAll(pending);
                                        return commitOk;
                                    }
                                    throw new AssertionError("Unexpected editor method: " + operation.getName());
                                });
                    }
                    throw new AssertionError("Unexpected preferences method: " + method.getName());
                });
    }
}
