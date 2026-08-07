/**
 * aurora.js — Reactbits-inspired Aurora background for Jarvis Browser UI
 *
 * Ports the aurora/orb approach from reactbits.dev to vanilla canvas.
 * Slow-drifting radial-gradient blobs in the Jarvis cyan/blue accent palette,
 * composited with "lighter" so they bloom together without washing out
 * the HUD panels above.
 */
(() => {
  "use strict";

  /* -------------------------------------------------------------------------
   * Canvas setup
   * ---------------------------------------------------------------------- */
  const canvas = document.createElement("canvas");
  canvas.id = "auroraCanvas";
  canvas.setAttribute("aria-hidden", "true");
  Object.assign(canvas.style, {
    position: "fixed",
    top: "0",
    left: "0",
    width: "100%",
    height: "100%",
    zIndex: "0",
    pointerEvents: "none",
  });
  // Prepend so it sits behind .noise / .scanlines / .app-shell
  document.body.prepend(canvas);

  const ctx = canvas.getContext("2d");

  /* -------------------------------------------------------------------------
   * Orb definitions
   * Each orb is a soft radial-gradient blob that drifts in a sinusoidal path.
   *
   *  x, y   — normalised anchor (0–1) relative to canvas size
   *  r      — normalised radius (fraction of max(W,H))
   *  color  — R,G,B string — tuned to Jarvis accent palette
   *  speed  — drift speed (ms⁻¹)
   *  phase  — initial phase offset (radians)
   *  ox, oy — orbit half-amplitude (normalised)
   * ---------------------------------------------------------------------- */
  const orbs = [
    // Core cyan — main accent #43c6ff
    { x: 0.52, y: 0.34, r: 0.55, color: "67,198,255",  speed: 0.000165, phase: 0.00, ox: 0.16, oy: 0.11 },
    // Deep blue — secondary fill
    { x: 0.18, y: 0.62, r: 0.48, color: "30,100,255",  speed: 0.000120, phase: 1.57, ox: 0.13, oy: 0.16 },
    // Electric blue — right side
    { x: 0.82, y: 0.68, r: 0.50, color: "38,82,230",   speed: 0.000195, phase: 3.14, ox: 0.15, oy: 0.10 },
    // Violet hint — upper area
    { x: 0.63, y: 0.12, r: 0.42, color: "115,60,255",  speed: 0.000105, phase: 0.90, ox: 0.18, oy: 0.09 },
    // Teal accent — bottom-left
    { x: 0.09, y: 0.42, r: 0.38, color: "0,194,210",   speed: 0.000145, phase: 2.40, ox: 0.11, oy: 0.14 },
    // Bright cyan highlight — centre-right
    { x: 0.73, y: 0.38, r: 0.32, color: "140,228,255", speed: 0.000175, phase: 4.80, ox: 0.09, oy: 0.12 },
  ];

  /* -------------------------------------------------------------------------
   * Resize handling
   * ---------------------------------------------------------------------- */
  function resize() {
    canvas.width  = window.innerWidth;
    canvas.height = window.innerHeight;
  }
  resize();
  window.addEventListener("resize", resize, { passive: true });

  /* -------------------------------------------------------------------------
   * Animation loop
   * ---------------------------------------------------------------------- */
  function draw(ts) {
    const W = canvas.width;
    const H = canvas.height;
    const D = Math.max(W, H);

    // Wipe previous frame (transparent background — body bg shows through)
    ctx.globalCompositeOperation = "source-over";
    ctx.clearRect(0, 0, W, H);

    // Bloom orbs with additive blending so they glow where they overlap
    ctx.globalCompositeOperation = "lighter";

    for (const o of orbs) {
      const cx = (o.x + Math.sin(ts * o.speed + o.phase) * o.ox) * W;
      const cy = (o.y + Math.cos(ts * o.speed * 0.71 + o.phase) * o.oy) * H;
      const r  = o.r * D;

      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
      g.addColorStop(0,    `rgba(${o.color},0.10)`);
      g.addColorStop(0.35, `rgba(${o.color},0.055)`);
      g.addColorStop(0.70, `rgba(${o.color},0.020)`);
      g.addColorStop(1,    `rgba(${o.color},0)`);

      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fillStyle = g;
      ctx.fill();
    }

    requestAnimationFrame(draw);
  }

  // Respect reduced-motion preference — skip animation, render a single static frame
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  if (prefersReducedMotion.matches) {
    draw(0);
  } else {
    requestAnimationFrame(draw);
  }
})();
