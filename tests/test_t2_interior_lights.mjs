// T2 animated interior lights (map-client/interiorLights.ts): a looping light's colour is interpolated between its
// states and added to the original lightmap texels as intensity * colour, saturating; alarm lights add nothing.
import assert from 'node:assert/strict';

class FakeContext {
  constructor(canvas) { this.canvas = canvas; }
  drawImage(image) { this.data = new Uint8ClampedArray(image.pixels); }
  getImageData() { return {data: new Uint8ClampedArray(this.data), width: this.canvas.width, height: this.canvas.height}; }
  putImageData(image) { this.data = new Uint8ClampedArray(image.data); }
}
globalThis.ImageData = class { constructor(data, width, height) { Object.assign(this, {data, width, height}); } };
globalThis.document = {createElement: () => {
  const canvas = {width: 0, height: 0, getContext: () => canvas.context ??= new FakeContext(canvas)};
  return canvas;
}};
const {animateInteriorLights, tickInteriorLights} = await import('../map-client/interiorLights.ts');

// One 2x1 surface at the lightmap's origin, grey 10 everywhere, lit by a looping light (state 0 black at 0 ms,
// state 1 red at 500 ms, 1000 ms long) and by an alarm light that must not show.
const lightmap = {image: {width: 2, height: 1, pixels: [10, 10, 10, 255, 10, 10, 10, 255]}, needsUpdate: false};
const model = {lightMaps: [lightmap], interior: {
  surfaces: [{lightCount: 2, lightStateInfoStart: 0, mapOffset: [0, 0], mapSize: [2, 1]}],
  normalLightMapIndices: [0],
  animatedLights: [{stateIndex: 0, stateCount: 2, flags: 3, duration: 1000}, {stateIndex: 2, stateCount: 1, flags: 11, duration: 1000}],
  lightStates: [
    {red: 0, green: 0, blue: 0, activeTime: 0, dataIndex: 0, dataCount: 1},
    {red: 255, green: 0, blue: 0, activeTime: 500, dataIndex: 1, dataCount: 1},
    {red: 0, green: 0, blue: 255, activeTime: 0, dataIndex: 2, dataCount: 1}],
  lightStateData: [{surfaceIndex: 0, mapIndex: 0, lightStateIndex: 0}, {surfaceIndex: 0, mapIndex: 0, lightStateIndex: 0},
    {surfaceIndex: 0, mapIndex: 0, lightStateIndex: 1}],
  stateDataBuffer: new Uint8Array([255, 128]),
}};
animateInteriorLights(model);
const pixels = () => [...lightmap.image.context.data];
assert.deepEqual(pixels(), [10, 10, 10, 255, 10, 10, 10, 255]);  // black state, alarm light ignored
tickInteriorLights(1000);
tickInteriorLights(1250);  // 250 ms in: colour 128, 0, 0
assert.deepEqual(pixels(), [10 + 128, 10, 10, 255, 10 + ((128 * 128 + 0x80) >> 8), 10, 10, 255]);
assert.equal(lightmap.needsUpdate, true);
tickInteriorLights(1500);  // red state: 10 + 254 saturates
assert.deepEqual(pixels(), [255, 10, 10, 255, 10 + ((255 * 128 + 0x80) >> 8), 10, 10, 255]);
tickInteriorLights(1750);
tickInteriorLights(2000);  // 1000 ms wraps back to the black first state
assert.deepEqual(pixels(), [10, 10, 10, 255, 10, 10, 10, 255]);
console.log('ok');
