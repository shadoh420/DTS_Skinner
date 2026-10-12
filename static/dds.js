/* DXT1/3/5, Doom RXGB, RGB24/RGBA32. DDS rows match PNG with flipY=false; keep raw
   blocks and authored mips, with no color-space conversion or recompression. */
'use strict';
window.skinnerDDS = {
  parse(buffer) {
    const view = new DataView(buffer), u32 = at => view.getUint32(at, true);
    if (buffer.byteLength < 128 || u32(0) !== 0x20534444 || u32(4) !== 124 || u32(76) !== 32) throw new Error('Invalid DDS header');
    const width = u32(16), height = u32(12), count = Math.max(1, u32(28));
    const formats = {0x31545844: [THREE.RGBA_S3TC_DXT1_Format, 8], 0x33545844: [THREE.RGBA_S3TC_DXT3_Format, 16], 0x35545844: [THREE.RGBA_S3TC_DXT5_Format, 16]};
    const rxgb = u32(84) === 0x42475852;
    const format = u32(80) & 4 ? formats[rxgb ? 0x35545844 : u32(84)] : null;
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
    const texture = rgb ? new THREE.DataTexture(mipmaps[0].data, width, height, THREE.RGBAFormat) : new THREE.CompressedTexture(mipmaps, width, height, format[0]);
    if (rgb) texture.mipmaps = mipmaps;
    // RXGB stays compressed. A normal shader must sample X from alpha, Y from
    // green, Z from blue; it must not pass this texture to a regular normalMap.
    if (rxgb) texture.userData.normalEncoding = 'rxgb';
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
