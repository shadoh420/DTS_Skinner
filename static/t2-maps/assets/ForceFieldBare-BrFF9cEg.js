import{i as e,t}from"./react-SIfiwpqq.js";import{f as n}from"./events-156d8d12.esm-D-eg-5DH.js";import{Ea as r,La as i,Ro as a,Tt as o,ut as s,xs as c,zo as l}from"./imageFileList-ChpBEnRl.js";import{c as u}from"./coordinates-mhGrzui6.js";import{s as d}from"./engineStore-BcQJtEg0.js";import{i as f}from"./cameraTourStore-Bex6igXg.js";import{m as p,u as m}from"./worldCollision-Dq90vdRv.js";import{f as h}from"./loaders-Hjx8vIH_.js";import{t as g}from"./DebugBounds-CibpD_iy.js";import{r as _}from"./globalFogUniforms-Ds1aGA7A.js";import{r as v}from"./placement-C0Chh4ls.js";import{t as y}from"./DebugSuspense-EJCeDBb4.js";import{t as b}from"./Texture-JVwug-cK.js";var x=e(t(),1),S=`
varying vec2 vUv;

void main() {
  vUv = uv;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`,C=`
uniform vec3 fogColor;
uniform float fieldHaze;

uniform sampler2D frame0;
uniform sampler2D frame1;
uniform sampler2D frame2;
uniform sampler2D frame3;
uniform sampler2D frame4;
uniform int currentFrame;
uniform float vScroll;
uniform vec2 uvScale;
uniform vec3 tintColor;
uniform vec3 powerOffColor;
uniform float opacity;
uniform float powerOffOpacity;
uniform float fieldAlpha;
uniform float opacityFactor;

varying vec2 vUv;

void main() {
  // Scale and scroll UVs
  vec2 scrolledUv = vec2(vUv.x * uvScale.x, vUv.y * uvScale.y + vScroll);

  // Sample the current frame
  vec4 texColor;
  if (currentFrame == 0) {
    texColor = texture2D(frame0, scrolledUv);
  } else if (currentFrame == 1) {
    texColor = texture2D(frame1, scrolledUv);
  } else if (currentFrame == 2) {
    texColor = texture2D(frame2, scrolledUv);
  } else if (currentFrame == 3) {
    texColor = texture2D(frame3, scrolledUv);
  } else {
    texColor = texture2D(frame4, scrolledUv);
  }

  // Open/close fade: color × alpha + powerOffColor × (1 − alpha), same
  // for the translucency.
  vec3 fieldColor = mix(powerOffColor, tintColor, fieldAlpha);
  float translucency = mix(powerOffOpacity, opacity, fieldAlpha) * opacityFactor;

  // Engine haze (ForceFieldBare::renderObject 0x676050): one
  // getHazeAndFog value for the whole object, computed per frame by the
  // component; glColor blends toward the fog color and the alpha is
  // scaled by 1 - haze (the constant at 0x7b9894 is 0), so an additive
  // field fades out into the fog instead of adding the fog colour.
  // The fog color arrives linear while this shader works in the textures'
  // raw sRGB values (sRGBTransferOETF comes from the colorspace chunk
  // Three prepends to every fragment).
  vec3 hazeColor = sRGBTransferOETF(vec4(fogColor, 1.0)).rgb;
  fieldColor = mix(fieldColor, hazeColor, fieldHaze);
  translucency *= 1.0 - fieldHaze;

  // Tribes 2 GL_MODULATE: output = texture * vertexColor
  // No gamma correction - textures use NoColorSpace and values pass through
  // directly to display, matching how WaterBlock handles sRGB textures.
  gl_FragColor = vec4(texColor.rgb * fieldColor, translucency);
}
`;function w(e,t,n){return e*n+t*(1-n)}function T({textures:e,scale:t,umapping:n,vmapping:r,color:s,powerOffColor:c,baseTranslucency:l,powerOffTranslucency:u}){let d=[...t].sort((e,t)=>t-e),f=new a(d[0]*n,d[1]*r),p=e[0];return new i({uniforms:{frame0:{value:p},frame1:{value:e[1]??p},frame2:{value:e[2]??p},frame3:{value:e[3]??p},frame4:{value:e[4]??p},currentFrame:{value:0},vScroll:{value:0},uvScale:{value:f},tintColor:{value:new o(...s)},powerOffColor:{value:new o(...c)},opacity:{value:l},powerOffOpacity:{value:u},fieldAlpha:{value:1},opacityFactor:{value:1},fogColor:{value:new o},fogNear:{value:1},fogFar:{value:2e3},fieldHaze:{value:0}},vertexShader:S,fragmentShader:C,transparent:!0,blending:2,side:2,depthWrite:!1,fog:!0})}var E=c(),D=new l;function O(e){e.wrapS=e.wrapT=r,e.colorSpace=``,e.flipY=!1,e.needsUpdate=!0}function k(e){let t=(0,x.useMemo)(()=>{let[t,n,r]=e,i=new s(t,n,r);return i.translate(t/2,n/2,r/2),i},[e]);return(0,x.useEffect)(()=>()=>t.dispose(),[t]),t}function A(e){return e.fieldAlpha??+!e.fieldOpen}function j(e,t){let[n,r,i]=e.dimensions;return n>0&&r>0&&i>0&&w(e.baseTranslucency,e.powerOffTranslucency,t)>0}function M({entity:e}){let t=e.forceFieldData,r=k(t.dimensions),i=(0,x.useRef)(null),a=(0,x.useMemo)(()=>({closed:new o(...t.color),open:new o(...t.powerOffColor)}),[t.color,t.powerOffColor]);return n(()=>{let n=i.current;if(!n)return;let r=A(e),o=n.material;o.color.copy(a.open).lerp(a.closed,r),o.opacity=w(t.baseTranslucency,t.powerOffTranslucency,r)*1,n.visible=j(t,r)}),(0,E.jsx)(`mesh`,{ref:i,geometry:r,renderOrder:1,children:(0,E.jsx)(`meshBasicMaterial`,{transparent:!0,blending:2,side:2,depthWrite:!1,fog:!1})})}function N({entity:e}){let t=e.forceFieldData,r=t.dimensions,{animationEnabled:i}=u(),a=k(r),o=(0,x.useRef)(null),s=(0,x.useMemo)(()=>t.textures.map(e=>h(e)),[t.textures]),c=b(s,e=>{e.forEach(e=>O(e))}),l=(0,x.useMemo)(()=>T({textures:c,scale:r,umapping:t.umapping,vmapping:t.vmapping,color:t.color,powerOffColor:t.powerOffColor,baseTranslucency:t.baseTranslucency,powerOffTranslucency:t.powerOffTranslucency}),[c,r,t]);(0,x.useEffect)(()=>()=>l.dispose(),[l]);let f=(0,x.useRef)(0);return n((n,r)=>{let a=A(e);l.uniforms.fieldAlpha.value=a;let s=o.current;if(s){s.visible=j(t,a);let e=n.scene.fog;s.getWorldPosition(D),l.uniforms.fieldHaze.value=e?_(D.distanceTo(n.camera.position),D.y,e.near,e.far):0}if(!i){f.current=0,l.uniforms.currentFrame.value=0,l.uniforms.vScroll.value=0;return}f.current+=d(r),l.uniforms.currentFrame.value=Math.round(f.current*t.framesPerSec)%t.numFrames,l.uniforms.vScroll.value=f.current*t.scrollSpeed}),(0,E.jsx)(`mesh`,{ref:o,geometry:a,material:l,renderOrder:1})}function P({entity:e}){let t=e.forceFieldData,n=t.dimensions,r=f(e.id);return(0,x.useEffect)(()=>{let t=v(e);if(t)return m(e.id,t.matrix,t.box,t.enabled),()=>p(e.id)},[e]),t.textures.length===0?(0,E.jsx)(M,{entity:e}):(0,E.jsxs)(E.Fragment,{children:[(0,E.jsx)(y,{name:`ForceField`,fallback:(0,E.jsx)(M,{entity:e}),children:(0,E.jsx)(N,{entity:e})}),r&&n&&(0,E.jsx)(g,{size:n})]})}export{P as ForceFieldBare};