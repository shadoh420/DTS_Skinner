// Mission environment objects upstream's mission bridge leaves out: Precipitation, Lightning, FireballAtmosphere,
// ParticleEmissionDummy and the lava screen overlay, plus the clock for animated interior lights. Each follows the
// V12 behaviour in the public tribes2/engine source (game/precipitation.cc, game/lightning.cc,
// game/fireballAtmosphere.cc, game/particleEmitter.cc, game/underLava.cc); particles use upstream's own system.
import {useEffect, useMemo, useRef, useState} from 'react';
import {useFrame} from '@react-three/fiber';
import {AdditiveBlending, BufferAttribute, BufferGeometry, DoubleSide, DynamicDrawUsage, Group, Mesh, NormalBlending,
  Quaternion, ShaderMaterial, Texture, Vector3} from 'three';
import {useEngineSelector} from '../src/state/engineStore';
import {getProperty} from '../src/mission';
import {misRotationToThreeQuat} from '../src/torqueScript/vecMath';
import {EmitterInstance} from '../src/particles/ParticleSystem';
import {createParticleGeometry, createParticleMaterial, getParticleTexture, particleTexturesReady,
  syncBuffers, type ParticleBuffers} from '../src/particles/particleRenderer';
import type {EmitterDataResolved, ParticleDataResolved, ParticleKey} from '../src/particles/types';
import {castWorldRay} from '../src/collision/worldCollision';
import {terrainHeightAt} from '../src/collision/terrainCollision';
import {castWaterRay, LIQUID_TYPES, submergedWaterAt} from '../src/collision/waterLevel';
import {tickInteriorLights} from './interiorLights';
import {addNote} from './notes';

type V3 = [number, number, number];
type Obj = Record<string, any>;
const prop = (obj: Obj | undefined, name: string) => getProperty(obj as any, name);
const num = (obj: Obj | undefined, name: string, fallback: number) => {
  const value = parseFloat(prop(obj, name));
  return Number.isFinite(value) ? value : fallback;
};
const truthy = (value: unknown) => value !== undefined && !/^(0|false|)$/i.test(String(value));
const vec = (text: unknown, fallback: number[]) => {
  const parts = String(text ?? '').trim().split(/\s+/).map(Number);
  return fallback.map((value, i) => Number.isFinite(parts[i]) ? parts[i] : value);
};
// Torque (x, y, z) is Three (y, z, x).
const toThree = (v: number[], out = new Vector3()) => out.set(v[1], v[2], v[0]);
const toTorque = (v: Vector3): V3 => [v.z, v.x, v.y];
const random = (min: number, max: number) => min + Math.random() * (max - min);
const normalize = (v: V3): V3 => { const l = Math.hypot(...v) || 1; return [v[0] / l, v[1] / l, v[2] / l]; };

/** MathUtils::randomDir: a direction between minAngle and maxAngle degrees from axis, at a random azimuth. */
function randomDir(axis: V3, minAngle: number, maxAngle: number): V3 {
  const a = normalize(axis);
  const helper: V3 = Math.abs(a[2]) < 0.9 ? [0, 0, 1] : [0, 1, 0];
  const x = normalize([a[1] * helper[2] - a[2] * helper[1], a[2] * helper[0] - a[0] * helper[2], a[0] * helper[1] - a[1] * helper[0]]);
  const y: V3 = [a[1] * x[2] - a[2] * x[1], a[2] * x[0] - a[0] * x[2], a[0] * x[1] - a[1] * x[0]];
  const theta = random(minAngle, maxAngle) * Math.PI / 180, phi = Math.random() * 2 * Math.PI;
  const s = Math.sin(theta), c = Math.cos(theta);
  return [0, 1, 2].map(i => a[i] * c + (x[i] * Math.cos(phi) + y[i] * Math.sin(phi)) * s) as V3;
}

function walk(object: Obj | undefined, found: Obj[] = []): Obj[] {
  if (!object) return found;
  found.push(object);
  for (const child of object._children ?? []) walk(child, found);
  return found;
}

// ---- Particles from script datablocks ----
// Upstream resolves particle datablocks from demo streams, whose values are bit-packed; mission datablocks hold
// the script values with V12's constructor defaults (game/particleEngine.cc).

function particleData(db: Obj): ParticleDataResolved {
  const times = [0, num(db, 'times1', 1), num(db, 'times2', 2), num(db, 'times3', 2)];
  let count = 0;
  while (count < 3 && times[count] < 1) count++;
  const keys: ParticleKey[] = times.slice(0, count + 1).map((time, i) => {
    const [r, g, b, a] = vec(prop(db, 'colors' + i), [1, 1, 1, 1]);
    return {r, g, b, a, size: num(db, 'sizes' + i, 1), time};
  });
  // textureName and animTexName[0] are one field in V12; an animated particle draws its first frame here.
  const textureName = String((truthy(prop(db, 'animateTexture')) && prop(db, 'animTexName0')) || prop(db, 'textureName') || prop(db, 'animTexName0') || '');
  return {
    dragCoefficient: num(db, 'dragCoefficient', 0), windCoefficient: num(db, 'windCoefficient', 1),
    gravityCoefficient: num(db, 'gravityCoefficient', 0), inheritedVelFactor: num(db, 'inheritedVelFactor', 0),
    constantAcceleration: num(db, 'constantAcceleration', 0), lifetimeMS: num(db, 'lifetimeMS', 1000),
    lifetimeVarianceMS: num(db, 'lifetimeVarianceMS', 0), spinSpeed: num(db, 'spinSpeed', 0),
    spinRandomMin: num(db, 'spinRandomMin', 0), spinRandomMax: num(db, 'spinRandomMax', 0),
    useInvAlpha: truthy(prop(db, 'useInvAlpha')), keys, textureName,
  };
}

