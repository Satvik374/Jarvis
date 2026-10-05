/**
 * Retina, motor readout and the agent that binds the connectome to the world.
 *
 * Retina   -- drives the real R1-6/R7/R8 photoreceptors from a luminance field
 *             sampled at each photoreceptor's own receptive-field direction.
 * Readout  -- a population decoder over the real descending neurons.  Its
 *             weights are derived from anatomy only: which T4/T5 direction
 *             channels each descending neuron receives, and where in the
 *             visual field its receptive field sits.  See `buildReadout`.
 * FlyAgent -- closes the sensorimotor loop.
 */

/** Photoreceptors, their visual-field direction and the injection gain. */
export class Retina {
  constructor(brain, opts = {}) {
    const photo = brain.groups.photoreceptors;
    this.ids = photo;
    const n = photo.length;
    this.az = new Float32Array(n);
    this.el = new Float32Array(n);
    this.side = new Int8Array(n);
    for (let k = 0; k < n; k++) {
      const i = photo[k];
      this.az[k] = Number.isFinite(brain.az[i]) ? brain.az[i] : 0;
      this.el[k] = Number.isFinite(brain.el[i]) ? brain.el[i] : 0;
      this.side[k] = brain.side[i];
    }
    this.fovW = opts.fovW ?? 260;    // world pixels spanned horizontally
    this.fovH = opts.fovH ?? 300;    // world pixels spanned vertically
    // Photoreceptors are graded, non-spiking cells in the real fly.  Here they
    // are the LIF units of the published model, so the receptor current must be
    // scaled into that model's range: an input of i mV moves the resting
    // potential by about i * tau_m / dt = 20 mV.
    this.gain = opts.gain ?? 0.62;   // mV per unit luminance
    this.offset = opts.offset ?? 0.15;
    this.mean = opts.mean ?? 0.5;    // adapting luminance
    this.adapt = opts.adapt ?? 0;    // 0 = do not subtract the running mean
    this._mean = this.mean;
    this.adaptTau = opts.adaptTau ?? 0.7;
    this._last = new Float32Array(n);
  }

  /**
   * @param eye  (az, el) -> luminance in [0, 1]
   */
  update(brain, eye, dt) {
    const n = this.ids.length;
    let sum = 0;
    for (let k = 0; k < n; k++) {
      const lum = eye(this.az[k], this.el[k], this.side[k]);
      this._last[k] = lum;
      sum += lum;
    }
    if (this.adapt > 0) {
      const m = sum / n;
      this._mean += (m - this._mean) * (1 - Math.exp(-dt / this.adaptTau));
    }
    const iExt = brain.iExt;
    for (let k = 0; k < n; k++) {
      const i = this.ids[k];
      iExt[i] = this.offset + this.gain * (this._last[k] - this._mean);
    }
  }
}

/**
 * Population readout over the descending neurons.
 *
 * Every weight here is a function of the wiring diagram, never of the game:
 *   `channelDir[i][ch]`  how much of neuron i's input comes from each of the
 *                        four T4/T5 direction channels (propagated along the
 *                        connectome in build/make_circuit.py),
 *   `fieldEl[i]`         the elevation of neuron i's receptive-field centre,
 *                        also read off the connectome.
 *
 * `channelSign` says which way each elementary motion detector points; it is
 * measured by running the moving-stimulus physiology experiment on the model
 * (see tests/physiology.js), not assumed.
 */
