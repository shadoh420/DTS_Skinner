// Run from the pinned upstream checkout: node --import=tsx/esm skinner/check-mouse-look.ts
import assert from 'node:assert/strict';
import {Euler, Vector3} from 'three';
import {mouseDelta} from './mouse-look';

for (const invertX of [false,true]) for (const invertY of [false,true]) {
  // Same Euler convention as InputConsumer.applyLocalCamera, for right/down input.
  const direction = new Vector3(0,0,-1).applyEuler(new Euler(
    -mouseDelta(10,0.002,invertY), -mouseDelta(10,0.002,invertX), 0, 'YXZ'));
  assert.equal(Math.sign(direction.x), invertX ? -1 : 1, 'horizontal direction');
  assert.equal(Math.sign(direction.y), invertY ? 1 : -1, 'vertical direction');
}
console.log('Mouse look: all four independent axis combinations passed');
