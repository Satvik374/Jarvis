/**
 * FlyBrain -- a leaky integrate-and-fire simulation running on the real
 * FlyWire connectome sprite produced by build/make_circuit.py.
 *
 * Model equations follow the published whole-brain model of
 * Shiu et al., Nature 2024 ("A Drosophila computational brain model reveals
 * sensorimotor processing"), which is itself built on FlyWire 783:
 *
 *     tau_m dv/dt = (v_rest - v + g)
 *     tau   dg/dt = -g
 *     a presynaptic spike at a synapse of weight w:   g += w
 *
 * v_rest = -52 mV, v_th = -45 mV, refractory 2.2 ms, tau_m = 20 ms, and
 * 0.275 mV per synapse; synaptic transmission takes 1.8 ms (Paul et al. 2015).
 * Weights come straight from the connectome: w = 0.275 mV x (signed synapse
 * count), the sign being the predicted transmitter of the presynaptic cell.
 *
 * Nothing in this file knows anything about Flappy Bird.
 * Runs unchanged in Node and in the browser (plain ES module, typed arrays).
 */

export const DT = 0.001; // seconds per simulation step

const DTYPES = {
  0: Float32Array, 1: Float64Array, 2: Int32Array, 3: Uint32Array,
  4: Int8Array, 5: Uint8Array, 6: BigInt64Array, 7: Uint16Array,
};

export function parseCircuit(buffer) {
  const view = new DataView(buffer);
  const magic = new TextDecoder().decode(new Uint8Array(buffer, 0, 8));
  if (magic !== 'FLYBRN01') throw new Error('bad circuit container: ' + magic);
  let o = 8;
  const jsonLen = view.getUint32(o, true); o += 4;
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, o, jsonLen)));
  o += jsonLen;
  const nBlocks = view.getUint32(o, true); o += 4;
  const arrays = {};
  for (let b = 0; b < nBlocks; b++) {
    const nameLen = view.getUint32(o, true); o += 4;
    const name = new TextDecoder().decode(new Uint8Array(buffer, o, nameLen)); o += nameLen;
    const dtypeCode = view.getUint8(o); o += 1;
    const ndim = view.getUint8(o); o += 1;
    const shape = [];
    for (let d = 0; d < ndim; d++) { shape.push(view.getUint32(o, true)); o += 4; }
    const nbytes = view.getUint32(o, true); o += 4;
    const Ctor = DTYPES[dtypeCode];
    arrays[name] = new Ctor(buffer.slice(o, o + nbytes));
    o += nbytes;
  }
  return { meta, arrays };
}

/** Sorted-by-presynaptic edge index: a CSR representation of the connectome. */
export function buildCsr(pre, post, w, n, delayOf) {
  const E = pre.length;
  const indptr = new Int32Array(n + 1);
  for (let e = 0; e < E; e++) indptr[pre[e] + 1]++;
  for (let i = 0; i < n; i++) indptr[i + 1] += indptr[i];
  const cursor = Int32Array.from(indptr.subarray(0, n));
  const indices = new Int32Array(E);
  const data = new Float32Array(E);
  const slotEdge = new Int32Array(E);
  const delay = new Uint8Array(E);
  for (let e = 0; e < E; e++) {
    const slot = cursor[pre[e]]++;
    indices[slot] = post[e];
    data[slot] = w[e];
    slotEdge[slot] = e;
    delay[slot] = delayOf ? delayOf(e) : 0;
  }
  return { indptr, indices, data, slotEdge, delay };
}

/**
 * Synaptic decay constants (s) assigned per cell type.
 *
 * Arenz et al. 2017 (Curr Biol 27:929) measured the temporal kernels of the
 * interneurons that feed the T4/T5 elementary motion detectors: cells on the
 * preferred and null side of a T4/T5 dendrite are low-pass, while the central
 * ones are band-pass.  Direction selectivity is built out of exactly that
 * temporal asymmetry, so a network with one single uniform time constant is
 * blind to motion direction no matter what its wiring looks like.  Types not
 * listed here use the published model default of 5 ms.
 */
