/**
 * Closed loop: game world, fly retina, fly connectome, fly wingbeats.
 *
 * The game advances by the *biological* time the simulation actually produced,
 * so a slow frame slows the bird down instead of making the brain skip steps.
 * The loop is never broken.
 */
import { DT, FlyBrain } from './flybrain.js';
import { Retina, buildReadout, readCommand, Motor } from './fly.js';
import { FlappyWorld } from './world.js';

export const DEFAULT_CONFIG = {
  wTotal: 200,
  inhibScale: 0.25,
  backgroundHz: 4,
  bgWeight: 5,
  lightGain: 0.95,
  lightOffset: 0.15,
  fovW: 220,
  fovH: 620,
  motionDelayGain: 0,
  stepsPerFrame: 14,
  tauScale: 1.0,
  // A FlyBrain game is deliberately a little gentler than the arcade original:
  // the fly has to solve it with a 97,973-neuron brain whose visual system
  // responds on a 10-50 ms timescale, not with a scripted controller.
  world: { speed: 110, gapH: 190, spacing: 260 },
};

export class FlyGame {
  constructor(circuit, opts = {}) {
    this.cfg = { ...DEFAULT_CONFIG, ...opts };
    const c = this.cfg;
    this.brain = circuit instanceof FlyBrain
      ? circuit
      : new FlyBrain(circuit, {
          wTotal: c.wTotal,
          inhibScale: c.inhibScale,
          backgroundHz: c.backgroundHz,
          bgWeight: c.bgWeight,
          motionDelayGain: c.motionDelayGain,
          tauScale: c.tauScale,
          seed: c.seed ?? 20260922,
        });
    this.world = new FlappyWorld({ ...DEFAULT_CONFIG.world, ...(opts.world || {}) });
    this.retina = new Retina(this.brain, {
      gain: c.lightGain, offset: c.lightOffset, adapt: 0, mean: 0.5,
      fovW: c.fovW, fovH: c.fovH,
    });
    this.readout = buildReadout(this.brain, c.channelSign || {}, opts.readout || {});
    // A wingbeat is an impulse: between beats gravity accelerates the body, so
    // the rate at which the wings must beat to hold altitude is a property of the
    // body alone -- gravity / (2 * impulse).  The 0.85 is the discrete-flapping
    // correction: the continuous approximation that gives the formula ignores
    // that each beat sets the velocity instantly, which over-lifts a real body,
    // and in this body it measures out at about 0.85.  Still the body's own
    // constant, not a game parameter.
    const hoverRate = 0.85 * this.world.gravity / (2 * Math.abs(this.world.flapV));
    this.motor = new Motor({ hoverRate, ...(opts.motor || {}) });
    this.bioTime = 0;
    this.frames = 0;
    this.score = 0;
    this.best = 0;
    this.history = [];
    this.trace = [];
    this.spikes = null;      // set by the UI to record spike times
    this.spikeTimes = [];
    this.traceEvery = opts.traceEvery ?? 8;
  }

  reset(seed) {
    this.world.reset(seed ? mulberry(seed) : Math.random);
    this.brain.reset();
    this.bioTime = 0;
    this.frames = 0;
    this.score = 0;
    this.motor.z = 0;
    this.motor._init = false;
    this.motor._last = -1e9;
    this.motor.nFlaps = 0;
    this.warmup();
  }

  /**
   * A fly is already flying before the game starts.  Let the brain and the
   * motor threshold settle on the visual scene for a moment with the world held
   * still, otherwise the very first frames are a transient in which the
   * adapting threshold has no idea what the command normally looks like.  No
   * wingbeats are emitted during warm-up.
   */
  warmup(steps = this.cfg.warmupSteps ?? 500) {
    const w = this.world;
    const eye = (az, el) => w.sceneAt(w.birdX + az * this.retina.fovW * 0.5,
                                      w.y - el * this.retina.fovH * 0.5);
    const cmds = [];
    for (let s = 0; s < steps; s++) {
      this.retina.update(this.brain, eye, DT);
      this.brain.step();
      if (s > steps * 0.6) {
        cmds.push(this.motor.kVel * readCommand(this.brain, this.readout).vel +
                  this.motor.kPos * readCommand(this.brain, this.readout).pos);
      }
    }
    this.motor.calibrate(cmds);
    this.motor._smooth = cmds.length ? cmds[cmds.length - 1] : 0;
    if (this.spikes) { this.spikes.length = 0; this.spikeTimes.length = 0; }
    this.motor.phase = 0;
    this.motor._last = -1e9;
    this.motor.nFlaps = 0;
  }

  /** Run one animation frame's worth of simulated biology. */
  stepFrame() {
    const c = this.cfg;
    const w = this.world;
    const eye = (az, el) => {
      const x = w.birdX + az * this.retina.fovW * 0.5;
      const y = w.y - el * this.retina.fovH * 0.5;
      return w.sceneAt(x, y);
    };
    if (!w.alive) return false;
    let flapped = false;
    for (let s = 0; s < c.stepsPerFrame; s++) {
      // 1. the world as the fly's eye sees it right now
      this.retina.update(this.brain, eye, DT);
      // 2. one millisecond of the fly's brain
      const nSpikes = this.brain.step();
      this.bioTime += DT;
      // keep the spikes around so the UI can draw the brain thinking
      if (this.spikes) {
        const list = this.brain.spikeList;
        for (let k = 0; k < nSpikes; k++) {
          this.spikes.push(list[k]);
          this.spikeTimes.push(this.bioTime);
        }
        if (this.spikes.length > 200000) {
          this.spikes.length = 0;
          this.spikeTimes.length = 0;
        }
      }
      // 3. read the wingbeats off the descending neurons ...
      const cmd = readCommand(this.brain, this.readout);
      const flap = this.motor.update(cmd, this.bioTime, DT);
      if (flap) flapped = true;
      // 4. ... and let the body act on them
      if (!w.step(DT, flap)) break;
    }
    this.frames++;
    this.score = w.score;
    if (this.trace.length < 4096) {
      this.trace.push(this.motor.normalised);
    }
    return w.alive;
  }

  /** Play until the bird dies or the step budget is spent. */
  play({ maxFrames = 3600, maxScore = Infinity, onFrame = null } = {}) {
    this._flappedAny = false;
    while (this.world.alive && this.frames < maxFrames && this.world.score < maxScore) {
      this.stepFrame();
      if (onFrame) onFrame(this);
    }
    this.history.push(this.world.score);
    this.best = Math.max(this.best, this.world.score);
    return this.world.score;
  }

  /** Mean firing rate of a group of real neurons. */
  groupRate(name) {
    return this.brain.meanRate(this.brain.groups[name] || []);
  }
}

function mulberry(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
