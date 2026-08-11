package com.jarvis.mobile;

import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Arrays;
import java.util.Locale;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

import org.bouncycastle.crypto.InvalidCipherTextException;
import org.bouncycastle.crypto.agreement.X25519Agreement;
import org.bouncycastle.crypto.modes.ChaCha20Poly1305;
import org.bouncycastle.crypto.params.AEADParameters;
import org.bouncycastle.crypto.params.Ed25519PrivateKeyParameters;
import org.bouncycastle.crypto.params.Ed25519PublicKeyParameters;
import org.bouncycastle.crypto.params.KeyParameter;
import org.bouncycastle.crypto.params.X25519PrivateKeyParameters;
import org.bouncycastle.crypto.params.X25519PublicKeyParameters;
import org.bouncycastle.crypto.signers.Ed25519Signer;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Android implementation of jarvis.remote's jarvis-remote-v1 envelope.
 *
 * The relay is untrusted: it only sees public keys and signed encrypted
 * packets. Keep the protocol strings and canonical byte representation in
 * exact lockstep with jarvis/remote.py and relay_server/main.py.
 */
final class Protocol {
    static final String VERSION = "jarvis-remote-v1";
    private static final SecureRandom RANDOM = new SecureRandom();

    private Protocol() {}

    static final class Identity {
        final String kxPrivate;
        final String kxPublic;
        final String signPrivate;
        final String signPublic;

        Identity(String kxPrivate, String kxPublic, String signPrivate, String signPublic) {
            this.kxPrivate = kxPrivate;
            this.kxPublic = kxPublic;
            this.signPrivate = signPrivate;
            this.signPublic = signPublic;
        }

        static Identity create() {
            X25519PrivateKeyParameters kx = new X25519PrivateKeyParameters(RANDOM);
            Ed25519PrivateKeyParameters signing = new Ed25519PrivateKeyParameters(RANDOM);
            return new Identity(
                    b64(kx.getEncoded()), b64(kx.generatePublicKey().getEncoded()),
                    b64(signing.getEncoded()), b64(signing.generatePublicKey().getEncoded()));
        }
    }

    static String b64(byte[] value) {
        return Base64.encodeToString(value, Base64.URL_SAFE | Base64.NO_WRAP);
    }

    static byte[] unb64(String value) {
        return Base64.decode(value, Base64.URL_SAFE | Base64.NO_WRAP);
    }

    static byte[] deriveSecret(String privateKey, String peerPublicKey, String pairId) throws Exception {
        X25519PrivateKeyParameters privateParams = new X25519PrivateKeyParameters(unb64(privateKey), 0);
        X25519PublicKeyParameters peerParams = new X25519PublicKeyParameters(unb64(peerPublicKey), 0);
        X25519Agreement agreement = new X25519Agreement();
        agreement.init(privateParams);
        byte[] shared = new byte[agreement.getAgreementSize()];
        agreement.calculateAgreement(peerParams, shared, 0);
        return hkdfSha256(shared, (VERSION + ":" + pairId).getBytes(StandardCharsets.UTF_8));
    }

    static String fingerprint(byte[] secret) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        byte[] prefix = "Jarvis Remote fingerprint\0".getBytes(StandardCharsets.UTF_8);
        digest.update(prefix);
        String hex = hex(digest.digest(secret)).substring(0, 20).toUpperCase(Locale.ROOT);
        return hex.substring(0, 4) + "-" + hex.substring(4, 8) + "-" + hex.substring(8, 12)
                + "-" + hex.substring(12, 16) + "-" + hex.substring(16, 20);
    }

    static JSONObject encrypt(byte[] secret, String signingPrivate, String pairId,
                              String sender, String recipient, JSONObject payload) throws Exception {
        byte[] nonce = new byte[12];
        RANDOM.nextBytes(nonce);
        byte[] ciphertext = crypt(true, secret, nonce, aad(pairId, sender, recipient),
                payload.toString().getBytes(StandardCharsets.UTF_8));
        String nonce64 = b64(nonce);
        String ciphertext64 = b64(ciphertext);
        Ed25519Signer signer = new Ed25519Signer();
        signer.init(true, new Ed25519PrivateKeyParameters(unb64(signingPrivate), 0));
        byte[] signed = canonical(pairId, sender, recipient, nonce64, ciphertext64);
        signer.update(signed, 0, signed.length);
        return new JSONObject()
                .put("nonce", nonce64)
                .put("ciphertext", ciphertext64)
                .put("signature", b64(signer.generateSignature()));
    }

    static JSONObject decrypt(byte[] secret, String peerSigningPublic, String pairId,
                              String sender, String recipient, JSONObject envelope) throws Exception {
        String nonce64 = envelope.getString("nonce");
        String ciphertext64 = envelope.getString("ciphertext");
        String signature64 = envelope.getString("signature");
        Ed25519Signer verifier = new Ed25519Signer();
        verifier.init(false, new Ed25519PublicKeyParameters(unb64(peerSigningPublic), 0));
        byte[] signed = canonical(pairId, sender, recipient, nonce64, ciphertext64);
        verifier.update(signed, 0, signed.length);
        if (!verifier.verifySignature(unb64(signature64))) {
            throw new SecurityException("Remote message signature is invalid");
        }
        byte[] plain = crypt(false, secret, unb64(nonce64), aad(pairId, sender, recipient),
                unb64(ciphertext64));
        return new JSONObject(new String(plain, StandardCharsets.UTF_8));
    }

    private static byte[] crypt(boolean encrypt, byte[] key, byte[] nonce, byte[] aad, byte[] input)
            throws InvalidCipherTextException {
        ChaCha20Poly1305 cipher = new ChaCha20Poly1305();
        cipher.init(encrypt, new AEADParameters(new KeyParameter(key), 128, nonce, aad));
        byte[] output = new byte[cipher.getOutputSize(input.length)];
        int written = cipher.processBytes(input, 0, input.length, output, 0);
        written += cipher.doFinal(output, written);
        return Arrays.copyOf(output, written);
    }

    private static byte[] hkdfSha256(byte[] ikm, byte[] info) throws Exception {
        byte[] salt = new byte[32]; // cryptography.HKDF(salt=None) uses hash-length zero bytes.
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(salt, "HmacSHA256"));
        byte[] prk = mac.doFinal(ikm);
        mac.init(new SecretKeySpec(prk, "HmacSHA256"));
        mac.update(info);
        mac.update((byte) 1);
        return mac.doFinal();
    }

    private static byte[] canonical(String pairId, String sender, String recipient,
                                    String nonce, String ciphertext) {
        return (VERSION + "|" + pairId + "|" + sender + "|" + recipient + "|"
                + nonce + "|" + ciphertext).getBytes(StandardCharsets.UTF_8);
    }

    private static byte[] aad(String pairId, String sender, String recipient) {
        return (VERSION + "|" + pairId + "|" + sender + "|" + recipient)
                .getBytes(StandardCharsets.UTF_8);
    }

    private static String hex(byte[] value) {
        StringBuilder out = new StringBuilder(value.length * 2);
        for (byte b : value) out.append(String.format("%02x", b));
        return out.toString();
    }
}