export const TAU_BY_TYPE = {
  L1: 0.014, L2: 0.014, L3: 0.020, L4: 0.020, L5: 0.020,
  Mi1: 0.024, Mi2: 0.032, Mi4: 0.032, Mi9: 0.038, Mi10: 0.038, Mi15: 0.038,
  Tm1: 0.024, Tm2: 0.024, Tm3: 0.032, Tm4: 0.045, Tm9: 0.050,
  T2: 0.032, T3: 0.032,
  'R1-6': 0.010, R7: 0.010, R8: 0.010,
};

export class FlyBrain {
  constructor(circuit, opts = {}) {
    const { meta, arrays } = circuit;
    this.meta = meta;
    const n = this.N = meta.n_neurons;

    this.cellTypes = meta.cell_types;
    this.types = new Array(n);
    for (let i = 0; i < n; i++) this.types[i] = meta.cell_types[arrays.ctid[i]];
    this.pre = arrays.pre;
    this.post = arrays.post;
    this.wSyn = arrays.w;
    this.pos = arrays.pos;
    this.az = arrays.az;
    this.el = arrays.el;
    this.dir = arrays.dir;
    this.dir4 = arrays.dir4;
    this.side = arrays.side;
    this.rootId = arrays.root_id;

    // --- published model constants ---
    this.vRest = -52;
    this.vReset = -52;
    this.vThresh = opts.vThresh ?? -45;
    this.tauM = 0.020;
    this.tauDefault = 0.005;
    this.tauRefrac = opts.tauRefrac ?? 0.0022;
    this.wPerSynapse = opts.wPerSynapse ?? 0.275;
    this.delaySteps = Math.max(1, Math.round((opts.synapticDelay ?? 0.0018) / DT));
    this.bias = opts.bias ?? 0;          // tonic depolarisation (mV)
    this.gain = opts.gain ?? 1;          // global synaptic gain multiplier
    this.backgroundHz = opts.backgroundHz ?? 0;  // per-neuron spontaneous rate
    this.bgWeight = opts.bgWeight ?? 3.0;        // mV per background event
    this._rng = mulberry32(opts.seed ?? 12345);

    // --- state ---
    this.v = new Float32Array(n).fill(this.vRest);
    this.g = new Float32Array(n);

    this.refrac = new Float32Array(n);
    this.rate = new Float32Array(n);
    this.spikeFlag = new Uint8Array(n);
    this.iExt = new Float32Array(n);
    this.steps = 0;

    this.tauScale = opts.tauScale ?? 1.0;
    this.tau = new Float32Array(n).fill(this.tauDefault * this.tauScale);
    let nCustom = 0;
    for (let i = 0; i < n; i++) {
      const t = TAU_BY_TYPE[this.types[i]];
      if (t) { this.tau[i] = t * this.tauScale; nCustom++; }
    }
    this.nCustomTau = nCustom;
    this.tauDecay = new Float32Array(n);
    for (let i = 0; i < n; i++) this.tauDecay[i] = Math.exp(-DT / this.tau[i]);
    this.tauMDecay = Math.exp(-DT / this.tauM);
    this.rateDecay = Math.exp(-DT / (opts.rateWindow ?? 0.04));

    // --- homeostatic synaptic scaling ---
    //
    // The connectome fixes how strong each connection is *relative* to the
    // other inputs of the same postsynaptic cell (its synapse count).  The
    // absolute scale is a free biophysical constant: real neurons homeostatically
    // set their own total synaptic strength, and the published per-synapse weight
    // is calibrated for a preparation that is driven by very strong external
    // Poisson input rather than by its own recurrent activity.  We therefore
    // keep the connectome's relative weights exactly, and give every neuron the
    // same total absolute input strength `wTotal` (mV), distributed across its
    // synapses in proportion to the real synapse counts.
    // A second free constant is the relative strength of inhibition.  Synapse
    // counts do not translate into equal postsynaptic charge for excitation and
    // inhibition -- the two use different receptors and conductances -- so the
    // ratio of the two is a biophysical parameter (as it is in every published
    // connectome model).  `inhibScale` multiplies the inhibitory weights only.
    this.inhibScale = opts.inhibScale ?? 1;
    this.wTotal = opts.wTotal ?? 120;
    const colSum = new Float64Array(n);
    const preA = arrays.pre, postA = arrays.post, wA = arrays.w;
    for (let e = 0; e < wA.length; e++) colSum[postA[e]] += Math.abs(wA[e]);
    const norm = new Float64Array(n);
    for (let j = 0; j < n; j++) norm[j] = this.wTotal / Math.max(colSum[j], 1e-9);
    this.wNorm = new Float32Array(wA.length);
    for (let e = 0; e < wA.length; e++) {
      this.wNorm[e] = wA[e] * norm[postA[e]] * (wA[e] < 0 ? this.inhibScale : 1);
    }
    this.meanInputCount = 0;
    for (let j = 0; j < n; j++) if (colSum[j] > 0) this.meanInputCount += colSum[j];

    this._colSum = colSum;
    this._wRaw = wA;
    // --- per-synapse conduction delay ---
    //
    // Every synapse in the published model has the same 1.8 ms delay, which
    // makes the network blind to motion direction: comparing signals from two
    // neighbouring columns with identical delays cannot tell which way an edge
    // travelled.  The retina's elementary motion detectors are delay-and-
    // correlate circuits, so the conduction delay of a synapse onto a T4/T5 cell
    // is extended in proportion to how far its partner sits along that cell's
    // preferred axis (measured from the connectome in build/make_circuit.py).
    this.baseDelay = this.delaySteps;
    this.motionDelayGain = opts.motionDelayGain ?? 260;  // steps per unit field
    this.motionDelaySign = opts.motionDelaySign ?? 1;
    this.motionDelayMax = opts.motionDelayMax ?? 5;
    this.delaySlots = this.baseDelay + this.motionDelayMax + 1;
    this.gDelay = new Float32Array(n * this.delaySlots);
    this.mproj = arrays.mproj;
    const mp = this.mproj, mdg = this.motionDelayGain;
    this.csr = buildCsr(arrays.pre, arrays.post, this.wNorm, n, (e) => {
      if (!mp[e]) return this.baseDelay;
      let extra = Math.round(mdg * (this.motionDelaySign >= 0 ? mp[e] : -mp[e]));
      if (extra < 0) extra = 0;
      if (extra > this.motionDelayMax) extra = this.motionDelayMax;
      return this.baseDelay + extra;
    });
    this.spikeList = new Int32Array(n);

    this.groups = {};
    for (const k of Object.keys(meta.blocks)) {
      if (k.startsWith('g_')) this.groups[k.slice(2)] = arrays[k];
    }
    for (const k of ['descending', 'photoreceptors', 'tangential', 'visual_projection']) {
      if (!this.groups[k]) this.groups[k] = new Uint32Array(0);
    }
  }

