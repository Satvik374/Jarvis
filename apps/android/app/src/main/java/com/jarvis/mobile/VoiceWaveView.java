package com.jarvis.mobile;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.util.AttributeSet;
import android.view.View;
import android.view.animation.LinearInterpolator;

/** A lightweight blue flowing wave that makes microphone capture unmistakable. */
public final class VoiceWaveView extends View {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path path = new Path();
    private float phase;
    private ValueAnimator animator;

    public VoiceWaveView(Context context) { this(context, null); }
    public VoiceWaveView(Context context, AttributeSet attrs) {
        super(context, attrs);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(dp(3));
        paint.setStrokeCap(Paint.Cap.ROUND);
        paint.setColor(0xFF35C9FF);
        setContentDescription("Jarvis is listening");
    }

    public void startFlow() {
        if (animator != null && animator.isRunning()) return;
        animator = ValueAnimator.ofFloat(0f, 1f);
        animator.setDuration(1_050L);
        animator.setRepeatCount(ValueAnimator.INFINITE);
        animator.setInterpolator(new LinearInterpolator());
        animator.addUpdateListener(value -> {
            phase = (float) value.getAnimatedValue() * ((float) Math.PI * 2f);
            invalidate();
        });
        animator.start();
    }

    public void stopFlow() {
        if (animator != null) animator.cancel();
        animator = null;
        phase = 0f;
        invalidate();
    }

    @Override protected void onDetachedFromWindow() {
        stopFlow();
        super.onDetachedFromWindow();
    }

    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float width = getWidth();
        float center = getHeight() / 2f;
        float amplitude = Math.max(dp(5), getHeight() * .25f);
        path.reset();
        for (float x = 0; x <= width; x += dp(3)) {
            float y = center + (float) Math.sin((x / Math.max(1f, width)) * Math.PI * 5f + phase)
                    * amplitude * (.55f + .45f * (float) Math.sin(phase * .7f + x / width));
            if (x == 0) path.moveTo(x, y); else path.lineTo(x, y);
        }
        canvas.drawPath(path, paint);
    }

    private float dp(float value) { return value * getResources().getDisplayMetrics().density; }
}
