// V12 fluid blocks (terrain/fluidSupport.cc in the public tribes2/engine source): a WaterBlock's fluid is split into
// blocks of 8 terrain squares (4 when it covers under a quarter of the terrain), and fluid::RebuildMasks keeps a
// block when any terrain point in it lies below the surface plus half the wave height. Only kept blocks are drawn
// and count as liquid (fluid::IsFluidAtXY): under the terrain, or where the ground rises above the surface, there
// is no fluid. The lava overlay asks fluidAt; the water surface discards fragments outside kept blocks.
// ponytail: removeWetEdges' flood fill is not applied; it only trims fluid along the surface's dry edges.
import {useEffect, useMemo} from 'react';
import {useFrame} from '@react-three/fiber';
import {DataTexture, NearestFilter, RedFormat, ShaderMaterial, Vector3} from 'three';
import {terrainHeightAt} from '../src/collision/terrainCollision';

type Grid = {x0: number; y0: number; per: number; nx: number; ny: number; level: number};

/** The block grid of a fluid at Torque (px, py) of size (sx, sy) whose kept blocks reach below level. */
export function fluidGrid(px: number, py: number, sx: number, sy: number, level: number): Grid {
  const x0 = Math.min(2040, Math.max(0, Math.floor((px + 1024) / 8 + 0.5)));
  const y0 = Math.min(2040, Math.max(0, Math.floor((py + 1024) / 8 + 0.5)));
  let nx = Math.floor(sx / 8 + 0.5), ny = Math.floor(sy / 8 + 0.5);
  const per = nx <= 128 && ny <= 128 ? 4 : 8;
  nx = Math.min(256, Math.max(per, (nx + per - 1) & ~(per - 1))) / per;
  ny = Math.min(256, Math.max(per, (ny + per - 1) & ~(per - 1))) / per;
  return {x0, y0, per, nx, ny, level};
}

/** Whether block (bx, by) is kept; true while no terrain is loaded, as upstream draws it. */
function kept(grid: Grid, bx: number, by: number): boolean {
  const {x0, y0, per, level} = grid;
  for (let j = 0; j <= per; j++) for (let i = 0; i <= per; i++) {
    const h = terrainHeightAt((x0 + bx * per + i) * 8 - 1024, (y0 + by * per + j) * 8 - 1024);
    if (h === null || h < level) return true;
  }
  return false;
}

/** Whether the fluid exists at Torque (x, y); the grid repeats every 2048 units, as the terrain does. */
export function fluidAt(grid: Grid, x: number, y: number): boolean {
  const bx = ((Math.floor(x + 1024) - grid.x0 * 8) & 2047) / (grid.per * 8) | 0;
  const by = ((Math.floor(y + 1024) - grid.y0 * 8) & 2047) / (grid.per * 8) | 0;
  return bx < grid.nx && by < grid.ny && kept(grid, bx, by);
}

type Scene = {transform: {position: {x: number; y: number; z: number}}; scale: {x: number; y: number; z: number}; waveMagnitude: number};

/** Discards an upstream water material's surface outside the block's kept fluid blocks. */
export function useFluidMask(material: ShaderMaterial, scene: Scene) {
  const {position: p} = scene.transform, s = scene.scale;
  const grid = useMemo(() => fluidGrid(p.x, p.y, s.x, s.y, p.z + s.z + scene.waveMagnitude / 2), [scene]);
  const mask = useMemo(() => {
    const texture = new DataTexture(new Uint8Array(grid.nx * grid.ny).fill(255), grid.nx, grid.ny, RedFormat);
    texture.magFilter = texture.minFilter = NearestFilter;
    texture.unpackAlignment = 1;  // Rows of one byte per block are not 4-byte aligned.
    texture.needsUpdate = true;
    return texture;
  }, [grid]);
  useEffect(() => () => mask.dispose(), [mask]);
  useMemo(() => {
    material.uniforms.uFluidMask = {value: mask};
    material.uniforms.uFluidGrid = {value: new Vector3(grid.x0 * 8, grid.y0 * 8, grid.per * 8)};
    material.onBeforeCompile = shader => {
      shader.fragmentShader = shader.fragmentShader.replace('void main() {', `uniform sampler2D uFluidMask; uniform vec3 uFluidGrid;
  void main() {
    // Three (x, z) is Torque (y, x).
    vec2 fluidBlock = floor(mod(floor(vWorldPosition.zx + 1024.0) - uFluidGrid.xy, 2048.0) / uFluidGrid.z);
    vec2 fluidBlocks = vec2(textureSize(uFluidMask, 0));
    if (any(greaterThanEqual(fluidBlock, fluidBlocks)) || texture2D(uFluidMask, (fluidBlock + 0.5) / fluidBlocks).r < 0.5) discard;`);
    };
    material.needsUpdate = true;
  }, [material, mask, grid]);
  // Terrain collision data arrives after the water mounts; the mask is filled once it is there.
  useFrame(() => {
    if (mask.userData.filled || terrainHeightAt(0, 0) === null) return;
    const data = mask.image.data as Uint8Array;
    for (let by = 0; by < grid.ny; by++) for (let bx = 0; bx < grid.nx; bx++) data[by * grid.nx + bx] = kept(grid, bx, by) ? 255 : 0;
    mask.needsUpdate = true;
    mask.userData.filled = true;
  });
}