  /** Rescale the whole network to a new total input strength per neuron. */
  setWTotal(wTotal, inhibScale = this.inhibScale) {
    this.wTotal = wTotal;
    this.inhibScale = inhibScale;
    const { data, slotEdge } = this.csr;
    const { post } = this;
    for (let s = 0; s < data.length; s++) {
      const e = slotEdge[s];
      const j = post[e];
      const raw = this._wRaw[e];
      data[s] = raw * (wTotal / Math.max(this._colSum[j], 1e-9)) *
                (raw < 0 ? inhibScale : 1);
    }
    return this;
  }

  /** One integration step. Returns the number of neurons that spiked. */
  step() {
    const n = this.N;
    const v = this.v, g = this.g, rate = this.rate;
    const D = this.delaySlots;
    const slot = (this.steps % D) * n;
    const gDelay = this.gDelay;
    const spikeList = this.spikeList;
    const tauMInv = DT / this.tauM;
    const bias = this.bias;
    const iExt = this.iExt;
    let nSpikes = 0;

    // 1. spontaneous background input.  Real neurons are noisy; a noiseless
    // network of identical cells collapses into a synchronised state and can
    // carry no information.  We deliver a Poisson train of small conductance
    // kicks to uniformly chosen neurons, which costs O(events) per step.
    if (this.backgroundHz > 0) {
      const lam = this.backgroundHz * n * DT;
      const sd = Math.sqrt(lam);
      const u1 = Math.max(this._rng(), 1e-12), u2 = this._rng();
      const gauss = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
      let events = Math.round(lam + sd * gauss);
      if (events < 0) events = 0;
      for (let k = 0; k < events; k++) {
        g[(this._rng() * n) | 0] += this.bgWeight;
      }
    }

    // 2. integrate: deliver the delayed conductance, leak, threshold, and
    //    update the rate estimate -- all in one pass over the neurons, because
    //    98,000 neurons x 18 steps per animation frame makes loop count matter
    const rd = this.rateDecay, oneMinus = 1 - rd, scaleOne = 1 / DT;
    const tauDecay = this.tauDecay;
    for (let i = 0; i < n; i++) {
      const a = gDelay[slot + i];
      let gi = a !== 0 ? (g[i] + a) * tauDecay[i] : g[i] * tauDecay[i];
      if (a !== 0) gDelay[slot + i] = 0;
      let vi = this.vRest + (v[i] - this.vRest) * this.tauMDecay
             + tauMInv * gi + iExt[i] + bias;
      let fired = 0;
      if (this.refrac[i] > 0) {
        this.refrac[i] -= DT;
        vi = this.vReset; gi = 0;
      } else if (vi >= this.vThresh) {
        spikeList[nSpikes++] = i;
        fired = 1;
        vi = this.vReset; gi = 0;
        this.refrac[i] = this.tauRefrac;
      }
      v[i] = vi;
      g[i] = gi;
      rate[i] = rate[i] * rd + (fired ? oneMinus * scaleOne : 0);
    }

    // 3. route spikes through the connectome into the delay ring buffer; the
    //    slot a spike lands in is decided by that synapse's conduction delay
    const { indptr, indices, data, delay } = this.csr;
    const wScale = this.gain;
    for (let s = 0; s < nSpikes; s++) {
      const i = spikeList[s];
      for (let e = indptr[i], end = indptr[i + 1]; e < end; e++) {
        const d = delay[e];
        const base = d === this.baseDelay ? slot
          : ((this.steps + d) % D) * n;
        gDelay[base + indices[e]] += data[e] * wScale;
      }
    }
    this.steps++;
    return nSpikes;
  }

  meanRate(group) {
    let s = 0;
    for (let k = 0; k < group.length; k++) s += this.rate[group[k]];
    return group.length ? s / group.length : 0;
  }

  meanRateOfType(type, limit = Infinity) {
    let s = 0, c = 0;
    for (let i = 0; i < this.N && c < limit; i++) {
      if (this.types[i] === type) { s += this.rate[i]; c++; }
    }
    return c ? s / c : 0;
  }

  totalRate() {
    let s = 0;
    for (let i = 0; i < this.N; i++) s += this.rate[i];
    return s / this.N;
  }

  reset() {
    this.v.fill(this.vRest);
    this.g.fill(0);
    this.gDelay.fill(0);
    this.refrac.fill(0);
    this.rate.fill(0);
    this.spikeFlag.fill(0);
    this.iExt.fill(0);
    this.steps = 0;
  }
}

export function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
