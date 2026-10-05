/**
 * The game world.
 *
 * The scene is an analytic luminance field `sceneAt(x, y) -> [0, 1]` in world
 * pixels rather than a bitmap, so the headless Node test harness and the
 * browser render exactly the same thing the fly's retina is being fed.
 *
 * Coordinates: x grows to the right, y grows downwards, (0, 0) is the top left
 * of the game view.  The bird is pinned at `birdX`; the pipes scroll past it.
 * The fly's eye rides on the bird, so the visual field it is presented with
 * shifts with the bird's own position -- that is the optic flow it reacts to.
 */

export class FlappyWorld {
  constructor(o = {}) {
    this.W = o.W ?? 480;
    this.H = o.H ?? 640;
    this.groundY = o.groundY ?? 560;
    this.pipeW = o.pipeW ?? 64;
    this.gapH = o.gapH ?? 152;
    this.spacing = o.spacing ?? 215;
    this.gravity = o.gravity ?? 1500;
    this.flapV = o.flapV ?? -430;
    this.speed = o.speed ?? 158;
    this.birdX = o.birdX ?? 140;
    this.birdR = o.birdR ?? 13;
    this.marginTop = o.marginTop ?? 56;
    this.reset();
  }

  reset(rand = Math.random) {
    this._rand = rand;
    this.y = this.H * 0.42;
    this.vy = 0;
    this.t = 0;
    this.alive = true;
    this.score = 0;
    this.pipes = [];
    let x = this.W + 90;
    for (let i = 0; i < 9; i++) {
      this.pipes.push({ x, gapY: this._newGapY(rand), passed: false });
      x += this.spacing;
    }
    return this;
  }

  _newGapY(rand) {
    const lo = this.marginTop + this.gapH / 2;
    const hi = this.groundY - 26 - this.gapH / 2;
    return lo + rand() * (hi - lo);
  }

  /** Advance one step. Returns true while the bird is alive. */
  step(dt, flap) {
    if (!this.alive) return false;
    this.t += dt;
    if (flap) this.vy = this.flapV;
    this.vy += this.gravity * dt;
    this.y += this.vy * dt;

    const dy = this.speed * dt;
    let rightmost = -Infinity;
    for (const p of this.pipes) {
      p.x -= dy;
      if (p.x > rightmost) rightmost = p.x;
      if (!p.passed && p.x + this.pipeW < this.birdX - this.birdR) {
        p.passed = true;
        this.score += 1;
      }
    }
    for (const p of this.pipes) {
      if (p.x < -this.pipeW - 10) {
        p.x = rightmost + this.spacing;
        rightmost = p.x;
        p.gapY = this._newGapY(this._rand);
        p.passed = false;
      }
    }

    if (this.y - this.birdR < 0 || this.y + this.birdR > this.groundY) {
      this.alive = false;
    }
    for (const p of this.pipes) {
      if (this.birdX + this.birdR > p.x && this.birdX - this.birdR < p.x + this.pipeW) {
        if (this.y - this.birdR < p.gapY - this.gapH / 2 ||
            this.y + this.birdR > p.gapY + this.gapH / 2) {
          this.alive = false;
        }
      }
    }
    return this.alive;
  }

  /** Luminance of the world at (x, y) in [0, 1]. */
  sceneAt(x, y) {
    // ground: dark, with a scrolling stripe pattern
    if (y >= this.groundY) {
      const s = Math.floor((x - this.t * this.speed * 0) / 16);
      const stripe = ((s % 2) + 2) % 2;
      return 0.16 + 0.10 * stripe;
    }
    // pipes
    for (const p of this.pipes) {
      if (x >= p.x && x < p.x + this.pipeW) {
        if (y < p.gapY - this.gapH / 2 || y > p.gapY + this.gapH / 2) {
          const edge = Math.min(x - p.x, p.x + this.pipeW - x);
          return edge < 5 ? 0.46 : 0.28;
        }
      }
    }
    // sky
    return 0.87 - 0.10 * (y / this.H);
  }
}

/**
 * Sample the visual field as seen from the bird.
 * `az` and `el` are the fly's retinal coordinates in [-1, 1] -- az increases
 * towards the front, el increases dorsally -- and they map onto a window of the
 * world centred on the eye.
 */
export function sampleField(world, az, el, fovW, fovH, out) {
  const x = world.birdX + az * fovW * 0.5;
  const y = world.y - el * fovH * 0.5;
  return out ? (out.value = world.sceneAt(x, y)) : world.sceneAt(x, y);
}
