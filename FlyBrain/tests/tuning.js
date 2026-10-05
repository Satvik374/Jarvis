/**
 * Sensor validation: does the descending-neuron readout actually report where
 * the gap is relative to the fly's heading?
 *
 * We hold the bird still, park a pipe right in front of it, and slide the gap
 * up and down.  A real sensor would produce a monotonic readout.
 *
 * Usage: node tests/tuning.js
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseCircuit, FlyBrain, DT } from '../sim/flybrain.js';
import { Retina, buildReadout, readCommand } from '../sim/fly.js';
import { FlappyWorld } from '../sim/world.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const buf = fs.readFileSync(path.join(HERE, '..', 'web', 'circuit.bin'));
const circuit = parseCircuit(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));

function argList(name, def) {
  const i = process.argv.indexOf('--' + name);
  if (i < 0) return def;
  return process.argv[i + 1].split(',').map(Number);
}
const wTotal = argList('wTotal', [200])[0];
const inhibScale = argList('inhibScale', [0.25])[0];
const lightGain = argList('lightGain', [0.95])[0];
const fovW = argList('fovW', [140])[0];
const fovH = argList('fovH', [270])[0];

const brain = new FlyBrain(circuit, {
  wTotal, inhibScale, backgroundHz: 4, bgWeight: 5, seed: 99,
});
const retina = new Retina(brain, { gain: lightGain, offset: 0.15, adapt: 0, mean: 0.5, fovW, fovH });
const readout = buildReadout(brain, {});

/** Run the brain on a static scene for `sec` seconds and average the readout. */
function measure(world, sec = 0.45) {
  brain.reset();
  const eye = (az, el) => world.sceneAt(world.birdX + az * fovW / 2, world.y - el * fovH / 2);
  let sp = 0, sv = 0, c = 0, rate = 0;
  const n = Math.round(sec / DT);
  for (let s = 0; s < n; s++) {
    retina.update(brain, eye, DT);
    brain.step();
    if (s > n * 0.4) {
      const cmd = readCommand(brain, readout);
      sp += cmd.pos; sv += cmd.vel; rate += brain.meanRate(brain.groups.descending);
      c++;
    }
  }
  return { pos: sp / c, vel: sv / c, dnRate: rate / c };
}

console.log(`wTotal=${wTotal} inhibScale=${inhibScale} lightGain=${lightGain} ` +
            `fov=${fovW}x${fovH}`);
console.log('\n  gap elevation (px above the bird)   pos readout   vel readout   DN Hz');
const results = [];
for (const gapOffset of [-160, -120, -80, -40, 0, 40, 80, 120, 160]) {
  const world = new FlappyWorld({ speed: 0, gravity: 0 });
  world.reset(() => 0.5);
  world.y = 300;
  world.vy = 0;
  // a single pipe parked in front of the bird, gap centred at bird y - gapOffset
  world.pipes.length = 0;
  world.pipes.push({ x: world.birdX - world.pipeW / 2, gapY: world.y - gapOffset, passed: true });
  const m = measure(world);
  results.push([gapOffset, m.pos]);
  console.log(`        ${String(gapOffset).padStart(5)}                        ` +
              `${m.pos.toFixed(4).padStart(9)}     ${m.vel.toFixed(4).padStart(9)}   ` +
              `${m.dnRate.toFixed(2)}`);
}

// monotonicity check: does the readout track gap elevation?
let inc = 0, dec = 0;
for (let i = 1; i < results.length; i++) {
  const d = results[i][1] - results[i - 1][1];
  if (d > 0) inc++; else if (d < 0) dec++;
}
console.log(`\nmonotonic steps up: ${inc}, down: ${dec}`);
const span = results[results.length - 1][1] - results[0][1];
console.log(`readout changes by ${span.toFixed(4)} across the field ` +
            `(${span > 0 ? 'increases' : 'decreases'} as the gap moves up)`);
console.log(span > 0
  ? '=> positive readout means the gap is ABOVE the heading'
  : '=> positive readout means the gap is BELOW the heading');
