// Animated interior lights, as Tribes 2's InteriorInstance runs them (interior/interiorLightAnim.cc and
// InteriorInstance::updateLightMap in the public tribes2/engine source): every light starts on add; in the normal
// (non-alarm) state the non-alarm lights advance, and each changed surface's lightmap region is rebuilt as the
// original lightmap plus intensity * colour of every non-alarm light state on it, saturating at 255.
// Every instance of an interior starts its lights at the same moment, so one animation per loaded model is the
// game's picture for all of its instances.
import type {DIFModel} from '../src/dif/difLoader';

const AnimationTypeMask = 7, AlarmLight = 8, Loop = 2, Flicker = 4;
type Light = {stateIndex: number; stateCount: number; flags: number; duration: number};
type State = {red: number; green: number; blue: number; activeTime: number; dataIndex: number; dataCount: number};
type StateData = {surfaceIndex: number; mapIndex: number; lightStateIndex: number};
type Interior = DIFModel['interior'] & {animatedLights?: Light[]; lightStates?: State[];
  lightStateData?: StateData[]; stateDataBuffer?: Uint8Array};
type Info = {map: number; color: number[]; alarm: boolean};
type Running = {light: Light; time: number; state: number; color: number[]};

const animations: (() => void)[] = [];
let last = 0;

/** Advance every animated interior; the map viewer calls it once per frame. */
export function tickInteriorLights(now: number) {
  const ms = last ? Math.min(Math.round(now - last), 250) : 0;
  last = now;
  if (ms > 0) for (const step of animations) step(ms);
  return ms;
}

export function animateInteriorLights(model: DIFModel) {
  const interior = model.interior as Interior;
  const lights = interior.animatedLights ?? [], states = interior.lightStates ?? [];
  const data = interior.lightStateData ?? [], buffer = interior.stateDataBuffer;
  if (!buffer || !lights.some(light => !(light.flags & AlarmLight))) return;
  const infos: Info[] = [];
  const invalid = new Set<number>();
  const install = (light: Light, run: Running) => {
    const state = states[light.stateIndex + run.state];
    for (let i = state.dataIndex; i < state.dataIndex + state.dataCount; i++) {
      const entry = data[i];
      infos[entry.lightStateIndex] = {map: entry.mapIndex, color: run.color, alarm: (light.flags & AlarmLight) !== 0};
      invalid.add(entry.surfaceIndex);
    }
  };
  const running = lights.map(light => {
    const first = states[light.stateIndex];
    const run = {light, time: 0, state: 0, color: [first.red, first.green, first.blue]};
    install(light, run);
    return run;
  }).filter(run => !(run.light.flags & AlarmLight));

  // Lightmaps become canvases holding the original pixels plus the current light colours.
  const canvases = new Map<number, {context: CanvasRenderingContext2D; original: ImageData; current: ImageData}>();
  const canvasFor = (index: number) => {
    let entry = canvases.get(index);
    const texture = model.lightMaps[index];
    if (!entry && texture?.image) {
      const image = texture.image as HTMLImageElement;
      const canvas = document.createElement('canvas');
      canvas.width = image.width;
      canvas.height = image.height;
      const context = canvas.getContext('2d', {willReadFrequently: true})!;
      context.drawImage(image, 0, 0);
      const original = context.getImageData(0, 0, canvas.width, canvas.height);
      entry = {context, original, current: new ImageData(new Uint8ClampedArray(original.data), canvas.width, canvas.height)};
      texture.image = canvas;
      canvases.set(index, entry);
    }
    return entry;
  };

  const rebuild = () => {
    const changed = new Set<number>();
    for (const surfaceIndex of invalid) {
      const surface = interior.surfaces[surfaceIndex];
      const index = interior.normalLightMapIndices[surfaceIndex];
      const target = surface?.lightCount ? canvasFor(index) : undefined;
      if (!target) continue;
      const {original, current} = target;
      const [ox, oy] = surface.mapOffset, [w, h] = surface.mapSize;
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        const p = ((oy + y) * original.width + ox + x) * 4;
        let r = original.data[p], g = original.data[p + 1], b = original.data[p + 2];
        for (let i = 0; i < surface.lightCount; i++) {
          const info = infos[surface.lightStateInfoStart + i];
          if (!info || info.alarm || info.map === 0xffffffff) continue;
          const intensity = buffer[info.map + y * w + x] ?? 0;
          r += (info.color[0] * intensity + 0x80) >> 8;
          g += (info.color[1] * intensity + 0x80) >> 8;
          b += (info.color[2] * intensity + 0x80) >> 8;
        }
        current.data[p] = r; current.data[p + 1] = g; current.data[p + 2] = b; // Uint8ClampedArray saturates
      }
      changed.add(index);
    }
    invalid.clear();
    for (const index of changed) {
      const {context, current} = canvases.get(index)!;
      context.putImageData(current, 0, 0);
      model.lightMaps[index].needsUpdate = true;
    }
  };

  const step = (ms: number) => {
    for (const run of running) {
      const {light} = run;
      const oldState = run.state, oldColor = run.color.join();
      const type = light.flags & AnimationTypeMask;
      if (type & Flicker) flicker(run, states, ms);
      else if (type & Loop) loop(run, states, ms);
      else ramp(run, states, ms);
      if (run.state !== oldState || run.color.join() !== oldColor) install(light, run);
    }
    if (invalid.size) rebuild();
  };
  rebuild();
  animations.push(step);
}

/** Position within the light's states at curTime, with the colour interpolated towards the next state. */
function interpolate(run: Running, states: State[], wrap: boolean) {
  const {light} = run;
  run.state = 0;
  for (let i = 1; i < light.stateCount; i++) {
    if (states[light.stateIndex + i].activeTime <= run.time) run.state = i;
    else break;
  }
  const state = states[light.stateIndex + run.state];
  const lastState = run.state === light.stateCount - 1;
  const next = !lastState ? states[light.stateIndex + run.state + 1] : wrap ? states[light.stateIndex] : state;
  const into = run.time - state.activeTime;
  const total = !lastState ? next.activeTime - state.activeTime : wrap ? light.duration - state.activeTime : into;
  const t = total > 0 ? into / total : 0;
  run.color = [state.red + (next.red - state.red) * t, state.green + (next.green - state.green) * t,
    state.blue + (next.blue - state.blue) * t].map(v => Math.floor(v + 0.5));
}

function loop(run: Running, states: State[], ms: number) {
  run.time = (run.time + ms) % Math.max(1, run.light.duration);
  interpolate(run, states, true);
}

function ramp(run: Running, states: State[], ms: number) {
  run.time = Math.min(run.time + ms, run.light.duration);
  interpolate(run, states, false);
}

function flicker(run: Running, states: State[], ms: number) {
  const {light} = run;
  run.time += ms;
  if (run.time < (states[light.stateIndex + 1]?.activeTime ?? 0)) return;
  run.time = 0;
  const picked = Math.floor(Math.random() * (light.duration + 1));
  run.state = 0;
  for (let i = 1; i < light.stateCount; i++) {
    if (states[light.stateIndex + i].activeTime <= picked) run.state = i;
    else break;
  }
  const state = states[light.stateIndex + run.state];
  run.color = [state.red, state.green, state.blue];
}
