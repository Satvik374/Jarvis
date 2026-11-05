package com.jarvis.mobile;

import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioTrack;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Plays Gemini TTS's signed-16-bit mono PCM returned by the Mobile gateway. */
final class PcmAudioPlayer {
    private static final Object LOCK = new Object();
    private static final ExecutorService PLAYBACK = Executors.newSingleThreadExecutor();
    private static AudioTrack activeTrack;

    private PcmAudioPlayer() { }

    static void playAsync(byte[] pcm, int sampleRate) {
        if (pcm == null || pcm.length == 0) return;
        PLAYBACK.execute(() -> play(pcm, sampleRate));
    }

    static void play(byte[] pcm, int sampleRate) {
        if (pcm == null || pcm.length == 0) return;
        int rate = Math.max(8_000, Math.min(48_000, sampleRate));
        stop();
        int minBuffer = AudioTrack.getMinBufferSize(rate, AudioFormat.CHANNEL_OUT_MONO,
                AudioFormat.ENCODING_PCM_16BIT);
        if (minBuffer <= 0) return;
        AudioTrack track = new AudioTrack.Builder()
                .setAudioAttributes(new AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ASSISTANT)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build())
                .setAudioFormat(new AudioFormat.Builder()
                        .setSampleRate(rate)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .build())
                .setBufferSizeInBytes(Math.max(minBuffer, Math.min(pcm.length, rate * 2)))
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build();
        synchronized (LOCK) { activeTrack = track; }
        try {
            track.play();
            track.write(pcm, 0, pcm.length, AudioTrack.WRITE_BLOCKING);
            // The track is streaming. Keep it alive for the small remainder
            // after write() has queued the PCM, but remain interruptible.
            long durationMs = Math.max(80L, (pcm.length * 1_000L) / (rate * 2L));
            Thread.sleep(durationMs + 80L);
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        } finally {
            synchronized (LOCK) {
                if (activeTrack == track) activeTrack = null;
            }
            try { track.stop(); } catch (Exception ignored) { }
            track.release();
        }
    }

    static void stop() {
        AudioTrack previous;
        synchronized (LOCK) {
            previous = activeTrack;
            activeTrack = null;
        }
        if (previous == null) return;
        try { previous.pause(); previous.flush(); previous.stop(); } catch (Exception ignored) { }
        try { previous.release(); } catch (Exception ignored) { }
    }
}
