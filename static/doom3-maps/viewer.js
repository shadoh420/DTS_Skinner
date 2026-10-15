/* Doom 3 ARB2 interactions, without stencil shadows. No Three lighting/color transforms. */
(async function() {
'use strict';
const $=id=>document.getElementById(id), M=window.doom3Materials, P=window.doom3Particles, config=window.idTech4MapConfig, base=config.data;
const npcs=window.skinnerNPCToggle;
document.title='DTS Skinner - '+config.label+' Maps';$('gameTitle').textContent=config.label+' Maps';
$('gamePath').placeholder=config.install;$('importStatus').textContent=config.description;
const renderer=new THREE.WebGLRenderer({canvas:$('c'),antialias:false,alpha:false});
renderer.setPixelRatio(1);renderer.outputEncoding=THREE.LinearEncoding;renderer.toneMapping=THREE.NoToneMapping;
renderer.setClearColor(0);renderer.sortObjects=true;
const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(75,1,2,100000);camera.up.set(0,0,1);
const skyCamera=camera.clone(),mirrorCamera=camera.clone();
// Doom uses destination alpha as scratch storage (makealpha + maskColor).
// Keep RGBA offscreen, then copy opaque RGB so browser compositing cannot alter it.
const colorTarget=new THREE.WebGLRenderTarget(1,1,{format:THREE.RGBAFormat,depthBuffer:true});
const copyScene=new THREE.Scene(),copyCamera=new THREE.Camera();
copyScene.add(new THREE.Mesh(new THREE.PlaneGeometry(2,2),new THREE.ShaderMaterial({
 uniforms:{source:{value:colorTarget.texture}},depthTest:false,depthWrite:false,blending:THREE.NoBlending,
 vertexShader:'varying vec2 st;void main(){st=uv;gl_Position=vec4(position.xy,0,1);}',
 fragmentShader:'uniform sampler2D source;varying vec2 st;void main(){gl_FragColor=vec4(texture2D(source,st).rgb,1);}'
})));
let data=null, tables={}, batches=[], textures=new Map(), geometry=null, yaw=0,pitch=0,speed=250,loading=false,paused=false,mapStart=performance.now(),soundAmplitudeOverride=null;
const keys=new Set(), matrix=new THREE.Matrix4(),frustum=new THREE.Frustum(),look=new THREE.Vector3();
const white=new THREE.DataTexture(new Uint8Array([255,255,255,255]),1,1);white.needsUpdate=true;
const black=new THREE.DataTexture(new Uint8Array([0,0,0,255]),1,1);black.needsUpdate=true;
const flat=new THREE.DataTexture(new Uint8Array([128,128,255,255]),1,1);flat.needsUpdate=true;
const vertex=`attribute vec3 doomTangent;attribute vec3 doomBitangent;attribute float doomAlpha;varying float vertexAlpha;
varying vec2 st;varying vec3 positionWorld, tangentX, tangentY, tangentZ, vertexRGB;
varying vec3 lightVector, halfVector;uniform vec3 lightOrigin;
void main(){st=uv;positionWorld=position;vertexRGB=color;vertexAlpha=doomAlpha;
tangentZ=normal;tangentX=doomTangent;tangentY=doomBitangent;
vec3 L=lightOrigin-position,V=cameraPosition-position;
lightVector=vec3(dot(L,tangentX),dot(L,tangentY),dot(L,tangentZ));
vec3 H=normalize(L)+normalize(V);halfVector=vec3(dot(H,tangentX),dot(H,tangentY),dot(H,tangentZ));
gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}`;
const fragment=`precision highp float;varying float vertexAlpha;
varying vec2 st;varying vec3 positionWorld,tangentX,tangentY,tangentZ,vertexRGB,lightVector,halfVector;
uniform sampler2D diffuseMap,bumpMap,specularMap,projectionMap,falloffMap;
uniform mat3 diffuseUV,bumpUV,specularUV,projectionUV;
uniform vec4 diffuseColor,specularColor,lightColor;uniform vec4 projectS,projectT,projectQ,projectF;
uniform float alphaCut,normalRXGB,vertexMode,specularVertexMode,lightScale,ambient,diffuseEnabled,vertexAlphaEnabled;
uniform int passKind;
vec3 vertexColor(float mode){return mode==0.0?vec3(1.0):mode>0.0?vertexRGB:vec3(1.0)-vertexRGB;}
void main(){
vec4 d=texture2D(diffuseMap,(diffuseUV*vec3(st,1.0)).xy)*diffuseColor;
d.a*=vertexAlphaEnabled==0.0||vertexMode==0.0?1.0:vertexMode>0.0?vertexAlpha:1.0-vertexAlpha;
if(d.a<alphaCut)discard;
if(passKind==0){gl_FragColor=vec4(0,0,0,1);return;}
if(passKind==2){gl_FragColor=vec4(d.rgb*vertexColor(vertexMode),d.a);return;}
vec4 p=vec4(positionWorld,1.0);float q=dot(projectQ,p),f=dot(projectF,p);
if(q<=0.0||f<0.0||f>1.0)discard;
vec2 uv=vec2(dot(projectS,p),dot(projectT,p))/q;
if(any(lessThan(uv,vec2(0)))||any(greaterThan(uv,vec2(1))))discard;
uv=(projectionUV*vec3(uv,1.0)).xy;
vec3 light=texture2D(projectionMap,uv).rgb*texture2D(falloffMap,vec2(f,.5)).rgb*lightColor.rgb*lightScale;
vec4 bn=texture2D(bumpMap,(bumpUV*vec3(st,1)).xy);
vec3 n=vec3(normalRXGB>0.5?bn.a:bn.r,bn.g,bn.b)*2.0-1.0;
vec3 face=vec3(1.0,1.0,gl_FrontFacing?1.0:-1.0);
float ndl=max(dot(n,normalize(lightVector*face)),0.0);
float h=max((dot(n,normalize(halfVector*face))-.75)*4.0,0.0);
// Image_init.cpp R_SpecularTableImage: quadratic quarter-lobe, NOT Phong pow().
float spec=min(h*h,1.0);
vec3 s=texture2D(specularMap,(specularUV*vec3(st,1)).xy).rgb*specularColor.rgb*2.0*spec;
vec3 diffuse=d.rgb*vertexColor(vertexMode)*diffuseEnabled;
// ARB2 ambientNormalMap, image_useNormalCompression=2: constant cube RGB.
if(ambient>0.5){float a=max(dot(n,vec3(1.0,29.0/255.0*2.0-1.0,227.0/255.0*2.0-1.0)),0.0);gl_FragColor=vec4(diffuse*light*a,1);return;}
gl_FragColor=vec4((diffuse+s*vertexColor(specularVertexMode))*light*ndl,1);
}`;
// R_LoadCubeImages camera-face transpose/flips, performed on coordinates so
// source DDS bytes and their mip chains stay intact. No lighting/overbright.
const cubeFragment=`precision highp float;
varying vec3 positionWorld,vertexRGB,tangentX,tangentY,tangentZ;varying float vertexAlpha;varying vec2 st;
uniform sampler2D cube0,cube1,cube2,cube3,cube4,cube5,bumpMap;
uniform mat3 skyMatrix,bumpUV;uniform float cameraCube,vertexMode,vertexAlphaEnabled,alphaCut,cubeMode,normalRXGB;
uniform vec4 diffuseColor;
vec3 bumpNormal(){vec4 b=texture2D(bumpMap,(bumpUV*vec3(st,1)).xy);vec3 l=vec3(normalRXGB>0.5?b.a:b.r,b.g,b.b)*2.0-1.0;
 return normalize(l.x*tangentX+l.y*tangentY+l.z*tangentZ);}
void main(){
 // cubeMode 0: view ray (skybox). 1: environment.vfp reflection off the vertex normal.
 // 2: bumpyEnvironment.vfp, normal map in the tangent frame (the program ignores stage colour).
 // 3: TG_DIFFUSE_CUBE, the normal itself.
 vec3 d=positionWorld-cameraPosition;
 if(cubeMode>0.5){
  vec3 n=cubeMode>1.5&&cubeMode<2.5?bumpNormal():normalize(tangentZ);
  vec3 e=normalize(-d);d=cubeMode>2.5?n:2.0*dot(e,n)*n-e;
 }
 d=skyMatrix*d;vec3 a=abs(d);vec2 st;vec4 pixel;
 if(a.x>=a.y&&a.x>=a.z){
  if(d.x>0.0){st=vec2(-d.z,-d.y)/a.x*.5+.5;if(cameraCube>0.5)st=st.yx;pixel=texture2D(cube0,st);}
  else{st=vec2(d.z,-d.y)/a.x*.5+.5;if(cameraCube>0.5)st=1.0-st.yx;pixel=texture2D(cube1,st);}
 }else if(a.y>=a.z){
  if(d.y>0.0){st=vec2(d.x,d.z)/a.y*.5+.5;if(cameraCube>0.5)st.y=1.0-st.y;pixel=texture2D(cube2,st);}
  else{st=vec2(d.x,-d.z)/a.y*.5+.5;if(cameraCube>0.5)st.x=1.0-st.x;pixel=texture2D(cube3,st);}
 }else{
  if(d.z>0.0){st=vec2(d.x,-d.y)/a.z*.5+.5;if(cameraCube>0.5)st=st.yx;pixel=texture2D(cube4,st);}
  else{st=vec2(-d.x,-d.y)/a.z*.5+.5;if(cameraCube>0.5)st=st.yx;pixel=texture2D(cube5,st);}
 }
 if(cubeMode>1.5&&cubeMode<2.5){if(pixel.a<alphaCut)discard;gl_FragColor=pixel;return;}
 pixel*=diffuseColor;
 if(vertexMode!=0.0){pixel.rgb*=vertexMode>0.0?vertexRGB:1.0-vertexRGB;if(vertexAlphaEnabled>0.5)pixel.a*=vertexMode>0.0?vertexAlpha:1.0-vertexAlpha;}
 if(pixel.a<alphaCut)discard;gl_FragColor=pixel;
}`;
// R_MirrorRender: the reflected view, projected onto the mirror; its own stages blend over it.
const mirrorFragment=`precision highp float;varying vec3 positionWorld;uniform sampler2D mirrorMap;uniform mat4 textureMatrix;
void main(){gl_FragColor=vec4(texture2DProj(mirrorMap,textureMatrix*vec4(positionWorld,1.0)).rgb,1.0);}`;
// Fog and blend lights draw only inside the light volume (a small margin keeps the fog volume's own faces).
const lightVolume=`uniform vec4 projectS,projectT,projectQ,projectF;
bool outside(vec4 p,out vec2 uv,out float f){float q=dot(projectQ,p);f=dot(projectF,p);uv=vec2(dot(projectS,p),dot(projectT,p))/max(q,1e-6);
 return q<=0.0||f<-.01||f>1.01||any(lessThan(uv,vec2(-.01)))||any(greaterThan(uv,vec2(1.01)));}`;
// RB_FogPass: R_FogImage alpha 1-0.982^(255*min(depth/distance,1)) by view depth, times R_FogEnterImage's
// FogFraction of the view and surface heights against the light's top plane (0.064 units per world unit).
const fogFragment=`precision highp float;varying vec3 positionWorld;uniform vec4 lightColor,fogPlane;${lightVolume}
float fogFraction(float view,float target){
 float total=abs(target-view);if(target>0.0&&view>0.0)return 0.0;if(target<-8.0&&view<-8.0)return 1.0;
 float above=target>0.0?target:view>0.0?view:0.0,top=min(max(view,target),0.0),bottom=max(min(view,target),-8.0);
 if(total==0.0)return -view/8.0;
 float ramp=(1.0-(top/8.0+bottom/8.0)*-0.5)*(top-bottom),frac=(total-above-ramp)/total,deep=min(view,target)/-30.0;
 return deep>=1.0?1.0:frac*(1.0-deep)+deep;}
void main(){vec4 p=vec4(positionWorld,1.0);vec2 uv;float f;if(outside(p,uv,f))discard;
 float distance=lightColor.a<=1.0?500.0:lightColor.a,depth=-(viewMatrix*p).z;
 float a=1.0-pow(.982,floor(clamp(depth/distance,0.0,1.0)*255.0));
 a*=clamp(fogFraction(.064*(dot(fogPlane.xyz,cameraPosition)+fogPlane.w),.064*(dot(fogPlane.xyz,positionWorld)+fogPlane.w)),0.0,1.0);
 gl_FragColor=vec4(lightColor.rgb,a);}`;
// RB_BlendLight: projected image x falloff x stage colour (alpha included), with the stage's own blend.
const blendLightFragment=`precision highp float;varying vec3 positionWorld;uniform sampler2D projectionMap,falloffMap;uniform mat3 projectionUV;uniform vec4 lightColor;${lightVolume}
void main(){vec4 p=vec4(positionWorld,1.0);vec2 uv;float f;if(outside(p,uv,f))discard;
 gl_FragColor=texture2D(projectionMap,(projectionUV*vec3(clamp(uv,0.0,1.0),1.0)).xy)*texture2D(falloffMap,vec2(clamp(f,0.0,1.0),.5))*lightColor;}`;
const FOG_ORDER=5e8;
function mirrorPlane(surface) {
 const i=geometry.index.array[surface.start],p=geometry.attributes.position.array,n=geometry.attributes.normal.array;
 const normal=new THREE.Vector3(n[i*3],n[i*3+1],n[i*3+2]).normalize(),point=new THREE.Vector3(p[i*3],p[i*3+1],p[i*3+2]);
 return {plane:new THREE.Plane().setFromNormalAndCoplanarPoint(normal,point),target:null,mesh:null};
}
function renderMirror(batch,select,width,height) {
 // three.js Reflector: mirrored eye/forward/up, projective texture matrix, then an oblique near plane on the mirror.
 const {plane}=batch.mirror;if(plane.distanceToPoint(camera.position)<=0)return false;
 const reflect=v=>v.sub(plane.normal.clone().multiplyScalar(2*v.dot(plane.normal)));
 const forward=reflect(camera.getWorldDirection(new THREE.Vector3())),up=reflect(new THREE.Vector3(0,1,0).applyQuaternion(camera.quaternion));
 mirrorCamera.copy(camera);mirrorCamera.position.copy(camera.position).sub(plane.normal.clone().multiplyScalar(2*plane.distanceToPoint(camera.position)));
 mirrorCamera.up.copy(up);mirrorCamera.lookAt(mirrorCamera.position.clone().add(forward));mirrorCamera.updateMatrixWorld();mirrorCamera.updateProjectionMatrix();
 batch.mirror.mesh.material.uniforms.textureMatrix.value.set(.5,0,0,.5,0,.5,0,.5,0,0,.5,.5,0,0,0,1).multiply(mirrorCamera.projectionMatrix).multiply(mirrorCamera.matrixWorldInverse);
 const clip=plane.clone().applyMatrix4(mirrorCamera.matrixWorldInverse),c=new THREE.Vector4(clip.normal.x,clip.normal.y,clip.normal.z,clip.constant),m=mirrorCamera.projectionMatrix.elements;
 const q=new THREE.Vector4((Math.sign(c.x)+m[8])/m[0],(Math.sign(c.y)+m[9])/m[5],-1,(1+m[10])/m[14]);c.multiplyScalar(2/c.dot(q));
 m[2]=c.x;m[6]=c.y;m[10]=c.z+1;m[14]=c.w;mirrorCamera.projectionMatrixInverse.copy(mirrorCamera.projectionMatrix).invert();
 const target=batch.mirror.target||(batch.mirror.target=new THREE.WebGLRenderTarget(width,height));
 if(target.width!==width||target.height!==height)target.setSize(width,height);
 select(mirrorCamera);for(const b of batches)if(b.mirror)b.mirror.mesh.visible=false;
 renderer.setRenderTarget(target);renderer.clear();renderer.render(scene,mirrorCamera);
 batch.mirror.mesh.material.uniforms.mirrorMap.value=target.texture;
 return true;
}
const defaultStage={color:[1,1,1,1],condition:1,uv:[],vertexColor:0,alphaTest:0};
const unit=()=>new THREE.Matrix3();
function uniforms() {return {diffuseMap:{value:white},bumpMap:{value:flat},specularMap:{value:black},projectionMap:{value:white},falloffMap:{value:white},
 diffuseUV:{value:unit()},bumpUV:{value:unit()},specularUV:{value:unit()},projectionUV:{value:unit()},
 diffuseColor:{value:new THREE.Vector4(1,1,1,1)},specularColor:{value:new THREE.Vector4(0,0,0,0)},lightColor:{value:new THREE.Vector4(1,1,1,1)},
 projectS:{value:new THREE.Vector4()},projectT:{value:new THREE.Vector4()},projectQ:{value:new THREE.Vector4()},projectF:{value:new THREE.Vector4()},
 lightOrigin:{value:new THREE.Vector3()},alphaCut:{value:0},normalRXGB:{value:0},vertexMode:{value:0},specularVertexMode:{value:0},lightScale:{value:2},ambient:{value:0},diffuseEnabled:{value:1},vertexAlphaEnabled:{value:0},passKind:{value:0}};}
const blendFactors={gl_zero:THREE.ZeroFactor,gl_one:THREE.OneFactor,gl_src_alpha:THREE.SrcAlphaFactor,gl_one_minus_src_alpha:THREE.OneMinusSrcAlphaFactor,
 gl_dst_color:THREE.DstColorFactor,gl_one_minus_dst_color:THREE.OneMinusDstColorFactor,gl_dst_alpha:THREE.DstAlphaFactor,gl_one_minus_dst_alpha:THREE.OneMinusDstAlphaFactor,
 gl_src_color:THREE.SrcColorFactor,gl_one_minus_src_color:THREE.OneMinusSrcColorFactor};
// Big maps (mars_city1) reference thousands of images; unbounded parallel fetches intermittently
// fail with net::ERR_INSUFFICIENT_RESOURCES and the whole map load aborts.
let loadSlots=32;const loadQueue=[];
async function limited(job) {
 if(loadSlots>0)loadSlots--;else await new Promise(r=>loadQueue.push(r));
 try{return await job();}finally{const next=loadQueue.shift();if(next)next();else loadSlots++;}
}
async function texture(image,clamp=false) {
 if(!image||image.status!=='ready')return null;
 const key=image.texture+':'+clamp;
 if(!textures.has(key))textures.set(key,limited(async()=>{
   const url=base+'textures/'+encodeURIComponent(image.texture);
   const t=image.texture.toLowerCase().endsWith('.dds')?await skinnerDDS.load(url):await new THREE.TextureLoader().loadAsync(url);
   t.flipY=false;t.encoding=THREE.LinearEncoding;t.wrapS=t.wrapT=clamp?THREE.ClampToEdgeWrapping:THREE.RepeatWrapping;
   t.needsUpdate=true;return t;
 }));return textures.get(key);
}
function stageColor(stage,seconds,parms,target,sound=0) {
 const active=M.evaluate(stage.condition,seconds,parms,tables,data.globalShaderParms,sound)!==0;
 target.fromArray(stage.color.map(c=>M.evaluate(c,seconds,parms,tables,data.globalShaderParms,sound)));if(!active)target.multiplyScalar(0);return active;
}
function stageUV(stage,seconds,parms,target,sound=0) {const m=M.uvMatrix(stage,seconds,parms,tables,data.globalShaderParms,sound);target.set(m[0],m[1],m[2],m[3],m[4],m[5],0,0,1);}
function interactionGroups(stages) {
 const out=[];let bump=null,diffuse=null,specular=null;
 const flush=()=>{if(diffuse||specular)out.push({bump,diffuse,specular});diffuse=specular=null;};
 for(const stage of stages) {
  if(stage.role==='bump'){flush();bump=stage;}
  else if(stage.role==='diffuse'){if(diffuse)flush();diffuse=stage;}
  else if(stage.role==='specular'){if(specular)flush();specular=stage;}
 }
 flush();return out;
}
function deformGeometry(surface,spec) {
 // A private copy of one surface, so tr_deform.cpp rebuilds don't touch the shared map buffers.
 const ix=geometry.index.array.subarray(surface.start,surface.start+surface.count);let lo=Infinity,hi=-1;
 for(const i of ix){if(i<lo)lo=i;if(i>hi)hi=i;}
 const a=geometry.attributes,slice=attr=>attr.array.slice(lo*attr.itemSize,(hi+1)*attr.itemSize);
 const base={position:slice(a.position),normal:slice(a.normal),uv:slice(a.uv),color:slice(a.color),index:Uint32Array.from(ix,i=>i-lo)};
 const layout=M.deformLayout(spec.kind,base);if(!layout)return null;
 const n=layout.vertices,g=new THREE.BufferGeometry();
 for(const [name,source,size] of [['position',base.position,3],['normal',base.normal,3],['uv',base.uv,2],['color',base.color,3],
   ['doomAlpha',slice(a.doomAlpha),1],['doomTangent',slice(a.doomTangent),3],['doomBitangent',slice(a.doomBitangent),3]]) {
  const values=new Float32Array(n*size);
  if(source.length===values.length)values.set(source);else for(let i=0;i<n;i++)values.set(source.subarray(0,size),i*size);
  const attribute=new THREE.BufferAttribute(values,size);if(['position','uv','color'].includes(name))attribute.setUsage(THREE.DynamicDrawUsage);
  g.setAttribute(name,attribute);
 }
 g.setIndex(layout.index);
 const at0=spec.values.map(v=>Math.abs(M.evaluate(v,0,surface.parms,tables,data.globalShaderParms)));
 const size=Math.hypot(...surface.bounds[1].map((v,i)=>v-surface.bounds[0][i]));
 return {geometry:g,spec,base,layout,margin:size+(at0[0]||0)*2,out:{position:g.attributes.position.array,uv:g.attributes.uv.array,color:g.attributes.color.array}};
}
function applyDeform(batch,view,seconds,sound) {
 const d=batch.deform,{spec}=d,parms=batch.surface.parms,e=view.matrixWorld.elements;
 const values=spec.values.map(v=>M.evaluate(v,seconds,parms,tables,data.globalShaderParms,sound));
 const lookup=tables[spec.table]?f=>M.evaluate(['table',spec.table,f],seconds,parms,tables):f=>Math.sin(f*2*Math.PI);
 if(!M.deform(spec.kind,d.base,d.layout,d.out,view.position.toArray(),[-e[0],-e[1],-e[2]],[e[4],e[5],e[6]],values,lookup))return false;
 for(const name of ['position','uv','color'])batch.geometry.attributes[name].needsUpdate=true;
 return true;
}
function particleGeometry({emitter,stage}) {
 const n=P.stageCapacity(stage),g=new THREE.BufferGeometry();
 for(const [name,size] of [['position',3],['normal',3],['uv',2],['color',3],['doomAlpha',1],['doomTangent',3],['doomBitangent',3]]) {
  const attribute=new THREE.BufferAttribute(new Float32Array(n*size),size);
  if(['position','uv','color','doomAlpha'].includes(name))attribute.setUsage(THREE.DynamicDrawUsage);g.setAttribute(name,attribute);
 }
 g.setIndex(P.quadIndex(n));g.setDrawRange(0,0);
 return {geometry:g,emitter,stage,out:{position:new Float32Array(n*3),uv:g.attributes.uv.array,color:new Float32Array(n*4),count:0}};
}
function applyParticles(batch,view,seconds) {
 // idRenderModelPrt in emitter space: view axes go in through the emitter axis, quads come out to world space.
 const {emitter,stage,out}=batch.particle,A=emitter.axis,o=emitter.origin,e=view.matrixWorld.elements,g=batch.geometry;
 const local=w=>[0,1,2].map(j=>A[j]*w[0]+A[3+j]*w[1]+A[6+j]*w[2]);
 const axes={left:local([-e[0],-e[1],-e[2]]),up:local([e[4],e[5],e[6]]),forward:local([-e[8],-e[9],-e[10]]),down:local([0,0,-1])};
 let t=seconds;
 if(emitter.fx) {
  // idEntityFx: each particle action lives [delay, delay+duration) of an effect that repeats every `period` (0: once).
  const f=emitter.fx;if(f.period>0)t%=f.period;else if(t>=f.duration)return false;
  t-=f.delay;if(t<0||t>=f.length)return false;
 }
 const n=P.simulate(stage,t*1000,emitter.parms,axes,out,(name,f)=>M.evaluate(['table',name,f],seconds,emitter.parms,tables));
 if(!n)return false;
 const p=g.attributes.position.array,c=g.attributes.color.array,a=g.attributes.doomAlpha.array,q=out.position;
 for(let i=0;i<n;i++) {
  for(let r=0;r<3;r++){p[i*3+r]=o[r]+A[r*3]*q[i*3]+A[r*3+1]*q[i*3+1]+A[r*3+2]*q[i*3+2];c[i*3+r]=out.color[i*4+r];}
  a[i]=out.color[i*4+3];
 }
 g.setDrawRange(0,n/4*6);
 for(const name of ['position','uv','color','doomAlpha'])g.attributes[name].needsUpdate=true;
 return true;
}
function disposeMap() {
 npcs.setCount(0);
 for(const b of batches){for(const mesh of b.meshes)mesh.material.dispose();b.mirror?.target?.dispose();b.group.clear();b.geometry.dispose();}
 scene.clear();batches=[];geometry?.dispose();geometry=null;
 for(const value of textures.values())value.then(t=>t.dispose()).catch(()=>{});textures=new Map();
}
async function buildMap(meta,binary) {
 data=meta;geometry=new THREE.BufferGeometry();
 const npcOwners=window.idTech4NPCs.owners(meta);
 for(const [key,attr,size] of [['vertices','position',3],['normals','normal',3],['uvs','uv',2],['colors','color',meta.colorSize||3]]) {
  const a=meta.geometry_buffers.arrays[key];geometry.setAttribute(attr,new THREE.BufferAttribute(new Float32Array(binary,a.offset,a.count),size));
 }
 const colors=geometry.attributes.color,alpha=new Float32Array(colors.count).fill(1);
 if(colors.itemSize===4){const rgb=new Float32Array(colors.count*3);for(let i=0;i<colors.count;i++){rgb.set(colors.array.subarray(i*4,i*4+3),i*3);alpha[i]=colors.array[i*4+3];}geometry.setAttribute('color',new THREE.BufferAttribute(rgb,3));}
 geometry.setAttribute('doomAlpha',new THREE.BufferAttribute(alpha,1));
 const ix=meta.geometry_buffers.arrays.indices;geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(binary,ix.offset,ix.count),1));M.tangents(geometry.attributes.position.array,geometry.attributes.uv.array,geometry.attributes.normal.array,geometry.index.array,meta.surfaces.filter(s=>meta.materials[s.material].unsmoothedTangents)).forEach((a,i)=>geometry.setAttribute(i?'doomBitangent':'doomTangent',new THREE.BufferAttribute(a,3)));
 // Resolve each image once before constructing passes. No run-time fetch waterfall per surface.
 const stages=Object.values(meta.materials).flatMap(m=>m.stages);
 await Promise.all(stages.map(async s=>s.texture=await texture(s.image,s.clamp)));
 await Promise.all(stages.filter(s=>s.cube).map(async s=>s.cubeTextures=await Promise.all(s.cube.faces.map(f=>texture(f,true)))));
 // Faces are picked per pixel, so derivatives jump at face edges and pick tiny mips (dashed seams).
 for(const s of stages)for(const t of s.cubeTextures||[])if(t)t.minFilter=THREE.LinearFilter;
 await Promise.all(meta.lights.map(async l=>l.falloffTexture=await texture(l.falloff,true)));
 const sources=[...meta.lights,...meta.surfaces].map(x=>x.sound).filter(s=>s?.file&&!s.waitForTrigger);
 if(sources.length) {
  // Offline decoding needs neither autoplay permission nor an audio output device.
  const decoder=new OfflineAudioContext(1,1,44100),envelopes=new Map();
  await Promise.all(sources.map(async source=>{
   if(!envelopes.has(source.file))envelopes.set(source.file,(async()=>{
    const response=await fetch(base+source.file);if(!response.ok)throw Error('Missing sound envelope source: '+source.source);
    const buffer=await decoder.decodeAudioData(await response.arrayBuffer());
    return M.soundEnvelope(buffer.getChannelData(0));
   })());
   try{source.envelope=await envelopes.get(source.file);}
   catch(error){const note=error.message+' (sound-reactive stages remain dark)';if(!meta.notes.includes(note))meta.notes.push(note);}
  }));
 }
 let passCount=0,ambientOrder=100;
 // Each emitter stage draws like one more surface whose quads are rebuilt per view.
 const particleSurfaces=(meta.emitters||[]).flatMap(emitter=>meta.particles[emitter.particle].stages.map((stage,k)=>({stage,k})).filter(({stage})=>stage.cycleMsec>0)
  .map(({stage,k})=>({material:emitter.materials[k],bounds:emitter.bounds,areas:emitter.areas,lights:emitter.lights,entity:emitter.entity,parms:emitter.parms,particle:{emitter,stage}})));
 for(const surface of [...meta.surfaces,...particleSurfaces]) {
  const material=meta.materials[surface.material],group=new THREE.Group(),meshes=[];
  const deformed=M.DEFORMS[material.deform?.kind]?deformGeometry(surface,material.deform):null;
  const particle=surface.particle?particleGeometry(surface.particle):null;
  let g=deformed?.geometry||particle?.geometry;
  if(!g){g=new THREE.BufferGeometry();g.attributes=geometry.attributes;g.index=geometry.index;g.setDrawRange(surface.start,surface.count);}
  g.boundingBox=new THREE.Box3(new THREE.Vector3(...surface.bounds[0]),new THREE.Vector3(...surface.bounds[1]));
  if(deformed)g.boundingBox.expandByScalar(deformed.margin);
  g.boundingSphere=g.boundingBox.getBoundingSphere(new THREE.Sphere());
  const batch={group,meshes,geometry:g,surface,deform:deformed,particle,npc:npcOwners.has(surface.entity)};batches.push(batch);scene.add(group);
  function pass(kind,diffuse,specular,bump,light,projection,order) {
   const u=uniforms();u.vertexAlphaEnabled.value=meta.game==='quake4'||particle?1:0;u.passKind.value=kind;u.diffuseMap.value=diffuse?.texture||(kind===1?black:white);
   u.specularMap.value=specular?.texture||black;u.bumpMap.value=bump?.texture||flat;
   u.normalRXGB.value=u.bumpMap.value.userData.normalEncoding==='rxgb'?1:0;
   u.vertexMode.value=diffuse?.vertexColor||0;u.specularVertexMode.value=specular?.vertexColor||0;
   if(light) {
    u.projectionMap.value=projection.texture||white;u.falloffMap.value=light.falloffTexture||white;
    if(light.noSpecular)u.specularMap.value=black;
    if(light.noDiffuse)u.diffuseEnabled.value=0;
    u.lightOrigin.value.fromArray(light.origin);u.ambient.value=+light.ambient;u.lightScale.value=meta.lightScale;
    ['projectS','projectT','projectQ','projectF'].forEach((n,i)=>u[n].value.fromArray(light.projection[i]));
   }
   if(kind===3){
    diffuse.cubeTextures.forEach((t,i)=>u['cube'+i]={value:t||black});
    u.cameraCube={value:+diffuse.cube.camera};u.skyMatrix={value:unit()};
    u.cubeMode={value:diffuse.texgen==='reflect'?(bump?2:1):diffuse.texgen==='normal'?3:0};
   }
   if(kind===5)u.fogPlane={value:new THREE.Vector4(...M.fogPlane(light))};
   if(kind===4){u.mirrorMap={value:black};u.textureMatrix={value:new THREE.Matrix4()};
   }
   const sm=new THREE.ShaderMaterial({vertexShader:vertex,fragmentShader:[fragment,fragment,fragment,cubeFragment,mirrorFragment,fogFragment,blendLightFragment][kind],uniforms:u,vertexColors:true,
    side:material.twoSided||deformed||particle?THREE.DoubleSide:THREE.FrontSide,depthWrite:kind===0,depthFunc:kind===0||material.translucent?THREE.LessEqualDepth:THREE.EqualDepth,transparent:kind!==0,
    blending:kind===0?THREE.NoBlending:THREE.CustomBlending,blendSrc:THREE.OneFactor,blendDst:kind===1?THREE.OneFactor:THREE.ZeroFactor});
   if(kind===2||kind===3||kind===6) {
    const factors=M.ambientBlend(kind===6?projection:diffuse).map(f=>blendFactors[f]);
    [sm.blendSrc,sm.blendDst,sm.blendSrcAlpha,sm.blendDstAlpha]=factors;
   }
   if(kind===5){sm.blendSrc=THREE.SrcAlphaFactor;sm.blendDst=THREE.OneMinusSrcAlphaFactor;}
   if(diffuse?.polygonOffset){sm.polygonOffset=true;sm.polygonOffsetFactor=-1;sm.polygonOffsetUnits=-1;}
   const mesh=new THREE.Mesh(g,sm);mesh.renderOrder=order;mesh.userData={kind,diffuse,specular,bump,light,projection,cubeAxis:surface.cubeAxis};group.add(mesh);meshes.push(mesh);passCount++;
  }
  const cut=material.stages.filter(s=>s.alphaTest!==0),opaque=material.stages.find(s=>s.role==='diffuse')||material.stages[0];
  // RoE portal-sky surfaces show the sky view already in the color buffer (_currentRender): depth only.
  if(material.portalSky){pass(0,null,null,null,null,null,0);meshes.at(-1).material.colorWrite=false;continue;}
  if(material.mirror){batch.mirror=mirrorPlane(surface);pass(4,null,null,null,null,null,50);batch.mirror.mesh=meshes.at(-1);}
  if(!material.translucent)for(const stage of cut.length?cut:[opaque])pass(0,stage,null,null,null,null,0);
  for(const interaction of interactionGroups(material.stages))for(const index of surface.lights) {
    const light=meta.lights[index];if(light.authoredOff||light.fog||light.blend)continue;
    for(const projection of meta.materials[light.shader].stages)if(projection.texture)
      pass(1,interaction.diffuse,interaction.specular,interaction.bump,light,projection,10);
  }
  // Finish each surface's stage chain before another surface overwrites scratch alpha.
  // RB_STD_T_RenderShaderPasses: a reflect stage uses the material's bump stage (GetBumpStage) when it has one.
  const bumpStage=material.stages.find(s=>s.role==='bump')||null;
  material.stages.filter(s=>s.role==='ambient').forEach(s=>pass(s.cube?3:2,s,null,s.texgen==='reflect'?bumpStage:null,null,null,M.ambientOrder(material.sort,ambientOrder++)));
  // RB_FogAllLights, after every shader pass: fog lights, then blend lights, over opaque surfaces in their volumes.
  if(!material.translucent)for(const index of surface.lights) {
   const light=meta.lights[index],stages=meta.materials[light.shader].stages;if(light.authoredOff)continue;
   if(light.fog&&stages[0])pass(5,null,null,null,light,stages[0],FOG_ORDER);
   else if(light.blend)for(const projection of stages)if(projection.texture)pass(6,null,null,null,light,projection,FOG_ORDER+1);
  }
 }
 // RB_FogPass also draws the fog volume's back faces (depth less), so fog covers what lies beyond the volume.
 for(const light of meta.lights) {
  const stage=meta.materials[light.shader].stages[0];if(!light.fog||!light.volume?.length||!stage||light.authoredOff)continue;
  const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(new Float32Array(light.volume),3));
  g.boundingBox=new THREE.Box3(new THREE.Vector3(...light.bounds[0]),new THREE.Vector3(...light.bounds[1]));g.boundingSphere=g.boundingBox.getBoundingSphere(new THREE.Sphere());
  const u=uniforms();u.lightColor.value.set(0,0,0,0);['projectS','projectT','projectQ','projectF'].forEach((n,i)=>u[n].value.fromArray(light.projection[i]));
  u.fogPlane={value:new THREE.Vector4(...M.fogPlane(light))};
  const mesh=new THREE.Mesh(g,new THREE.ShaderMaterial({vertexShader:vertex,fragmentShader:fogFragment,uniforms:u,side:THREE.BackSide,depthWrite:false,
   depthFunc:THREE.LessDepth,transparent:true,blending:THREE.CustomBlending,blendSrc:THREE.SrcAlphaFactor,blendDst:THREE.OneMinusSrcAlphaFactor}));
  mesh.renderOrder=FOG_ORDER;mesh.userData={kind:5,light,projection:stage};
  const group=new THREE.Group();group.add(mesh);scene.add(group);
  batches.push({group,meshes:[mesh],geometry:g,surface:{areas:light.areas,parms:light.parms,entity:light.name},npc:false});passCount++;
 }
 npcs.setCount(npcOwners.size);
 return passCount;
}
function updatePass(mesh,seconds,parms,sound) {
 const {kind,diffuse,specular,bump,light,projection}=mesh.userData,u=mesh.material.uniforms;
 const d=diffuse||defaultStage;
 const da=stageColor(d,seconds,parms,u.diffuseColor.value,sound);
 const sa=specular?stageColor(specular,seconds,parms,u.specularColor.value,sound):false;
 mesh.visible=kind===1?(da||sa)&&(!bump||M.evaluate(bump.condition,seconds,parms,tables,data.globalShaderParms,sound)!==0):da;
 stageUV(d,seconds,parms,u.diffuseUV.value,sound);u.alphaCut.value=kind===1?0:M.evaluate(d.alphaTest,seconds,parms,tables,data.globalShaderParms,sound);
 if(kind===3){
  const wobble=d.wobble?.map(v=>M.evaluate(v,seconds,parms,tables,data.globalShaderParms,sound));
  u.skyMatrix.value.set(...M.skyMatrix(wobble,seconds,mesh.userData.cubeAxis));
 }
 if(bump)stageUV(bump,seconds,parms,u.bumpUV.value,sound);
 if(specular)stageUV(specular,seconds,parms,u.specularUV.value,sound);
 if(light) {const amplitude=M.soundAmplitude(light.sound,seconds,soundAmplitudeOverride);mesh.visible=stageColor(projection,seconds,light.parms,u.lightColor.value,amplitude)&&mesh.visible;stageUV(projection,seconds,light.parms,u.projectionUV.value,amplitude);}
}
let lastMetrics={};
function draw(seconds=0,target=null) {
 if(!data)return;
 function select(view){
  view.updateMatrixWorld();matrix.multiplyMatrices(view.projectionMatrix,view.matrixWorldInverse);frustum.setFromProjectionMatrix(matrix);
  const areas=M.visibleAreas(view.position.toArray(),data,matrix.elements);let surfaces=0;
  for(const batch of batches){
   batch.group.visible=(!batch.npc||npcs.enabled)&&(!batch.surface.areas.length||batch.surface.areas.some(a=>areas.has(a)))&&frustum.intersectsBox(batch.geometry.boundingBox);
   if(batch.group.visible){
    const sound=M.soundAmplitude(batch.surface.sound,seconds,soundAmplitudeOverride);
    if(batch.deform&&!applyDeform(batch,view,seconds,sound)||batch.particle&&!applyParticles(batch,view,seconds)){batch.group.visible=false;continue;}
    surfaces++;for(const mesh of batch.meshes)updatePass(mesh,seconds,batch.surface.parms,sound);
   }
  }
  return {areas,surfaces};
 }
 const width=target?target.width:renderer.domElement.width,height=target?target.height:renderer.domElement.height;
 if(colorTarget.width!==width||colorTarget.height!==height)colorTarget.setSize(width,height);
 let selected=select(camera),skyMetrics={calls:0,triangles:0};
 const mirrors=batches.filter(b=>b.mirror&&b.group.visible).map(b=>[b,renderMirror(b,select,width,height)]);
 if(mirrors.length){selected=select(camera);for(const [b,ok] of mirrors)if(!ok)b.mirror.mesh.visible=false;}
 renderer.setRenderTarget(colorTarget);
 const portalSky=M.portalSkyOrigin(data,selected.areas);
 if(portalSky){
  // Q4 SingleView renders the fixed-origin sky scene first, keeps its color,
  // clears depth, then draws the primary view with its original eye and axes.
  skyCamera.copy(camera);skyCamera.position.fromArray(portalSky);const skySelected=select(skyCamera);
  renderer.render(scene,skyCamera);skyMetrics={calls:renderer.info.render.calls,triangles:renderer.info.render.triangles,areas:skySelected.areas.size,surfaces:skySelected.surfaces};
  renderer.clearDepth();selected=select(camera);renderer.autoClear=false;
 }
 renderer.render(scene,camera);renderer.autoClear=true;
 lastMetrics={areas:selected.areas.size,surfaces:selected.surfaces,calls:renderer.info.render.calls+skyMetrics.calls,triangles:renderer.info.render.triangles+skyMetrics.triangles,sky:skyMetrics};
 renderer.setRenderTarget(target);renderer.render(copyScene,copyCamera);renderer.setRenderTarget(null);
 lastMetrics.calls+=renderer.info.render.calls;lastMetrics.triangles+=renderer.info.render.triangles;
}
function pointCamera(){look.set(Math.cos(yaw)*Math.cos(pitch),Math.sin(yaw)*Math.cos(pitch),Math.sin(pitch));camera.lookAt(look.add(camera.position));}
function showViewpoint(which=0) {
 if(!data)return;
 const v=typeof which==='number'?data.viewpoints[which]:which;if(!v)return;
 camera.position.fromArray(v.origin);yaw=(v.yaw||0)*Math.PI/180;pitch=(v.pitch||0)*Math.PI/180;pointCamera();draw(0);
 if(typeof which==='number')$('viewpoint').value=String(which);
}
function resize(){const r=$('c').parentElement.getBoundingClientRect();renderer.setSize(r.width,r.height,false);camera.aspect=r.width/r.height;
 // id's r_fov is horizontal at 4:3, expanded horizontally on widescreen.
 camera.fov=2*Math.atan(Math.tan(+$('fov').value*Math.PI/360)/(4/3))*180/Math.PI;camera.updateProjectionMatrix();}
