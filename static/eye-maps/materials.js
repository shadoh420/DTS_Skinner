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
  // TextureScroll on $basetexturetransform: uv + time * rate * (cos, sin)(angle), wrapped to [0, 1)
  // (texturescrollmaterialproxy.cpp OnBind); rate and angle may name a material variable (CFloatInput).
  function pan(p={}) {
    const b=(p.proxies||{}).texturescroll;
    if(!b||String(b.texturescrollvar).toLowerCase()!=='$basetexturetransform')return [0,0];
    const value=(v,d)=>{if(typeof v==='string'&&v[0]==='$')v=p[v.toLowerCase()];const n=parseFloat(v);return Number.isFinite(n)?n:d;};
    const rate=value(b.texturescrollrate,1),angle=value(b.texturescrollangle,0)*Math.PI/180;
    return [rate*Math.cos(angle),rate*Math.sin(angle)];
  }
  const api = {arrays,pan};
  if (typeof module !== 'undefined') module.exports = api;
  if (!root.THREE) return;
  const extraTextures = new Map(),time=root.skinnerMapTime=root.skinnerMapTime||{value:0};
  // Fog: every material shares these two uniforms; update() and pass() repoint fogRange (start, end, max density) and refill fogColor.
  const fog={fogColor:{value:new THREE.Vector3()},fogRange:{value:null}},OFF=new THREE.Vector3(0,1,0),forward=new THREE.Vector3();
  const spec=f=>f&&{range:new THREE.Vector3(f.start,f.end,f.max??1),color:f.color,color2:f.color2||f.color,dir:new THREE.Vector3(...(f.dir||[0,0,1])),blend:!!f.blend};
  // Hypothesis: the shader API uploads the gamma fog colour as linear (g_LinearFogColor in common_ps_fxc.h FinalOutput, as water.cpp
  // sends $fogcolor with SetPixelShaderConstantGammaToLinear).
  const linear=(s,b,i)=>Math.pow(s.color[i]*b+s.color2[i]*(1-b),2.2);
  let fogs={main:null,sky:null,current:null,regions:new Map()},view=null,animated=[];
  function applyFog(s) {
    fog.fogRange.value=s?s.range:OFF;
    if(!s)return;
    // fogblend: primary colour toward fogdir, secondary opposite, mixed in gamma bytes (viewrender.cpp GetFogColor).
    const b=s.blend&&view?.5*view.getWorldDirection(forward).dot(s.dir)+.5:1;
    fog.fogColor.value.set(linear(s,b,0),linear(s,b,1),linear(s,b,2));
  }
  applyFog(null);
  root.skinnerMapViewerConfig = {
    label:'E.Y.E.',storageKey:'skinner.eyemaps',debugName:'skinnerEyeMaps',
    games:[['eye','C:\\Program Files (x86)\\Steam\\steamapps\\common\\EYE']],dataRoot:'/eye-map-data/',importRoute:'/import_eye_maps',
    configureTexture(texture) { texture.anisotropy=4; texture.magFilter=THREE.LinearFilter; texture.minFilter=THREE.LinearMipmapLinearFilter; },
    async load(map,loadTexture) {
      extraTextures.clear();animated=[];
      // The 3D sky pass uses the sky_camera's fog (none when it has fogenable 0); in water, the water material's fog.
      fogs={main:spec(map.fog),sky:spec(map.sky3d?.fog),regions:new Map((map.regions||[]).filter(Boolean).map(r=>[r,spec(r.fog)]))};
      fogs.current=fogs.main;applyFog(fogs.main);
      this.exposure=map.lighting.exposure;
      await root.quake2Maps.loadSky(map.skybox,loadTexture);
      const groups=[...map.groups,...Object.values(map.model_library).flatMap(m=>m.groups)];
      await Promise.all(groups.filter(g=>g.texture2).map(async g=>extraTextures.set(g.texture2,await loadTexture(g.texture2))));
    },
    update(seconds,camera,map,region) {
      view=camera;fogs.current=region&&fogs.regions.get(region)||fogs.main;applyFog(fogs.current);
      for(const a of animated){const t=a.frames[Math.floor(seconds*a.fps)%a.frames.length];if(t)a.uniform.value=t;}
    },
    pass(which) { applyFog(which==='sky'?fogs.sky:fogs.current); },
    material(group,texture,lightMap) {
      // The 2D sky is never fogged: sky_hdr_dx9.cpp sets MATERIAL_VAR_NOFOG (drawn by quake2Maps' sky material).
      if (group.kind==='sky') return root.quake2Maps.material(group,texture,lightMap);
      const s=group.settings||{},blend=s.blend||[],alpha=group.alpha??1,model=group.kind==='model',p=s.parameters||{};
      // Additive surfaces fog toward black, as Source's additive passes do (BaseVSShader.cpp FogToBlack); $nofog: none.
      // Modulating decals (DecalModulate) multiply what is behind by twice their colour, fogging toward grey.
      const modulate=blend[0]==='gl_dst_color';
      const fogTo=!['0','',undefined].includes(p['$nofog'])?-1:blend[1]==='gl_one'?0:modulate?.5:1;
      return new THREE.ShaderMaterial({
        transparent:!!blend.length||alpha<1,depthWrite:!blend.length&&alpha>=1,
        side:s.cull==='none'?THREE.DoubleSide:THREE.FrontSide,
        blending:modulate?THREE.CustomBlending:blend[1]==='gl_one'?THREE.AdditiveBlending:THREE.NormalBlending,
        blendSrc:THREE.DstColorFactor,blendDst:THREE.SrcColorFactor,
        uniforms:{surface:{value:texture},second:{value:extraTextures.get(group.texture2)||texture},lights:{value:lightMap},
          transition:{value:!!group.texture2},unlit:{value:!!s.unlit},selfillum:{value:!!s.selfillum},
          cutout:{value:!!s.alphaFunc},useAlpha:{value:!!blend.length||!!s.alphaFunc},alpha:{value:alpha},
          exposure:{value:this.exposure},tint:{value:new THREE.Vector3(...(group.tint||[1,1,1]))},
          fogColor:fog.fogColor,fogRange:fog.fogRange,fogTo:{value:fogTo},seconds:time,pan:{value:pan(p)}},
        vertexShader:`${model?'attribute vec3 bakedLight;':'attribute vec2 uv2; attribute vec4 color;'}
          uniform vec2 pan; uniform float seconds;
          varying vec2 texUV,lightUV; varying vec3 illumination; varying float terrain,fogDepth;
          void main(){texUV=uv+fract(pan*seconds); ${model?'lightUV=vec2(0.); illumination=bakedLight; terrain=0.;':'lightUV=uv2; illumination=vec3(1.); terrain=color.a;'}
            vec4 view=modelViewMatrix*vec4(position,1.); fogDepth=-view.z; gl_Position=projectionMatrix*view;}`,
        fragmentShader:`uniform sampler2D surface,second,lights; uniform bool transition,unlit,selfillum,cutout,useAlpha;
          uniform float alpha,exposure,fogTo; uniform vec3 tint,fogColor,fogRange;
          varying vec2 texUV,lightUV; varying vec3 illumination; varying float terrain,fogDepth;
          void main(){ vec4 base=texture2D(surface,texUV);
            if(transition)base=mix(base,texture2D(second,texUV),terrain);
            if(cutout && base.a<.5)discard;
            vec3 lighting=${model?'illumination*exposure':'pow(texture2D(lights,lightUV).rgb,vec3(2.2))'};
            if(unlit)lighting=vec3(1.); if(selfillum)lighting=mix(lighting,vec3(1.),base.a);
            vec3 rgb=clamp(pow(base.rgb,vec3(2.2))*lighting,0.,1.);
            // Range fog on view depth, capped at fogmaxdensity, squared, mixed in linear light (common_ps_fxc.h CalcRangeFog, BlendPixelFog).
            float f=min(fogRange.z,clamp((fogDepth-fogRange.x)/max(fogRange.y-fogRange.x,1e-4),0.,1.));
            if(fogTo==.5)rgb=mix(rgb,vec3(.2176),f*f); else if(fogTo>=0.)rgb=mix(rgb,fogColor*fogTo,f*f);  // .2176: grey .5 in linear
            rgb=pow(rgb,vec3(1./2.2));
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
        // AnimatedTexture frames (group.anim); clones below share the surface uniform, so one swap moves every placement.
        await Promise.all(m.groups.map(async(g,i)=>{if(g.anim?.frames?.length>1)animated.push({uniform:materials[i].uniforms.surface,fps:g.anim.fps,frames:await Promise.all(g.anim.frames.map(loadTexture))});}));
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
          for(const key of ['surface','fogColor','fogRange','seconds'])copy.uniforms[key]=material.uniforms[key];  // Shared, swapped per frame/pass.
          copy.uniforms.alpha.value=instance.alpha??1;copy.uniforms.tint.value.fromArray(instance.tint||[1,1,1]);
          if((instance.alpha??1)<1){copy.transparent=true;copy.depthWrite=false;}
          return copy;
        }));
        const materials=states.get(state);
        const mesh=new THREE.Mesh(g,materials);mesh.matrix.fromArray(instance.matrix);mesh.matrixAutoUpdate=false;
        mesh.userData={npc:instance.npc,entity:instance.entity,classname:instance.classname,sky3d:!!instance.sky3d};objects.push(mesh);
      }
      return objects;
    }
  };
})(typeof window==='undefined'?globalThis:window);
