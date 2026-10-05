/**
 * Physiology experiment on the simulated fly.
 *
 * This is the experiment Maisak et al. 2013 (Nature 500:212) performed on real
 * flies: present a full-field moving grating and record which of the four
 * T4/T5 subtypes responds.  We run it on the connectome model to find out
 * whether the simulated optic lobe actually computes motion direction -- and,
 * if it does, which anatomical direction each channel prefers.  Nothing here is
 * assumed; the answer is read out of the simulation and used to label the
 * motor decoder.
 *
 * Usage: node tests/physiology.js
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseCircuit, FlyBrain, DT } from '../sim/flybrain.js';
import { Retina } from '../sim/fly.js';
import { mulberry32 } from '../sim/flybrain.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CIRCUIT = path.join(HERE, '..', 'web', 'circuit.bin');

export function movingGrating({ cycles = 2.2, omega = 6.0, dirAz = 0, dirEl = 0, contrast = 1 }) {
  // full-field sinusoidal grating translating in the (dirAz, dirEl) direction
  return (az, el) => {
    const phase = cycles * (az * dirAz + el * dirEl) - omega * 0;
    return 0.5 + 0.5 * contrast * Math.sin(phase);
  };
}

const DIRS = [
  ['front-to-back', -1, 0],
  ['back-to-front', 1, 0],
  ['upward', 0, 1],
  ['downward', 0, -1],
];

export function runExperiment(brain, subtypeGroups, { seconds = 1.0, warmup = 0.5, lightGain = 0.9, lightOffset = 0.15 } = {}) {
  const retina = new Retina(brain, {
    gain: lightGain, offset: lightOffset, adapt: 0, mean: 0.5,
  });
  const results = {};
  for (const [name, da, de] of DIRS) {
    brain.reset();
    // moving grating: rebuild the stimulus each step with the right phase
    const cycles = 2.2, omega = 9.0;
    const t0 = performance.now();
    const nSteps = Math.round((seconds + warmup) / DT);
    const acc = {};
    for (const st of Object.keys(subtypeGroups)) acc[st] = 0;
    let counted = 0;
    for (let s = 0; s < nSteps; s++) {
      const t = s * DT;
      const stim = (az, el) => {
        const phase = cycles * (az * da + el * de) * Math.PI - omega * t;
        return 0.5 + 0.5 * Math.sin(phase);
      };
      retina.update(brain, stim, DT);
      brain.step();
      if (t >= warmup) {
        for (const st of Object.keys(acc)) acc[st] += brain.meanRate(subtypeGroups[st]);
        counted++;
      }
    }
    const row = {};
    for (const st of Object.keys(acc)) row[st] = acc[st] / counted;
    results[name] = row;
    void t0;
  }
  return results;
}

function main() {
  const t0 = performance.now();
  const buf = fs.readFileSync(CIRCUIT);
  const circuit = parseCircuit(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
  const argv = process.argv.slice(2).map(Number);
  const brain = new FlyBrain(circuit, {
    wTotal: argv[0] || 220,
    inhibScale: argv[1] || 0.25,
    backgroundHz: argv[2] ?? 4,
    bgWeight: argv[3] ?? 5,
    bias: 0,
  });
  const lightGain = argv[4] || 0.9;
  console.log(`circuit: ${brain.N.toLocaleString()} neurons, ` +
              `${(brain.csr.data.length / 1e6).toFixed(2)}M synapses, ` +
              `${brain.nCustomTau.toLocaleString()} neurons with a measured ` +
              `cell-type time constant`);
  console.log(`load: ${(performance.now() - t0).toFixed(0)} ms`);

  const subtypeGroups = {};
  for (const st of ['T4a', 'T4b', 'T4c', 'T4d', 'T5a', 'T5b', 'T5c', 'T5d']) {
    subtypeGroups[st] = brain.groups[st.replace('T', 'T')] || brain.groups[st];
  }
  // groups come out of the container as g_T4a, g_T4b, ...
  for (const st of Object.keys(subtypeGroups)) subtypeGroups[st] = brain.groups[st];

  console.log(`config: wTotal=${brain.wTotal} inhibScale=${brain.inhibScale} ` +
              `bgHz=${brain.backgroundHz} lightGain=${lightGain}`);

  const t1 = performance.now();
  const res = runExperiment(brain, subtypeGroups, { seconds: 1.0, warmup: 0.5, lightGain });
  const perStep = (performance.now() - t1) / ((1.5 / DT) * DIRS.length);
  console.log(`\nsimulation speed: ${perStep.toFixed(2)} ms per 1 ms step ` +
              `(${(DT * 1000 / perStep).toFixed(1)}x real time)`);

  const subs = Object.keys(subtypeGroups);
  console.log('\nmean firing rate (Hz) of each elementary motion detector subtype');
  console.log('subtype ' + DIRS.map(([n]) => n.padStart(15)).join(''));
  for (const st of subs) {
    const cells = DIRS.map(([n]) => res[n][st].toFixed(3).padStart(15)).join('');
    console.log(st.padEnd(8) + cells);
  }

  console.log('\ndiscrimination index (rate - mean) / mean, per subtype:');
  const signs = {};
  for (const st of subs) {
    const vals = DIRS.map(([n]) => res[n][st]);
    const mean = vals.reduce((a, b) => a + b, 0) / vals.length || 1e-9;
    const dev = DIRS.map(([n], i) => [n, (vals[i] - mean) / mean]);
    const best = dev.reduce((a, b) => (Math.abs(b[1]) > Math.abs(a[1]) ? b : a));
    signs[st] = best[0];
    console.log(`  ${st}: ` + dev.map(([n, d]) => `${n} ${d >= 0 ? '+' : ''}${d.toFixed(3)}`).join('  ') +
                `   -> prefers ${best[0]}`);
  }
  console.log('\nderived channel directions:');
  for (const st of subs) console.log(`  ${st}: ${signs[st]}`);
  fs.writeFileSync(path.join(HERE, '..', 'web', 'channels.json'),
    JSON.stringify(signs, null, 1));
  console.log('\nwrote web/channels.json');
}

if (process.argv[1] && process.argv[1].endsWith('physiology.js')) main();