async function loadMap(id) {
 loading=true;$('map').disabled=true;$('status').textContent='Loading '+id+'…';disposeMap();data=null;
 try {
  const entry=catalog.find(e=>e.id===id);const url=base+entry.scene,dir=url.slice(0,url.lastIndexOf('/')+1);
  const response=await fetch(url);if(!response.ok)throw Error('Map data missing');const meta=await response.json();
  const [binary,tab]=await Promise.all([fetch(dir+meta.geometry_buffers.file).then(r=>r.arrayBuffer()),fetch(dir+'tables.json').then(r=>r.json())]);tables=tab;
  const count=await buildMap(meta,binary);$('viewpoint').replaceChildren(...meta.viewpoints.map((v,i)=>new Option(v.name,i)));
  $('notes').textContent=meta.notes.join('\n')+'\n'+JSON.stringify({stats:meta.stats,skippedLights:meta.skippedLights,skippedEntities:meta.skippedEntities},null,2);
  $('status').textContent=entry.label+' · '+meta.lights.length+' lights · '+meta.entities.length+' entities · '+count+' passes (uncapped)';
  mapStart=performance.now();resize();showViewpoint(0);history.replaceState(null,'','#'+encodeURIComponent(id));
 }catch(e){disposeMap();data=null;$('status').textContent=e.message;console.error(e);}finally{loading=false;$('map').disabled=false;}
}
let catalog=[];
async function loadCatalog(){const r=await fetch(base+'catalog.json');if(!r.ok){$('importPanel').open=true;$('status').textContent='Import '+config.label+' maps to start.';return;}
 catalog=await r.json();$('map').replaceChildren(...catalog.map(m=>new Option(m.label,m.id)));
 const id=decodeURIComponent(location.hash.slice(1));$('map').value=catalog.some(m=>m.id===id)?id:catalog[0]?.id;if($('map').value)await loadMap($('map').value);}