function emitterData(datablocks: Map<string, Obj> | any, name: unknown): EmitterDataResolved | null {
  const db = name ? datablocks.get(String(name)) : undefined;
  if (!db) return null;
  const particle = String(prop(db, 'particles') ?? '').split(/\s+/).map(item => datablocks.get(item)).find(Boolean);
  if (!particle) return null;
  return {
    ejectionPeriodMS: Math.max(1, num(db, 'ejectionPeriodMS', 100)), periodVarianceMS: num(db, 'periodVarianceMS', 0),
    ejectionVelocity: num(db, 'ejectionVelocity', 2), velocityVariance: num(db, 'velocityVariance', 1),
    ejectionOffset: num(db, 'ejectionOffset', 0), thetaMin: num(db, 'thetaMin', 0), thetaMax: num(db, 'thetaMax', 90),
    phiReferenceVel: num(db, 'phiReferenceVel', 0), phiVariance: num(db, 'phiVariance', 360),
    overrideAdvances: truthy(prop(db, 'overrideAdvance')), orientParticles: truthy(prop(db, 'orientParticles')),
    orientOnVelocity: prop(db, 'orientOnVelocity') === undefined || truthy(prop(db, 'orientOnVelocity')),
    useEmitterColors: truthy(prop(db, 'useEmitterColors')),
    lifetimeMS: num(db, 'lifetimeMS', 0), lifetimeVarianceMS: num(db, 'lifetimeVarianceMS', 0),
    particles: particleData(particle),
  };
}

type Source = ParticleBuffers & {mesh: Mesh; material: ShaderMaterial; texture: Texture};
function createSource(data: EmitterDataResolved, group: Group, timeMultiple = 1): Source {
  // Enough room for every particle the emitter can keep alive (upstream's own cap is 256).
  const alive = (data.particles.lifetimeMS + data.particles.lifetimeVarianceMS) / Math.max(1, data.ejectionPeriodMS - data.periodVarianceMS);
  const max = Math.min(4096, Math.ceil(alive * timeMultiple) + 16);
  const emitter = new EmitterInstance(data, max);
  const texture = getParticleTexture(data.particles.textureName);
  const geometry = createParticleGeometry(max);
  const material = createParticleMaterial(texture, data.particles.useInvAlpha, data.orientParticles);
  const mesh = new Mesh(geometry, material);
  mesh.frustumCulled = false;
  group.add(mesh);
  return {emitter, geometry, mesh, material, texture};
}
function drawSource(source: Source) {
  if (source.material.uniforms.particleTexture.value !== source.texture && particleTexturesReady.has(source.texture))
    source.material.uniforms.particleTexture.value = source.texture;
  syncBuffers(source);
}
function disposeSource(source: Source) {
  source.mesh.removeFromParent();
  source.geometry.dispose();
  source.material.dispose();
}

