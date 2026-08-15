/**
 * hologram.js — Interactive 3D Holographic Core for Jarvis Web UI
 *
 * Built with Three.js (WebGL). Features:
 * - 3D Geodesic Singularity Core with pulsing vertex lattice
 * - 1,200+ Volumetric Quantum Particle Swarm with orbital bands & audio displacement
 * - Triple Concentric Gyroscopic Holographic Arc Rings (Gimbals) with neon tick marks
 * - Oscillating Planar Holographic Laser Scanner
 * - Expanding Sonic Shockwaves on speech beats and state transitions
 * - Interactive Mouse Orbit, Parallax Tilt, Wheel Zoom, and Click Pulse
 * - Multi-Mode Hologram Display (Hologram, Orbit, Wireframe, Quantum)
 * - State-reactive color palettes and real-time audio spectrogram synchronization
 */

(() => {
  "use strict";

  // State color palettes in RGB normalized (0..1)
  const STATE_PALETTES = {
    booting:      { core: [0.11, 1.0, 1.0],  accent: [0.04, 0.3, 1.0],  bg: [0.01, 0.02, 0.06] },
    listening:    { core: [0.0, 1.0, 0.87],  accent: [0.0, 0.73, 0.67], bg: [0.0, 0.05, 0.04] },
    perceiving:   { core: [0.73, 0.27, 1.0], accent: [0.33, 0.0, 0.8],  bg: [0.04, 0.0, 0.08] },
    thinking:     { core: [0.49, 0.23, 1.0], accent: [0.2, 0.0, 0.73],  bg: [0.02, 0.0, 0.06] },
    planning:     { core: [0.4, 0.13, 1.0],  accent: [0.13, 0.0, 0.67], bg: [0.02, 0.0, 0.05] },
    verifying:    { core: [0.33, 0.27, 1.0], accent: [0.13, 0.07, 0.8], bg: [0.02, 0.0, 0.05] },
    transcribing: { core: [0.27, 0.67, 1.0], accent: [0.0, 0.4, 0.8],   bg: [0.0, 0.03, 0.08] },
    working:      { core: [0.0, 0.53, 1.0],  accent: [0.0, 0.27, 0.8],  bg: [0.0, 0.03, 0.07] },
    acting:       { core: [0.0, 0.6, 1.0],   accent: [0.0, 0.33, 0.87], bg: [0.0, 0.04, 0.09] },
    responding:   { core: [0.33, 0.8, 1.0],  accent: [0.07, 0.47, 0.8], bg: [0.0, 0.04, 0.09] },
    success:      { core: [0.0, 1.0, 0.53],  accent: [0.0, 0.8, 0.4],   bg: [0.0, 0.06, 0.02] },
    warning:      { core: [1.0, 0.67, 0.0],  accent: [0.8, 0.33, 0.0],  bg: [0.08, 0.04, 0.0] },
    error:        { core: [1.0, 0.2, 0.0],   accent: [0.6, 0.0, 0.0],   bg: [0.08, 0.0, 0.0] },
    offline:      { core: [1.0, 0.13, 0.0],  accent: [0.47, 0.0, 0.0],  bg: [0.06, 0.0, 0.0] },
  };

  class HolographicCore3D {
    constructor(canvasContainer, canvasElement) {
      this.container = canvasContainer || canvasElement.parentElement;
      this.canvas = canvasElement;
      this.isWebGLAvailable = this.checkWebGL();
      if (!this.isWebGLAvailable) {
        console.warn("WebGL not available; 3D Holographic Core fallback active.");
        return;
      }

      this.state = "booting";
      this.displayMode = "hologram"; // "hologram", "orbit", "wireframe", "quantum"
      this.autoOrbit = true;

      // Audio / speech state
      this.speaking = false;
      this.speechDuration = 0;
      this.speechStartedAt = 0;
      this.speechEnvelope = [];
      this.speechMix = 0;
      this.speechLevel = 0;
      this.bars = new Float32Array(24);
      this.bass = 0;
      this.bassAverage = 0;
      this.shocks = [];

      // Color interpolation
      this.currentColor = {
        r: 0.11, g: 1.0, b: 1.0,
        ar: 0.04, ag: 0.3, ab: 1.0
      };
      this.targetColor = { ...this.currentColor };

      // Pointer / Interaction State
      this.pointer = { x: 0, y: 0, targetX: 0, targetY: 0 };
      this.drag = {
        isDragging: false,
        startX: 0, startY: 0,
        rotX: 0, rotY: 0,
        targetRotX: 0, targetRotY: 0
      };
      this.zoom = { current: 1.0, target: 1.0, min: 0.65, max: 1.8 };

      this.initThree();
      this.buildHologram();
      this.bindEvents();
      this.startLoop();
    }

    checkWebGL() {
      try {
        if (!window.THREE) return false;
        const testCanvas = document.createElement("canvas");
        return Boolean(
          window.WebGLRenderingContext &&
          (testCanvas.getContext("webgl") || testCanvas.getContext("experimental-webgl"))
        );
      } catch (e) {
        return false;
      }
    }

    initThree() {
      const rect = this.container.getBoundingClientRect();
      this.width = Math.max(10, rect.width || 600);
      this.height = Math.max(10, rect.height || 600);

      this.scene = new THREE.Scene();
      this.camera = new THREE.PerspectiveCamera(45, this.width / this.height, 0.1, 1000);
      this.camera.position.set(0, 0, 32);

      this.renderer = new THREE.WebGLRenderer({
        canvas: this.canvas,
        alpha: true,
        antialias: true,
        powerPreference: "high-performance",
      });

      this.dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.renderer.setSize(this.width, this.height, false);
      this.renderer.setPixelRatio(this.dpr);
      this.renderer.setClearColor(0x000000, 0);

      // Root holographic group for full scene tilt & rotation
      this.hologramRoot = new THREE.Group();
      this.scene.add(this.hologramRoot);

      // Ambient & point lights
      this.ambientLight = new THREE.AmbientLight(0xffffff, 0.4);
      this.scene.add(this.ambientLight);

      this.coreLight = new THREE.PointLight(0x00ffff, 2.5, 50);
      this.coreLight.position.set(0, 0, 0);
      this.hologramRoot.add(this.coreLight);
    }

    buildHologram() {
      // -------------------------------------------------------------
      // 1. Central Geodesic Singularity Core
      // -------------------------------------------------------------
      const coreGeo = new THREE.IcosahedronGeometry(4.2, 2);
      this.corePositionsOrig = coreGeo.attributes.position.clone();

      this.coreWireMat = new THREE.MeshBasicMaterial({
        color: 0x1cffff,
        wireframe: true,
        transparent: true,
        opacity: 0.75,
        blending: THREE.AdditiveBlending,
      });
      this.coreMesh = new THREE.Mesh(coreGeo, this.coreWireMat);
      this.hologramRoot.add(this.coreMesh);

      // Inner solid glow sphere
      const innerGeo = new THREE.SphereGeometry(2.8, 24, 24);
      this.innerGlowMat = new THREE.MeshBasicMaterial({
        color: 0x00aaff,
        transparent: true,
        opacity: 0.35,
        blending: THREE.AdditiveBlending,
      });
      this.innerSphere = new THREE.Mesh(innerGeo, this.innerGlowMat);
      this.hologramRoot.add(this.innerSphere);

      // -------------------------------------------------------------
      // 2. Quantum Particle Swarm (1,200+ particles)
      // -------------------------------------------------------------
      const particleCount = 1250;
      const partGeo = new THREE.BufferGeometry();
      const positions = new Float32Array(particleCount * 3);
      const velocities = new Float32Array(particleCount * 3);
      const phases = new Float32Array(particleCount);
      const radii = new Float32Array(particleCount);

      for (let i = 0; i < particleCount; i++) {
        const u = Math.random();
        const v = Math.random();
        const theta = u * 2.0 * Math.PI;
        const phi = Math.acos(2.0 * v - 1.0);
        const r = 5.2 + Math.pow(Math.random(), 1.6) * 9.5;

        positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
        positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
        positions[i * 3 + 2] = r * Math.cos(phi);

        velocities[i * 3] = (Math.random() - 0.5) * 0.03;
        velocities[i * 3 + 1] = (Math.random() - 0.5) * 0.03;
        velocities[i * 3 + 2] = (Math.random() - 0.5) * 0.03;

        phases[i] = Math.random() * Math.PI * 2;
        radii[i] = r;
      }

      partGeo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      this.particleData = { velocities, phases, radii, origPositions: positions.slice() };

      this.particleMat = new THREE.PointsMaterial({
        color: 0x1cffff,
        size: 0.28,
        transparent: true,
        opacity: 0.85,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      });

      this.particleSystem = new THREE.Points(partGeo, this.particleMat);
      this.hologramRoot.add(this.particleSystem);

      // -------------------------------------------------------------
      // 3. Concentric Gyroscopic HUD Arc Rings
      // -------------------------------------------------------------
      this.rings = [];
      const ringConfigs = [
        { radius: 7.2, tube: 0.04, axis: new THREE.Vector3(1, 0.4, 0), speed: 0.55, dashes: true },
        { radius: 9.6, tube: 0.05, axis: new THREE.Vector3(0, 1, 0.5), speed: -0.42, dashes: false },
        { radius: 12.0, tube: 0.06, axis: new THREE.Vector3(0.5, 0, 1), speed: 0.32, dashes: true },
      ];

      ringConfigs.forEach((cfg, idx) => {
        const ringGeo = new THREE.TorusGeometry(cfg.radius, cfg.tube, 8, 80);
        const ringMat = new THREE.MeshBasicMaterial({
          color: 0x1cffff,
          transparent: true,
          opacity: 0.65,
          blending: THREE.AdditiveBlending,
          wireframe: cfg.dashes,
        });
        const mesh = new THREE.Mesh(ringGeo, ringMat);
        mesh.rotation.x = idx * 0.75;
        mesh.rotation.y = idx * 0.5;
        this.hologramRoot.add(mesh);
        this.rings.push({ mesh, cfg, mat: ringMat });
      });

      // HUD Circular Ticks Ring
      const tickGeo = new THREE.BufferGeometry();
      const tickPositions = [];
      const tickCount = 64;
      const tickRadius = 13.8;
      for (let i = 0; i < tickCount; i++) {
        if (i % 4 === 1) continue;
        const angle = (i / tickCount) * Math.PI * 2;
        const len = (i % 8 === 0) ? 0.8 : (i % 2 === 0 ? 0.4 : 0.2);
        const x1 = Math.cos(angle) * tickRadius;
        const y1 = Math.sin(angle) * tickRadius;
        const x2 = Math.cos(angle) * (tickRadius + len);
        const y2 = Math.sin(angle) * (tickRadius + len);
        tickPositions.push(x1, y1, 0, x2, y2, 0);
      }
      tickGeo.setAttribute("position", new THREE.Float32BufferAttribute(tickPositions, 3));
      this.tickMat = new THREE.LineBasicMaterial({
        color: 0x1cffff,
        transparent: true,
        opacity: 0.45,
        blending: THREE.AdditiveBlending,
      });
      this.tickLines = new THREE.LineSegments(tickGeo, this.tickMat);
      this.hologramRoot.add(this.tickLines);

      // -------------------------------------------------------------
      // 4. Vertical Oscillating Planar Holographic Laser Scanner
      // -------------------------------------------------------------
      const scanGeo = new THREE.RingGeometry(0.5, 14.5, 48, 2);
      this.scanMat = new THREE.MeshBasicMaterial({
        color: 0x7c3aff,
        transparent: true,
        opacity: 0.25,
        side: THREE.DoubleSide,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      });
      this.scanPlane = new THREE.Mesh(scanGeo, this.scanMat);
      this.scanPlane.rotation.x = Math.PI / 2;
      this.hologramRoot.add(this.scanPlane);

      // -------------------------------------------------------------
      // 5. Shockwave Torus
      // -------------------------------------------------------------
      const shockGeo = new THREE.TorusGeometry(1, 0.08, 6, 64);
      this.shockMat = new THREE.MeshBasicMaterial({
        color: 0x00ffff,
        transparent: true,
        opacity: 0,
        blending: THREE.AdditiveBlending,
      });
      this.shockMesh = new THREE.Mesh(shockGeo, this.shockMat);
      this.hologramRoot.add(this.shockMesh);
    }

    bindEvents() {
      const el = this.container;

      // Mouse move / Parallax
      window.addEventListener("pointermove", (e) => {
        const x = (e.clientX / window.innerWidth) * 2 - 1;
        const y = -(e.clientY / window.innerHeight) * 2 + 1;
        this.pointer.targetX = x * 0.35;
        this.pointer.targetY = y * 0.25;

        if (this.drag.isDragging) {
          const dx = e.clientX - this.drag.startX;
          const dy = e.clientY - this.drag.startY;
          this.drag.targetRotY += dx * 0.007;
          this.drag.targetRotX += dy * 0.007;
          this.drag.startX = e.clientX;
          this.drag.startY = e.clientY;
        }
      }, { passive: true });

      // Mouse drag controls
      el.addEventListener("pointerdown", (e) => {
        this.drag.isDragging = true;
        this.drag.startX = e.clientX;
        this.drag.startY = e.clientY;
        el.style.cursor = "grabbing";
      });

      window.addEventListener("pointerup", () => {
        this.drag.isDragging = false;
        el.style.cursor = "grab";
      });

      // Mouse Wheel Zoom
      el.addEventListener("wheel", (e) => {
        e.preventDefault();
        const delta = e.deltaY * 0.0012;
        this.zoom.target = Math.max(this.zoom.min, Math.min(this.zoom.max, this.zoom.target - delta));
      }, { passive: false });

      // Click to Pulse
      el.addEventListener("click", (e) => {
        if (Math.abs(e.clientX - this.drag.startX) < 4 && Math.abs(e.clientY - this.drag.startY) < 4) {
          this.triggerPulse(1.4);
        }
      });

      // Resize observer
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(this.container);
    }

    resize() {
      if (!this.renderer || !this.camera) return;
      const rect = this.container.getBoundingClientRect();
      this.width = Math.max(10, rect.width);
      this.height = Math.max(10, rect.height);
      this.camera.aspect = this.width / this.height;
      this.camera.updateProjectionMatrix();
      this.renderer.setSize(this.width, this.height, false);
    }

    // -------------------------------------------------------------
    // State & Audio API
    // -------------------------------------------------------------

    setState(name) {
      if (!STATE_PALETTES[name]) name = "working";
      this.state = name;
      const pal = STATE_PALETTES[name];
      this.targetColor = {
        r: pal.core[0], g: pal.core[1], b: pal.core[2],
        ar: pal.accent[0], ag: pal.accent[1], ab: pal.accent[2]
      };
      this.triggerPulse(1.0);
    }

    setDisplayMode(mode) {
      this.displayMode = mode || "hologram";
      if (this.displayMode === "wireframe") {
        this.coreMesh.visible = true;
        this.innerSphere.visible = false;
        this.particleSystem.visible = false;
        this.rings.forEach(r => r.mesh.visible = true);
      } else if (this.displayMode === "quantum") {
        this.coreMesh.visible = false;
        this.innerSphere.visible = true;
        this.particleSystem.visible = true;
        this.rings.forEach(r => r.mesh.visible = false);
      } else if (this.displayMode === "orbit") {
        this.coreMesh.visible = true;
        this.innerSphere.visible = true;
        this.particleSystem.visible = true;
        this.rings.forEach(r => r.mesh.visible = true);
        this.autoOrbit = true;
      } else { // full hologram
        this.coreMesh.visible = true;
        this.innerSphere.visible = true;
        this.particleSystem.visible = true;
        this.rings.forEach(r => r.mesh.visible = true);
      }
    }

    resetView() {
      this.drag.targetRotX = 0;
      this.drag.targetRotY = 0;
      this.zoom.target = 1.0;
      this.triggerPulse(1.2);
    }

    triggerPulse(power = 1.0) {
      const now = performance.now() / 1000;
      this.shocks.push({ born: now, power, duration: 1.2 });
    }

    setSpeaking(payload) {
      this.speaking = Boolean(payload?.active);
      if (this.speaking) {
        this.speechDuration = Math.max(0.2, (Number(payload.durationMs) || 0) / 1000);
        const elapsed = Math.max(0, (Number(payload.elapsedMs) || 0) / 1000);
        this.speechStartedAt = (performance.now() / 1000) - elapsed;
        this.speechEnvelope = Array.isArray(payload.levels)
          ? payload.levels.slice(0, 96).map(v => Math.max(0, Math.min(1, Number(v) / 255)))
          : [];
      } else {
        this.speechEnvelope = [];
      }
    }

    updateAudio(t, dt) {
      let rawLevel = 0;
      if (this.speaking && this.speechEnvelope.length) {
        const prog = Math.max(0, Math.min(1, (t - this.speechStartedAt) / this.speechDuration));
        const idx = prog * (this.speechEnvelope.length - 1);
        const lower = Math.floor(idx);
        const upper = Math.min(this.speechEnvelope.length - 1, lower + 1);
        const blend = idx - lower;
        rawLevel = this.speechEnvelope[lower] * (1 - blend) + this.speechEnvelope[upper] * blend;
      } else if (this.speaking) {
        rawLevel = 0.5 + Math.sin(t * 8.0) * 0.3;
      }

      this.speechMix += ((this.speaking ? 1 : 0) - this.speechMix) * Math.min(1, 0.15 * dt);
      this.speechLevel += (rawLevel - this.speechLevel) * Math.min(1, 0.22 * dt);

      // Synthesize spectrogram frequency bins
      for (let i = 0; i < 24; i++) {
        const tilt = 1 - (i / 24) * 0.5;
        const wobble = Math.abs(Math.sin(i * 0.8 + t * 6.0));
        const target = this.speechLevel * tilt * wobble;
        this.bars[i] += (target - this.bars[i]) * Math.min(1, 0.35 * dt);
      }

      this.bass = (this.bars[0] + this.bars[1] + this.bars[2]) / 3;
      this.bassAverage += (this.bass - this.bassAverage) * Math.min(1, 0.05 * dt);

      if (this.bass > 0.35 && this.bass > this.bassAverage * 1.5 && this.speechMix > 0.3) {
        this.triggerPulse(Math.min(1.5, this.bass * 2.0));
      }
    }

    // -------------------------------------------------------------
    // Animation Loop
    // -------------------------------------------------------------

    startLoop() {
      let lastTime = performance.now();

      const animate = (currentTime) => {
        requestAnimationFrame(animate);

        const dt = Math.min(40, currentTime - lastTime) / 16.67;
        lastTime = currentTime;
        const t = currentTime / 1000;

        this.updateAudio(t, dt);
        this.updateColors(dt);
        this.updateTransforms(t, dt);
        this.updateSingularity(t);
        this.updateParticles(t, dt);
        this.updateRings(t, dt);
        this.updateScanner(t);
        this.updateShockwaves(t);

        this.renderer.render(this.scene, this.camera);
      };

      requestAnimationFrame(animate);
    }

    updateColors(dt) {
      const lerpSpeed = Math.min(1, 0.08 * dt);
      const cur = this.currentColor;
      const tgt = this.targetColor;

      cur.r += (tgt.r - cur.r) * lerpSpeed;
      cur.g += (tgt.g - cur.g) * lerpSpeed;
      cur.b += (tgt.b - cur.b) * lerpSpeed;
      cur.ar += (tgt.ar - cur.ar) * lerpSpeed;
      cur.ag += (tgt.ag - cur.ag) * lerpSpeed;
      cur.ab += (tgt.ab - cur.ab) * lerpSpeed;

      const cColor = new THREE.Color(cur.r, cur.g, cur.b);
      const aColor = new THREE.Color(cur.ar, cur.ag, cur.ab);

      this.coreWireMat.color.copy(cColor);
      this.innerGlowMat.color.copy(aColor);
      this.particleMat.color.copy(cColor);
      this.tickMat.color.copy(cColor);
      this.scanMat.color.copy(aColor);
      this.shockMat.color.copy(cColor);
      this.coreLight.color.copy(cColor);

      this.rings.forEach(r => r.mat.color.copy(cColor));
    }

    updateTransforms(t, dt) {
      // Smooth Pointer Parallax
      this.pointer.x += (this.pointer.targetX - this.pointer.x) * 0.05 * dt;
      this.pointer.y += (this.pointer.targetY - this.pointer.y) * 0.05 * dt;

      // Smooth Drag Rotation
      this.drag.rotX += (this.drag.targetRotX - this.drag.rotX) * 0.08 * dt;
      this.drag.rotY += (this.drag.targetRotY - this.drag.rotY) * 0.08 * dt;

      // Smooth Zoom
      this.zoom.current += (this.zoom.target - this.zoom.current) * 0.08 * dt;
      this.camera.position.z = 32 / this.zoom.current;

      // Auto-Orbit drift
      const autoDrift = this.autoOrbit && !this.drag.isDragging ? t * 0.15 : 0;

      this.hologramRoot.rotation.x = this.pointer.y + this.drag.rotX;
      this.hologramRoot.rotation.y = this.pointer.x + this.drag.rotY + autoDrift;
    }

    updateSingularity(t) {
      const beat = 1 + Math.sin(t * 2.2) * 0.05 + this.speechMix * this.speechLevel * 0.35;
      this.coreMesh.scale.set(beat, beat, beat);
      this.innerSphere.scale.set(beat * 0.95, beat * 0.95, beat * 0.95);

      this.coreMesh.rotation.x = t * 0.22;
      this.coreMesh.rotation.y = t * 0.35;
      this.innerSphere.rotation.y = -t * 0.4;
    }

    updateParticles(t, dt) {
      const posAttr = this.particleSystem.geometry.attributes.position;
      const positions = posAttr.array;
      const { velocities, phases, radii, origPositions } = this.particleData;
      const count = positions.length / 3;

      const voiceDeform = this.speechMix * this.speechLevel * 2.8;

      for (let i = 0; i < count; i++) {
        const i3 = i * 3;
        const phase = phases[i];
        const rOrig = radii[i];

        // Orbit around Y axis
        const speed = (0.2 + (i % 5) * 0.1) * (i % 2 === 0 ? 1 : -1);
        const theta = t * speed + phase;

        const currentR = rOrig + Math.sin(t * 3.0 + phase) * 0.4 + voiceDeform;

        positions[i3] = Math.cos(theta) * currentR;
        positions[i3 + 1] = origPositions[i3 + 1] + Math.sin(t * 1.5 + phase) * 0.6;
        positions[i3 + 2] = Math.sin(theta) * currentR;
      }

      posAttr.needsUpdate = true;
      this.particleSystem.rotation.z = t * 0.08;
    }

    updateRings(t, dt) {
      const stateSpeedMultiplier = (this.state === "acting" || this.state === "thinking") ? 2.2 : (this.state === "perceiving" ? 1.6 : 1.0);

      this.rings.forEach((r, idx) => {
        const spin = r.cfg.speed * stateSpeedMultiplier * dt * 0.03;
        r.mesh.rotateOnAxis(r.cfg.axis, spin);
        const voicePulse = 1 + (this.bars[idx * 4] || 0) * 0.25 * this.speechMix;
        r.mesh.scale.set(voicePulse, voicePulse, voicePulse);
      });

      this.tickLines.rotation.z = -t * 0.05 * stateSpeedMultiplier;
    }

    updateScanner(t) {
      const isScanning = ["perceiving", "verifying", "thinking", "acting"].includes(this.state);
      this.scanPlane.visible = isScanning || this.displayMode === "hologram";

      if (this.scanPlane.visible) {
        this.scanPlane.position.y = Math.sin(t * 1.8) * 8.5;
        this.scanMat.opacity = isScanning ? (0.35 + Math.sin(t * 4.0) * 0.15) : 0.15;
      }
    }

    updateShockwaves(t) {
      this.shocks = this.shocks.filter(s => t - s.born < s.duration);
      if (this.shocks.length > 0) {
        const latest = this.shocks[this.shocks.length - 1];
        const progress = (t - latest.born) / latest.duration;
        const scale = (0.5 + Math.pow(progress, 0.7) * 16.0) * latest.power;
        const opacity = (1 - progress) * 0.75 * latest.power;

        this.shockMesh.scale.set(scale, scale, scale);
        this.shockMat.opacity = Math.max(0, opacity);
        this.shockMesh.visible = true;
      } else {
        this.shockMesh.visible = false;
      }
    }
  }

  // Export to window
  window.HolographicCore3D = HolographicCore3D;
})();
