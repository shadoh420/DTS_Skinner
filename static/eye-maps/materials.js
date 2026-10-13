/* Source adapter: original DXT textures, authored HDR light and shared model buffers. */
(function(root) {
  'use strict';
  function arrays(description, buffer) {
    if (!/^[a-f0-9]{64}\.bin$/.test(description.file)) throw Error('Invalid model buffer name');
    const result = {}; let end = 0;
    for (const [name, item] of Object.entries(description.arrays)) {
      if (!['vertices','normals','uvs','indices','colors'].includes(name) || !['<f4','<u4'].includes(item.dtype) ||
          item.offset !== end || !Number.isSafeInteger(item.count) || item.count < 0 || end+item.count*4 > buffer.byteLength) throw Error('Invalid model buffer range');
      result[name] = item.dtype === '<u4' ? new Uint32Array(buffer,end,item.count) : new Float32Array(buffer,end,item.count);
      end += item.count*4;
    }
    if (end !== buffer.byteLength) throw Error('Trailing model buffer bytes');
    return result;
  }
  const api = {arrays};
  if (typeof module !== 'undefined') module.exports = api;
  if (!root.THREE) return;
  const extraTextures = new Map();
  root.skinnerMapViewerConfig = {
    label:'E.Y.E.',storageKey:'skinner.eyemaps',debugName:'skinnerEyeMaps',
    games:[['eye','C:\\Program Files (x86)\\Steam\\steamapps\\common\\EYE']],dataRoot:'/eye-map-data/',importRoute:'/import_eye_maps',
    configureTexture(texture) { texture.anisotropy=4; texture.magFilter=THREE.LinearFilter; texture.minFilter=THREE.LinearMipmapLinearFilter; },
    async load(map,loadTexture) {
      extraTextures.clear();
      this.exposure=map.lighting.exposure;
      await root.quake2Maps.loadSky(map.skybox,loadTexture);
      const groups=[...map.groups,...Object.values(map.model_library).flatMap(m=>m.groups)];
      await Promise.all(groups.filter(g=>g.texture2).map(async g=>extraTextures.set(g.texture2,await loadTexture(g.texture2))));
    },
    material(group,texture,lightMap) {
      if (group.kind==='sky') return root.quake2Maps.material(group,texture,lightMap);
      const s=group.settings||{},blend=s.blend||[],alpha=group.alpha??1,model=group.kind==='model';
      return new THREE.ShaderMaterial({
        transparent:!!blend.length||alpha<1,depthWrite:!blend.length&&alpha>=1,
        side:s.cull==='none'?THREE.DoubleSide:THREE.FrontSide,
        blending:blend[1]==='gl_one'?THREE.AdditiveBlending:THREE.NormalBlending,
        uniforms:{surface:{value:texture},second:{value:extraTextures.get(group.texture2)||texture},lights:{value:lightMap},
          transition:{value:!!group.texture2},unlit:{value:!!s.unlit},selfillum:{value:!!s.selfillum},
          cutout:{value:!!s.alphaFunc},useAlpha:{value:!!blend.length||!!s.alphaFunc},alpha:{value:alpha},
          exposure:{value:this.exposure},tint:{value:new THREE.Vector3(...(group.tint||[1,1,1]))}},
        vertexShader:`${model?'attribute vec3 bakedLight;':'attribute vec2 uv2; attribute vec4 color;'}
          varying vec2 texUV,lightUV; varying vec3 illumination; varying float terrain;
          void main(){texUV=uv; ${model?'lightUV=vec2(0.); illumination=bakedLight; terrain=0.;':'lightUV=uv2; illumination=vec3(1.); terrain=color.a;'}
            gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}`,
        fragmentShader:`uniform sampler2D surface,second,lights; uniform bool transition,unlit,selfillum,cutout,useAlpha;
          uniform float alpha,exposure; uniform vec3 tint;
          varying vec2 texUV,lightUV; varying vec3 illumination; varying float terrain;
          void main(){ vec4 base=texture2D(surface,texUV);
            if(transition)base=mix(base,texture2D(second,texUV),terrain);
            if(cutout && base.a<.5)discard;
            vec3 lighting=${model?'illumination*exposure':'pow(texture2D(lights,lightUV).rgb,vec3(2.2))'};
            if(unlit)lighting=vec3(1.); if(selfillum)lighting=mix(lighting,vec3(1.),base.a);
            vec3 rgb=pow(clamp(pow(base.rgb,vec3(2.2))*lighting,0.,1.),vec3(1./2.2));
            gl_FragColor=vec4(rgb*tint,(useAlpha?base.a:1.)*alpha);}`
      });
    },
    async buildInstances(map,data,get,loadTexture) {
      // Attribute/index objects are shared by every placement/skin of a model.
      // Only baked lighting and transforms differ per instance; no flattened positions.
      const geometries=new Map(),models=new Map();
      await Promise.all(Object.entries(map.model_library).map(async([look,m])=>{
        const file=m.geometry.file;
        if(!geometries.has(file))geometries.set(file,(async()=>{
          const buffer=await (await get(data+'models/'+file)).arrayBuffer(),a=arrays(m.geometry,buffer);
          return {position:new THREE.BufferAttribute(a.vertices,3),normal:new THREE.BufferAttribute(a.normals,3),
            uv:new THREE.BufferAttribute(a.uvs,2),index:new THREE.BufferAttribute(a.indices,1)};
        })());
        const materials=await Promise.all(m.groups.map(async g=>this.material(g,await loadTexture(g.texture),null)));
        models.set(look,{attributes:await geometries.get(file),materials,groups:m.groups});
      }));
      const light=await (await get(data+'maps/'+map.id+'/lighting.bin')).arrayBuffer(),objects=[],states=new Map();
      for(const instance of map.instances){
        const m=models.get(instance.look),g=new THREE.BufferGeometry(),attrs=m.attributes;
        g.setAttribute('position',attrs.position);g.setAttribute('normal',attrs.normal);g.setAttribute('uv',attrs.uv);g.setIndex(attrs.index);
        const l=instance.lighting;
        if(l.count!==attrs.position.count||l.offset%4||l.offset<0||l.offset+l.count*12>light.byteLength)throw Error('Invalid instance lighting');
        g.setAttribute('bakedLight',new THREE.BufferAttribute(new Float32Array(light,l.offset,l.count*3),3));
        m.groups.forEach((group,index)=>g.addGroup(group.start,group.count,index));
        // Per-instance tint/alpha are small uniforms; textures and geometry stay shared.
        const state=JSON.stringify([instance.look,instance.alpha??1,instance.tint||[1,1,1]]);
        if(!states.has(state))states.set(state,m.materials.map(material=>{
          const copy=material.clone();
          // UniformsUtils.clone also clones textures. Keep those GPU resources shared.
          for(const [key,uniform] of Object.entries(material.uniforms))if(uniform.value?.isTexture)copy.uniforms[key].value=uniform.value;
          copy.uniforms.alpha.value=instance.alpha??1;copy.uniforms.tint.value.fromArray(instance.tint||[1,1,1]);
          if((instance.alpha??1)<1){copy.transparent=true;copy.depthWrite=false;}
          return copy;
        }));
        const materials=states.get(state);
        const mesh=new THREE.Mesh(g,materials);mesh.matrix.fromArray(instance.matrix);mesh.matrixAutoUpdate=false;
        mesh.userData={npc:instance.npc,entity:instance.entity,classname:instance.classname};objects.push(mesh);
      }
      return objects;
    }
  };
})(typeof window==='undefined'?globalThis:window);