function ParticleObjects({dummies, fireballs, datablocks}: {dummies: Obj[]; fireballs: Obj[]; datablocks: any}) {
  const group = useMemo(() => new Group(), []);
  const state = useMemo(() => {
    const emitters = dummies.flatMap(dummy => {
      const data = emitterData(datablocks, prop(dummy, 'emitter'));
      if (!data) { addNote(`Particle emitter ${prop(dummy, 'emitter')} is not defined; one smoke or fire source is not drawn.`); return []; }
      const timeMultiple = num(datablocks.get(String(prop(dummy, 'dataBlock'))), 'timeMultiple', 1);
      const axis = toTorque(new Vector3(0, 1, 0).applyQuaternion(new Quaternion(...misRotationToThreeQuat(String(dummy.rotation ?? '1 0 0 0')))));
      const position = vec(dummy.position, [0, 0, 0]) as V3;
      const velocity = num(dummy, 'velocity', 1);
      return [{source: createSource(data, group, timeMultiple), position, axis, timeMultiple,
        velocity: axis.map(v => v * velocity) as V3}];
    });
    const atmospheres = fireballs.map(object => {
      const debris = datablocks.get(String(prop(datablocks.get(String(prop(object, 'dataBlock'))), 'fireball') ?? ''));
      return {object, debris, timer: 0, dropRadius: num(object, 'dropRadius', 600), perMinute: num(object, 'dropsPerMinute', 3),
        minAngle: num(object, 'minDropAngle', 0), maxAngle: num(object, 'maxDropAngle', 30),
        speed: num(object, 'startVelocity', 20), height: num(object, 'dropHeight', 500),
        dir: normalize(vec(prop(object, 'dropDir'), [0.5, 0.5, -0.5]) as V3)};
    }).filter(atmosphere => atmosphere.debris);
    type Ball = {pos: V3; vel: V3; life: number; trails: Source[]; debris: Obj; gone?: boolean};
    return {emitters, atmospheres, balls: [] as Ball[], bursts: [] as {source: Source; pos: V3; left: number}[]};
  }, [dummies, fireballs, datablocks, group]);
  useEffect(() => () => {
    state.emitters.forEach(item => disposeSource(item.source));
    state.balls.forEach(ball => ball.trails.forEach(disposeSource));
    state.bursts.forEach(burst => disposeSource(burst.source));
  }, [state]);

  useFrame(({camera}, delta) => {
    const ms = Math.min(250, delta * 1000);
    if (ms <= 0) return;
    for (const item of state.emitters) {
      item.source.emitter.update(ms);
      item.source.emitter.emitPeriodic(item.position, item.position, ms * item.timeMultiple, item.axis, item.velocity);
      drawSource(item.source);
    }
    // FireballAtmosphere::dropNewFireball: a Debris launched dropHeight up, within dropRadius of the camera.
    const camera3 = toTorque(camera.position);
    for (const atmosphere of state.atmospheres) {
      atmosphere.timer += ms / 1000;
      const every = 60 / Math.max(0.01, atmosphere.perMinute);
      while (atmosphere.timer > every) {
        atmosphere.timer -= every;
        const vel = randomDir(atmosphere.dir, atmosphere.minAngle, atmosphere.maxAngle).map(v => v * atmosphere.speed) as V3;
        const around = randomDir([0, 0, -1], 90, 90).map(v => v * atmosphere.dropRadius * random(0.1, 1));
        const t = vel[2] < 0 ? atmosphere.height / vel[2] : 0;
        const pos: V3 = [camera3[0] + around[0] + vel[0] * t, camera3[1] + around[1] + vel[1] * t, vel[2] * t];
        const trails = [0, 1, 2, 3].map(i => emitterData(datablocks, prop(atmosphere.debris, 'emitters' + i)))
          .filter(Boolean).map(data => createSource(data!, group));
        state.balls.push({pos, vel, life: num(atmosphere.debris, 'lifetime', 3), trails, debris: atmosphere.debris});
      }
    }
    const terminal = (ball: {debris: Obj}) => num(ball.debris, 'terminalVelocity', 0);
    for (const ball of state.balls) {
      if (!ball.gone) {
        ball.life -= ms / 1000;
        // Debris::computeNewState: gravity until the terminal speed (the atmosphere sets it to startVelocity).
        const speed = Math.hypot(...ball.vel), cap = terminal(ball);
        if (cap > 0.0001 && speed > cap) ball.vel = ball.vel.map(v => v / speed * cap) as V3;
        else ball.vel[2] -= 9.81 * num(ball.debris, 'gravModifier', 1) * ms / 1000;
        const next = ball.pos.map((v, i) => v + ball.vel[i] * ms / 1000) as V3;
        const hit = castWorldRay(ball.pos, next) ?? (truthy(prop(ball.debris, 'ignoreWater')) ? null : castWaterRay(ball.pos, next));
        const end = hit ? hit.point as V3 : next;
        const axis = normalize(ball.vel.map(v => -v) as V3);
        for (const trail of ball.trails) trail.emitter.emitPeriodic(ball.pos, end, ms, axis, ball.vel);
        ball.pos = end;
        if (hit || ball.life <= 0) {
          ball.gone = true;
          ball.trails.forEach(trail => trail.emitter.kill());
          if (hit) explode(datablocks.get(String(prop(ball.debris, 'explosion') ?? '')), end);
        }
      }
      for (const trail of ball.trails) { trail.emitter.update(ms); drawSource(trail); }
    }
    for (const ball of state.balls) if (ball.gone && ball.trails.every(trail => trail.emitter.isDead())) ball.trails.forEach(disposeSource);
    state.balls = state.balls.filter(ball => !(ball.gone && ball.trails.every(trail => trail.emitter.isDead())));
    for (const burst of state.bursts) {
      burst.source.emitter.update(ms);
      if (burst.left > 0) burst.source.emitter.emitPeriodic(burst.pos, burst.pos, Math.min(ms, burst.left), [0, 0, 1]);
      else burst.source.emitter.kill();
      burst.left -= ms;
      drawSource(burst.source);
    }
    for (const burst of state.bursts) if (burst.source.emitter.isDead()) disposeSource(burst.source);
    state.bursts = state.bursts.filter(burst => !burst.source.emitter.isDead());
  });

  // Explosion: particleDensity particles within particleRadius at once, and each emitters[i] for the lifetime.
  // ponytail: the explosion's sub-explosion shapes, light and camera shake are not drawn.
  function explode(explosion: Obj | undefined, pos: V3) {
    if (!explosion) return;
    const burst = emitterData(datablocks, prop(explosion, 'particleEmitter'));
    if (burst) {
      const source = createSource(burst, group);
      source.emitter.emitRadial(pos, num(explosion, 'particleRadius', 1), num(explosion, 'particleDensity', 10));
      state.bursts.push({source, pos, left: 0});
    }
    for (let i = 0; i < 4; i++) {
      const data = emitterData(datablocks, prop(explosion, 'emitter' + i));
      if (data) state.bursts.push({source: createSource(data, group), pos, left: num(explosion, 'lifetimeMS', 1000)});
    }
  }
  return <primitive object={group}/>;
}

