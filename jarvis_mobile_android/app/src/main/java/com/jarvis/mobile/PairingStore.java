package com.jarvis.mobile;

import android.content.Context;
import android.content.SharedPreferences;

import androidx.security.crypto.EncryptedSharedPreferences;
import androidx.security.crypto.MasterKey;

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

    PairingRecord load() throws Exception {
        String raw = prefs.getString(RECORD, null);
        return raw == null ? null : PairingRecord.fromJson(raw);
    }

    void save(PairingRecord record) throws Exception {
        prefs.edit().putString(RECORD, record.toJson().toString()).apply();
    }

    void clear() { prefs.edit().remove(RECORD).apply(); }
}
