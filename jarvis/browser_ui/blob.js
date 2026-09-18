/**
 * Fluid Jarvis Blob
 * =================
 *
 * An organic, liquid "energy blob" rendered as a signed-distance field on a
 * fullscreen shader. It replaces the dotted wireframe sphere as the default
 * core visual: instead of a rigid globe it is a soft, gel-like mass of merged
 * metaballs whose silhouette, interior caustics and colour all track what
 * Jarvis is actually doing.
 *
 * Reactivity comes from three independent inputs:
 *
 *   - state      (`setState`)  - wobble amplitude, spin, viscosity, palette.
 *   - voice      (`setSignals`)- the live voice level and 24-band spectrum
 *                                push the membrane outward and light a crown
 *                                of spikes around the membrane.
 *   - actions    (`pulse`)     - a thrust + expanding shockwave rings emitted
 *                                whenever Jarvis starts or finishes something.
 *
 * The blob renders inside EnergyCore's animation frame (see app.js), so the
 * page keeps a single requestAnimationFrame loop. Sizing is driven by a
 * ResizeObserver plus an adaptive render scale, so it stays sharp on a phone
 * and stays smooth on a 4K window.
 */

(() => {
  const DEFAULTS = {
    maxDevicePixels: 1_450_000,
    maxDpr: 1.6,
    octavesHigh: 4,
    octavesLow: 3,
    bands: 24,
    shocks: 3,
  };

  /**
   * Motion character per Jarvis state. Colours deliberately live in
   * `EnergyCore.profiles` (and the shared hologram palette) so the blob never
   * becomes a second source of truth for state colour - it only owns motion.
   */
  const BLOB_PROFILES = {
    booting:      { deform: 0.18, speed: 0.34, energy: 0.42, pulse: 0.38, tension: 0.55 },
    connecting:   { deform: 0.34, speed: 0.78, energy: 0.62, pulse: 0.72, tension: 0.42 },
    listening:    { deform: 0.30, speed: 0.42, energy: 0.92, pulse: 1.15, tension: 0.62 },
    perceiving:   { deform: 0.44, speed: 0.68, energy: 0.72, pulse: 0.48, tension: 0.36 },
    thinking:     { deform: 0.86, speed: 1.28, energy: 1.00, pulse: 1.05, tension: 0.22 },
    planning:     { deform: 0.54, speed: 0.86, energy: 0.74, pulse: 0.82, tension: 0.34 },
    verifying:    { deform: 0.30, speed: 0.60, energy: 0.82, pulse: 0.56, tension: 0.60 },
    transcribing: { deform: 0.62, speed: 0.74, energy: 0.78, pulse: 1.30, tension: 0.46 },
    working:      { deform: 0.50, speed: 0.66, energy: 0.79, pulse: 0.72, tension: 0.48 },
    acting:       { deform: 0.62, speed: 1.52, energy: 1.00, pulse: 1.40, tension: 0.30 },
    responding:   { deform: 0.38, speed: 0.52, energy: 0.78, pulse: 0.96, tension: 0.58 },
    speaking:     { deform: 0.44, speed: 0.58, energy: 0.88, pulse: 1.05, tension: 0.52 },
    success:      { deform: 0.20, speed: 0.34, energy: 1.00, pulse: 1.55, tension: 0.74 },
    warning:      { deform: 0.72, speed: 0.72, energy: 0.84, pulse: 0.92, tension: 0.40 },
    error:        { deform: 0.90, speed: 0.46, energy: 0.68, pulse: 0.60, tension: 0.16 },
    muted:        { deform: 0.16, speed: 0.26, energy: 0.42, pulse: 0.34, tension: 0.68 },
    idle:         { deform: 0.22, speed: 0.30, energy: 0.48, pulse: 0.40, tension: 0.64 },
    offline:      { deform: 0.08, speed: 0.10, energy: 0.10, pulse: 0.10, tension: 0.80 },
  };

  const VERTEX_SHADER = `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = vec4(position.xy, 0.0, 1.0);
    }
  `;

  const FRAGMENT_SHADER = `
    precision highp float;

    varying vec2 vUv;

    uniform vec2  uResolution;
    uniform float uTime;
    uniform vec2  uPointer;
    uniform vec3  uCore;
    uniform vec3  uAccent;
    uniform float uEnergy;
    uniform float uDeform;
    uniform float uSpeed;
    uniform float uPulse;
    uniform float uSpeech;
    uniform float uSpeechMix;
    uniform float uThrust;
    uniform float uBass;
    uniform float uTension;
    uniform float uOctaves;
    uniform float uBands[${DEFAULTS.bands}];
    uniform float uShockRad[${DEFAULTS.shocks}];
    uniform float uShockPow[${DEFAULTS.shocks}];

    float hash21(vec2 p) {
      p = fract(p * vec2(123.34, 345.45));
      p += dot(p, p + 34.345);
      return fract(p.x * p.y);
    }

    float vnoise(vec2 p) {
      vec2 i = floor(p);
      vec2 f = fract(p);
      f = f * f * (3.0 - 2.0 * f);
      float a = hash21(i);
      float b = hash21(i + vec2(1.0, 0.0));
      float c = hash21(i + vec2(0.0, 1.0));
      float d = hash21(i + vec2(1.0, 1.0));
      return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
    }

    float fbm(vec2 p) {
      float value = 0.0;
      float amplitude = 0.5;
      for (int i = 0; i < 4; i++) {
        if (float(i) >= uOctaves) break;
        value += amplitude * vnoise(p);
        p = p * 2.03 + 17.3;
        amplitude *= 0.5;
      }
      return value;
    }

    /**
     * Two-octave noise. Deliberately gentler than fbm: shaping the silhouette
     * with all four octaves scallops the edge into a ragged amoeba instead of
     * a liquid surface.
     */
    float smoothNoise(vec2 p) {
      return vnoise(p) * 0.65 + vnoise(p * 2.02 + 11.7) * 0.35;
    }

    /** Triangular interpolation of the ${DEFAULTS.bands}-band voice spectrum. */
    float crown(float ang01) {
      float x = ang01 * ${DEFAULTS.bands}.0;
      float acc = 0.0;
      for (int i = 0; i < ${DEFAULTS.bands}; i++) {
        float dist = abs(x - (float(i) + 0.5));
        acc += uBands[i] * max(0.0, 1.0 - min(dist, 1.0));
      }
      return clamp(acc, 0.0, 1.0);
    }

    /**
     * Signed distance to the blob: a domain-warped sphere smooth-min unioned
     * with orbiting droplets, so they visibly stretch out of the mass and melt
     * back into it.
     *
     * The warp runs in two passes: the first supplies broad currents, the
     * second breaks the surface into the finer unevenness that keeps the
     * silhouette organic. A single low-frequency warp leaves it visibly
     * polygonal, which is what a naive version of this looks like.
     */
    float blobField(vec2 p, float t, float speechPush, out float ang01) {
      vec2 current = vec2(
        smoothNoise(p * 2.3 + vec2(0.0, t * 0.10)),
        smoothNoise(p * 2.3 + vec2(4.7, -t * 0.08))
      );
      vec2 ripple = vec2(
        smoothNoise(p * 6.4 + current * 1.6 + vec2(t * 0.14, 1.3)),
        smoothNoise(p * 6.4 + current * 1.6 + vec2(-2.1, -t * 0.12))
      );
      // "tension" pulls the surface taut; low tension lets it slosh.
      float slosh = (0.17 + uDeform * 0.38) * (1.35 - uTension * 0.5);
      vec2 q = p + (current - 0.5) * slosh * 0.62 + (ripple - 0.5) * slosh * 0.30;

      float r = length(q);
      float a = atan(q.y, q.x);
      ang01 = a * 0.15915494 + 0.5;

      // A slow, low-order breath on top of the noise, so the mass also swells
      // and settles as a whole rather than only rippling in place.
      float breath =
        sin(a * 2.0 + t * (0.35 + uSpeed * 0.40)) * 0.55 +
        sin(a * 3.0 - t * (0.24 + uSpeed * 0.30)) * 0.28;

      float radius = 0.272;
      radius += breath * (0.010 + uDeform * 0.022) * (1.35 - uTension * 0.5);
      radius += speechPush * (0.018 + crown(ang01) * 0.045);
      radius += uThrust * 0.038;
      radius += uBass * 0.018;

      float d = r - radius;

      for (int i = 0; i < 6; i++) {
        float fi = float(i);
        float dir = mod(fi, 2.0) < 0.5 ? 1.0 : -1.0;
        float spin = t * (0.18 + fi * 0.055) * (0.55 + uSpeed * 0.75) * dir;
        float angle = spin + fi * 2.399;
        float orbit = 0.302
          + 0.058 * sin(t * 0.42 + fi * 1.7)
          + uDeform * 0.038 * sin(t * 0.80 + fi * 2.3)
          + uThrust * 0.046;
        float size = 0.034
          + 0.014 * sin(t * 0.63 + fi * 2.1)
          + uSpeechMix * uBands[i] * 0.028;
        vec2 center = vec2(cos(angle), sin(angle)) * orbit;
        float droplet = length(q - center) - size;

        // Polynomial smooth-min (union): the droplets fuse into the mass
        // instead of intersecting it. Swapping these operands inverts the
        // blend into a smooth-max, which erases the blob's interior entirely.
        float k = 0.070;
        float h = clamp(0.5 + 0.5 * (droplet - d) / k, 0.0, 1.0);
        d = mix(droplet, d, h) - k * h * (1.0 - h);
      }

      return d;
    }

    void main() {
      // Normalise so 1.0 unit is the container's short edge: the blob keeps its
      // proportions on a phone, a laptop or an ultrawide window.
      float shortEdge = min(uResolution.x, uResolution.y);
      vec2 p = (vUv - 0.5) * (uResolution / shortEdge);
      p += uPointer * 0.030;

      float t = uTime;
      float aa = 1.5 / shortEdge;
      float speechPush = uSpeech * (0.32 + uSpeechMix * 0.68);

      float ang01;
      float d = blobField(p, t, speechPush, ang01);
      float r = length(p);

      float inside = smoothstep(aa * 1.8, -aa * 1.8, d);

      // Crisp membrane, with a per-channel offset so the edge refracts like
      // liquid. This is the silhouette the eye actually reads.
      float rim = smoothstep(aa * 1.9, 0.0, abs(d));
      float dispersion = aa * 1.8;
      float rimRed = smoothstep(aa * 3.0, 0.0, abs(d - dispersion));
      float rimBlue = smoothstep(aa * 3.0, 0.0, abs(d + dispersion));

      // Luminous volume: the body glows toward the middle and thins out under
      // the membrane, so the silhouette stays crisp over a translucent rim.
      float coreRamp = smoothstep(0.04, 0.30, -d) * inside;
      vec3 col = mix(uAccent * 0.40, uCore, 0.60) * inside * (0.16 + 0.21 * coreRamp);

      // Internal churn: two layers of higher-frequency noise give the interior
      // visible currents and caustics, and a third drifts broad luminous
      // patches through it. Without this the body reads as flat paint.
      float c1 = fbm(p * 11.0 + vec2(t * 0.26, -t * 0.19));
      float c2 = fbm(p * 19.0 - vec2(t * 0.17, t * 0.23));
      float currents = fbm(p * 3.2 + vec2(0.0, t * 0.07));
      // Ridged noise turns the churn into filaments rather than soft blotches,
      // and the raw product is recentred before scaling so the interior keeps a
      // visible current almost everywhere instead of a few rare highlights.
      float filament = 1.0 - abs(c1 * 2.0 - 1.0);
      float churn = clamp((c1 * c2 - 0.055) * 6.0, 0.0, 1.2) * (0.55 + 0.45 * filament);
      col += uCore * churn * inside * (0.10 + uEnergy * 0.16) * (0.35 + 0.65 * coreRamp);
      col += mix(uAccent, uCore, 0.5) * smoothstep(0.30, 0.86, currents) * inside * 0.22;

      // Soft bloom at the middle, so the core reads as a luminous source behind
      // the centre label rather than a hole.
      col += mix(uAccent, uCore, 0.65) * inside * exp(-length(p) * 5.5) * 0.30;

      col += vec3(rimRed, rim, rimBlue) * uCore * (0.72 + uPulse * 0.34);
      col += rim * 0.34 * mix(uCore, vec3(1.0), 0.65);
      col += uCore * smoothstep(-0.055, 0.0, d) * inside * 0.30;

      // Outer halo.
      col += uCore * exp(-max(d, 0.0) * (13.0 + uEnergy * 10.0)) * (0.06 + uEnergy * 0.07);

      // Voice crown: spectrum spikes rise off the membrane as Jarvis speaks.
      float spikes = pow(crown(ang01), 1.6) * exp(-max(d, 0.0) * 7.0);
      col += mix(uCore, vec3(1.0), 0.3) * spikes * uSpeech * (0.25 + uSpeechMix * 0.45) * 1.1;

      // Action thrust: a hot flash through the core when something is executed.
      col += vec3(0.55, 0.9, 1.0) * exp(-max(d, 0.0) * 20.0) * uThrust * 0.22;

      // Shockwave rings emitted on state changes and actions.
      // Named "firing" because "active" is a reserved word under the GLSL ES 3.0
      // dialect three.js targets on WebGL2.
      for (int i = 0; i < ${DEFAULTS.shocks}; i++) {
        float firing = step(0.0005, uShockPow[i]);
        float ringWidth = 2.6 / shortEdge;
        float ring = exp(-abs(r - uShockRad[i]) / ringWidth);
        col += uCore * ring * uShockPow[i] * firing * 0.75;
      }

      // Instrument HUD: two counter-rotating dashed orbits.
      float angle = atan(p.y, p.x);
      float dash = smoothstep(0.35, 0.75, sin(angle * 46.0 + t * (0.6 + uSpeed * 1.1)));
      float ringLine = smoothstep(1.2 / shortEdge, 0.0, abs(r - 0.470));
      col += uCore * ringLine * (0.09 + dash * 0.15) * (0.40 + uEnergy * 0.80);

      float dash2 = smoothstep(0.55, 0.95, sin(angle * 18.0 - t * (0.30 + uSpeed * 0.50)));
      float ringLine2 = smoothstep(1.0 / shortEdge, 0.0, abs(r - 0.545));
      col += uAccent * ringLine2 * dash2 * (0.07 + uEnergy * 0.12);

      // Dither, so the large soft gradients never band.
      col += (hash21(p * shortEdge * 0.5 + vec2(fract(t) * 37.0)) - 0.5) * 0.018;

      // Soft roll-off keeps the additive blend bright without clipping to flat white.
      col = col / (1.0 + col * 0.30);

      gl_FragColor = vec4(col, 1.0);
    }
  `;

  class FluidBlob {
    constructor(container, options = {}) {
      this.container = container;
      this.available = false;
      this.active = true;
      this.state = "booting";
      this.reducedMotion = Boolean(options.reducedMotion);
      this.bandCount = DEFAULTS.bands;
      this.bands = new Float32Array(DEFAULTS.bands);
      this.shocks = [];
      this.thrust = 0;
      this.octaves = DEFAULTS.octavesHigh;
      this.renderScale = 1;
      this.frameMs = 16;
      this.slowFrames = 0;
      this.fastFrames = 0;
      this.pointer = { x: 0, y: 0, tx: 0, ty: 0 };
      this.signals = {
        time: 0,
        energy: 0.42,
        deform: 0.18,
        speed: 0.34,
        pulse: 0.38,
        speech: 0,
        speechMix: 0,
        bass: 0,
        tension: 0.55,
      };
      this.colors = { core: [0.07, 1.0, 1.0], accent: [0.04, 0.30, 1.0] };
      this.targetColors = { core: [...this.colors.core], accent: [...this.colors.accent] };
      this.profile = BLOB_PROFILES.booting;
      this.targetProfile = { ...this.profile };
      this.lastTime = 0;
      this.available = this.init();
    }

    init() {
      if (!window.THREE) {
        console.warn("FluidBlob unavailable: three.js is not loaded.");
        return false;
      }
      try {
        this.canvas = document.createElement("canvas");
        this.canvas.setAttribute("aria-hidden", "true");
        this.container.appendChild(this.canvas);

        this.renderer = new THREE.WebGLRenderer({
          canvas: this.canvas,
          alpha: true,
          antialias: false,
          powerPreference: "high-performance",
        });
        this.renderer.setClearColor(0x000000, 0);
        this.renderer.autoClear = true;

        this.scene = new THREE.Scene();
        this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

        this.uniforms = {
          uResolution: { value: new THREE.Vector2(1, 1) },
          uTime: { value: 0 },
          uPointer: { value: new THREE.Vector2(0, 0) },
          uCore: { value: new THREE.Color(0.07, 1.0, 1.0) },
          uAccent: { value: new THREE.Color(0.04, 0.30, 1.0) },
          uEnergy: { value: this.signals.energy },
          uDeform: { value: this.signals.deform },
          uSpeed: { value: this.signals.speed },
          uPulse: { value: this.signals.pulse },
          uSpeech: { value: 0 },
          uSpeechMix: { value: 0 },
          uThrust: { value: 0 },
          uBass: { value: 0 },
          uTension: { value: this.signals.tension },
          uOctaves: { value: this.octaves },
          uBands: { value: this.bands },
          uShockRad: { value: new Float32Array(DEFAULTS.shocks) },
          uShockPow: { value: new Float32Array(DEFAULTS.shocks) },
        };

        const material = new THREE.ShaderMaterial({
          uniforms: this.uniforms,
          vertexShader: VERTEX_SHADER,
          fragmentShader: FRAGMENT_SHADER,
          transparent: true,
          depthTest: false,
          depthWrite: false,
          blending: THREE.AdditiveBlending,
        });

        this.quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), material);
        this.quad.frustumCulled = false;
        this.scene.add(this.quad);

        this.resizeObserver = new ResizeObserver(() => this.resize());
        this.resizeObserver.observe(this.container);
        window.addEventListener("pointermove", (event) => {
          if (this.reducedMotion) return;
          this.pointer.tx = (event.clientX / window.innerWidth - 0.5) * 2;
          this.pointer.ty = -(event.clientY / window.innerHeight - 0.5) * 2;
        }, { passive: true });

        this.resize();
        return true;
      } catch (error) {
        console.warn("FluidBlob unavailable; falling back to the 2D core.", error);
        if (this.canvas && this.canvas.parentElement) this.canvas.remove();
        this.renderer = null;
        return false;
      }
    }

    setActive(active) {
      this.active = Boolean(active);
      if (this.canvas) this.canvas.style.display = this.active ? "block" : "none";
      // Re-measure on the way back: while hidden the container reports 0x0.
      if (this.active) this.resize();
    }

    setReducedMotion(reduced) {
      this.reducedMotion = Boolean(reduced);
      if (this.canvas) this.canvas.style.display = this.active ? "block" : "none";
    }

    /** Jarvis state name; unknown names fall back to `working`. */
    setState(name) {
      const profile = BLOB_PROFILES[name] || BLOB_PROFILES.working;
      this.state = name;
      this.targetProfile = { ...profile };
      if (this.reducedMotion) this.signals.tension = profile.tension;
    }

    /** State colour, already eased by EnergyCore's palette interpolation. */
    setColor(rgb) {
      if (!Array.isArray(rgb) || rgb.length < 3) return;
      const core = rgb.map((value) => Math.max(0, Math.min(1, value / 255)));
      this.targetColors.core = core;
      this.targetColors.accent = core.map((value) => value * 0.55);
    }

    /** Per-frame voice + action inputs, supplied by EnergyCore. */
    setSignals(payload) {
      const signals = this.signals;
      if (Number.isFinite(payload.time)) signals.time = payload.time;
      if (Number.isFinite(payload.energy)) signals.energy = payload.energy;
      if (Number.isFinite(payload.deform)) signals.deform = payload.deform;
      if (Number.isFinite(payload.speed)) signals.speed = payload.speed;
      if (Number.isFinite(payload.pulse)) signals.pulse = payload.pulse;
      if (Number.isFinite(payload.speech)) signals.speech = Math.max(0, Math.min(1, payload.speech));
      if (Number.isFinite(payload.speechMix)) signals.speechMix = Math.max(0, Math.min(1, payload.speechMix));
      if (Number.isFinite(payload.bass)) signals.bass = Math.max(0, Math.min(1, payload.bass));
      if (Array.isArray(payload.color)) this.setColor(payload.color);
      if (payload.bars && payload.bars.length) this.setBands(payload.bars);
    }

    setBands(bars) {
      const count = Math.min(DEFAULTS.bands, bars.length);
      for (let index = 0; index < count; index += 1) {
        this.bands[index] = Math.max(0, Math.min(1, Number(bars[index]) || 0));
      }
      for (let index = count; index < DEFAULTS.bands; index += 1) {
        this.bands[index] = 0;
      }
    }

    /** Emit an action pulse: a core flash plus two expanding shockwave rings. */
    pulse(power = 1) {
      if (!this.available) return;
      const strength = Math.max(0.15, Math.min(2, Number(power) || 1));
      this.thrust = Math.min(1.6, this.thrust + strength * 0.55);
      const born = this.signals.time;
      this.shocks.push({ born, power: Math.min(1, 0.45 + strength * 0.35) });
      if (this.shocks.length > 2) this.shocks.splice(0, this.shocks.length - 2);
    }

    resize() {
      if (!this.renderer || !this.canvas) return;
      const rect = this.container.getBoundingClientRect();
      const width = Math.max(1, rect.width);
      const height = Math.max(1, rect.height);
      this.width = width;
      this.height = height;

      let dpr = Math.min(window.devicePixelRatio || 1, DEFAULTS.maxDpr);
      const budget = Math.sqrt(DEFAULTS.maxDevicePixels / (width * height * dpr * dpr));
      dpr *= Math.min(1, budget) * this.renderScale;

      // The renderer owns the drawing buffer: assigning canvas.width by hand and
      // then calling setPixelRatio() makes three re-apply the size it remembers
      // (the 300x150 default) and silently overwrite it.
      const deviceWidth = Math.max(2, Math.round(width * dpr));
      const deviceHeight = Math.max(2, Math.round(height * dpr));
      this.canvas.style.width = `${width}px`;
      this.canvas.style.height = `${height}px`;

      this.renderer.setPixelRatio(1);
      this.renderer.setSize(deviceWidth, deviceHeight, false);

      this.shortEdge = Math.min(width, height);
      this.uniforms.uResolution.value.set(width, height);

      // Small stages get fewer noise octaves so phones stay fluid.
      const tier = this.shortEdge < 300 ? DEFAULTS.octavesLow
        : this.shortEdge < 420 ? DEFAULTS.octavesLow + 0.5
          : DEFAULTS.octavesHigh;
      this.octaves = Math.min(tier, DEFAULTS.octavesHigh);
      this.uniforms.uOctaves.value = this.octaves;
    }

    /** Drop render scale when frames get expensive; restore it when they are cheap. */
    adapt(frameMs) {
      this.frameMs = this.frameMs * 0.9 + frameMs * 0.1;
      if (this.frameMs > 26 && this.renderScale > 0.62) {
        this.slowFrames += 1;
        if (this.slowFrames > 45) {
          this.slowFrames = 0;
          this.renderScale = Math.max(0.62, this.renderScale - 0.18);
          this.resize();
        }
      } else {
        this.slowFrames = 0;
      }
      if (this.frameMs < 11 && this.renderScale < 1) {
        this.fastFrames += 1;
        if (this.fastFrames > 240) {
          this.fastFrames = 0;
          this.renderScale = Math.min(1, this.renderScale + 0.18);
          this.resize();
        }
      } else {
        this.fastFrames = 0;
      }
    }

    /** Called from EnergyCore's frame loop with the shared clock in seconds. */
    render(time) {
      if (!this.available || !this.active || !this.renderer) return;

      const dt = Math.min(0.05, Math.max(0, time - (this.lastTime || time)));
      this.lastTime = time;
      const step = dt * 60;

      // EnergyCore already eases energy/deform/speed/pulse and the palette, so
      // only the blob-exclusive properties are interpolated here.
      const ease = this.reducedMotion ? 1 : Math.min(1, 0.05 * step);
      const signals = this.signals;
      signals.tension += (this.targetProfile.tension - signals.tension) * ease;
      for (let channel = 0; channel < 3; channel += 1) {
        this.colors.core[channel] +=
          (this.targetColors.core[channel] - this.colors.core[channel]) * ease;
        this.colors.accent[channel] +=
          (this.targetColors.accent[channel] - this.colors.accent[channel]) * ease;
      }

      // EnergyCore already smooths the voice level; a light spring here keeps
      // the membrane from snapping on a single loud frame.
      const thrustEase = this.reducedMotion ? 1 : Math.min(1, 0.16 * step);
      this.thrust += (0 - this.thrust) * thrustEase;

      const pointerEase = this.reducedMotion ? 1 : Math.min(1, 0.05 * step);
      this.pointer.x += (this.pointer.tx - this.pointer.x) * pointerEase;
      this.pointer.y += (this.pointer.ty - this.pointer.y) * pointerEase;

      const u = this.uniforms;
      // Reduced motion renders a fixed pose, so the redraws triggered by state
      // changes stay still instead of jumping to a new noise field.
      u.uTime.value = this.reducedMotion ? 0 : time;
      u.uEnergy.value = signals.energy;
      u.uDeform.value = signals.deform;
      u.uSpeed.value = signals.speed;
      u.uPulse.value = signals.pulse;
      u.uTension.value = signals.tension;
      u.uSpeech.value = signals.speech;
      u.uSpeechMix.value = signals.speechMix;
      u.uBass.value = signals.bass;
      u.uThrust.value = this.thrust;
      u.uPointer.value.set(this.pointer.x, this.pointer.y);
      u.uCore.value.setRGB(this.colors.core[0], this.colors.core[1], this.colors.core[2]);
      u.uAccent.value.setRGB(this.colors.accent[0], this.colors.accent[1], this.colors.accent[2]);

      // Shocks expand from the membrane out past the HUD rings, then expire.
      const radii = u.uShockRad.value;
      const powers = u.uShockPow.value;
      for (let index = 0; index < DEFAULTS.shocks; index += 1) {
        radii[index] = 0;
        powers[index] = 0;
      }
      const life = 1.25;
      for (let index = this.shocks.length - 1; index >= 0; index -= 1) {
        const shock = this.shocks[index];
        const age = (time - shock.born) / life;
        if (age >= 1 || age < 0) {
          this.shocks.splice(index, 1);
          continue;
        }
        const slot = Math.min(DEFAULTS.shocks - 1, index);
        const eased = 1 - (1 - age) ** 3;
        radii[slot] = 0.33 + eased * 0.62;
        powers[slot] = (1 - age) ** 2 * shock.power;
      }

      const started = performance.now();
      this.renderer.render(this.scene, this.camera);
      this.adapt(performance.now() - started);

      this.signals.time = time;
    }

    dispose() {
      if (this.resizeObserver) this.resizeObserver.disconnect();
      if (this.renderer) this.renderer.dispose();
      if (this.canvas && this.canvas.parentElement) this.canvas.remove();
      this.available = false;
    }
  }

  window.FluidBlob = FluidBlob;
  window.FLUID_BLOB_PROFILES = BLOB_PROFILES;
})();
