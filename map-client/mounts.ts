// Use the same authored DTS mount extraction as the pinned upstream manifest builder.
import {readFileSync, writeFileSync} from 'node:fs';
import path from 'node:path';
import {parseDTS} from '../src/dts/dts';
import {extractMountTransforms} from '../scripts/lib/mounts';

const root = process.argv[2];
if (!root) throw new Error('Pass the imported map pack directory');
const filename = path.join(root, 'manifest.json');
const manifest = JSON.parse(readFileSync(filename, 'utf8'));
const mounts: Record<string, unknown> = {};
for (const resource of Object.keys(manifest.resources).sort()) {
  if (!resource.endsWith('.dts')) continue;
  const data = readFileSync(path.join(root, 'base', resource));
  if (data.length === 0) {console.warn(`Skipping empty stock shape ${resource}`); continue;}
  const shape = parseDTS(data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength));
  const transforms = extractMountTransforms(shape);
  if (transforms) mounts[path.basename(resource, '.dts')] = transforms;
}
if (!(mounts.vehicle_pad as Record<string, unknown>)?.mount0) throw new Error('Missing vehicle pad mount0');
manifest.mounts = mounts;
writeFileSync(filename, JSON.stringify(manifest));
console.log(`Extracted authored mounts for ${Object.keys(mounts).length} shapes`);
