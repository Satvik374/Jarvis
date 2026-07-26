// Headless smoke test: stubs the DOM, loads the game script, drives update()
// and asserts the core mechanics. Run: node test_game.js
'use strict';
const fs = require('fs');
const assert = require('assert');

// ---- DOM stubs ----
const noopCtx = new Proxy({}, {
  get(t, prop) {
    if (prop === 'createLinearGradient') return () => ({ addColorStop() {} });
    return typeof prop === 'string' ? () => {} : undefined;
  },
  set() { return true; },
});
global.document = {
  getElementById: () => ({ getContext: () => noopCtx, width: 800, height: 448 }),
  body: { innerHTML: '' },
};
global.Image = class {
  set src(_) { this.width = 768; this.height = 224; setTimeout(() => this.onload && this.onload(), 0); }
};
global.addEventListener = () => {};
global.requestAnimationFrame = () => {};
global.window = {};  // no AudioContext -> sfx() no-ops

// ---- load game script ----
const html = fs.readFileSync(require('path').join(__dirname, 'index.html'), 'utf8');
const src = html.match(/<script>([\s\S]*)<\/script>/)[1] +
  '\n;globalThis.__g = { get state(){return state;}, resetLevel, update, keys, tileAt, render };';
eval(src);

setTimeout(() => {
  const g = globalThis.__g;
  const state = g.state;
  const steps = n => { for (let i = 0; i < n; i++) g.update(1 / 60); };

  // 1. level parsed
  assert.ok(state.player, 'player exists');
  assert.strictEqual(state.enemies.length, 9, '9 enemies spawned');
  assert.ok(state.flagX > 0, 'flag placed');

  // 2. runs right
  state.mode = 'play';
  const x0 = state.player.x;
  g.keys['arrowright'] = true;
  steps(120);
  assert.ok(state.player.x > x0 + 100, 'player ran right: ' + (state.player.x - x0).toFixed(0) + 'px');
  g.keys['arrowright'] = false;

  // 3. jump + land
  state.player.jumpBuf = 6;
  g.update(1 / 60);
  assert.ok(state.player.vy < -8, 'jump launched');
  steps(120);
  assert.ok(state.player.onGround, 'landed after jump');

  // 4. coin tile collect (coins at row 7, cols 30-32)
  const c0 = state.coins;
  state.player.x = 30 * 32; state.player.y = 7 * 32; state.player.vy = 0;
  g.update(1 / 60);
  assert.ok(state.coins > c0, 'tile coin collected');

  // 5. ?-block head bump (qblock at row 8, col 10)
  g.resetLevel(true); state.mode = 'play';
  state.player.x = 10 * 32 + 7; state.player.y = 9 * 32 + 4; state.player.vy = -6;
  g.update(1 / 60);
  assert.strictEqual(state.grid[8][10], 'U', '?-block became used');
  assert.strictEqual(state.coins, 1, 'coin popped from block');

  // 6. brick break (brick at row 8, col 20)
  state.player.x = 20 * 32 + 7; state.player.y = 9 * 32 + 4; state.player.vy = -6;
  g.update(1 / 60);
  assert.strictEqual(state.grid[8][20], ' ', 'brick broke');

  // 7. stomp enemy
  g.resetLevel(true); state.mode = 'play';
  const en = state.enemies[0];
  en.active = true;
  state.player.x = en.x; state.player.y = en.y - 24; state.player.vy = 5;
  g.update(1 / 60);
  assert.ok(en.squash > 0, 'enemy stomped');
  assert.ok(state.player.vy < 0, 'bounced off enemy');

  // 8. side hit kills player, lives drop, level resets
  g.resetLevel(true); state.mode = 'play';
  const en2 = state.enemies[0];
  en2.active = true;
  state.player.x = en2.x - 10; state.player.y = en2.y - 6; state.player.vy = 0;
  g.update(1 / 60);
  assert.strictEqual(state.mode, 'dying', 'side contact starts death');
  steps(100);
  assert.strictEqual(state.lives, 2, 'lost a life');
  assert.strictEqual(state.mode, 'play', 'respawned');

  // 9. pit fall dies too
  state.player.y = 16 * 32; state.player.x = 31 * 32;
  g.update(1 / 60);
  assert.strictEqual(state.mode, 'dying', 'pit kills');

  // 10. flag wins
  g.resetLevel(true); state.mode = 'play';
  state.player.x = state.flagX + 2;
  g.update(1 / 60);
  assert.strictEqual(state.mode, 'win', 'flag triggers win');

  // 11. shuriken kills enemy
  g.resetLevel(true); state.mode = 'play';
  const en3 = state.enemies[0];
  en3.active = true;
  state.shurikens.push({ x: en3.x - 20, y: en3.y + 4, vx: 7, t: 0 });
  steps(10);
  assert.ok(!en3.alive, 'shuriken killed enemy');

  // 12. render path throws nothing (stubbed ctx)
  g.render();

  console.log('ALL 12 CHECKS PASSED');
}, 20);
