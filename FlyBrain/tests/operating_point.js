/**
 * Find the physiologically sensible operating point of the simulated fly.
 *
 * A spiking network built from nothing but the connectome has three free
 * biophysical constants: how much tonic drive the cells receive, how noisy they
 * are, and the global synaptic gain.  We sweep them and keep configurations
 * where (a) the resting rate of the sprite is in the range reported for a fly
 * brain (a few Hz, not silent and not epileptic) and (b) a luminance step
 * actually drives the lamina and propagates to the motion detectors.
 *
 * Usage: node tests/operating_point.js
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseCircuit, FlyBrain, DT, mulberry32 } from '../sim/flybrain.js';
import { Retina } from '../sim/fly.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CIRCUIT = path.join(HERE, '..', 'web', 'circuit.bin');

const STRATA = {
  'photo R1-6': ['R1-6'],
  L1: ['L1'], L2: ['L2'],
  Mi1: ['Mi1'], Tm3: ['Tm3'], LC4: ['LC4'],
  T4: ['T4a', 'T4b', 'T4c', 'T4d'],
  T5: ['T5a', 'T5b', 'T5c', 'T5d'],
};

function groupOf(brain, types) {
  const set = new Set(types);
  const out = [];
  for (let i = 0; i < brain.N; i++) if (set.has(brain.types[i])) out.push(i);
  return Uint32Array.from(out);
}

function run(brain, groups, cfg, { seconds = 1.0, stimulus = null } = {}) {
  brain.reset();
  brain.bias = cfg.bias;
  brain.backgroundHz = cfg.bgHz;
  brain.bgWeight = cfg.bgWeight;
  brain.gain = cfg.gain ?? 1;
  const retina = new Retina(brain, {
    gain: cfg.light, offset: cfg.lightOffset ?? 0.15, adapt: 0, mean: 0.5,
  });
  const nSteps = Math.round(seconds / DT);
  const acc = {}; for (const k of Object.keys(groups)) acc[k] = 0;
  for (let s = 0; s < nSteps; s++) {
    const t = s * DT;
    if (stimulus) {
      if (stimulus === 'step') retina.update(brain, () => (t > 0.4 ? 0.95 : 0.05), DT);
      else retina.update(brain, stimulus(t), DT);
    }
    brain.step();
    if (t > seconds * 0.5) for (const k of Object.keys(acc)) acc[k] += brain.meanRate(groups[k]);
  }
  const out = {};
  for (const k of Object.keys(acc)) out[k] = acc[k] / (nSteps * 0.5);
  out.__all = brain.totalRate();
  return out;
}

function main() {
  const buf = fs.readFileSync(CIRCUIT);
  const circuit = parseCircuit(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
  const brain = new FlyBrain(circuit, {});
  const groups = {};
  for (const k of Object.keys(STRATA)) groups[k] = groupOf(brain, STRATA[k]);
  console.log('group sizes:', Object.fromEntries(
    Object.entries(groups).map(([k, v]) => [k, v.length])));

  const grid = [];
  for (const wTotal of [60, 100, 160]) {
    for (const inhibScale of [1, 0.5, 0.25]) {
      for (const bgHz of [4]) {
        grid.push({ bgHz, bgWeight: 5, gain: 1, bias: 0, light: 0.8, wTotal, inhibScale });
      }
    }
  }
  const show = (v) => (v < 0.005 ? '    .' : v.toFixed(2).padStart(5));
  console.log('\nwTot inh bgHz |  all  photo    L1    L2   Mi1   Tm3    T4    T5 | stepL1 stepMi1 stepTm3 stepT4 stepT5 stepLC4');
  for (const cfg of grid) {
    brain.setWTotal(cfg.wTotal, cfg.inhibScale);
    const rest = run(brain, groups, cfg, { seconds: 0.6 });
    const step = run(brain, groups, cfg, { seconds: 0.6, stimulus: 'step' });
    const flag = (rest.__all > 0.2 && rest.__all < 25) ? '*' : ' ';
    console.log(
      `${flag}${String(cfg.wTotal).padStart(4)} ${String(cfg.inhibScale).padStart(3)} ` +
      `${String(cfg.bgHz).padStart(4)} | ` +
      [rest.__all, rest['photo R1-6'], rest.L1, rest.L2, rest.Mi1, rest.Tm3, rest.T4, rest.T5]
        .map(show).join(' ') +
      ` | ${show(step.L1)} ${show(step.Mi1)} ${show(step.Tm3)} ${show(step.T4)} ` +
      `${show(step.T5)} ${show(step.LC4)}`);
  }
}

main();
