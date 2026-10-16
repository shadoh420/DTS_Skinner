import{i as e,t}from"./react-SIfiwpqq.js";import{b as n,g as r}from"./terrainCollision-Dkb5qutI.js";import{Br as i,Ea as a,La as o,Tt as s,pi as c,xs as l}from"./imageFileList-ChpBEnRl.js";import{c as u,n as d,r as f,s as p,t as m}from"./coordinates-mhGrzui6.js";import{s as h}from"./engineStore-BcQJtEg0.js";import{i as g}from"./cameraTourStore-Bex6igXg.js";import{o as _,s as v}from"./waterLevel-Cm9IdnUL.js";import"./ghostToScene-CC21zt-r.js";import{f as y}from"./loaders-Hjx8vIH_.js";import{l as b,t as x}from"./fogShader-De5clWDe.js";import{t as S}from"./useAnisotropy-PThkV3vt.js";import{t as C}from"./DebugBounds-CibpD_iy.js";import{n as w}from"./globalFogUniforms-Ds1aGA7A.js";import{o as T}from"./placement-C0Chh4ls.js";import{i as E,t as D}from"./extends-C7fWSf-M.js";import{t as O}from"./Texture-rPEcPPL0.js";import"./misToScene-D6nBhx5z.js";var k=e(t());function A(e,t){let n=e+`Geometry`;return k.forwardRef(({args:e,children:r,...i},a)=>{let o=k.useRef(null);return k.useImperativeHandle(a,()=>o.current),k.useLayoutEffect(()=>void t?.(o.current)),k.createElement(`mesh`,D({ref:o},i),k.createElement(n,{attach:`geometry`,args:e}),r)})}var j=A(`box`),M=`
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
`,N=`
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
`;function P(e){return new o({uniforms:{uTime:{value:0},uOpacity:{value:e?.opacity??.75},uWaveMagnitude:{value:e?.waveMagnitude??1},uEnvMapIntensity:{value:e?.envMapIntensity??1},uBaseTexture:{value:e?.baseTexture??null},uEnvMapTexture:{value:e?.envMapTexture??null},fogColor:{value:new s},fogNear:{value:1},fogFar:{value:2e3},fogVolumeData:w.fogVolumeData,cameraHeight:w.cameraHeight,fogEnabled:w.fogEnabled,fogDistanceScale:w.fogDistanceScale,fogRowBase:w.fogRowBase,fogRowStep:w.fogRowStep},vertexShader:M,fragmentShader:N,transparent:!0,side:2,depthWrite:!0,fog:!0})}var F=l(),I=2048,L=1024;function R(e,t){let n=e<=1024&&t<=1024?8:16;return[Math.max(4,Math.ceil(e/n)),Math.max(4,Math.ceil(t/n))]}function z(e){let t=e+L,n=Math.trunc(t/I);return t<0&&n--,n}function B(e,t){let n=[];for(let r=t-1;r<=t+1;r++)for(let t=e-1;t<=e+1;t++)n.push([t,r]);return n}function V({surfaceTexture:e,attach:t}){let n=y(e),r=S(),i=O(n,e=>b(e,{anisotropy:r}));return(0,F.jsx)(`meshStandardMaterial`,{attach:t,map:i,transparent:!0,opacity:.8,side:2})}var H=(0,k.memo)(function({entity:e}){let t=e.waterData,i=g(e.id),{debugMode:a}=p(),o=(0,k.useMemo)(()=>m(t.transform),[t.transform]),s=(0,k.useMemo)(()=>f(t.transform.position),[t.transform]),l=(0,k.useMemo)(()=>d(t.scale),[t.scale]),[u,h,v]=l,y=n(e=>e.camera),b=t.waveMagnitude;(0,k.useEffect)(()=>{let e=`ghost:${t.ghostIndex}`;return _(e,T(t)),()=>_(e,null)},[t]);let x=(0,k.useMemo)(()=>{let[e,t,n]=s,r=e+L,i=n+L,a=Math.round(r/8),o=Math.round(i/8);return a=Math.max(0,Math.min(2040,a)),o=Math.max(0,Math.min(2040,o)),[a*8,t,o*8]},[s]),[S,w]=(0,k.useState)(()=>B(z(y.position.x),z(y.position.z))),E=(0,k.useRef)({x:z(y.position.x),z:z(y.position.z)});r(()=>{let e=z(y.position.x),t=z(y.position.z),n=E.current;(n.x!==e||n.z!==t)&&(n.x=e,n.z=t,w(B(e,t)))});let D=t.surfaceName||`liquidTiles/BlueWater`,O=t.envMapName||void 0,A=t.surfaceOpacity,M=t.envMapIntensity,N=(0,k.useMemo)(()=>{let[e,t]=R(u,v),n=new c(u,v,e,t);return n.rotateX(-Math.PI/2),n.translate(u/2,h,v/2),n},[u,h,v]);return(0,k.useEffect)(()=>()=>{N.dispose()},[N]),(0,F.jsxs)(`group`,{quaternion:o,children:[a&&(0,F.jsx)(j,{args:l,position:[s[0]+u/2,s[1]+h/2,s[2]+v/2],children:(0,F.jsx)(`meshBasicMaterial`,{color:`#00fbff`,wireframe:!0})}),i&&(0,F.jsx)(`group`,{position:[s[0]+u/2,s[1]+h/2,s[2]+v/2],children:(0,F.jsx)(C,{size:[u,h,v]})}),(0,F.jsx)(k.Suspense,{fallback:S.map(([e,t])=>{let n=x[0]+e*I-L,r=x[2]+t*I-L;return(0,F.jsx)(`mesh`,{geometry:N,position:[n,x[1],r],children:(0,F.jsx)(`meshStandardMaterial`,{color:`#00fbff`,transparent:!0,opacity:.4,wireframe:!0,side:2})},`${e},${t}`)}),children:(0,F.jsx)(U,{reps:S,basePosition:x,surfaceGeometry:N,surfaceTexture:D,envMapTexture:O,opacity:A,waveMagnitude:b,envMapIntensity:M,fluid:t})})]})}),U=(0,k.memo)(function({reps:e,basePosition:t,surfaceGeometry:n,surfaceTexture:o,envMapTexture:s,opacity:c,waveMagnitude:l,envMapIntensity:d,fluid:f}){let p=y(o),m=y(s??`special/lush_env`,{BASE_PATH:`/static/t2-maps/`,GAME_ASSETS_BASE_URL:`/t2-map-data/base/`,LOG_LEVEL:`warn`,RELAY_URL:``,DEMOS_BASE_URL:``,CAST_BASE_URL:``}.BASE_PATH+`black.png`),g=S(),[_,x]=O([p,m],e=>{(Array.isArray(e)?e:[e]).forEach(e=>{b(e,{anisotropy:g}),e.colorSpace=``,e.wrapS=a,e.wrapT=a})}),{animationEnabled:C}=u(),w=(0,k.useMemo)(()=>P({opacity:c,waveMagnitude:l,envMapIntensity:d,baseTexture:_,envMapTexture:x}),[c,l,d,_,x]);E(w,f);let T=(0,k.useRef)(0),D=(0,k.useRef)(null),A=(0,k.useRef)(new i),j=(0,k.useRef)(null),M=(0,k.useRef)(null);return r((n,r)=>{C?(T.current+=h(r),w.uniforms.uTime.value=T.current):(T.current=0,w.uniforms.uTime.value=0),v(T.current);let i=D.current;if(!i||i===j.current&&e===M.current)return;j.current=i,M.current=e;let a=A.current;for(let n=0;n<e.length;n++){let[r,o]=e[n],s=t[0]+r*I-L,c=t[2]+o*I-L;a.makeTranslation(s,t[1],c),i.setMatrixAt(n,a)}i.count=e.length,i.instanceMatrix.needsUpdate=!0}),(0,k.useEffect)(()=>()=>{w.dispose()},[w]),(0,F.jsx)(`instancedMesh`,{ref:D,args:[n,w,9],frustumCulled:!1,renderOrder:-1})});export{H as WaterBlock,V as WaterMaterial};