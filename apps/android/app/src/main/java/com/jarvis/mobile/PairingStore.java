package com.jarvis.mobile;

import android.content.Context;
import android.content.SharedPreferences;

import androidx.security.crypto.EncryptedSharedPreferences;
import androidx.security.crypto.MasterKey;

import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

/** Encrypts local private keys using the Android Keystore-backed master key. */
final class PairingStore {
    private static final String FILE = "jarvis_remote_pairing";
    private static final String RECORD = "record";
    private final SharedPreferences prefs;

    PairingStore(Context context) throws Exception {
        MasterKey key = new MasterKey.Builder(context)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build();
        prefs = EncryptedSharedPreferences.create(context, FILE, key,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM);
    }

    PairingStore(SharedPreferences prefs) { this.prefs = prefs; }

    boolean alreadyProcessed(String pairId, String taskId) {
        if (taskId == null || taskId.isBlank()) return true;
        // Separate from the pairing record so UI saves cannot erase task history.
        synchronized (PairingStore.class) {
            String key = "processed_tasks:" + pairId;
            Set<String> ids = new HashSet<>(prefs.getStringSet(key, Collections.emptySet()));
            if (!ids.add(taskId)) return true;
            // Signed tasks have no expiry; never evict IDs while the pairing can be used.
            if (!prefs.edit().putStringSet(key, ids).commit()) {
                throw new IllegalStateException("Could not persist remote task replay protection");
            }
            return false;
        }
    }

    PairingRecord load() throws Exception {
        String raw = prefs.getString(RECORD, null);
        return raw == null ? null : PairingRecord.fromJson(raw);
    }

    void save(PairingRecord record) throws Exception {
        prefs.edit().putString(RECORD, record.toJson().toString()).apply();
    }

    void clear() { prefs.edit().remove(RECORD).apply(); }
}
