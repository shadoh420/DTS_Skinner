/* Vengeance adapter: shared geometry, compact terrain, authored vertex lighting. */
(function(root) {
  'use strict';
  function arrays(description, buffer) {
    if (!/^[a-f0-9]{64}\.bin$/.test(description.file)) throw Error('Invalid model buffer name');
    const result={}; let end=0;
    for(const [name,item] of Object.entries(description.arrays)) {
      if(!['vertices','normals','uvs','indices'].includes(name)||!['<f4','<u4'].includes(item.dtype)||
          item.offset!==end||!Number.isSafeInteger(item.count)||item.count<0||end+item.count*4>buffer.byteLength) throw Error('Invalid model buffer range');
      result[name]=item.dtype==='<u4'?new Uint32Array(buffer,end,item.count):new Float32Array(buffer,end,item.count);
      end+=item.count*4;
    }
    if(end!==buffer.byteLength)throw Error('Trailing model buffer bytes');
    return result;
  }
  function terrainArrays(t,buffer) {
    const {width:w,height:h,offset}=t,n=w*h;
    if(!Number.isInteger(w)||!Number.isInteger(h)||w<2||h<2||w>8192||h>8192||!Number.isInteger(offset)||offset<0||offset%2||
        t.bytes!==n*2||offset+t.bytes+t.visibility_bytes+t.edge_bytes>buffer.byteLength)throw Error('Invalid terrain grid');
    const heights=new Uint16Array(buffer,offset,n),c=t.to_world,positions=new Float32Array(n*3),uv=new Float32Array(n*2);
    const visible=new Uint8Array(buffer,offset+t.bytes,t.visibility_bytes),edges=new Uint8Array(buffer,offset+t.bytes+t.visibility_bytes,t.edge_bytes);
    const bit=(a,i,otherwise)=>a.length?!!(a[i>>3]&(1<<(i&7))):otherwise;
    if((visible.length&&visible.length*8<n)||(edges.length&&edges.length*8<n)||c.length!==12||c.some(v=>!Number.isFinite(v)))throw Error('Invalid terrain metadata');
    for(let y=0;y<h;y++)for(let x=0;x<w;x++) {
      const i=y*w+x,a=x-c[0],b=y-c[1],d=heights[i]-c[2];
      const wx=a*c[3]+b*c[4]+d*c[5],wy=a*c[6]+b*c[7]+d*c[8],wz=a*c[9]+b*c[10]+d*c[11];
      positions.set([-wy/52.5,wz/52.5,wx/52.5],i*3);uv.set([(x+.5)/w,(y+.5)/h],i*2);
    }
    const indices=[];
    for(let y=0;y<h-1;y++)for(let x=0;x<w-1;x++) {
      const a=y*w+x,b=a+1,c=a+w,d=c+1;
      if(!bit(visible,a,true))continue;
      const face=bit(edges,a,false)?[a,d,b,a,c,d]:[a,c,b,b,c,d];
      // Viewer axes reflect native XY. These triangles face positive viewer Y.
      if(t.inverted)for(let i=0;i<6;i+=3)[face[i+1],face[i+2]]=[face[i+2],face[i+1]];
      indices.push(...face);
    }
    return {positions,uv,indices:new Uint32Array(indices)};
  }
  // One checkbox per terrain decoration mesh (grass, rocks, ...) plus All; hidden types are remembered across maps.
  function decorationToggles(batches,key='skinner.tvgmaps.hiddenDecorations') {
    const panel=root.document?.getElementById('decorPanel'),list=root.document?.getElementById('decorList');
    if(!panel||!batches.length)return;
    list.replaceChildren();let hidden=new Set();
    try{hidden=new Set(JSON.parse(localStorage.getItem(key)||'[]'));}catch(_){/* Defaults stay usable. */}
    const types=new Map();
    for(const mesh of batches)types.set(mesh.userData.decorationType,[...(types.get(mesh.userData.decorationType)||[]),mesh]);
    const save=()=>{try{localStorage.setItem(key,JSON.stringify([...hidden]));}catch(_){/* Keep the in-page choice. */}};
    const checkbox=(text,title,checked)=>{
      const input=Object.assign(root.document.createElement('input'),{type:'checkbox',checked}),label=root.document.createElement('label');
      label.title=title;label.append(input,' '+text);list.append(label);return input;
    };
    const all=checkbox('All','Show or hide every decoration type',true),boxes=[];
    const show=(name,on)=>{for(const mesh of types.get(name))mesh.visible=on;if(on)hidden.delete(name);else hidden.add(name);};
    const sync=()=>{all.checked=boxes.every(b=>b.checked);all.indeterminate=!all.checked&&boxes.some(b=>b.checked);};
    for(const [name,meshes] of [...types].sort(([a],[b])=>a.localeCompare(b))) {
      const short=name.split('.').pop().replace(/^decolayer/i,'')||name,count=meshes.reduce((n,m)=>n+m.userData.instances,0);
      const box=checkbox(`${short} (${count.toLocaleString()})`,name,!hidden.has(name));
      show(name,box.checked);boxes.push(box);
      box.addEventListener('change',()=>{show(name,box.checked);sync();save();});
    }
    all.addEventListener('change',()=>{for(const [i,name] of [...types.keys()].sort((a,b)=>a.localeCompare(b)).entries()){boxes[i].checked=all.checked;show(name,all.checked);}sync();save();});
    sync();panel.hidden=false;
  }
  // A clamped BSP surface (Vengeance's sky cube faces) can come out of the importer a whole number of tiles away, or
  // with v running -1..0: right under wrapping, smeared edge texels under clamping. Bring each clamped group's
  // coordinates back into 0..1 per axis, exactly when it spans one tile, as UT2004's renderer normalises a surface.
  function normalizeClampedUVs(uv,index,groups) {
    for(const g of groups) {
      if(!g.settings?.clamp)continue;
      const ids=[...new Set(index.subarray(g.start,g.start+g.count))];
      for(let axis=0;axis<2;axis++) {
        let low=Infinity,high=-Infinity;
        for(const i of ids){low=Math.min(low,uv[i*2+axis]);high=Math.max(high,uv[i*2+axis]);}
        const shift=Math.abs(high-low-1)<.01?low:Math.round(low);
        if(shift)for(const i of ids)uv[i*2+axis]-=shift;
      }
    }
    return uv;
  }
  const api={arrays,terrainArrays,decorationToggles,normalizeClampedUVs};
  if(typeof module!=='undefined')module.exports=api;
  if(!root.THREE)return;
  // One fog for every material and the terrain shader, switched by uniforms alone (no program rebuilds). Both modes give
  // inscattered colour in rgb and transmission in a. Mode 1, UE2 distance fog: D3D linear table fog on view depth, as the
  // game's log reports it ("D3D Driver: Using w- pixel fog", tv_cd_dvd.log): t = (end - depth) / (end - start).
  // Mode 2, UE3 exponential height fog: GetExponentialHeightFog of HeightFogCommon.usf (Engine/Shaders in Ascend's
  // install), radial distance; ueFogParams = density, height falloff, cos(terminator angle), start distance (page units).
  const fog=root.skinnerUnrealFog={ueFogMode:{value:0},ueFogColor:{value:new THREE.Color()},ueFogRange:{value:new THREE.Vector2(1e8,1e9)},
    ueFogParams:{value:new THREE.Vector4(0,1,0,0)},ueFogHeight:{value:0},ueFogOpposite:{value:new THREE.Color()},
    ueFogLight:{value:new THREE.Vector3(0,1,0)},ueFogMinOpacity:{value:0}};
  const FOG=`uniform int ueFogMode;uniform vec3 ueFogColor,ueFogOpposite,ueFogLight;uniform vec2 ueFogRange;uniform vec4 ueFogParams;
    uniform float ueFogHeight,ueFogMinOpacity;varying vec3 ueFogWorld;varying float ueFogDepth;
    vec4 ueFog(){
      if(ueFogMode==1){float t=clamp((ueFogRange.y-ueFogDepth)/(ueFogRange.y-ueFogRange.x),0.,1.);return vec4(ueFogColor*(1.-t),t);}
      if(ueFogMode!=2)return vec4(0.,0.,0.,1.);
      vec3 d=ueFogWorld-cameraPosition;
      float line=ueFogParams.x*exp2(-ueFogParams.y*(cameraPosition.y-ueFogHeight))*max(length(d)-ueFogParams.w,0.);
      float z=abs(d.y)>.00019?d.y:.00019;  // FLT_EPSILON2: 0.01 units
      line*=(1.-exp2(-ueFogParams.y*z))/(ueFogParams.y*z);
      float c=dot(ueFogLight,normalize(d));vec3 mid=(ueFogOpposite+ueFogColor)*.5;float a=clamp((c-ueFogParams.z)/(1.-ueFogParams.z),0.,1.);
      vec3 color=c<ueFogParams.z?mix(ueFogOpposite,mid,clamp((c+1.)/(ueFogParams.z+1.),0.,1.)):mix(mid,ueFogColor,a*a);
      float t=max(clamp(exp2(-line),0.,1.),ueFogMinOpacity);return vec4(color*(1.-t),t);}\n`;
  const FOG_VERTEX=`{vec4 ueW=vec4(transformed,1.);
#ifdef USE_INSTANCING
ueW=instanceMatrix*ueW;
#endif
ueW=modelMatrix*ueW;ueFogWorld=ueW.xyz;ueFogDepth=-(viewMatrix*ueW).z;}\n`;
  // Fog and emissive go into Three's own materials by replacing the fog chunks. Additive surfaces only lose light to
  // the fog and modulated ones are left alone (UE3 BasePassPixelShader.usf: Color * VertexFog.a for additive;
  // modulate is not in NEEDS_BASEPASS_FOGGING). Hypothesis: UE2's fixed-function passes do the same.
  // Emissive: added to the lit result, or with mode 'lerp' (a SelfIlluminationMask) replacing it by the glow's alpha.
  function shade(material,s,glow,glowMatrix) {
    const blend=s.blend||[],additive=blend[1]==='gl_one'||blend[1]==='gl_one_minus_src_color',modulate=blend[0]==='gl_dst_color';
    const uniforms=glow?{ueGlowMap:{value:glow},ueGlowColor:{value:new THREE.Color(...(s.emissive.color||[1,1,1]))},
      ueGlowMatrix:{value:glowMatrix},ueGlowMix:{value:s.emissive.mode==='lerp'?1:0}}:{};
    const prior=material.onBeforeCompile,key=material.customProgramCacheKey===THREE.Material.prototype.customProgramCacheKey?'':material.customProgramCacheKey();
    material.onBeforeCompile=function(shader,renderer){
      prior.call(this,shader,renderer);Object.assign(shader.uniforms,fog,uniforms);
      shader.vertexShader=shader.vertexShader.replace('#include <fog_pars_vertex>','varying vec3 ueFogWorld;varying float ueFogDepth;'+(glow?'varying vec2 ueUv;':''))
        .replace('#include <fog_vertex>',FOG_VERTEX+(glow?'ueUv=uv;':''));
      let f=shader.fragmentShader.replace('#include <fog_pars_fragment>',FOG+(glow?'uniform sampler2D ueGlowMap;uniform vec3 ueGlowColor;uniform mat3 ueGlowMatrix;uniform float ueGlowMix;varying vec2 ueUv;':''))
        .replace('#include <fog_fragment>',modulate?'':`{vec4 ueF=ueFog();gl_FragColor.rgb=gl_FragColor.rgb*ueF.a${additive?'':'+ueF.rgb'};}`);
      if(glow)f=f.replace('#include <color_fragment>','#include <color_fragment>\nvec4 ueE=texture2D(ueGlowMap,(ueGlowMatrix*vec3(ueUv,1.)).xy);'+
        'diffuseColor.rgb*=1.-ueE.a*ueGlowMix;vec3 ueGlow=ueE.rgb*ueGlowColor*mix(1.,ueE.a,ueGlowMix);')
        .replace('#include <output_fragment>','outgoingLight+=ueGlow;\n#include <output_fragment>');
      shader.fragmentShader=f;
    };
    material.customProgramCacheKey=()=>key+'unreal-fog-v1'+(glow?'e':'')+(additive?'a':modulate?'m':'');
    return material;
  }
  // UV modifiers belong to the material, so cloned sampler state shares the image. Moving ones are animated in update().
  const moving=[],glows=new Map(),white=new THREE.DataTexture(new Uint8Array([255,255,255,255]),1,1);white.needsUpdate=true;
  function uvTexture(texture,s,custom=false) {
    if(!texture||!(s.clamp||s.uvScale||s.uvOffset||s.uvRotation||s.uvCenter||s.pan||s.spin||s.osc))return texture;
    texture=texture.clone();texture.needsUpdate=true;
    if(s.clamp)texture.wrapS=texture.wrapT=THREE.ClampToEdgeWrapping;
    if(s.uvScale)texture.repeat.fromArray(s.uvScale);
    if(s.uvOffset)texture.offset.fromArray(s.uvOffset);
    if(s.uvCenter)texture.center.fromArray(s.uvCenter);
    texture.rotation=s.uvRotation||0;texture.updateMatrix();
    if(s.pan||s.spin||s.osc)moving.push({texture,s,custom,offset:texture.offset.toArray(),rotation:texture.rotation});
    return texture;
  }
  // Region fog (UE2 zones and fog volumes): the camera's region sets the linear fog, blended over the new region's
  // DistanceFogBlendTime. Hypothesis: the blend is linear in time (UE2's native PlayerController code is not on disk).
  const fade={region:undefined,from:null,to:null,start:0,time:0};
  function linearFog(f) {
    fog.ueFogMode.value=f?1:0;
    if(f){fog.ueFogColor.value.fromArray(f.color);fog.ueFogRange.value.set(f.start,f.end);}
  }
  function regionFog(seconds,region) {
    if(region&&region!==fade.region) {
      const was=fog.ueFogMode.value===1?{color:fog.ueFogColor.value.toArray(),start:fog.ueFogRange.value.x,end:fog.ueFogRange.value.y}:null;
      fade.region=region;fade.from=was;fade.to=region.fog||null;fade.start=seconds;fade.time=region.fog?.blend_time||0;
    }
    if(!fade.region)return;
    const k=fade.time>0?Math.min(1,(seconds-fade.start)/fade.time):1,{from,to}=fade;
    if(k>=1||!from&&!to)return linearFog(to);
    const a=from||{...to,start:1e8,end:1e9},b=to||{...from,start:1e8,end:1e9};
    fog.ueFogMode.value=1;fog.ueFogColor.value.setRGB(...a.color.map((v,i)=>v+(b.color[i]-v)*k));
    fog.ueFogRange.value.set(a.start+(b.start-a.start)*k,a.end+(b.end-a.end)*k);
  }
  const config=root.skinnerMapViewerConfig={
    label:'Tribes: Vengeance',storageKey:'skinner.tvgmaps',debugName:'skinnerTvgMaps',
    games:[['tvg','C:\\Program Files (x86)\\VUGames\\Tribes Vengeance']],
    dataRoot:'/tvg-map-data/',importRoute:'/import_tvg_maps',computeNormals:true,filterUnavailable:true,
    configureTexture(texture) { texture.anisotropy=4; },
    prepareGeometry(geometry,map) {  // Vengeance only: the other UE pages' adapters keep their own coordinates.
      if(map.game!=='tvg')return;
      normalizeClampedUVs(geometry.attributes.uv.array,geometry.index.array,map.groups);geometry.attributes.uv.needsUpdate=true;
    },
    // Shared by the three pages: emissive textures load before materials are made; prepare() is each game's fog.
    async load(map,loadTexture) {
      moving.length=0;glows.clear();fade.region=undefined;fog.ueFogMode.value=0;
      const files=new Set([...(map.groups||[]),...Object.values(map.model_library||{}).flatMap(m=>m.groups||[])].map(g=>g.settings?.emissive?.texture).filter(Boolean));
      if(loadTexture)await Promise.all([...files].map(async file=>glows.set(file,await loadTexture(file))));
      await config.prepare(map);
    },
    prepare(map) {
      const {scene,renderer}=root[config.debugName];
      scene.background=null;scene.fog=null;renderer.setClearColor(0x28313c);
      // Before the camera's region is known (and in packs without regions): the first fog zone.
      const zone=map.zones.find(z=>z.bDistanceFog&&z.DistanceFogEnd>z.DistanceFogStart);
      if(zone) {
        const c=zone.DistanceFogColor||[80,90,110,255];
        linearFog({color:[c[2]/255,c[1]/255,c[0]/255],start:zone.DistanceFogStart/52.5,end:zone.DistanceFogEnd/52.5});
        renderer.setClearColor(fog.ueFogColor.value);
      }
    },
    update(seconds,camera,map,region) {
      for(const m of moving) {
        const {texture,s}=m;
        if(s.pan)texture.offset.set(m.offset[0]+s.pan[0]*seconds%1,m.offset[1]+s.pan[1]*seconds%1);
        else texture.offset.fromArray(m.offset);
        if(s.osc)for(let i=0;i<2;i++)texture.offset.setComponent(i,texture.offset.getComponent(i)+s.osc.amplitude[i]*Math.sin(2*Math.PI*(s.osc.rate[i]*seconds+s.osc.phase[i])));
        if(s.spin)texture.rotation=m.rotation+s.spin*seconds%(2*Math.PI);
        if(m.custom)texture.updateMatrix();  // Three updates only its own maps' matrices.
      }
      if(map.regions)regionFog(seconds,region);
    },
    material(group,texture,baked=false) {
      const s=group.settings||{},blend=s.blend||[];
      let material=group.kind==='sky'?config.skyMaterial?.(group,texture)||new THREE.MeshBasicMaterial({colorWrite:false,side:THREE.DoubleSide}):config.groupMaterial?.(group,texture);
      let glow=null,glowMatrix=null;
      if(!material) {
        texture=uvTexture(texture,s);
        const Material=baked||s.unlit?THREE.MeshBasicMaterial:THREE.MeshLambertMaterial;
        const factors={gl_zero:THREE.ZeroFactor,gl_one:THREE.OneFactor,gl_src_alpha:THREE.SrcAlphaFactor,
          gl_one_minus_src_alpha:THREE.OneMinusSrcAlphaFactor,gl_src_color:THREE.SrcColorFactor,
          gl_one_minus_src_color:THREE.OneMinusSrcColorFactor,gl_dst_color:THREE.DstColorFactor};
        material=new Material({map:texture,color:texture?0xffffff:0x999999,vertexColors:baked&&!s.unlit,
          side:s.cull==='none'?THREE.DoubleSide:THREE.FrontSide,alphaTest:s.alphaTest||0,
          transparent:!!blend.length,depthWrite:s.depthWrite??!blend.length,opacity:s.opacity??1,
          ...(blend.length?{blending:THREE.CustomBlending,blendSrc:factors[blend[0]]??THREE.SrcAlphaFactor,
            blendDst:factors[blend[1]]??THREE.OneMinusSrcAlphaFactor}:{})});
        const e=s.emissive;
        glow=e&&(e.texture?glows.get(e.texture):white);
        if(glow) {
          const own=e.texture&&(e.uvScale||e.uvOffset||e.uvRotation||e.uvCenter||e.pan||e.spin||e.osc);
          if(own)glow=uvTexture(glow,e,true);
          // A self-illumination layer without its own modifier follows the diffuse coordinates (a modifier around a UE2
          // shader moves every layer); otherwise the mesh's own coordinates.
          glowMatrix=own?glow.matrix:e.shared_uv&&texture?texture.matrix:new THREE.Matrix3();
        }
      }
      return shade(material,s,glow,glowMatrix);
    },
    async terrainMaterial(t,loadTexture) {
      const uniforms={...fog},layers=t.layers.slice(0,5);
      const tex=async file=>file?loadTexture(file):null;
      const macro=await tex(t.textures.RenderMacroTexture||t.textures.FinalisedMacroTexture||t.textures.MacroTexture);
      const light=await tex(t.textures.LightmapTexture||t.vertex_light);
      uniforms.macro={value:macro};uniforms.hasMacro={value:!!macro};uniforms.light={value:light};uniforms.hasLight={value:!!light};
      let declarations='',blend='vec3 rgb=hasMacro?texture2D(macro,gridUV).rgb:vec3(.45);';
      for(let i=0;i<layers.length;i++) {
        const l=layers[i];uniforms['layer'+i]={value:await tex(l.texture)};uniforms['alpha'+i]={value:await tex(l.alpha)};
        let matrix=l.matrix;
        if(!matrix) { // Old/custom layers without a stored matrix use their authored controls.
          const angle=l.rotation*Math.PI/32768,cs=Math.cos(angle),sn=Math.sin(angle),u=1/(l.scale[0]||1),v=1/(l.scale[1]||1);
          matrix=[[cs*u,sn*v,0,0],[-sn*u,cs*v,0,0],[0,0,1,0],[l.pan[0],l.pan[1],0,1]];
        }
        uniforms['matrix'+i]={value:new THREE.Matrix4().fromArray(matrix.flat())};
        declarations+=`uniform sampler2D layer${i},alpha${i}; uniform mat4 matrix${i};`;
        blend+=`rgb=mix(rgb,texture2D(layer${i},(matrix${i}*vec4(nativePosition,1.)).xy).rgb,${l.alpha?`texture2D(alpha${i},gridUV).r`:'1.'});`;
      }
      return new THREE.ShaderMaterial({uniforms,side:t.inverted?THREE.DoubleSide:THREE.FrontSide,
        vertexShader:`varying vec2 gridUV; varying vec3 nativePosition; varying vec3 ueFogWorld; varying float ueFogDepth;
          void main(){gridUV=uv;nativePosition=vec3(position.z,-position.x,position.y)*52.5;
            vec4 w=modelMatrix*vec4(position,1.),p=viewMatrix*w;ueFogWorld=w.xyz;ueFogDepth=-p.z;gl_Position=projectionMatrix*p;}`,
        fragmentShader:`uniform sampler2D macro,light;uniform bool hasMacro,hasLight;
          varying vec2 gridUV; varying vec3 nativePosition;${FOG}${declarations}
          void main(){${blend} if(hasLight)rgb*=texture2D(light,gridUV).rgb*1.5;
            vec4 f=ueFog();gl_FragColor=vec4(rgb*f.a+f.rgb,1.);}`});
    },
    async buildInstances(map,data,get,loadTexture) {
      const buffers=new Map(),models=new Map(),objects=[];
      await Promise.all(Object.entries(map.model_library).map(async([look,m])=>{
        const file=m.geometry.file;
        if(!buffers.has(file))buffers.set(file,(async()=>{
          const store=m.store==='map-models'?'map-models':'models';
          const a=arrays(m.geometry,await(await get(data+store+'/'+file)).arrayBuffer());
          return {position:new THREE.BufferAttribute(a.vertices,3),normal:new THREE.BufferAttribute(a.normals,3),
            uv:new THREE.BufferAttribute(a.uvs,2),index:new THREE.BufferAttribute(a.indices,1)};
        })());
        const textures=await Promise.all(m.groups.map(g=>loadTexture(g.texture)));
        models.set(look,{attributes:await buffers.get(file),groups:m.groups,
          lit:m.groups.map((g,i)=>this.material(g,textures[i],false)),baked:m.groups.map((g,i)=>this.material(g,textures[i],true))});
      }));
      function geometry(m) {
        const g=new THREE.BufferGeometry();
        for(const [k,a] of Object.entries(m.attributes))if(k==='index')g.setIndex(a);else g.setAttribute(k,a);
        m.groups.forEach((group,i)=>g.addGroup(group.start,group.count,i));return g;
      }
      const lighting=await(await get(data+'maps/'+map.id+'/lighting.bin')).arrayBuffer();
      for(const i of map.instances) {
        const m=models.get(i.look),g=geometry(m);
        if(i.colors) {
          const {offset,count}=i.colors;
          if(count!==m.attributes.position.count||offset<0||offset+count*4>lighting.byteLength)throw Error('Invalid vertex lighting');
          const source=new Uint8Array(lighting,offset,count*4),rgb=new Uint8Array(count*3);
          for(let j=0;j<count;j++)rgb.set([source[j*4+2],source[j*4+1],source[j*4]],j*3);
          g.setAttribute('color',new THREE.BufferAttribute(rgb,3,true));
        }
        const mesh=new THREE.Mesh(g,i.colors?m.baked:m.lit);mesh.matrix.fromArray(i.matrix);mesh.matrixAutoUpdate=false;
        if(map.model_library[i.look].sky&&config.skyMaterial) {
          mesh.renderOrder=map.model_library[i.look].sky_order??-10001;mesh.frustumCulled=false;
        }
        mesh.userData={npc:i.npc===true,entity:i.entity,classname:i.classname};objects.push(mesh);
      }
      if(map.terrain.length) {
        const blob=await(await get(data+'maps/'+map.id+'/terrain.bin')).arrayBuffer();
        for(const t of map.terrain) {
          const a=terrainArrays(t,blob),g=new THREE.BufferGeometry();
          g.setAttribute('position',new THREE.BufferAttribute(a.positions,3));g.setAttribute('uv',new THREE.BufferAttribute(a.uv,2));
          g.setIndex(new THREE.BufferAttribute(a.indices,1));
          objects.push(new THREE.Mesh(g,await this.terrainMaterial(t,loadTexture)));
        }
      }
      if(map.decorations.length) {
        const response=await get(data+'maps/'+map.id+'/decorations.bin.gz');
        const blob=await new Response(response.body.pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
        const bounds=new Map(),matrix=new THREE.Matrix4(),sphere=new THREE.Sphere(),point=new THREE.Vector3();
        for(const d of map.decorations) {
          const m=models.get(d.look);
          if(!Number.isSafeInteger(d.count)||d.count<0||d.offset<0||d.offset%4||d.offset+d.count*64>blob.byteLength)throw Error('Invalid decoration instances');
          const g=geometry(m);
          if(!bounds.has(d.look)){g.computeBoundingSphere();bounds.set(d.look,g.boundingSphere.clone());}
          const mesh=new THREE.InstancedMesh(g,m.lit,d.count);
          mesh.instanceMatrix=new THREE.InstancedBufferAttribute(new Float32Array(blob,d.offset,d.count*16),16);
          // Vendored Three r149 culls against geometry bounds, not InstancedMesh bounds.
          // Each batch owns bounds/groups only; its actual vertex attributes stay shared.
          const box=new THREE.Box3();
          for(let j=0;j<d.count;j++) {
            matrix.fromArray(mesh.instanceMatrix.array,j*16);sphere.copy(bounds.get(d.look)).applyMatrix4(matrix);
            box.expandByPoint(point.copy(sphere.center).addScalar(sphere.radius));
            box.expandByPoint(point.copy(sphere.center).addScalar(-sphere.radius));
          }
          g.boundingSphere=box.getBoundingSphere(new THREE.Sphere());mesh.frustumCulled=true;
          if(d.fade?.[1]>0) {
            const far=d.fade[1]/52.5;
            mesh.onBeforeRender=function(renderer,scene,camera){
              // Keep spatial culling; authored maximum fade distance avoids drawing distant grass.
              this.count=g.boundingSphere.center.distanceTo(camera.position)>far+g.boundingSphere.radius?0:d.count;
            };
          }
          mesh.userData={npc:false,entity:d.entity,terrainDecoration:true,instances:d.count,
            decorationType:d.decorationType||map.model_library?.[d.look]?.source||d.look};objects.push(mesh);
        }
        decorationToggles(objects.filter(o=>o.userData.terrainDecoration),config.storageKey+'.hiddenDecorations');
      }
      const levels=config.lightLevels?config.lightLevels(map):{ambient:.65,sun:map.sun?.brightness??.65};
      const sun=new THREE.DirectionalLight(map.sun?new THREE.Color(...map.sun.color):0xffffff,levels.sun);
      sun.position.fromArray(map.sun?.direction||[-.4,1,.6]);
      objects.push(new THREE.AmbientLight(0xffffff,levels.ambient),sun);
      return objects;
    }
  };
  Object.assign(config,root.skinnerInstancedMapOptions||{});
})(typeof window==='undefined'?globalThis:window);