// ---- Precipitation (game/precipitation.cc, renderPrecip for an observer camera) ----
// V12 moves each drop by its speed once per rendered frame, so the game's rain and snow fall faster the faster
// it draws. Measured 2026-10-16 on the reference PC (vsync off, 280 Hz screen): 282-295 fps, and Katabatic's
// snow then moved 176 flake widths a second (median), against 179 on the page at 285 and 79 at 30.
// ponytail: one fixed rate; another PC's game runs at its own.
const FRAME_RATE = 290;
const precipVertex = `attribute vec4 dropColor; varying vec4 vColor; varying vec2 vUv;
  void main() { vColor = dropColor; vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
const precipFragment = `uniform sampler2D map; varying vec4 vColor; varying vec2 vUv;
  void main() { vec4 c = texture2D(map, vUv) * vColor; if (c.a <= 0.1) discard; gl_FragColor = c; }`;

function Precipitation({object, datablocks, sky}: {object: Obj; datablocks: any; sky?: Obj}) {
  const setup = useMemo(() => {
    const db = datablocks.get(String(prop(object, 'dataBlock'))) ?? {};
    const type = num(db, 'type', 0);
    const colors = [1, 2, 3].map(i => vec(prop(object, 'color' + i), [-1, 0, 0, 1])).filter((c, i, all) => all.slice(0, i + 1).every(item => item[0] >= 0));
    if (!colors.length) colors.push(type === 0 ? [0.6, 0.6, 0.6, 1] : type === 1 ? [1, 1, 1, 1] : [0.9, 0.8, 0.5, 1]);
    const maxDrops = Math.min(2000, Math.max(0, num(object, 'maxNumDrops', -1) < 0 ? 2000 : num(object, 'maxNumDrops', 2000)));
    const percentage = Math.min(1, Math.max(0, num(object, 'percentage', 1)));
    const minVelocity = num(object, 'minVelocity', -1) >= 0 ? num(object, 'minVelocity', 0) : type === 0 ? 1.25 : 0.25;
    const maxVelocity = num(object, 'maxVelocity', -1) >= 0 ? num(object, 'maxVelocity', 0) : type === 0 ? 4 : type === 1 ? 1.5 : 1;
    const radius = num(object, 'maxRadius', -1) >= 0 ? num(object, 'maxRadius', 0) : type === 1 ? 125 : 80;
    // The material list's first entry is the 4x4 atlas of drops; the .dml lives in textures/.
    const atlas = String(prop(db, 'materialList') ?? '').toLowerCase().startsWith('snow') ? 'precipitation/snowflakes' : 'precipitation/raindrops';
    const wind = vec(prop(sky, 'windVelocity'), [0, 0, 0]), windLength = Math.hypot(wind[0], wind[1]);
    const offsetSpeed = num(object, 'offsetSpeed', 0.25);
    const offset: V3 = truthy(prop(sky, 'windEffectPrecipitation')) && windLength > 0
      ? [wind[0] / windLength * offsetSpeed, wind[1] / windLength * offsetSpeed, 1] : [0, 0, 1];
    return {type, colors, count: Math.round(maxDrops * percentage), minVelocity, maxVelocity, radius, atlas, offset,
      sizeX: num(db, 'sizeX', 1), sizeY: num(db, 'sizeY', 1), divHeight: num(db, 'divHeightVal', 1.5) || 1.5};
  }, [object, datablocks, sky]);
  const mesh = useMemo(() => {
    const {count, atlas} = setup;
    const geometry = new BufferGeometry();
    const index = new Uint32Array(count * 6);
    for (let i = 0; i < count; i++) index.set([i * 4, i * 4 + 1, i * 4 + 2, i * 4, i * 4 + 2, i * 4 + 3], i * 6);
    geometry.setIndex(new BufferAttribute(index, 1));
    for (const [name, size] of [['position', 3], ['uv', 2], ['dropColor', 4]] as const)
      geometry.setAttribute(name, new BufferAttribute(new Float32Array(count * 4 * size), size).setUsage(DynamicDrawUsage));
    const texture = getParticleTexture(atlas);
    const material = new ShaderMaterial({vertexShader: precipVertex, fragmentShader: precipFragment, uniforms: {map: {value: texture}},
      transparent: true, depthWrite: false, blending: NormalBlending, side: DoubleSide});
    const result = new Mesh(geometry, material);
    result.frustumCulled = false;
    result.renderOrder = 5;
    return result;
  }, [setup]);
  useEffect(() => () => { mesh.geometry.dispose(); (mesh.material as ShaderMaterial).dispose(); }, [mesh]);
  const drops = useMemo(() => Array.from({length: setup.count}, () => ({pos: [0, 0, 0] as V3, speed: 0, color: 0, tex: 0,
    startDiff: 1, top: true, valid: false})), [setup]);
  const last = useRef<{camera?: V3; first: boolean}>({first: true});
  const right = useMemo(() => new Vector3(), []), up = useMemo(() => new Vector3(), []);

  useFrame(({camera}, delta) => {
    const {radius, divHeight, minVelocity, maxVelocity, offset, colors, type, sizeX, sizeY} = setup;
    const cam = toTorque(camera.position);
    const frames = Math.min(delta, 0.25) * FRAME_RATE;
    // An observer camera has no player velocity: the box is the camera's, shifted upwind by the drift.
    const shift = last.current.camera && Math.hypot(...cam.map((v, i) => v - last.current.camera![i])) > radius / 2
      ? cam.map((v, i) => v - last.current.camera![i]) : [0, 0, 0];
    last.current.camera = cam;
    const min = [cam[0] - radius, cam[1] - radius, cam[2] - radius / divHeight];
    const max = [cam[0] + radius, cam[1] + radius, cam[2] + radius / divHeight];
    const center = [offset[0] * radius, offset[1] * radius];
    camera.matrixWorld.extractBasis(right, up, new Vector3());
    const r = toTorque(right).map(v => v * sizeX), u = toTorque(up).map(v => v * sizeY);
    // Quad corners around a drop: snow is a camera-facing square; a raindrop stretches 2 * sizeY along its fall.
    const c1 = r.map((v, k) => v - u[k]), c2 = r.map((v, k) => -v - u[k]);
    const corners = type === 1 ? [r.map((v, k) => v + u[k]), c1, c2, r.map((v, k) => -v + u[k])]
      : [c1.map((v, k) => v + offset[k] * sizeY * 2), c1, c2, c2.map((v, k) => v + offset[k] * sizeY * 2)];
    const positions = mesh.geometry.getAttribute('position') as BufferAttribute;
    const uvs = mesh.geometry.getAttribute('uv') as BufferAttribute;
    const tints = mesh.geometry.getAttribute('dropColor') as BufferAttribute;
    const p = positions.array as Float32Array, t = uvs.array as Float32Array, c = tints.array as Float32Array;
    drops.forEach((drop, i) => {
      for (let k = 0; k < 3; k++) drop.pos[k] += shift[k] + (k < 2 ? offset[k] * drop.speed : drop.speed) * frames;
      const inside = drop.pos.every((v, k) => v >= min[k] && v <= max[k]);
      if (!drop.valid || !inside) {
        const r1 = Math.random(), r2 = Math.random(), r3 = Math.random();
        const angle = Math.random() * 2 * Math.PI;
        const along = radius * Math.min(1, Math.max(0.01, r2 < 0.2 ? r3 : r2));
        drop.speed = -minVelocity - (maxVelocity - minVelocity) * r3;
        // The first frame fills the whole box, as V12's mRandomHeight intends, rather than starting every drop at the top.
        const height = last.current.first ? random(min[2], max[2] - 5) : max[2] - 5;
        drop.pos = [cam[0] + center[0] + Math.cos(angle) * along, cam[1] + center[1] + Math.sin(angle) * along, height];
        drop.startDiff = height - cam[2] + 20;
        drop.color = Math.max(0, Math.ceil(colors.length * r1) - 1);
        const pick = Math.random();
        drop.tex = Math.max(0, Math.ceil(15 * (pick > 0.99995 && pick < 0.99999 ? 1.01 : pick)) - 1);
        drop.valid = true;
      }
      // Torque (x, y, z) is written as Three (y, z, x).
      for (let k = 0; k < 4; k++) {
        const o = (i * 4 + k) * 3;
        p[o] = drop.pos[1] + corners[k][1]; p[o + 1] = drop.pos[2] + corners[k][2]; p[o + 2] = drop.pos[0] + corners[k][0];
      }
      // GL t = 0 is the image's top row; Three's v = 1 is.
      const cx = (drop.tex % 4) * 0.25, cy = 1 - Math.floor(drop.tex / 4) * 0.25, o = i * 8;
      t[o] = cx; t[o + 1] = cy; t[o + 2] = cx; t[o + 3] = cy - 0.25; t[o + 4] = cx + 0.25; t[o + 5] = cy - 0.25; t[o + 6] = cx + 0.25; t[o + 7] = cy;
      const color = colors[drop.color];
      const alpha = 1 - (drop.pos[2] - cam[2]) / drop.startDiff;
      for (let k = 0; k < 4; k++) { const q = (i * 4 + k) * 4; c[q] = color[0]; c[q + 1] = color[1]; c[q + 2] = color[2]; c[q + 3] = alpha; }
    });
    last.current.first = false;
    positions.needsUpdate = uvs.needsUpdate = tints.needsUpdate = true;
  });
  return <primitive object={mesh}/>;
}

// ---- Lightning (game/lightning.cc) ----
type Nodes = V3[];
type Bolt = {start: V3; end: V3; width: number; major: Nodes; minor: Nodes[]; minorCount: number; minorAngle: number;
  splits: Bolt[]; elapsed: number; renderTime: number; started: boolean; fading: boolean; fade: number};
function generateNodes(start: V3, end: V3, count: number, maxAngle: number): Nodes {
  const dist = Math.hypot(...end.map((v, i) => v - start[i]));
  const minStep = dist / (count - 1), maxStep = minStep / Math.cos(maxAngle * Math.PI / 180);
  const nodes: Nodes = [start];
  let toLine = normalize(end.map((v, i) => v - start[i]) as V3);
  for (let i = 1; i < count - 1; i++) {
    const dir = randomDir(toLine, 0, maxAngle), step = random(minStep, maxStep);
    const point = nodes[i - 1].map((v, k) => v + dir[k] * step) as V3;
    nodes.push(point);
    toLine = normalize(end.map((v, k) => v - point[k]) as V3);
  }
  nodes.push(end);
  return nodes;
}
function makeBolt(start: V3, end: V3, width: number, majorCount: number, majorAngle: number, minorCount: number, minorAngle: number): Bolt {
  const major = generateNodes(start, end, majorCount, majorAngle);
  const bolt: Bolt = {start, end, width, major, minor: [], minorCount, minorAngle, splits: [], elapsed: 0, renderTime: 0,
    started: true, fading: false, fade: 0};
  generateMinor(bolt);
  return bolt;
}
function generateMinor(bolt: Bolt) {
  bolt.minor = bolt.major.slice(0, -1).map((point, i) => generateNodes(point, bolt.major[i + 1], bolt.minorCount, bolt.minorAngle));
}
function createSplit(bolt: Bolt, start: V3, end: V3, depth: number, width: number) {
  if (depth === 0 || Math.random() > 0.7) return;
  width = Math.max(width, 0.75);
  const diff = end.map((v, i) => v - start[i]) as V3, length = Math.hypot(...diff);
  bolt.splits.push(makeBolt(start, end, width, 3, 30, 3, 10));
  for (let i = 0; i < 2; i++) {
    const dir = randomDir(diff, 10, 45), reach = random(0.5, 1.5) * length;
    createSplit(bolt, end, end.map((v, k) => v + dir[k] * reach) as V3, depth - 1, width * 0.3);
  }
}

function Lightning({object, datablocks}: {object: Obj; datablocks: any}) {
  const setup = useMemo(() => {
    const db = datablocks.get(String(prop(object, 'dataBlock')));
    return {position: vec(object.position, [0, 0, 0]) as V3, scale: vec(object.scale, [512, 512, 300]) as V3,
      perMinute: num(object, 'strikesPerMinute', 12), width: num(object, 'strikeWidth', 2.5),
      startRadius: num(object, 'boltStartRadius', 20), color: vec(prop(object, 'color'), [1, 1, 1, 1]),
      fadeColor: vec(prop(object, 'fadeColor'), [0.1, 0.1, 1, 1]),
      texture: String(prop(db, 'strikeTextures0') ?? 'special/skyLightning')};
  }, [object, datablocks]);
  const mesh = useMemo(() => {
    const geometry = new BufferGeometry();
    const material = new ShaderMaterial({uniforms: {map: {value: getParticleTexture(setup.texture)}},
      vertexShader: `attribute vec4 boltColor; varying vec4 vColor; varying vec2 vUv;
        void main() { vColor = boltColor; vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: `uniform sampler2D map; varying vec4 vColor; varying vec2 vUv;
        void main() { gl_FragColor = texture2D(map, vUv) * vColor; }`,
      transparent: true, depthWrite: false, blending: AdditiveBlending, side: DoubleSide});
    // ponytail: useFog is ignored; V12 enables GL fog with no fog coordinates, so bolts are drawn unfogged anyway.
    const result = new Mesh(geometry, material);
    result.frustumCulled = false;
    return result;
  }, [setup]);
  useEffect(() => () => { mesh.geometry.dispose(); (mesh.material as ShaderMaterial).dispose(); }, [mesh]);
  const strikes = useRef<{age: number; bolts: Bolt[]}[]>([]);
  const timer = useRef(0);

  useFrame(({camera}, delta) => {
    const dt = Math.min(delta, 0.25);
    const {position, scale, perMinute, width, startRadius, color, fadeColor} = setup;
    timer.current += dt;
    const every = 60 / Math.max(0.01, perMinute);
    while (timer.current > every) {
      timer.current -= every;
      // strikeRandomPoint: anywhere in the object's box (its rotation is not applied), down to what a ray finds.
      const x = position[0] + (Math.random() - 0.5) * scale[0], y = position[1] + (Math.random() - 0.5) * scale[1];
      const top = position[2] + scale[2] * 0.5;
      const hit = castWorldRay([x, y, top], [x, y, position[2] - scale[2] * 0.5]) ?? castWaterRay([x, y, top], [x, y, position[2] - scale[2] * 0.5]);
      const ground: V3 = [x, y, hit ? hit.point[2] : terrainHeightAt(x, y) ?? 0];
      strikes.current.push({age: 0, bolts: [0, 1, 2].map(() => {
        const start: V3 = [x + random(-startRadius, startRadius), y + random(-startRadius, startRadius), top];
        const bolt = makeBolt(start, ground, width, 10, 30, 4, 15);
        for (let i = 0; i < bolt.major.length - 1; i++) {
          if (Math.random() > 0.3) continue;
          const a = bolt.major[i], b = bolt.major[i + 1];
          const seg = b.map((v, k) => v - a[k]) as V3, length = Math.hypot(...seg);
          const dir = randomDir(seg, 20, 40), reach = random(0.5, 1.5) * length;
          createSplit(bolt, a, a.map((v, k) => v + dir[k] * reach) as V3, 4, width * 0.3);
        }
        Object.assign(bolt, {started: false, renderTime: random(0, 0.25)});
        return bolt;
      })});
    }
    const positions: number[] = [], uvs: number[] = [], colors: number[] = [], index: number[] = [];
    const cam = toTorque(camera.position);
    const strip = (bolt: Bolt, rgba: number[]) => {
      const verts: number[][] = [];
      bolt.minor.forEach((segment, s) => segment.forEach((point, i) => {
        const lastSegment = s === bolt.minor.length - 1;
        if (i === segment.length - 1 && !lastSegment) return;
        const dir = normalize((i === segment.length - 1 ? point.map((v, k) => v - segment[i - 1][k]) : segment[i + 1].map((v, k) => v - point[k])) as V3);
        const fromCam = point.map((v, k) => v - cam[k]);
        const cross = normalize([fromCam[1] * dir[2] - fromCam[2] * dir[1], fromCam[2] * dir[0] - fromCam[0] * dir[2], fromCam[0] * dir[1] - fromCam[1] * dir[0]]).map(v => v * bolt.width * 0.5);
        verts.push([...point.map((v, k) => v - cross[k]), i % 2, 0], [...point.map((v, k) => v + cross[k]), i % 2, 1]);
      }));
      const base = positions.length / 3;
      for (const [x, y, z, u, v] of verts) { positions.push(y, z, x); uvs.push(u, v); colors.push(...rgba); }
      for (let i = 2; i < verts.length; i++) index.push(base + i - 2, base + i - 1, base + i);
    };
    const render = (bolt: Bolt, fading: boolean) => {
      if (!fading) generateMinor(bolt);
      const alpha = Math.max(0, 1 - bolt.fade);
      strip(bolt, fading ? [fadeColor[0], fadeColor[1], fadeColor[2], alpha] : color);
      for (const split of bolt.splits) render(split, fading);
    };
    for (const strike of strikes.current) {
      strike.age += dt;
      for (const bolt of strike.bolts) {
        // LightningBolt::update: a delayed start, then 0.2 s at full colour, then a fade over the lifetime of 1 s.
        bolt.elapsed += dt;
        if (bolt.elapsed > 0.2) { bolt.fading = true; bolt.fade = bolt.elapsed + 0.2; }
        if (bolt.elapsed > bolt.renderTime && !bolt.started) { bolt.started = true; bolt.fading = false; bolt.elapsed = 0; }
        if (bolt.started) { for (const split of bolt.splits) split.fade = bolt.fade; render(bolt, bolt.fading); }
      }
    }
    strikes.current = strikes.current.filter(strike => strike.age <= 1.6);
    const geometry = mesh.geometry;
    geometry.setAttribute('position', new BufferAttribute(new Float32Array(positions), 3));
    geometry.setAttribute('uv', new BufferAttribute(new Float32Array(uvs), 2));
    geometry.setAttribute('boltColor', new BufferAttribute(new Float32Array(colors), 4));
    geometry.setIndex(index);
  });
  return <primitive object={mesh}/>;
}