export function buildReadout(brain, channelSign, opts = {}) {
  const dn = brain.groups.descending;
  const ids = [];
  for (let k = 0; k < dn.length; k++) {
    const i = dn[k];
    if (Number.isFinite(brain.el[i])) ids.push(i);
  }
  const n = ids.length;
  const drive = new Float32Array(n);  // how visually driven each DN is
  const el = new Float32Array(n);     // where in the field its RF sits
  const vert = new Float32Array(n);   // up vs down T4/T5 channel balance
  const hor = new Float32Array(n);    // horizontal T4/T5 channel balance
  const dir = brain.dir;              // (N, 8), one column per T4/T5 subtype
  const upCols = [], downCols = [], horCols = [];
  for (let s = 0; s < 8; s++) {
    const sg = channelSign[s];
    if (sg === 2) horCols.push(s);
    else if (sg > 0) upCols.push(s);
    else if (sg < 0) downCols.push(s);
  }

  let elSum = 0;
  for (let k = 0; k < n; k++) elSum += brain.el[ids[k]];
  const elMean = n ? elSum / n : 0;

  for (let k = 0; k < n; k++) {
    const i = ids[k];
    let d = 0, u = 0, dn2 = 0, h = 0;
    for (let s = 0; s < 8; s++) {
      const v = dir[i * 8 + s];
      if (!v) continue;
      d += v;
      if (upCols.includes(s)) u += v;
      if (downCols.includes(s)) dn2 += v;
      if (horCols.includes(s)) h += v;
    }
    drive[k] = d;
    vert[k] = u - dn2;
    hor[k] = h;
    el[k] = brain.el[i] - elMean;
  }

  const norm = (a) => {
    let s = 0;
    for (let k = 0; k < a.length; k++) s += a[k] * a[k];
    s = Math.sqrt(s) || 1;
    for (let k = 0; k < a.length; k++) a[k] /= s;
    return a;
  };
  // Vertical position of what the eye is looking at.  Bright sky only reaches
  // the photoreceptors through the gap in the pipes, so the elevation-weighted
  // visual drive of the descending population is an estimate of where the gap
  // sits relative to the heading -- obtained without needing direction
  // selectivity at all.
  const pos = new Float32Array(n);
  for (let k = 0; k < n; k++) pos[k] = drive[k] * el[k];
  norm(pos);
  const vel = new Float32Array(n);
  for (let k = 0; k < n; k++) vel[k] = vert[k];
  norm(vel);
  const horw = new Float32Array(n);
  for (let k = 0; k < n; k++) horw[k] = hor[k] * -el[k];
  norm(horw);

  return { ids: Uint32Array.from(ids), wPos: pos, wVel: vel, wHor: horw, n, elMean,
           drive, el, vert, hor };
}

export function readCommand(brain, ro) {
  const { ids, wVel, wPos } = ro;
  const rate = brain.rate;
  let vel = 0, pos = 0;
  for (let k = 0; k < ids.length; k++) {
    const r = rate[ids[k]];
    vel += wVel[k] * r;
    pos += wPos[k] * r;
  }
  return { vel, pos };
}

/**
 * Motor stage: turns the descending-neuron readout into wingbeats.
 *
 * The gain on each channel and the flap threshold are free parameters of the
 * model, exactly as they would be in a real animal.  The *signs* are not: a
 * positive velocity command means "the world is streaming upwards over my
 * eyes, so I am falling, so beat the wings", and a positive position command
 * means "the horizontally moving texture sits below my heading, so the gap is
 * above me".  Those follow from the anatomy, not from the game.
 */
