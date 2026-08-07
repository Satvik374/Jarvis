/**
 * grainient.js — Reactbits Grainient ported to vanilla WebGL2 for Jarvis Browser UI
 *
 * No OGL, no React, no dependencies. Uses a fullscreen triangle (gl_VertexID
 * trick) + the original GLSL fragment shader verbatim.
 *
 * Each Jarvis state gets its own color palette. When the state changes,
 * window.grainientSetState(name) is called from app.js and the shader colors
 * lerp smoothly to the new palette over ~1.5 seconds.
 */
(() => {
  "use strict";

  /* ── State → color palettes ─────────────────────────────────────────────
   * Each palette = { c1, c2, c3 } matching the shader's uColor1/2/3.
   * c1 = bright highlight, c2 = dark base, c3 = mid accent.
   * ─────────────────────────────────────────────────────────────────────── */
  const PALETTES = {
    // Boot — deep navy with electric cyan (the "cold start" look)
    booting:      { c1: "#1cffff", c2: "#02050f", c3: "#0a4cff" },

    // Listening — vivid aqua/teal, open and receptive
    listening:    { c1: "#00ffdd", c2: "#001a16", c3: "#00bbaa" },

    // Perceiving (screenshot / vision scan) — purple scanning sweep
    perceiving:   { c1: "#bb44ff", c2: "#0a0018", c3: "#5500cc" },

    // Thinking / planning / verifying — deep indigo, processing feel
    thinking:     { c1: "#7c3aff", c2: "#050010", c3: "#3300bb" },
    planning:     { c1: "#6622ff", c2: "#050010", c3: "#2200aa" },
    verifying:    { c1: "#5544ff", c2: "#050010", c3: "#2211cc" },
    transcribing: { c1: "#44aaff", c2: "#000814", c3: "#0066cc" },

    // Working / acting — electric mid-blue, active execution
    working:      { c1: "#0088ff", c2: "#000814", c3: "#0044cc" },
    acting:       { c1: "#0099ff", c2: "#000a18", c3: "#0055dd" },

    // Responding — warm steel blue, composing output
    responding:   { c1: "#55ccff", c2: "#001122", c3: "#1177cc" },

    // Success — green pulse, task complete
    success:      { c1: "#00ff88", c2: "#001a0a", c3: "#00cc66" },

    // Warning — amber/orange, attention needed
    warning:      { c1: "#ffaa00", c2: "#140a00", c3: "#cc5500" },

    // Error — deep red, something went wrong
    error:        { c1: "#ff3300", c2: "#1a0000", c3: "#990000" },

    // Offline — crimson/dark red, disconnected
    offline:      { c1: "#ff2200", c2: "#1a0000", c3: "#770000" },
  };

  // Fallback for any unmapped state name
  const DEFAULT_PALETTE = PALETTES.booting;

  /* ── Hex → [r, g, b] in 0-1 range ──────────────────────────────────────── */
  function hexToRgb(hex) {
    const r = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    return r
      ? [parseInt(r[1], 16) / 255, parseInt(r[2], 16) / 255, parseInt(r[3], 16) / 255]
      : [1, 1, 1];
  }

  function paletteToRgb(p) {
    return [hexToRgb(p.c1), hexToRgb(p.c2), hexToRgb(p.c3)];
  }

  /* ── GLSL: fullscreen triangle, no VBO needed ────────────────────────────── */
  const VERT = `#version 300 es
void main() {
  vec2 pos[3];
  pos[0] = vec2(-1.0, -1.0);
  pos[1] = vec2( 3.0, -1.0);
  pos[2] = vec2(-1.0,  3.0);
  gl_Position = vec4(pos[gl_VertexID], 0.0, 1.0);
}`;

  /* ── GLSL: original Grainient fragment shader, verbatim ─────────────────── */
  const FRAG = `#version 300 es
precision highp float;
uniform vec2  iResolution;
uniform float iTime;
uniform float uTimeSpeed;
uniform float uColorBalance;
uniform float uWarpStrength;
uniform float uWarpFrequency;
uniform float uWarpSpeed;
uniform float uWarpAmplitude;
uniform float uBlendAngle;
uniform float uBlendSoftness;
uniform float uRotationAmount;
uniform float uNoiseScale;
uniform float uGrainAmount;
uniform float uGrainScale;
uniform float uGrainAnimated;
uniform float uContrast;
uniform float uGamma;
uniform float uSaturation;
uniform vec2  uCenterOffset;
uniform float uZoom;
uniform vec3  uColor1;
uniform vec3  uColor2;
uniform vec3  uColor3;
out vec4 fragColor;

#define S(a,b,t) smoothstep(a,b,t)
mat2 Rot(float a){float s=sin(a),c=cos(a);return mat2(c,-s,s,c);}
vec2 hash(vec2 p){
  p=vec2(dot(p,vec2(2127.1,81.17)),dot(p,vec2(1269.5,283.37)));
  return fract(sin(p)*43758.5453);
}
float noise(vec2 p){
  vec2 i=floor(p),f=fract(p),u=f*f*(3.0-2.0*f);
  return 0.5+0.5*mix(
    mix(dot(-1.0+2.0*hash(i+vec2(0,0)),f-vec2(0,0)),
        dot(-1.0+2.0*hash(i+vec2(1,0)),f-vec2(1,0)),u.x),
    mix(dot(-1.0+2.0*hash(i+vec2(0,1)),f-vec2(0,1)),
        dot(-1.0+2.0*hash(i+vec2(1,1)),f-vec2(1,1)),u.x),u.y);
}

void main(){
  float t   = iTime * uTimeSpeed;
  vec2  uv  = gl_FragCoord.xy / iResolution.xy;
  float ratio = iResolution.x / iResolution.y;
  vec2  tuv = uv - 0.5 + uCenterOffset;
  tuv /= max(uZoom, 0.001);

  float degree = noise(vec2(t*0.1, tuv.x*tuv.y) * uNoiseScale);
  tuv.y *= 1.0/ratio;
  tuv   *= Rot(radians((degree-0.5)*uRotationAmount+180.0));
  tuv.y *= ratio;

  float frequency = uWarpFrequency;
  float ws        = max(uWarpStrength, 0.001);
  float amplitude = uWarpAmplitude / ws;
  float warpTime  = t * uWarpSpeed;
  tuv.x += sin(tuv.y*frequency + warpTime) / amplitude;
  tuv.y += sin(tuv.x*(frequency*1.5) + warpTime) / (amplitude*0.5);

  float b  = uColorBalance;
  float s  = max(uBlendSoftness, 0.0);
  float blendX = (tuv * Rot(radians(uBlendAngle))).x;
  vec3 layer1  = mix(uColor3, uColor2, S(-0.3-b-s, 0.2-b+s, blendX));
  vec3 layer2  = mix(uColor2, uColor1, S(-0.3-b-s, 0.2-b+s, blendX));
  vec3 col     = mix(layer1, layer2, S(0.5-b+s, -0.3-b-s, tuv.y));

  vec2 grainUv = uv * max(uGrainScale, 0.001);
  if (uGrainAnimated > 0.5) grainUv += vec2(iTime*0.05);
  float grain  = fract(sin(dot(grainUv, vec2(12.9898,78.233)))*43758.5453);
  col += (grain-0.5) * uGrainAmount;

  col    = (col-0.5) * uContrast + 0.5;
  float luma = dot(col, vec3(0.2126,0.7152,0.0722));
  col    = mix(vec3(luma), col, uSaturation);
  col    = pow(max(col, 0.0), vec3(1.0/max(uGamma,0.001)));
  col    = clamp(col, 0.0, 1.0);

  fragColor = vec4(col, 1.0);
}`;

  /* ── Static shader parameters (non-color uniforms) ──────────────────────── */
  const CFG = {
    timeSpeed:       0.18,
    colorBalance:    0.05,
    warpStrength:    1.0,
    warpFrequency:   5.0,
    warpSpeed:       1.4,
    warpAmplitude:   60.0,
    blendAngle:      0.0,
    blendSoftness:   0.08,
    rotationAmount:  420.0,
    noiseScale:      2.2,
    grainAmount:     0.055,
    grainScale:      2.0,
    grainAnimated:   false,
    contrast:        1.45,
    gamma:           1.0,
    saturation:      1.1,
    centerX:         0.0,
    centerY:         0.0,
    zoom:            0.92,
  };

  /* ── WebGL helpers ──────────────────────────────────────────────────────── */
  function compileShader(gl, type, src) {
    const sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      console.error("[grainient] shader error:", gl.getShaderInfoLog(sh));
      gl.deleteShader(sh);
      return null;
    }
    return sh;
  }

  function buildProgram(gl) {
    const vs = compileShader(gl, gl.VERTEX_SHADER,   VERT);
    const fs = compileShader(gl, gl.FRAGMENT_SHADER, FRAG);
    if (!vs || !fs) return null;
    const prog = gl.createProgram();
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      console.error("[grainient] link error:", gl.getProgramInfoLog(prog));
      return null;
    }
    return prog;
  }

  /* ── Canvas + WebGL context ─────────────────────────────────────────────── */
  const canvas = document.createElement("canvas");
  canvas.id = "grainientCanvas";
  canvas.setAttribute("aria-hidden", "true");
  Object.assign(canvas.style, {
    position: "fixed",
    top: "0", left: "0",
    width: "100%", height: "100%",
    zIndex: "0",
    pointerEvents: "none",
  });
  document.body.prepend(canvas);

  const gl = canvas.getContext("webgl2", {
    alpha: false,
    antialias: false,
    powerPreference: "low-power",
  });
  if (!gl) {
    console.warn("[grainient] WebGL2 unavailable");
    canvas.remove();
    return;
  }

  const program = buildProgram(gl);
  if (!program) { canvas.remove(); return; }
  gl.useProgram(program);

  /* ── Cache uniform locations ────────────────────────────────────────────── */
  const UL = {};
  for (const name of [
    "iResolution","iTime",
    "uTimeSpeed","uColorBalance","uWarpStrength","uWarpFrequency","uWarpSpeed",
    "uWarpAmplitude","uBlendAngle","uBlendSoftness","uRotationAmount","uNoiseScale",
    "uGrainAmount","uGrainScale","uGrainAnimated","uContrast","uGamma","uSaturation",
    "uCenterOffset","uZoom","uColor1","uColor2","uColor3",
  ]) UL[name] = gl.getUniformLocation(program, name);

  /* ── Upload static (non-color) uniforms once ────────────────────────────── */
  function uploadStaticUniforms() {
    gl.uniform1f(UL.uTimeSpeed,       CFG.timeSpeed);
    gl.uniform1f(UL.uColorBalance,    CFG.colorBalance);
    gl.uniform1f(UL.uWarpStrength,    CFG.warpStrength);
    gl.uniform1f(UL.uWarpFrequency,   CFG.warpFrequency);
    gl.uniform1f(UL.uWarpSpeed,       CFG.warpSpeed);
    gl.uniform1f(UL.uWarpAmplitude,   CFG.warpAmplitude);
    gl.uniform1f(UL.uBlendAngle,      CFG.blendAngle);
    gl.uniform1f(UL.uBlendSoftness,   CFG.blendSoftness);
    gl.uniform1f(UL.uRotationAmount,  CFG.rotationAmount);
    gl.uniform1f(UL.uNoiseScale,      CFG.noiseScale);
    gl.uniform1f(UL.uGrainAmount,     CFG.grainAmount);
    gl.uniform1f(UL.uGrainScale,      CFG.grainScale);
    gl.uniform1f(UL.uGrainAnimated,   CFG.grainAnimated ? 1.0 : 0.0);
    gl.uniform1f(UL.uContrast,        CFG.contrast);
    gl.uniform1f(UL.uGamma,           CFG.gamma);
    gl.uniform1f(UL.uSaturation,      CFG.saturation);
    gl.uniform2f(UL.uCenterOffset,    CFG.centerX, CFG.centerY);
    gl.uniform1f(UL.uZoom,            CFG.zoom);
  }
  uploadStaticUniforms();

  /* ── Color interpolation state ──────────────────────────────────────────── */
  // currentRgb / targetRgb: [[r,g,b], [r,g,b], [r,g,b]] for color1/2/3
  let currentRgb = paletteToRgb(DEFAULT_PALETTE);
  let targetRgb  = paletteToRgb(DEFAULT_PALETTE);

  // Upload current colors to the shader
  function uploadColors() {
    gl.uniform3f(UL.uColor1, currentRgb[0][0], currentRgb[0][1], currentRgb[0][2]);
    gl.uniform3f(UL.uColor2, currentRgb[1][0], currentRgb[1][1], currentRgb[1][2]);
    gl.uniform3f(UL.uColor3, currentRgb[2][0], currentRgb[2][1], currentRgb[2][2]);
  }
  uploadColors();

  // Lerp a single channel
  function lerp(a, b, t) { return a + (b - a) * t; }

  // Lerp rate per frame at 60fps → ~1.5 s transition (1 - 0.97^90 ≈ 1)
  const LERP_SPEED = 0.03;

  function stepColors() {
    let changed = false;
    for (let i = 0; i < 3; i++) {
      for (let ch = 0; ch < 3; ch++) {
        const next = lerp(currentRgb[i][ch], targetRgb[i][ch], LERP_SPEED);
        if (Math.abs(next - currentRgb[i][ch]) > 0.0001) {
          currentRgb[i][ch] = next;
          changed = true;
        }
      }
    }
    if (changed) uploadColors();
  }

  /* ── Public API: called from app.js setState() ──────────────────────────── */
  window.grainientSetState = function (stateName) {
    const palette = PALETTES[stateName] || DEFAULT_PALETTE;
    targetRgb = paletteToRgb(palette);
  };

  /* ── Resize ─────────────────────────────────────────────────────────────── */
  const dpr = Math.min(window.devicePixelRatio || 1, 2);

  function resize() {
    const w = Math.floor(window.innerWidth  * dpr);
    const h = Math.floor(window.innerHeight * dpr);
    if (canvas.width === w && canvas.height === h) return;
    canvas.width  = w;
    canvas.height = h;
    gl.viewport(0, 0, w, h);
  }
  resize();
  window.addEventListener("resize", resize, { passive: true });

  /* ── Empty VAO (required by WebGL2 for attribute-less draws) ────────────── */
  gl.bindVertexArray(gl.createVertexArray());

  /* ── Render loop ─────────────────────────────────────────────────────────── */
  const t0 = performance.now();
  let   raf = 0;

  function draw(ts) {
    stepColors();   // advance color lerp each frame
    gl.uniform2f(UL.iResolution, canvas.width, canvas.height);
    gl.uniform1f(UL.iTime, (ts - t0) * 0.001);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    raf = requestAnimationFrame(draw);
  }

  function tryStart() { if (raf === 0 && !document.hidden) raf = requestAnimationFrame(draw); }
  function tryStop()  { if (raf !== 0) { cancelAnimationFrame(raf); raf = 0; } }

  const io = new IntersectionObserver(([e]) => e.isIntersecting ? tryStart() : tryStop(), { threshold: 0 });
  io.observe(canvas);
  document.addEventListener("visibilitychange", () => document.hidden ? tryStop() : tryStart());

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    gl.uniform2f(UL.iResolution, canvas.width, canvas.height);
    gl.uniform1f(UL.iTime, 0);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  } else {
    tryStart();
  }
})();
