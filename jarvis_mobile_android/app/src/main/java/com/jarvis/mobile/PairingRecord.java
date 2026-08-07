package com.jarvis.mobile;

import org.json.JSONException;
import org.json.JSONObject;

/** Private pairing material stored only on this Android device. */
final class PairingRecord {
    final String endpoint;
    final String pairId;
    final String localName;
    final String peerName;
    final String secret;
    final String signingPrivate;
    final String peerSigningPublic;
    boolean trusted;
    long receivedSequence;

    PairingRecord(String endpoint, String pairId, String localName, String peerName, String secret,
                  String signingPrivate, String peerSigningPublic, boolean trusted, long receivedSequence) {
        this.endpoint = endpoint;
        this.pairId = pairId;
        this.localName = localName;
        this.peerName = peerName;
        this.secret = secret;
        this.signingPrivate = signingPrivate;
        this.peerSigningPublic = peerSigningPublic;
        this.trusted = trusted;
        this.receivedSequence = receivedSequence;
    }

    byte[] secretBytes() { return Protocol.unb64(secret); }

    String fingerprint() throws Exception { return Protocol.fingerprint(secretBytes()); }

    JSONObject toJson() throws JSONException {
        return new JSONObject()
                .put("endpoint", endpoint).put("pairId", pairId).put("localName", localName)
                .put("peerName", peerName).put("secret", secret).put("signingPrivate", signingPrivate)
                .put("peerSigningPublic", peerSigningPublic).put("trusted", trusted)
                .put("receivedSequence", receivedSequence);
    }

    static PairingRecord fromJson(String raw) throws JSONException {
        JSONObject json = new JSONObject(raw);
        return new PairingRecord(json.getString("endpoint"), json.getString("pairId"),
                json.getString("localName"), json.getString("peerName"), json.getString("secret"),
                json.getString("signingPrivate"), json.getString("peerSigningPublic"),
                json.optBoolean("trusted", false), json.optLong("receivedSequence", 0));
    }
}