export class Motor {
  /**
   * Wingbeat generator.
   *
   * In a fly the wingbeat is continuous and the descending neurons modulate its
   * rate and amplitude; thrust follows.  We model that directly: the
   * descending-neuron readout sets the wingbeat rate, and each wingbeat is a
   * discrete upward impulse.
   *
   *   lambda = lambda_hover * exp(rateGain * command)
   *
   * `lambda_hover` is just gravity / (impulse per wingbeat) -- the rate at which
   * the body has to beat its wings to hold altitude.  It is a property of the
   * body, not of the brain, and it is the only calibration on the motor side.
   * A positive command (gap above, or the world streaming upwards over the
   * eyes) raises the wingbeat rate and the fly climbs.
   */
  constructor(o = {}) {
    this.kVel = o.kVel ?? 0.0;
    this.kPos = o.kPos ?? 1.0;
    this.hoverRate = o.hoverRate ?? 1.74;  // Hz needed to hold altitude
    this.rateGain = o.rateGain ?? 0.45;    // e-fold change per unit command
    this.maxRate = o.maxRate ?? 22;        // Hz, wingbeat ceiling
    this.minInterval = o.minInterval ?? 0.028;
    // Two different timescales, and the difference matters.  The *scale* is
    // normalised quickly, so the decoder's arbitrary units never have to be
    // matched by hand.  The *mean* is removed only very slowly: a fast running
    // mean would subtract away the very thing the fly needs, namely the steady
    // offset between where the gap is and where the fly is pointing.
    this.adaptTau = o.adaptTau ?? 0.30;   // scale (fast)
    this.meanTau = o.meanTau ?? 0.0;      // 0 = frozen reference direction
    this.smoothTau = o.smoothTau ?? 0.0; // motor integration of the command
    this._smooth = 0;
    this.nFlaps = 0;
    this.phase = 0;
    this._last = -1e9;
    this._mean = 0;
    this._mad = 1;
    this._abs = 1;
    this._init = false;
    this.command = 0;
    this.normalised = 0;
    this.wingbeatRate = this.hoverRate;
  }

  /**
   * Set the reference direction from a batch of commands recorded while the
   * fly looked at a neutral scene.  Afterwards `command - mean` is zero when
   * nothing interesting is in front of the eye and the wings beat at exactly
   * the rate that holds altitude.  This is the fly's straight-ahead reference,
   * and it is the only thing on the sensory side that is calibrated rather than
   * derived from the connectome.
   */
  calibrate(commands) {
    if (!commands.length) return this;
    let s = 0, a = 0;
    for (const c of commands) s += c;
    const mean = s / commands.length;
    for (const c of commands) a += Math.abs(c - mean);
    this._mean = mean;
    this._mad = Math.max(a / commands.length, 1e-6);
    let ab = 0;
    for (const c of commands) ab += Math.abs(c);
    this._abs = Math.max(ab / commands.length, 1e-6);
    this._init = true;
    return this;
  }

  /**
   * @param cmd  {vel, pos} descending-neuron readout
   * @param t    simulated clock (s)
   * @returns    true if the fly beats its wings on this step
   */
  update(cmd, t, dt) {
    let c = this.kVel * cmd.vel + this.kPos * cmd.pos;
    // Descending commands are integrated by the thoracic circuits before they
    // reach the muscles, which also averages away single-spike jitter.
    const ks = this.smoothTau > 0 ? 1 - Math.exp(-dt / this.smoothTau) : 1;
    this._smooth += (c - this._smooth) * ks;
    c = this._smooth;
    this.command = c;
    // The decoder's units are arbitrary, so the command is normalised on-line
    // against its own running mean and spread.  Real sensory systems do the
    // same thing, and it means the *sign* of the command carries the
    // information, not its absolute scale.
    const k = 1 - Math.exp(-dt / this.adaptTau);
    const km = this.meanTau > 0 ? 1 - Math.exp(-dt / this.meanTau) : 0;
    if (!this._init) {
      this._mean = c;
      this._abs = Math.max(Math.abs(c), 1e-6);
      this._mad = this._abs;
      this._init = true;
    }
    this._mean += (c - this._mean) * km;
    this._mad += (Math.abs(c - this._mean) - this._mad) * k;
    this._abs += (Math.abs(c) - this._abs) * k;
    const denom = Math.max(this._mad, 0.08 * this._abs, 1e-6);
    let cn = (c - this._mean) / denom;
    if (cn > 6) cn = 6; else if (cn < -6) cn = -6;
    this.normalised = cn;

    let lambda = this.hoverRate * Math.exp(this.rateGain * cn);
    if (lambda > this.maxRate) lambda = this.maxRate;
    if (lambda < 0.05) lambda = 0.05;
    this.wingbeatRate = lambda;

    let flap = false;
    this.phase += lambda * dt;
    if (this.phase >= 1 && t - this._last >= this.minInterval) {
      flap = true;
      this.phase = 0;
      this._last = t;
      this.nFlaps++;
    }
    return flap;
  }
}
