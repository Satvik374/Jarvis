/**
 * Reflex battery.
 *
 * We present the fly's real retina with a series of visual stimuli and measure
 * what the real, anatomically-identified cell classes do:
 *
 *   loom      -- a dark disc expanding away from the centre.  This is the
 *                classic releasing stimulus for the fly's escape jump, and in
 *                the real animal it is detected by LC4 and LPLC2, which are
 *                present in this connectome in their thousands.
 *   contract  -- the same disc shrinking.  The control: identical edge energy,
 *                opposite direction of travel, so a true loom detector must
 *                separate the two.
 *   bar       -- a dark bar sweeping across the field.  Local motion with no
 *                expansion.
 *   flash     -- the whole field darkens at once.  A large luminance change
 *                with no edge at all.
 *   blank     -- uniform grey, the baseline.
 *
 * Nothing here is fitted or scripted: the stimulus is defined in the visual
 * field, the retina drives the real photoreceptors, and we just read the real
 * cells' firing rates.
 *
 * Usage: node tests/reflexes.js
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseCircuit, FlyBrain, DT } from '../sim/flybrain.js';
import { Retina } from '../sim/fly.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const buf = fs.readFileSync(path.join(HERE, '..', 'web', 'circuit.bin'));
const circuit = parseCircuit(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));

const brain = new FlyBrain(circuit, {
  wTotal: 200, inhibScale: 0.25, backgroundHz: 4, bgWeight: 5,
  motionDelayGain: 0, seed: 4242,
});
const retina = new Retina(brain, {
  gain: 0.95, offset: 0.15, adapt: 0, mean: 0.5, fovW: 220, fovH: 220,
});

/** Indices of every neuron whose cell type name starts with one of `prefixes`. */
function pick(prefixes) {
  const out = [];
  for (let i = 0; i < brain.N; i++) {
    const t = brain.types[i];
    for (const p of prefixes) {
      if (t === p || t.startsWith(p)) { out.push(i); break; }
    }
  }
  return Int32Array.from(out);
}

const CLASSES = {
  'R1-6 photorec.': pick(['R1-6']),
  'R7/R8 photorec.': pick(['R7', 'R8']),
  'lamina L1-L5': pick(['L1', 'L2', 'L3', 'L4', 'L5']),
  'T4 (ON motion)': pick(['T4a', 'T4b', 'T4c', 'T4d']),
  'T5 (OFF motion)': pick(['T5a', 'T5b', 'T5c', 'T5d']),
  'LC4 loom': pick(['LC4']),
  'LPLC2 loom': pick(['LPLC2']),
  'LPLC1/LPLC4': pick(['LPLC1', 'LPLC4']),
  'LLPC1-3': pick(['LLPC1', 'LLPC2', 'LLPC3']),
  'LC31/LC6/LC9/LC11': pick(['LC31', 'LC6', 'LC9', 'LC11']),
  'tangential H1/H2': pick(['H1', 'H2']),
  'descending (all)': Array.from(brain.groups.descending),
  'ocelli': pick(['OCG']),
};

/** A disc whose radius runs from r0 to r1 over the trial. */
const disc = (r0, r1, inside, outside) => (t) => {
  const r = r0 + (r1 - r0) * t;
  return (az, el) => (Math.hypot(az, el) <= r ? inside : outside);
};

/** Each stimulus maps normalised trial time -> a luminance field over the eye. */
const STIMULI = {
  blank: () => () => 0.5,
  'loom (dark grows)': disc(0.06, 0.90, 0.06, 0.62),
  'contract (dark shrinks)': disc(0.90, 0.06, 0.06, 0.62),
  'loom (bright grows)': disc(0.06, 0.90, 0.95, 0.30),
  'contract (bright shrinks)': disc(0.90, 0.06, 0.95, 0.30),
  'static bright disc': disc(0.45, 0.45, 0.95, 0.30),
  'bar sweeps': (t) => {
    const x = -0.9 + 1.8 * t;
    return (az) => (Math.abs(az - x) < 0.11 ? 0.06 : 0.62);
  },
  'flash (whole field)': (t) => () => (t < 0.15 ? 0.62 : 0.06),
};