// V12 counts the camera as in a liquid only where its fluid exists (fluid::IsFluidAtXY): the fluid is split into
// blocks of 8 terrain squares (4 when it covers under a quarter of the terrain), and fluid::RebuildMasks keeps a
// block when any terrain point in it lies below the surface plus half the wave height. Under the terrain, or
// where the ground rises above the surface, there is no fluid and so no overlay.
// ponytail: removeWetEdges' flood fill is not applied; it only trims fluid along the surface's dry edges.
function fluidAt(block: Obj, x: number, y: number): boolean {
  const [px, py, pz] = vec(block.position, [0, 0, 0]), [sx, sy, sz] = vec(block.scale, [1, 1, 1]);
  const x0 = Math.min(2040, Math.max(0, Math.floor((px + 1024) / 8 + 0.5)));
  const y0 = Math.min(2040, Math.max(0, Math.floor((py + 1024) / 8 + 0.5)));
  let nx = Math.floor(sx / 8 + 0.5), ny = Math.floor(sy / 8 + 0.5);
  const high = nx <= 128 && ny <= 128, per = high ? 4 : 8;
  nx = Math.min(256, Math.max(per, (nx + per - 1) & ~(per - 1)));
  ny = Math.min(256, Math.max(per, (ny + per - 1) & ~(per - 1)));
  const bx = ((Math.floor(x + 1024) - x0 * 8) & 2047) >> (high ? 5 : 6);
  const by = ((Math.floor(y + 1024) - y0 * 8) & 2047) >> (high ? 5 : 6);
  if (bx >= nx / per || by >= ny / per) return false;
  const level = pz + sz + num(block, 'waveMagnitude', 1) / 2;
  for (let j = 0; j <= per; j++) for (let i = 0; i <= per; i++) {
    const h = terrainHeightAt((x0 + bx * per + i) * 8 - 1024, (y0 + by * per + j) * 8 - 1024);
    if (h === null || h < level) return true;
  }
  return false;
}

