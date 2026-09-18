import{r as e}from"./rolldown-runtime-hePW80VL.js";import{Gr as t,as as n,ha as r,ia as i,is as a,rt as o,vr as s}from"./imageFileList-BA5Ei791.js";import{f as c,g as l}from"./events-156d8d12.esm-BxFD0TfH.js";import{c as u,n as d,r as f,s as p,t as m}from"./coordinates-D26IBf7-.js";import{s as h}from"./engineStore-DqTKOi-c.js";import{i as g}from"./cameraTourStore-CYRUGmbv.js";import{o as _,s as v}from"./waterLevel-BwA2BnoU.js";import"./ghostToScene-DqyYKzd6.js";import{f as y}from"./loaders-cXYnLKZz.js";import{l as b,t as x}from"./fogShader-e13Hspig.js";import{n as S}from"./globalFogUniforms-Ds1aGA7A.js";import{t as C}from"./useAnisotropy-BvgMr5Pc.js";import{t as w}from"./DebugBounds-DASUmg9f.js";import{o as T}from"./placement-D_CCo5qR.js";import{t as E}from"./extends-CvVTau-c.js";import{t as D}from"./Texture-djAYtzqQ.js";import"./misToScene-BzewP2Nt.js";var O=e(n());function k(e,t){let n=e+`Geometry`;return O.forwardRef(({args:e,children:r,...i},a)=>{let o=O.useRef(null);return O.useImperativeHandle(a,()=>o.current),O.useLayoutEffect(()=>void t?.(o.current)),O.createElement(`mesh`,E({ref:o},i),O.createElement(n,{attach:`geometry`,args:e}),r)})}var A=k(`box`),j=`
  #include <fog_pars_vertex>

  #ifdef USE_FOG
    #define USE_FOG_WORLD_POSITION
    varying vec3 vFogWorldPosition;
  #endif

  uniform float uTime;
  uniform float uWaveMagnitude;

  varying vec3 vWorldPosition;
  varying vec3 vViewVector;
  varying float vDistance;

  // Wave function matching Tribes 2 engine
  // Z = surfaceZ + (sin(X*0.05 + time) + sin(Y*0.05 + time)) * waveFactor
  // waveFactor = waveAmplitude * 0.25
  // Note: Using xz for Three.js Y-up (Torque uses XY with Z-up)
  float getWaveHeight(vec3 worldPos) {
    float waveFactor = uWaveMagnitude * 0.25;
    return (sin(worldPos.x * 0.05 + uTime) + sin(worldPos.z * 0.05 + uTime)) * waveFactor;
  }

  void main() {
    // Apply instance transform when using InstancedMesh.
    #ifdef USE_INSTANCING
      mat4 localModel = modelMatrix * instanceMatrix;
    #else
      mat4 localModel = modelMatrix;
    #endif

    // Get world position for wave calculation
    vec4 worldPos = localModel * vec4(position, 1.0);
    vWorldPosition = worldPos.xyz;

    // Apply wave displacement to Y (vertical axis in Three.js)
    vec3 displaced = position;
    displaced.y += getWaveHeight(worldPos.xyz);

    // Calculate final world position after displacement for fog
    #ifdef USE_FOG
      vec4 displacedWorldPos = localModel * vec4(displaced, 1.0);
      vFogWorldPosition = displacedWorldPos.xyz;
    #endif

    // Calculate view vector for environment mapping
    vViewVector = cameraPosition - worldPos.xyz;
    vDistance = length(vViewVector);

    vec4 mvPosition = viewMatrix * localModel * vec4(displaced, 1.0);
    gl_Position = projectionMatrix * mvPosition;

    // Set fog depth (distance from camera) - normally done by fog_vertex include
    // but we can't use that include because it references 'transformed' which we don't have
    #ifdef USE_FOG
      vFogDepth = length(mvPosition.xyz);
    #endif
  }
`,M=`
  #define HAS_FOG_DISTANCE_SCALE
  #include <fog_pars_fragment>

  // Enable volumetric fog (must be defined before fog uniforms)
  #ifdef USE_FOG
    #define USE_VOLUMETRIC_FOG
    #define USE_FOG_WORLD_POSITION
  #endif

  uniform float uTime;
  uniform float uOpacity;
  uniform float uEnvMapIntensity;
  uniform sampler2D uBaseTexture;
  uniform sampler2D uEnvMapTexture;

  // Volumetric fog uniforms
  #ifdef USE_FOG
    uniform vec4 fogVolumeData[3];
    uniform float cameraHeight;
    uniform float fogRowBase;
    uniform float fogRowStep;
    uniform bool fogEnabled;
    varying vec3 vFogWorldPosition;
  #endif

  varying vec3 vWorldPosition;
  varying vec3 vViewVector;
  varying float vDistance;

  #define TWO_PI 6.283185307179586

  // Constants from Tribes 2 engine
  #define BASE_DRIFT_CYCLE_TIME 8.0
  #define BASE_DRIFT_RATE 0.02
  #define BASE_DRIFT_SCALAR 0.03
  #define TEXTURE_SCALE (1.0 / 48.0)

  // Environment map UV wobble constants
  #define Q1 150.0
  #define Q2 2.0
  #define Q3 0.01

  // Rotate UV coordinates
  vec2 rotateUV(vec2 uv, float angle) {
    float c = cos(angle);
    float s = sin(angle);
    return vec2(
      uv.x * c - uv.y * s,
      uv.x * s + uv.y * c
    );
  }

  void main() {
    // Calculate base texture UVs using world position (1/48 tiling)
    vec2 baseUV = vWorldPosition.xz * TEXTURE_SCALE;

    // Phase (time in radians for drift cycle)
    float phase = mod(uTime * (TWO_PI / BASE_DRIFT_CYCLE_TIME), TWO_PI);

    // Base texture drift
    float baseDriftX = uTime * BASE_DRIFT_RATE;
    float baseDriftY = cos(phase) * BASE_DRIFT_SCALAR;

    // === Phase 1a: First base texture pass (rotated 30 degrees) ===
    vec2 uv1a = rotateUV(baseUV, radians(30.0));

    // === Phase 1b: Second base texture pass (rotated 60 degrees total, with drift) ===
    vec2 uv1b = rotateUV(baseUV + vec2(baseDriftX, baseDriftY), radians(60.0));

    // Calculate cross-fade swing value
    float A1 = cos(((vWorldPosition.x / Q1) + (uTime / Q2)) * 6.0);
    float A2 = sin(((vWorldPosition.z / Q1) + (uTime / Q2)) * TWO_PI);
    float swing = (A1 + A2) * 0.15 + 0.5;

    // Cross-fade alpha calculation from engine
    float alpha1a = ((1.0 - swing) * uOpacity) / max(1.0 - (swing * uOpacity), 0.001);
    float alpha1b = swing * uOpacity;

    // Sample base texture for both passes
    vec4 texColor1a = texture2D(uBaseTexture, uv1a);
    vec4 texColor1b = texture2D(uBaseTexture, uv1b);

    // Combined alpha and color
    float combinedAlpha = 1.0 - (1.0 - alpha1a) * (1.0 - alpha1b);
    vec3 baseColor = (texColor1a.rgb * alpha1a * (1.0 - alpha1b) + texColor1b.rgb * alpha1b) / max(combinedAlpha, 0.001);

    // === Phase 3: Environment map / specular ===
    vec3 reflectVec = -vViewVector;
    reflectVec.y = abs(reflectVec.y);
    if (reflectVec.y < 0.001) reflectVec.y = 0.001;

    vec2 envUV;
    if (vDistance < 0.001) {
      envUV = vec2(0.0);
    } else {
      float value = (vDistance - reflectVec.y) / (vDistance * vDistance);
      envUV.x = reflectVec.x * value;
      envUV.y = reflectVec.z * value;
    }

    envUV = envUV * 0.5 + 0.5;
    envUV.x += A1 * Q3;
    envUV.y += A2 * Q3;

    vec4 envColor = texture2D(uEnvMapTexture, envUV);
    vec3 finalColor = baseColor + envColor.rgb * envColor.a * uEnvMapIntensity;

    // Note: Tribes 2 water does NOT use lighting - Phase 2 (lightmap) is disabled
    // in the original engine. Water colors come directly from textures.

    gl_FragColor = vec4(finalColor, combinedAlpha);

    // Apply volumetric fog using shared Torque-style fog shader
    ${x}
  }
`;function N(e){return new r({uniforms:{uTime:{value:0},uOpacity:{value:e?.opacity??.75},uWaveMagnitude:{value:e?.waveMagnitude??1},uEnvMapIntensity:{value:e?.envMapIntensity??1},uBaseTexture:{value:e?.baseTexture??null},uEnvMapTexture:{value:e?.envMapTexture??null},fogColor:{value:new o},fogNear:{value:1},fogFar:{value:2e3},fogVolumeData:S.fogVolumeData,cameraHeight:S.cameraHeight,fogEnabled:S.fogEnabled,fogDistanceScale:S.fogDistanceScale,fogRowBase:S.fogRowBase,fogRowStep:S.fogRowStep},vertexShader:j,fragmentShader:M,transparent:!0,side:2,depthWrite:!0,fog:!0})}var P=a(),F=2048,I=1024;function L(e,t){let n=e<=1024&&t<=1024?8:16;return[Math.max(4,Math.ceil(e/n)),Math.max(4,Math.ceil(t/n))]}function R(e){let t=e+I,n=Math.trunc(t/F);return t<0&&n--,n}function z(e,t){let n=[];for(let r=t-1;r<=t+1;r++)for(let t=e-1;t<=e+1;t++)n.push([t,r]);return n}function B({surfaceTexture:e,attach:t}){let n=y(e),r=C(),i=D(n,e=>b(e,{anisotropy:r}));return(0,P.jsx)(`meshStandardMaterial`,{attach:t,map:i,transparent:!0,opacity:.8,side:2})}var V=(0,O.memo)(function({entity:e}){let n=e.waterData,r=g(e.id),{debugMode:i}=p(),a=(0,O.useMemo)(()=>m(n.transform),[n.transform]),o=(0,O.useMemo)(()=>f(n.transform.position),[n.transform]),s=(0,O.useMemo)(()=>d(n.scale),[n.scale]),[u,h,v]=s,y=l(e=>e.camera),b=n.waveMagnitude;(0,O.useEffect)(()=>{let e=`ghost:${n.ghostIndex}`;return _(e,T(n)),()=>_(e,null)},[n]);let x=(0,O.useMemo)(()=>{let[e,t,n]=o,r=e+I,i=n+I,a=Math.round(r/8),s=Math.round(i/8);return a=Math.max(0,Math.min(2040,a)),s=Math.max(0,Math.min(2040,s)),[a*8,t,s*8]},[o]),[S,C]=(0,O.useState)(()=>z(R(y.position.x),R(y.position.z))),E=(0,O.useRef)({x:R(y.position.x),z:R(y.position.z)});c(()=>{let e=R(y.position.x),t=R(y.position.z),n=E.current;(n.x!==e||n.z!==t)&&(n.x=e,n.z=t,C(z(e,t)))});let D=n.surfaceName||`liquidTiles/BlueWater`,k=n.envMapName||void 0,j=n.surfaceOpacity,M=n.envMapIntensity,N=(0,O.useMemo)(()=>{let[e,n]=L(u,v),r=new t(u,v,e,n);return r.rotateX(-Math.PI/2),r.translate(u/2,h,v/2),r},[u,h,v]);return(0,O.useEffect)(()=>()=>{N.dispose()},[N]),(0,P.jsxs)(`group`,{quaternion:a,children:[i&&(0,P.jsx)(A,{args:s,position:[o[0]+u/2,o[1]+h/2,o[2]+v/2],children:(0,P.jsx)(`meshBasicMaterial`,{color:`#00fbff`,wireframe:!0})}),r&&(0,P.jsx)(`group`,{position:[o[0]+u/2,o[1]+h/2,o[2]+v/2],children:(0,P.jsx)(w,{size:[u,h,v]})}),(0,P.jsx)(O.Suspense,{fallback:S.map(([e,t])=>{let n=x[0]+e*F-I,r=x[2]+t*F-I;return(0,P.jsx)(`mesh`,{geometry:N,position:[n,x[1],r],children:(0,P.jsx)(`meshStandardMaterial`,{color:`#00fbff`,transparent:!0,opacity:.4,wireframe:!0,side:2})},`${e},${t}`)}),children:(0,P.jsx)(H,{reps:S,basePosition:x,surfaceGeometry:N,surfaceTexture:D,envMapTexture:k,opacity:j,waveMagnitude:b,envMapIntensity:M})})]})}),H=(0,O.memo)(function({reps:e,basePosition:t,surfaceGeometry:n,surfaceTexture:r,envMapTexture:a,opacity:o,waveMagnitude:l,envMapIntensity:d}){let f=y(r),p=y(a??`special/lush_env`),m=C(),[g,_]=D([f,p],e=>{(Array.isArray(e)?e:[e]).forEach(e=>{b(e,{anisotropy:m}),e.colorSpace=``,e.wrapS=i,e.wrapT=i})}),{animationEnabled:x}=u(),S=(0,O.useMemo)(()=>N({opacity:o,waveMagnitude:l,envMapIntensity:d,baseTexture:g,envMapTexture:_}),[o,l,d,g,_]),w=(0,O.useRef)(0),T=(0,O.useRef)(null),E=(0,O.useRef)(new s),k=(0,O.useRef)(null),A=(0,O.useRef)(null);return c((n,r)=>{x?(w.current+=h(r),S.uniforms.uTime.value=w.current):(w.current=0,S.uniforms.uTime.value=0),v(w.current);let i=T.current;if(!i||i===k.current&&e===A.current)return;k.current=i,A.current=e;let a=E.current;for(let n=0;n<e.length;n++){let[r,o]=e[n],s=t[0]+r*F-I,c=t[2]+o*F-I;a.makeTranslation(s,t[1],c),i.setMatrixAt(n,a)}i.count=e.length,i.instanceMatrix.needsUpdate=!0}),(0,O.useEffect)(()=>()=>{S.dispose()},[S]),(0,P.jsx)(`instancedMesh`,{ref:T,args:[n,S,9],frustumCulled:!1,renderOrder:-1})});export{V as WaterBlock,B as WaterMaterial};