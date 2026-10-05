/**
 * Headless closed-loop test: does the fly actually play?
 *
 * Usage:
 *   node tests/play.js                     play 8 games
 *   node tests/play.js --games 20
 *   node tests/play.js --wTotal 200 --inhibScale 0.25 --lightGain 0.95
 *   node tests/play.js --phase 1           locomotion phase only (no walls)
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseCircuit } from '../sim/flybrain.js';
import { FlyGame, DEFAULT_CONFIG } from '../sim/game.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CIRCUIT = path.join(HERE, '..', 'web', 'circuit.bin');

function arg(name, def) {
  const i = process.argv.indexOf('--' + name);
  return i >= 0 ? Number(process.argv[i + 1]) : def;
}
function flag(name) { return process.argv.includes('--' + name); }

const buf = fs.readFileSync(CIRCUIT);
const circuit = parseCircuit(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));

const cfg = { ...DEFAULT_CONFIG };
for (const k of ['wTotal', 'inhibScale', 'backgroundHz', 'bgWeight', 'lightGain',
                 'lightOffset', 'fovW', 'fovH', 'stepsPerFrame', 'motionDelayGain',
                 'tauScale', 'rateWindow', 'synapticDelay', 'bias']) {
  const v = arg(k, undefined);
  if (v !== undefined) cfg[k] = v;
}
for (const k of ['kVel', 'kPos', 'refractory', 'duty', 'adaptTau']) {
  const v = arg(k, undefined);
  if (v !== undefined) cfg['motor_' + k] = v;
}

const games = arg('games', 8);
const maxFrames = arg('maxFrames', 5000);

const motorOpts = {};
for (const k of ['kVel', 'kPos', 'rateGain', 'hoverRate', 'adaptTau', 'minInterval',
                 'maxRate', 'smoothTau']) {
  const v = arg(k, undefined);
  if (v !== undefined) motorOpts[k] = v;
}
const world = {};
for (const k of ['gravity', 'flapV', 'speed', 'gapH', 'spacing', 'pipeW']) {
  const v = arg(k, undefined);
  if (v !== undefined) world[k] = v;
}

const game = new FlyGame(circuit, { ...cfg, motor: motorOpts, world });
console.log('fly:', game.brain.N.toLocaleString(), 'neurons,',
            (game.brain.csr.data.length / 1e6).toFixed(2), 'M synapses');
console.log('descending neurons in the decoder:', game.readout.n);
console.log('config:', JSON.stringify({ ...cfg, ...motorOpts, ...world }));

const scores = [];
const t0 = performance.now();
let totalFrames = 0;
for (let g = 0; g < games; g++) {
  game.reset(1000 + g);
  const s = game.play({ maxFrames });
  scores.push(s);
  totalFrames += game.frames;
}
const dt = (performance.now() - t0) / 1000;
const bio = totalFrames * cfg.stepsPerFrame * 0.001;

console.log(`\n${games} games: scores ${scores.join(', ')}`);
console.log(`mean ${(scores.reduce((a, b) => a + b, 0) / games).toFixed(2)}` +
            `  best ${Math.max(...scores)}  median ` +
            `${scores.slice().sort((a, b) => a - b)[games >> 1]}`);
console.log(`simulated ${bio.toFixed(1)} s of biology in ${dt.toFixed(1)} s wall ` +
            `(${(bio / dt).toFixed(2)}x real time)`);

// what the surviving fly's neurons were doing
const g = game.brain;
for (const name of ['photoreceptors', 'descending', 'tangential', 'visual_projection']) {
  console.log(`  mean rate ${name}: ${game.groupRate(name).toFixed(2)} Hz`);
}
const m = game.motor;
console.log(`  wingbeats: ${m.nFlaps} (${(m.nFlaps / Math.max(bio, 1e-9)).toFixed(2)} Hz)`);
console.log(`  readout: pos=${game.readout.wPos.length} weights, ` +
            `mean |w| ${(game.readout.wPos.reduce((a, b) => a + Math.abs(b), 0) / game.readout.n).toFixed(2)}`);