$('map').onchange=()=>loadMap($('map').value);$('viewpoint').onchange=()=>showViewpoint(+$('viewpoint').value);$('reset').onclick=()=>showViewpoint(0);
$('fov').onchange=resize;window.addEventListener('resize',resize);
$('import').onclick=async()=>{const button=$('import');button.disabled=true;$('importStatus').textContent='Importing maps…';try{
 const r=await fetch(config.import,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:$('gamePath').value,replace:$('replace').checked})});
 const result=await r.json();if(!r.ok)throw Error(result.error);$('importStatus').textContent=result.ready+' maps imported.';await loadCatalog();
 }catch(e){$('importStatus').textContent=e.message;}finally{button.disabled=false;}};
let dragging=false;
$('c').addEventListener('click',()=>{if(!document.pointerLockElement)$('c').requestPointerLock?.();});
$('c').addEventListener('pointerdown',()=>dragging=true);window.addEventListener('pointerup',()=>dragging=false);
window.addEventListener('mousemove',e=>{if(document.pointerLockElement!==$('c')&&!dragging)return;yaw-=e.movementX*.002;pitch=Math.max(-1.55,Math.min(1.55,pitch-e.movementY*.002));pointCamera();});
window.addEventListener('keydown',e=>{if(/INPUT|SELECT|TEXTAREA/.test(e.target.tagName))return;keys.add(e.code);if(/^Digit[1-6]$/.test(e.code))showViewpoint(+e.code.at(-1)-1);if(e.code==='Space')e.preventDefault();});
window.addEventListener('keyup',e=>keys.delete(e.code));window.addEventListener('blur',()=>keys.clear());
$('c').addEventListener('wheel',e=>{speed=Math.max(10,Math.min(8000,speed*Math.exp(-e.deltaY*.001)));e.preventDefault();},{passive:false});
let previous=performance.now(),benchmarking=false;
function animate(now){requestAnimationFrame(animate);const dt=Math.min(.05,(now-previous)/1000);previous=now;if(loading||benchmarking||paused||!data)return;
 const forward=(+keys.has('KeyW'))-(+keys.has('KeyS')),right=(+keys.has('KeyD'))-(+keys.has('KeyA'));
 camera.position.x+=dt*speed*(Math.cos(yaw)*forward+Math.sin(yaw)*right);camera.position.y+=dt*speed*(Math.sin(yaw)*forward-Math.cos(yaw)*right);
 camera.position.z+=dt*speed*((+keys.has('Space'))-(+(keys.has('ShiftLeft')||keys.has('ShiftRight'))));if(forward||right||keys.has('Space')||keys.has('ShiftLeft')||keys.has('ShiftRight'))pointCamera();draw((now-mapStart)/1000);}
