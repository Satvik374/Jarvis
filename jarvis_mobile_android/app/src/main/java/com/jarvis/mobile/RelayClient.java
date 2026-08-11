package com.jarvis.mobile;

import android.net.Uri;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;

/** Thin HTTP client for the relay_server FastAPI API. */
final class RelayClient {
    private final String endpoint;

    RelayClient(String endpoint) {
        this.endpoint = normalizeEndpoint(endpoint);
    }

    static String normalizeEndpoint(String raw) {
        String value = raw == null ? "" : raw.trim().replaceAll("/+$", "");
        Uri uri = Uri.parse(value);
        String host = uri.getHost() == null ? "" : uri.getHost().toLowerCase(Locale.ROOT);
        boolean local = host.equals("localhost") || host.equals("127.0.0.1") || host.equals("::1");
        if ((!"https".equalsIgnoreCase(uri.getScheme()) && !(local && "http".equalsIgnoreCase(uri.getScheme())))
                || host.isEmpty()) {
            throw new IllegalArgumentException("Relay URL must be https:// (http is allowed only for localhost)");
        }
        return value;
    }

    PairingRecord claim(String code, String phoneName) throws Exception {
        Protocol.Identity identity = Protocol.Identity.create();
        JSONObject body = new JSONObject()
                .put("code", code.trim().toUpperCase(Locale.ROOT))
                .put("name", phoneName)
                .put("kx_public", identity.kxPublic)
                .put("sign_public", identity.signPublic);
        JSONObject response = request("POST", "/v1/pairings/claim", body, 15);
        JSONObject controller = response.getJSONObject("controller");
        String pairId = response.getString("pair_id");
        byte[] secret = Protocol.deriveSecret(identity.kxPrivate, controller.getString("kx_public"), pairId);
        return new PairingRecord(endpoint, pairId, phoneName, controller.getString("name"), Protocol.b64(secret),
                identity.signPrivate, controller.getString("sign_public"), false, 0);
    }

    void send(PairingRecord pairing, JSONObject payload) throws Exception {
        if (!pairing.trusted) throw new SecurityException("Pairing must be trusted before the phone agent starts");
        JSONObject envelope = Protocol.encrypt(pairing.secretBytes(), pairing.signingPrivate, pairing.pairId,
                "agent", "controller", payload);
        request("POST", "/v1/pairs/" + Uri.encode(pairing.pairId) + "/messages",
                new JSONObject().put("sender", "agent").put("envelope", envelope), 20);
    }

    List<JSONObject> receive(PairingRecord pairing, int timeoutSeconds) throws Exception {
        if (!pairing.trusted) throw new SecurityException("Pairing must be trusted before polling");
        int timeout = Math.max(0, Math.min(30, timeoutSeconds));
        String path = "/v1/pairs/" + Uri.encode(pairing.pairId) + "/messages?recipient=agent&after="
                + pairing.receivedSequence + "&timeout=" + timeout;
        JSONObject response = request("GET", path, null, timeout + 15);
        JSONArray messages = response.optJSONArray("messages");
        if (messages == null) return Collections.emptyList();
        List<JSONObject> ordered = new ArrayList<>();
        for (int i = 0; i < messages.length(); i++) ordered.add(messages.getJSONObject(i));
        ordered.sort(Comparator.comparingLong(value -> value.optLong("sequence", 0)));
        List<JSONObject> decrypted = new ArrayList<>();
        long highest = pairing.receivedSequence;
        for (JSONObject message : ordered) {
            long sequence = message.optLong("sequence", 0);
            if (sequence <= pairing.receivedSequence) continue;
            highest = Math.max(highest, sequence);
            if (!"controller".equals(message.optString("sender"))
                    || !"agent".equals(message.optString("recipient"))) continue;
            try {
                decrypted.add(Protocol.decrypt(pairing.secretBytes(), pairing.peerSigningPublic, pairing.pairId,
                        "controller", "agent", message));
            } catch (Exception ignored) {
                // Advance past an invalid/replayed packet. It never reaches the executor.
            }
        }
        pairing.receivedSequence = highest;
        return decrypted;
    }

    private JSONObject request(String method, String path, JSONObject body, int timeoutSeconds) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(endpoint + path).openConnection();
        connection.setRequestMethod(method);
        connection.setConnectTimeout(8_000);
        connection.setReadTimeout(Math.max(10_000, timeoutSeconds * 1_000));
        connection.setRequestProperty("Accept", "application/json");
        if (body != null) {
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            try (BufferedWriter writer = new BufferedWriter(new OutputStreamWriter(
                    connection.getOutputStream(), StandardCharsets.UTF_8))) {
                writer.write(body.toString());
            }
        }
        int status = connection.getResponseCode();
        InputStream stream = status >= 200 && status < 300 ? connection.getInputStream() : connection.getErrorStream();
        String response = read(stream);
        connection.disconnect();
        if (status < 200 || status >= 300) {
            String detail = "";
            try { detail = new JSONObject(response).optString("detail", response); } catch (Exception ignored) { }
            throw new IllegalStateException("Relay returned " + status + ": " + detail);
        }
        return new JSONObject(response);
    }

    private static String read(InputStream stream) throws Exception {
        if (stream == null) return "";
        StringBuilder out = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) out.append(line);
        }
        return out.toString();
    }
}