// ---- Lava overlay (game/underLava.cc, drawn when the camera is in a lava block's fluid) ----
function LavaFilter({textures, blocks}: {textures: string[]; blocks: Obj[]}) {
  const mesh = useMemo(() => {
    const geometry = new BufferGeometry();
    geometry.setAttribute('position', new BufferAttribute(new Float32Array([-1, -1, 0, 3, -1, 0, -1, 3, 0]), 3));
    const maps = textures.map(name => name ? getParticleTexture(name) : null);
    const material = new ShaderMaterial({
      uniforms: {layer0: {value: maps[0]}, layer1: {value: maps[1]}, has1: {value: !!maps[1]}, time: {value: 0}, frequency: {value: 1}},
      vertexShader: `varying vec2 vScreen; void main() { vScreen = position.xy * 0.5 + 0.5; gl_Position = vec4(position.xy, 0.0, 1.0); }`,
      // Two layers over a full-screen grid whose texture coordinates ripple: amplitude 0.02, frequency 2,
      // phase 2 per second (the second layer 10 ahead), drifting 0.025 per second; each drawn at alpha 0.5.
      fragmentShader: `uniform sampler2D layer0; uniform sampler2D layer1; uniform bool has1; uniform float time; uniform float frequency;
        varying vec2 vScreen;
        vec2 ripple(float phase) {
          vec2 uv = vec2(vScreen.x * frequency, vScreen.y);
          uv += time * 0.025 + 0.02 * sin(uv * 6.283185307 * 2.0 + phase);
          return vec2(uv.x, 1.0 - uv.y);
        }
        void main() {
          vec4 a = texture2D(layer0, ripple(time * 2.0)); a.a *= 0.5;
          vec4 color = vec4(a.rgb * a.a, a.a);
          if (has1) { vec4 b = texture2D(layer1, ripple(time * 2.0 + 10.0)); b.a *= 0.5; color = vec4(b.rgb * b.a + color.rgb * (1.0 - b.a), b.a + color.a * (1.0 - b.a)); }
          gl_FragColor = vec4(color.rgb / max(color.a, 1e-4), color.a);
        }`,
      transparent: true, depthTest: false, depthWrite: false});
    const result = new Mesh(geometry, material);
    result.frustumCulled = false;
    result.renderOrder = 10001;
    result.visible = false;
    return result;
  }, [textures]);
  useEffect(() => () => { mesh.geometry.dispose(); (mesh.material as ShaderMaterial).dispose(); }, [mesh]);
  useFrame(({camera, size, clock}) => {
    const pos = camera.position;
    const info = submergedWaterAt(pos.z, pos.x, pos.y);
    mesh.visible = !!textures[0] && info !== null && info.liquidType >= 4 && info.liquidType <= 6
      && blocks.some(block => fluidAt(block, pos.z, pos.x));
    const u = (mesh.material as ShaderMaterial).uniforms;
    u.time.value = clock.elapsedTime;
    u.frequency.value = Math.floor(size.width / Math.max(1, size.height));  // V12 divides the integer viewport sizes
  });
  return <primitive object={mesh}/>;
}