async function benchmark(frames=120) {
 if(!data||loading)return;benchmarking=true;$('benchmark').disabled=true;
 try{const times=[],intervals=[];let last=performance.now();const gl=renderer.getContext();
  for(let i=0;i<frames+10;i++){await new Promise(requestAnimationFrame);const begin=performance.now();draw(0);gl.finish();const end=performance.now();if(i>=10){times.push(end-begin);intervals.push(end-last);}last=end;}
  const sorted=[...times].sort((a,b)=>a-b),ext=gl.getExtension('WEBGL_debug_renderer_info');
  const result={map:data.name,frames,width:renderer.domElement.width,height:renderer.domElement.height,camera:camera.position.toArray(),
   meanMs:times.reduce((a,b)=>a+b,0)/times.length,p95Ms:sorted[Math.floor(sorted.length*.95)],rafMeanMs:intervals.reduce((a,b)=>a+b,0)/intervals.length,
   gpu:ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER),...lastMetrics};
  $('performance').textContent=JSON.stringify(result);window.skinnerDoom3Maps.lastBenchmark=result;return result;
 }finally{benchmarking=false;$('benchmark').disabled=false;}
}
$('benchmark').onclick=()=>benchmark();
npcs.onChange=()=>{if(!loading)draw((performance.now()-mapStart)/1000);};
window.skinnerDoom3Maps={game:config.game,renderer,scene,camera,draw,showViewpoint,benchmark,get soundAmplitudeOverride(){return soundAmplitudeOverride;},set soundAmplitudeOverride(value){soundAmplitudeOverride=value;},get paused(){return paused;},set paused(value){paused=!!value;},get data(){return data;},get metrics(){return lastMetrics;}};
resize();requestAnimationFrame(animate);await loadCatalog();
})().catch(e=>{document.getElementById('status').textContent=e.message;console.error(e);});
