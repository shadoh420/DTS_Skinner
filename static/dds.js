/* DXT1/3/5, Doom RXGB, RGB24/RGBA32. DDS rows match PNG with flipY=false; keep raw
   blocks and authored mips, with no color-space conversion or recompression. DXT levels
   WebGL refuses (WEBGL_compressed_texture_s3tc: level 0 a multiple of 4, later levels
   1, 2 or a multiple of 4; Doom 3's 2x64 ppipe_s) are decoded to RGBA instead. */
'use strict';
window.skinnerDDS = {
  parse(buffer) {
    const view = new DataView(buffer), u32 = at => view.getUint32(at, true);
    if (buffer.byteLength < 128 || u32(0) !== 0x20534444 || u32(4) !== 124 || u32(76) !== 32) throw new Error('Invalid DDS header');
    const width = u32(16), height = u32(12), count = Math.max(1, u32(28));
    const formats = {0x31545844: [THREE.RGBA_S3TC_DXT1_Format, 8], 0x33545844: [THREE.RGBA_S3TC_DXT3_Format, 16], 0x35545844: [THREE.RGBA_S3TC_DXT5_Format, 16]};
    const rxgb = u32(84) === 0x42475852;
    const fourcc = rxgb ? 0x35545844 : u32(84), format = u32(80) & 4 ? formats[fourcc] : null;
    const rgb = !(u32(80) & 4) && (u32(80) & 0x40) && [24,32].includes(u32(88));
    const masks = [u32(92),u32(96),u32(100),u32(104)];
    const channels = masks.map(mask => [0xff,0xff00,0xff0000,0xff000000].indexOf(mask));
    if ((!format && !rgb) || (rgb && (channels.slice(0,3).some(c=>c<0 || c>=u32(88)/8) || new Set(channels.slice(0,3)).size!==3 || (masks[3] && (u32(88)!==32 || channels[3]<0 || channels.slice(0,3).includes(channels[3]))))) || !width || !height || width > 16384 || height > 16384 || count > Math.floor(Math.log2(Math.max(width,height)))+1 || u32(112)) throw new Error('Unsupported DDS layout');
    let offset = 128, w = width, h = height;
    const mipmaps = [];
    for (let i = 0; i < count; i++) {
      const bytes = rgb ? u32(88)/8 : 0;
      const pitch = rgb ? (i===0 && (u32(8)&8) ? u32(20) : w*bytes) : 0;
      if (rgb && pitch < w*bytes) throw new Error('Unsupported DDS pitch');
      const size = rgb ? pitch*h : Math.ceil(w/4)*Math.ceil(h/4)*format[1];
      if (offset+size > buffer.byteLength) throw new Error('Truncated DDS mip');
      let data = new Uint8Array(buffer, offset, size);
      if (rgb) {
        const rgba = new Uint8Array(w*h*4);
        for (let y=0; y<h; y++) for (let x=0; x<w; x++) {
          const src=y*pitch+x*bytes, dst=(y*w+x)*4;
          for (let c=0; c<3; c++) rgba[dst+c]=data[src+channels[c]];
          rgba[dst+3]=masks[3] ? data[src+channels[3]] : 255;
        }
        data=rgba;
      }
      mipmaps.push({data, width: w, height: h});
      offset += size; w = Math.max(1, w >> 1); h = Math.max(1, h >> 1);
    }
    if (offset !== buffer.byteLength) throw new Error('Unexpected DDS bytes');
    const unaligned = format && mipmaps.some((m, i) => [m.width, m.height].some(d => d % 4 && (!i || d > 2)));
    if (unaligned) for (const m of mipmaps) m.data = this.decode(m.data, m.width, m.height, (fourcc >>> 24) - 48);
    const raw = rgb || unaligned;
    const texture = raw ? new THREE.DataTexture(mipmaps[0].data, width, height, THREE.RGBAFormat) : new THREE.CompressedTexture(mipmaps, width, height, format[0]);
    if (raw) texture.mipmaps = mipmaps;
    // RXGB stays compressed (or decoded with the same channels). A normal shader must sample
    // X from alpha, Y from green, Z from blue; it must not pass this texture to a regular normalMap.
    if (rxgb) texture.userData.normalEncoding = 'rxgb';
    texture.flipY = false; texture.generateMipmaps = false;
    texture.magFilter = THREE.LinearFilter;
    texture.minFilter = mipmaps.at(-1).width === 1 && mipmaps.at(-1).height === 1 ? THREE.LinearMipmapLinearFilter : THREE.LinearFilter;
    texture.userData.completeMipChain = texture.minFilter === THREE.LinearMipmapLinearFilter;
    texture.needsUpdate = true;
    return texture;
  },
  // One DXT1/3/5 level (kind 1, 3 or 5) to RGBA, as the GPU decodes it; texels past w x h are dropped.
  decode(data, w, h, kind) {
    const rgba = new Uint8Array(w*h*4), size = kind === 1 ? 8 : 16, wide = Math.ceil(w/4);
    const rgb = v => [(v>>11)*255/31, (v>>5&63)*255/63, (v&31)*255/31];
    for (let at = 0; at < data.length; at += size) {
      const bx = at/size%wide*4, by = Math.floor(at/size/wide)*4, c = at+size-8;
      const c0 = data[c]|data[c+1]<<8, c1 = data[c+2]|data[c+3]<<8, p0 = rgb(c0), p1 = rgb(c1);
      const four = kind !== 1 || c0 > c1, mix = (a, b) => p0.map((v, k) => (a*v+b*p1[k])/(a+b));
      const colors = [p0, p1, four ? mix(2,1) : mix(1,1), four ? mix(1,2) : [0,0,0]];
      const a0 = data[at], a1 = data[at+1], ramp = a0 > a1 ? 7 : 5;
      const alphas = [a0, a1, ...Array.from({length: ramp-1}, (_, k) => ((ramp-1-k)*a0+(k+1)*a1)/ramp), ...(ramp === 5 ? [0,255] : [])];
      const bits = [data[at+2]|data[at+3]<<8|data[at+4]<<16, data[at+5]|data[at+6]<<8|data[at+7]<<16];
      for (let i = 0; i < 16; i++) {
        const x = bx+(i&3), y = by+(i>>2), out = (y*w+x)*4;
        if (x >= w || y >= h) continue;
        const index = data[c+4+(i>>2)] >> 2*(i&3) & 3;
        for (let k = 0; k < 3; k++) rgba[out+k] = Math.round(colors[index][k]);
        rgba[out+3] = kind === 1 ? (four || index < 3 ? 255 : 0)
          : kind === 3 ? (data[at+(i>>1)] >> 4*(i&1) & 15)*17
          : Math.round(alphas[bits[i>>3] >> 3*(i&7) & 7]);
      }
    }
    return rgba;
  },
  async load(url, signal) {
    const response = await fetch(url, {signal});
    if (!response.ok) throw new Error(`Missing texture: ${url}`);
    return this.parse(await response.arrayBuffer());
  }
};