export function MapEnvironment() {
  const runtime = useEngineSelector(state => state.runtime.runtime);
  const [objects, setObjects] = useState<Obj[] | null>(null);
  const waited = useRef(0);
  useFrame((_, delta) => {
    tickInteriorLights(performance.now());
    // The mission's objects exist once its MissionGroup does; look about twice a second until then.
    if (objects || !runtime || (waited.current += delta) < 0.5) return;
    waited.current = 0;
    const group = runtime.getObjectByName('MissionGroup') as Obj | undefined;
    if (group?._children?.length) setObjects(walk(group));
  });
  const found = useMemo(() => {
    const of = (name: string) => (objects ?? []).filter(object => object._className === name);
    const water = of('WaterBlock');
    // WaterBlock::onAdd sets the one global pair of submerge textures; a later block with a name replaces it.
    // The overlay only shows inside lava, so a map without lava loads nothing for it.
    const lavaBlocks = water.filter(block => {
      const type = LIQUID_TYPES[String(prop(block, 'liquidType') ?? '').toLowerCase()] ?? 0;
      return type >= 4 && type <= 6;
    });
    const lava = lavaBlocks.length ? [0, 1].map(i => water.reduce((name, block) => prop(block, 'submergeTexture' + i) || name, '')) : ['', ''];
    return {precipitation: of('Precipitation'), lightning: of('Lightning'), fireballs: of('FireballAtmosphere'),
      dummies: of('ParticleEmissionDummy'), sky: of('Sky')[0], lava, lavaBlocks};
  }, [objects]);
  if (!objects || !runtime) return null;
  const datablocks = runtime.state.datablocks;
  return <>
    {found.precipitation.map(object => <Precipitation key={object._id} object={object} datablocks={datablocks} sky={found.sky}/>)}
    {found.lightning.map(object => <Lightning key={object._id} object={object} datablocks={datablocks}/>)}
    {(found.dummies.length > 0 || found.fireballs.length > 0) && <ParticleObjects dummies={found.dummies} fireballs={found.fireballs} datablocks={datablocks}/>}
    {found.lava[0] && <LavaFilter textures={found.lava} blocks={found.lavaBlocks}/>}
  </>;
}