const DURATION = 0.6;   // seconds of stimulus
const N = Math.round(DURATION / DT);
// The averaging window is SYMMETRIC about the middle of the trial, and that is
// the whole point of the design.  An expanding disc runs radius 0.06 -> 0.90
// and a contracting one runs 0.90 -> 0.06, so over [0.1, 0.9] of the trial the
// two stimuli sweep exactly the same set of radii and therefore cast exactly
// the same mean amount of light on the retina.  Any difference the loom
// detectors show in this window is expansion itself, not area.  (Averaging
// over the last half instead would let "expansion ends large" masquerade as
// "expansion is detected" -- which is exactly the trap this window avoids.)
const LO = 0.1, HI = 0.9;   // normalised trial time: matched-mean-size window

console.log(`wTotal=${brain.wTotal} inhibScale=${brain.inhibScale} ` +
            `stimulus ${DURATION}s, averaging t in [${LO}, ${HI}] ` +
            `(matched mean size)\n`);

const rows = {};
for (const [name, make] of Object.entries(STIMULI)) {
  brain.reset();
  const acc = {}; const cnt = {};
  for (const k of Object.keys(CLASSES)) { acc[k] = 0; cnt[k] = 0; }
  for (let s = 0; s < N; s++) {
    const t = s / N;
    const eye = make(t);
    retina.update(brain, eye, DT);
    brain.step();
    if (t >= LO && t <= HI) {
      for (const [k, ids] of Object.entries(CLASSES)) {
        let sum = 0;
        for (let j = 0; j < ids.length; j++) sum += brain.rate[ids[j]];
        acc[k] += sum / ids.length; cnt[k]++;
      }
    }
  }
  rows[name] = {};
  for (const k of Object.keys(CLASSES)) rows[name][k] = acc[k] / cnt[k];
  process.stdout.write(`done: ${name}\n`);
}

const keys = Object.keys(CLASSES);
const w = 22;
console.log('\n=== mean firing rate (Hz) by cell class ===');
console.log('class'.padEnd(w) + Object.keys(STIMULI).map(s => s.slice(0, 13).padStart(15)).join(''));
for (const k of keys) {
  console.log(k.padEnd(w) + Object.keys(STIMULI).map(s => rows[s][k].toFixed(1).padStart(14)).join(''));
}

console.log('\n--- does the circuit compute looming, or just pass luminance through? ---');
console.log('Same stimulus pair, symmetric window, identical mean disc size.');
console.log('If the connectome detects expansion, the loom detectors must show a');
console.log('LARGER expand/contract ratio than the photoreceptors that feed them.\n');
const loomKeys = ['R1-6 photorec.', 'lamina L1-L5', 'T4 (ON motion)',
                  'LC4 loom', 'LPLC2 loom', 'LPLC1/LPLC4', 'LLPC1-3',
                  'LC31/LC6/LC9/LC11'];
const cols = ['loom (bright grows)', 'contract (bright shrinks)', 'static bright disc'];
console.log('class'.padEnd(w) + cols.map(c => c.slice(0, 12).padStart(15)).join('') +
            'expand/contract'.padStart(18));
const ratios = {};
for (const k of loomKeys) {
  const e = rows[cols[0]][k], c = rows[cols[1]][k], s = rows[cols[2]][k];
  const r = c > 1e-9 ? e / c : (e > 0.05 ? Infinity : NaN);
  ratios[k] = r;
  console.log(k.padEnd(w) +
    e.toFixed(2).padStart(15) + c.toFixed(2).padStart(15) + s.toFixed(2).padStart(15) +
    (Number.isNaN(r) ? '-' : (Number.isFinite(r) ? r.toFixed(2) + 'x' : 'inf')).padStart(18));
}
const photoRatio = ratios['R1-6 photorec.'];
console.log(`\nphotoreceptor baseline ratio: ${photoRatio.toFixed(2)}x`);
for (const k of loomKeys.slice(1)) {
  const r = ratios[k];
  if (!Number.isFinite(r)) { console.log(`  ${k.padEnd(w)} no response`); continue; }
  const gain = r / photoRatio;
  console.log(`  ${k.padEnd(w)} ${r.toFixed(2)}x  = ${gain.toFixed(2)}x the input's ` +
              `selectivity  ${gain > 1.25 ? '<== amplifies expansion' : 'no expansion gain'}`);
}
