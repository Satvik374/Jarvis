/**
 * Ablations: is the behaviour actually coming from this connectome?
 *
 * If the fly were a hardcoded Flappy Bird bot, none of the perturbations below
 * would change anything.  Each one keeps the *code path* identical and only
 * damages the biology, so any drop in score is caused by the biology.
 *
 * Usage: node tests/ablation.js [--games 8]
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseCircuit, buildCsr } from '../sim/flybrain.js';
import { FlyGame, DEFAULT_CONFIG } from '../sim/game.js';
import { mulberry32 } from '../sim/flybrain.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const buf = fs.readFileSync(path.join(HERE, '..', 'web', 'circuit.bin'));
const base = parseCircuit(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));

const G = Number((process.argv[process.argv.indexOf('--games') + 1]) || 8);
const MAXF = Number((process.argv[process.argv.indexOf('--maxFrames') + 1]) || 2600);

function shuffled(arr, rng) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    const t = a[i]; a[i] = a[j]; a[j] = t;
  }
  return a;
}

function cloneCircuit(label, mutate) {
  const arrays = {};
  for (const k of Object.keys(base.arrays)) arrays[k] = base.arrays[k].slice();
  mutate(arrays);
  return { meta: base.meta, arrays, label };
}

const rng = mulberry32(4242);
const variants = [];

// --- control ---------------------------------------------------------------
variants.push(parseCircuit(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength)));

// --- 1. shuffled wiring (degree distribution preserved) --------------------
variants.push(cloneCircuit('shuffled wiring', (a) => {
  const r = mulberry32(11);
  const pre = a.pre, E = pre.length;
  // keep how many outputs each neuron has, but send them to random partners
  const counts = new Uint32Array(base.meta.n_neurons + 1);
  for (let e = 0; e < E; e++) counts[pre[e]]++;
  const post = new Uint32Array(E);
  let o = 0;
  for (let i = 0; i < base.meta.n_neurons; i++) {
    for (let k = 0; k < counts[i]; k++) {
      post[o++] = Math.floor(r() * base.meta.n_neurons);
    }
  }
  a.post = post;
}));

// --- 2. shuffled synapse weights -------------------------------------------
variants.push(cloneCircuit('shuffled weights', (a) => {
  const r = mulberry32(22);
  const w = a.w;
  for (let i = w.length - 1; i > 0; i--) {
    const j = Math.floor(r() * (i + 1));
    const t = w[i]; w[i] = w[j]; w[j] = t;
  }
}));

// --- 3. scrambled retina: photoreceptors get shuffled visual-field positions -
variants.push(cloneCircuit('scrambled retina', (a) => {
  const r = mulberry32(33);
  const photo = Array.from(a.g_photoreceptors);
  const azv = photo.map((i) => a.az[i]);
  const elv = photo.map((i) => a.el[i]);
  const saz = shuffled(azv, r), sel = shuffled(elv, r);
  photo.forEach((i, k) => { a.az[i] = saz[k]; a.el[i] = sel[k]; });
}));

// --- 4. scrambled readout: descending neurons lose their receptive fields ----
variants.push(cloneCircuit('scrambled readout anatomy', (a) => {
  const r = mulberry32(44);
  const dn = Array.from(a.g_descending);
  const elv = dn.map((i) => a.el[i]);
  const s = shuffled(elv, r);
  dn.forEach((i, k) => { a.el[i] = s[k]; });
  // and cut the T4/T5 direction drive into them
  const dir = a.dir;
  for (const i of dn) for (let c = 0; c < 8; c++) dir[i * 8 + c] = 0;
  for (const i of dn) for (let c = 0; c < 4; c++) a.dir4[i * 4 + c] = 0;
}));

// --- 5. direction drive cut off everywhere ---------------------------------
variants.push(cloneCircuit('no T4/T5 drive', (a) => {
  a.dir.fill(0);
  a.dir4.fill(0);
}));

const results = [];
for (const v of variants) {
  const game = new FlyGame(v, { ...DEFAULT_CONFIG });
  const scores = [];
  let steps = 0;
  for (let g = 0; g < G; g++) {
    game.reset(1000 + g);
    scores.push(game.play({ maxFrames: MAXF }));
    steps += game.frames;
  }
  const mean = scores.reduce((a, b) => a + b, 0) / G;
  const sorted = scores.slice().sort((a, b) => a - b);
  results.push({ label: v.label || 'FLY (control)', mean, best: sorted[G - 1],
                 median: sorted[G >> 1], scores });
  console.log(`${(v.label || 'FLY (control)').padEnd(30)} mean ${mean.toFixed(2)}  ` +
              `best ${sorted[G - 1]}  median ${sorted[G >> 1]}  [${scores.join(',')}]`);
}

const ctrl = results[0].mean;
console.log('\nrelative to the intact fly:');
for (const r of results.slice(1)) {
  const pct = ctrl > 0 ? (100 * r.mean / ctrl) : 0;
  console.log(`  ${r.label.padEnd(30)} ${pct.toFixed(0)}% of control`);
}
fs.writeFileSync(path.join(HERE, '..', 'web', 'ablation.json'),
                 JSON.stringify(results, null, 1));
console.log('\nwrote web/ablation.json');
