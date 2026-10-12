/* DXT1/3/5 only. DDS rows match our PNG upload with flipY=false; keep raw
   blocks and authored mips, with no color-space conversion or recompression. */
'use strict';
window.skinnerDDS = {
  parse(buffer) {
    const view = new DataView(buffer), u32 = at => view.getUint32(at, true);
    if (buffer.byteLength < 128 || u32(0) !== 0x20534444 || u32(4) !== 124 || u32(76) !== 32 || !(u32(80) & 4)) throw new Error('Invalid DDS header');
    const width = u32(16), height = u32(12), count = Math.max(1, u32(28));
    const formats = {0x31545844: [THREE.RGBA_S3TC_DXT1_Format, 8], 0x33545844: [THREE.RGBA_S3TC_DXT3_Format, 16], 0x35545844: [THREE.RGBA_S3TC_DXT5_Format, 16]};
    const format = formats[u32(84)];
    if (!format || !width || !height || width > 16384 || height > 16384 || count > Math.floor(Math.log2(Math.max(width,height)))+1 || u32(112)) throw new Error('Unsupported DDS layout');
    let offset = 128, w = width, h = height;
    const mipmaps = [];
    for (let i = 0; i < count; i++) {
      const size = Math.ceil(w/4)*Math.ceil(h/4)*format[1];
      if (offset+size > buffer.byteLength) throw new Error('Truncated DDS mip');
      mipmaps.push({data: new Uint8Array(buffer, offset, size), width: w, height: h});
      offset += size; w = Math.max(1, w >> 1); h = Math.max(1, h >> 1);
    }
    if (offset !== buffer.byteLength) throw new Error('Unexpected DDS bytes');
    const texture = new THREE.CompressedTexture(mipmaps, width, height, format[0]);
    texture.flipY = false; texture.generateMipmaps = false;
    texture.magFilter = THREE.LinearFilter;
    texture.minFilter = mipmaps.at(-1).width === 1 && mipmaps.at(-1).height === 1 ? THREE.LinearMipmapLinearFilter : THREE.LinearFilter;
    texture.userData.completeMipChain = texture.minFilter === THREE.LinearMipmapLinearFilter;
    texture.needsUpdate = true;
    return texture;
  },
  async load(url, signal) {
    const response = await fetch(url, {signal});
    if (!response.ok) throw new Error(`Missing texture: ${url}`);
    return this.parse(await response.arrayBuffer());
  }
};
