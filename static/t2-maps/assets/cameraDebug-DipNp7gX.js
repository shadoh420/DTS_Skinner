import{t as e}from"./react-SIfiwpqq.js";import{$t as t,Ao as n,Co as r,Ea as i,Fi as a,Jt as o,La as s,Tr as c,ln as l,po as u,pt as d,yn as f}from"./imageFileList-ChpBEnRl.js";import{d as p}from"./engineStore-BcQJtEg0.js";import{r as m,t as h}from"./cameraTourStore-Bex6igXg.js";import{r as g,t as _}from"./commandCircuitStore-C0OYI5iA.js";import{n as v,o as y}from"./demoDirectorStore-ayN-yXuw.js";import{l as b}from"./watchFollow-S5JV5fZO.js";import{N as x,y as S}from"./shockwaveRequests-Dya3C3bV.js";import{f as C}from"./loaders-Hjx8vIH_.js";import{a as w}from"./fogShader-BooWJJSS.js";import{v as T}from"./shapeMaterial-_1h7Lqp7.js";import{t as ee}from"./Texture-JVwug-cK.js";var te={perspective:null,ortho:null};function E(e,t,n){return e?`tour`:t?`director`:n?`commandCircuit`:`input`}function D(){return E(h.getState().animation!==null,v.getState().status===`playing`,_.getState().active)}function ne(){return E(m(e=>e.animation!==null),y(e=>e.status===`playing`),g(e=>e.active))}function re(e){return e&&p.getState().cameraMode===`orbitOverride`&&D()===`input`&&b()}var O=Math.PI/180,k=-9.81,A=[0,0,0],j=Math.PI/18e4,M=1/100,N=-1e3,P=50,F=5,I=5,ie=10;function L(e,t,n){let r=e[t];return typeof r==`number`&&Number.isFinite(r)?r:n}function R(e,t,n){let r=e[t];return typeof r==`boolean`?r:typeof r==`number`?r!==0:n}function ae(e){let t=e.keys,n=[];if(Array.isArray(t)&&t.length>0)for(let e=0;e<t.length&&e<4;e++){let r=t[e];n.push({r:r.r??1,g:r.g??1,b:r.b??1,a:r.a??1,size:(r.size??1/P)*P,time:e===0?0:r.time??1})}n.length===0&&n.push({r:1,g:1,b:1,a:1,size:1,time:0}),n.length<2&&n.push({...n[0],time:1});let r=``;if(typeof e.textureName==`string`&&e.textureName)r=e.textureName;else{let t=e.textures;Array.isArray(t)&&t.length>0&&t[0]&&(r=t[0])}return{dragCoefficient:L(e,`dragCoefficient`,0)*I,windCoefficient:L(e,`windCoefficient`,1),gravityCoefficient:L(e,`gravityCoefficient`,0)*ie,inheritedVelFactor:L(e,`inheritedVelFactor`,0),constantAcceleration:L(e,`constantAcceleration`,0),lifetimeMS:L(e,`lifetimeMS`,31)<<F,lifetimeVarianceMS:L(e,`lifetimeVarianceMS`,0)<<F,spinSpeed:L(e,`spinSpeed`,0),spinRandomMin:L(e,`spinRandomMin`,1e3)+N,spinRandomMax:L(e,`spinRandomMax`,1e3)+N,useInvAlpha:R(e,`useInvAlpha`,!1),keys:n,textureName:r}}function z(e,t){let n,r=e.particles;if(Array.isArray(r)){for(let e of r)if(typeof e==`number`&&(n=t(e),n))break}return n?{ejectionPeriodMS:L(e,`ejectionPeriodMS`,100),periodVarianceMS:L(e,`periodVarianceMS`,0),ejectionVelocity:L(e,`ejectionVelocity`,200)*M,velocityVariance:L(e,`velocityVariance`,100)*M,ejectionOffset:L(e,`ejectionOffset`,0)*M,thetaMin:L(e,`thetaMin`,0),thetaMax:L(e,`thetaMax`,90),phiReferenceVel:L(e,`phiReferenceVel`,0),phiVariance:L(e,`phiVariance`,360),overrideAdvances:R(e,`overrideAdvances`,!1),orientParticles:R(e,`orientParticles`,!1),orientOnVelocity:R(e,`orientOnVelocity`,!0),useEmitterColors:R(e,`useEmitterColors`,!1),lifetimeMS:L(e,`lifetimeMS`,0)<<F,lifetimeVarianceMS:L(e,`lifetimeVarianceMS`,0)<<F,particles:ae(n)}:null}function B(e,t,n=Math.random){return e+n()*(t-e)}function V(e,t,n=Math.random){return e+(n()*2-1)*t}function H(e,t,n){let r,i,a;Math.abs(n)<.9?(r=t,i=-e,a=0):(r=-n,i=0,a=e);let o=Math.sqrt(r*r+i*i+a*a);return o<1e-8?[1,0,0]:[r/o,i/o,a/o]}function U(e,t,n,r,i,a,o){let s=Math.cos(o),c=Math.sin(o),l=e*r+t*i+n*a,u=i*n-a*t,d=a*e-r*n,f=r*t-i*e;return[e*s+u*c+r*l*(1-s),t*s+d*c+i*l*(1-s),n*s+f*c+a*l*(1-s)]}function W(e,t){for(let n=1;n<e.length;n++)if(e[n].time>=t){let r=e[n-1],i=e[n],a=i.time-r.time,o=a>0?(t-r.time)/a:0;return{r:r.r+(i.r-r.r)*o,g:r.g+(i.g-r.g)*o,b:r.b+(i.b-r.b)*o,a:r.a+(i.a-r.a)*o,size:r.size+(i.size-r.size)*o}}let n=e[e.length-1];return{r:n.r,g:n.g,b:n.b,a:n.a,size:n.size}}function G(e,t,n){return[e[0]+(t[0]-e[0])*n,e[1]+(t[1]-e[1])*n,e[2]+(t[2]-e[2])*n]}var K=class{data;particles=[];revision=0;maxParticles;internalClock=0;nextParticleTime=0;elapsedMS=0;lifetimeMS;killed=!1;colorKeys;colors;worldGravity=k;random;constructor(e,t=256,n=Math.random){this.random=n,this.data=e,this.maxParticles=t;let r=e.lifetimeMS;e.lifetimeVarianceMS>0&&(r+=Math.round(V(0,e.lifetimeVarianceMS,this.random))),this.lifetimeMS=r}setColors(e){this.data.useEmitterColors&&e!==this.colors&&(this.colors=e,this.colorKeys=this.data.particles.keys.map((t,n)=>({...t,...e[n]??{r:1,g:1,b:1,a:0}})))}emitRadial(e,t,n,r=[0,0,1],i=A,a=this.random){let o=Math.hypot(...r),s=o>0?r.map(e=>e/o):[0,0,1],c=Math.abs(s[2])<.98?[s[1],-s[0],0]:[-s[2],0,s[0]],l=Math.hypot(...c);for(let e=0;e<3;e++)c[e]/=l;let u=[s[1]*c[2]-s[2]*c[1],s[2]*c[0]-s[0]*c[2],s[0]*c[1]-s[1]*c[0]];for(let r=0;r<n&&this.particles.length<this.maxParticles;r++){let n=t*(1-2*a()),r=t*(1-2*a()),o=t*a(),l=u.map((e,t)=>e*n+c[t]*r+s[t]*o),d=Math.hypot(...l),f=d>0?l.map(e=>e/d):s;this.addParticle(l.map((t,n)=>t+e[n]),f,i,s,a)}}emitBurst(e,t,n=[0,0,1]){for(let r=0;r<t&&this.particles.length<this.maxParticles;r++)this.addParticle(e,n,A)}emitPeriodic(e,t,n,r=[0,0,1],i=A){if(this.killed||this.lifetimeExpired())return;let a=Math.round(n);if(a<=0)return;let o=0;if(this.nextParticleTime>0){if(this.nextParticleTime>a){this.nextParticleTime-=a,this.internalClock+=a;return}o=this.nextParticleTime,this.nextParticleTime=0,this.internalClock+=o,this.emitAlong(e,t,o,a,r,i,!0)}for(;o<a;){let n=this.data.ejectionPeriodMS;if(this.data.periodVarianceMS>0&&(n+=Math.round(V(0,this.data.periodVarianceMS,this.random))),n=Math.max(1,n),o+n>a){this.nextParticleTime=o+n-a,this.internalClock+=a-o;break}this.internalClock+=n,o+=n,this.emitAlong(e,t,o,a,r,i,!this.data.overrideAdvances)}}update(e){let t=Math.round(e);if(!(t<=0)){this.elapsedMS+=t,this.particles.length&&this.revision++;for(let e=this.particles.length-1;e>=0;e--){let n=this.particles[e];if(n.currentAge+=t,n.currentAge>=n.totalLifetime){this.particles[e]=this.particles[this.particles.length-1],this.particles.pop();continue}this.updateParticle(n,t)}}}isDead(){return(this.killed||this.lifetimeExpired())&&this.particles.length===0}kill(){this.killed=!0}lifetimeExpired(){return this.lifetimeMS>0&&this.elapsedMS>this.lifetimeMS}emitAlong(e,t,n,r,i,a,o){if(this.particles.length>=this.maxParticles)return;let s=this.addParticle(G(e,t,n/r),i,a),c=r-n;if(!(!o||c<=0)){if(s.totalLifetime<c){this.particles.pop();return}this.updateParticle(s,c)}}updateParticle(e,t){let n=t/1e3,r=this.data.particles,i=r.dragCoefficient,a=r.gravityCoefficient,o=e.acc[0]-e.vel[0]*i,s=e.acc[1]-e.vel[1]*i,c=e.acc[2]-e.vel[2]*i+this.worldGravity*a;e.vel[0]+=o*n,e.vel[1]+=s*n,e.vel[2]+=c*n,e.pos[0]+=e.vel[0]*n,e.pos[1]+=e.vel[1]*n,e.pos[2]+=e.vel[2]*n;let l=W(this.colorKeys??r.keys,e.currentAge/e.totalLifetime);e.r=l.r,e.g=l.g,e.b=l.b,e.a=l.a,e.size=l.size,e.currentSpin=e.spinSpeed*e.currentAge*j}addParticle(e,t,n,r,i=this.random){let a=this.data,o=a.particles,s=t[0],c=t[1],l=t[2],u=r??H(s,c,l),d=(a.thetaMin+i()*(a.thetaMax-a.thetaMin))*O,f=(this.internalClock/1e3*a.phiReferenceVel+i()*a.phiVariance)*O;[s,c,l]=U(s,c,l,u[0],u[1],u[2],d),[s,c,l]=U(s,c,l,t[0],t[1],t[2],f);let p=Math.sqrt(s*s+c*c+l*l);p>1e-8&&(s/=p,c/=p,l/=p);let m=V(a.ejectionVelocity,a.velocityVariance,i),h=[e[0]+s*a.ejectionOffset,e[1]+c*a.ejectionOffset,e[2]+l*a.ejectionOffset],g=o.inheritedVelFactor,_=[s*m+n[0]*g,c*m+n[1]*g,l*m+n[2]*g],v=o.constantAcceleration,y=[_[0]*v,_[1]*v,_[2]*v],b=o.lifetimeMS;o.lifetimeVarianceMS>0&&(b+=Math.round(V(0,o.lifetimeVarianceMS,i))),b=Math.max(1,b);let x=o.spinSpeed+B(o.spinRandomMin,o.spinRandomMax,i),S=this.colorKeys?.[0]??o.keys[0],C={pos:h,vel:_,acc:y,orientDir:[s,c,l],currentAge:0,totalLifetime:b,dataIndex:0,spinSpeed:x,currentSpin:0,r:S.r,g:S.g,b:S.b,a:S.a,size:S.size};return this.particles.push(C),this.revision++,C}},q=`
// 'position' is auto-declared by Three.js for ShaderMaterial.
attribute vec4 particleColor;
attribute float particleSize;
attribute float particleSpin;
attribute vec2 quadCorner; // (-0.5,-0.5) to (0.5,0.5)
attribute vec3 orientDir;

uniform bool uOrientParticles;
// cameraPosition is a built-in Three.js uniform.

varying vec2 vUv;
varying vec4 vColor;

#ifdef INTERPOLATE_PARTICLES
attribute vec3 particleVelocity;
attribute vec3 particleAcceleration;
attribute float particleSpinRate;
uniform float renderDelta;
#endif

void main() {
  vUv = quadCorner + 0.5; // [0,1] range
  vColor = particleColor;
  vec3 renderPosition = position;
  float renderSpin = particleSpin;
#ifdef INTERPOLATE_PARTICLES
  renderPosition += (particleVelocity + particleAcceleration * renderDelta) * renderDelta;
  renderSpin += particleSpinRate * renderDelta;
#endif

  if (uOrientParticles) {
    if (length(orientDir) < 0.0001) {
      // V12: don't render oriented particles with zero velocity.
      gl_Position = vec4(0.0, 0.0, 0.0, 0.0);
      return;
    }
    // V12 oriented particle: quad aligned along direction, facing camera.
    vec3 worldPos = (modelMatrix * vec4(renderPosition, 1.0)).xyz;
    vec3 dir = normalize(orientDir);
    vec3 dirFromCam = worldPos - cameraPosition;
    vec3 crossDir = normalize(cross(dirFromCam, dir));

    // V12 maps U along dir (velocity) — match by using quadCorner.x for dir.
    vec3 offset = dir * quadCorner.x + crossDir * quadCorner.y;
    worldPos += offset * particleSize;

    gl_Position = projectionMatrix * viewMatrix * vec4(worldPos, 1.0);
  } else {
    // Standard camera-facing billboard.
    vec3 viewPos = (modelViewMatrix * vec4(renderPosition, 1.0)).xyz;

    // Apply spin rotation to quad corner.
    float c = cos(renderSpin);
    float s = sin(renderSpin);
    vec2 rotated = vec2(
      c * quadCorner.x - s * quadCorner.y,
      s * quadCorner.x + c * quadCorner.y
    );

    // Offset in view space (camera-facing billboard).
    viewPos.xy += rotated * particleSize;

    gl_Position = projectionMatrix * vec4(viewPos, 1.0);
  }
}
`,oe=`
uniform sampler2D particleTexture;
uniform bool hasTexture;
uniform float debugOpacity;

varying vec2 vUv;
varying vec4 vColor;

void main() {
  if (hasTexture) {
    vec4 texColor = texture2D(particleTexture, vUv);
    gl_FragColor = texColor * vColor;
  } else {
    gl_FragColor = vColor;
  }
  gl_FragColor.a *= debugOpacity;
}
`,se=new Float32Array([-.5,-.5,.5,-.5,.5,.5,-.5,.5]),J=new Map,Y=new Set,X=new o(new Uint8Array([255,255,255,255]),1,1,a,n);X.needsUpdate=!0;function ce(e){S(e,``),e.wrapS=i,e.wrapT=i,e.generateMipmaps=!0,e.minFilter=c}function le(e){if(!e)return X;let t=J.get(e);if(t)return t;try{let t=C(e),n=new u;return ce(n),n.source=w(t,e=>{n.source=e.source,n.needsUpdate=!0,Y.add(n)}).source,J.set(e,n),n}catch{return X}}function ue(e,t=!1){let n=new d,i=e*4,a=e*6,o=new Float32Array(i*2);for(let t=0;t<e;t++)o.set(se,t*8);let s=new Uint16Array(a);for(let t=0;t<e;t++){let e=t*4,n=t*6;s[n]=e,s[n+1]=e+1,s[n+2]=e+2,s[n+3]=e,s[n+4]=e+2,s[n+5]=e+3}let c=new Float32Array(i*3),u=new Float32Array(i*4),p=new Float32Array(i),m=new Float32Array(i),h=new Float32Array(i*3);n.setIndex(new r(s,1)),n.setAttribute(`quadCorner`,new f(o,2)),n.setAttribute(`position`,new f(c,3)),n.setAttribute(`particleColor`,new f(u,4)),n.setAttribute(`particleSize`,new f(p,1)),n.setAttribute(`particleSpin`,new f(m,1)),n.setAttribute(`orientDir`,new f(h,3)),t&&(n.setAttribute(`particleVelocity`,new f(new Float32Array(i*3),3)),n.setAttribute(`particleAcceleration`,new f(new Float32Array(i*3),3)),n.setAttribute(`particleSpinRate`,new f(new Float32Array(i),1)));for(let[e,t]of Object.entries(n.attributes))e!==`quadCorner`&&t.setUsage(l);return n.setDrawRange(0,0),n}function de(e,t,n=!1,r=!1){let i=Y.has(e);return new s({defines:r?{INTERPOLATE_PARTICLES:1}:{},vertexShader:q,fragmentShader:oe,uniforms:{particleTexture:{value:i?e:X},hasTexture:{value:!0},debugOpacity:{value:1},uOrientParticles:{value:n},renderDelta:{value:0}},transparent:!0,depthWrite:!1,depthTest:!0,side:2,blending:t?1:2})}function fe(e){if(e.uploadedRevision===e.emitter.revision)return;e.uploadedRevision=e.emitter.revision;let t=e.emitter.particles,n=e.geometry,r=n.getAttribute(`position`),i=n.getAttribute(`particleColor`),a=n.getAttribute(`particleSize`),o=n.getAttribute(`particleSpin`),s=n.getAttribute(`orientDir`),c=n.getAttribute(`particleVelocity`),l=n.getAttribute(`particleAcceleration`),u=n.getAttribute(`particleSpinRate`),d=r.array,f=i.array,p=a.array,m=o.array,h=s.array,g=Math.min(t.length,e.emitter.maxParticles),_=e.emitter.data.orientOnVelocity;for(let n=0;n<g;n++){let r=t[n],i=r.pos[1],a=r.pos[2],o=r.pos[0],s=_?r.vel:r.orientDir,g=s[1],v=s[2],y=s[0],b=r.r,x=r.g,S=r.b,C=r.a;for(let t=0;t<4;t++){let s=n*4+t,_=s*3;d[_]=i,d[_+1]=a,d[_+2]=o;let w=s*4;f[w]=b,f[w+1]=x,f[w+2]=S,f[w+3]=C;let T=s*3;if(h[T]=g,h[T+1]=v,h[T+2]=y,p[s]=r.size,m[s]=r.currentSpin,c&&l&&u){let t=e.emitter.data.particles;c.setXYZ(s,r.vel[1],r.vel[2],r.vel[0]),l.setXYZ(s,r.acc[1]-r.vel[1]*t.dragCoefficient,r.acc[2]-r.vel[2]*t.dragCoefficient+e.emitter.worldGravity*t.gravityCoefficient,r.acc[0]-r.vel[0]*t.dragCoefficient),u.setX(s,r.spinSpeed*Math.PI/180)}}}let v=g;Z(r,v*4*3),Z(i,v*4*4),Z(a,v*4),Z(o,v*4),Z(s,v*4*3),c&&l&&u&&(Z(c,v*12),Z(l,v*12),Z(u,v*4)),n.setDrawRange(0,g*6)}function Z(e,t){e.clearUpdateRanges(),t>0&&(e.addUpdateRange(0,t),e.needsUpdate=!0)}function pe(e){switch(e.renderType){case`TerrainBlock`:case`InteriorInstance`:case`Sky`:case`Sun`:case`WaterBlock`:case`MissionArea`:return!0;default:return!1}}e();var Q=0,$=x(e=>(t.onStart=(t,n,r)=>{e({active:!0,item:t,loaded:n,total:r,progress:(n-Q)/(r-Q)*100})},t.onLoad=()=>{e({active:!1})},t.onError=t=>e(e=>({errors:[...e.errors,t]})),t.onProgress=(t,n,r)=>{n===r&&(Q=r),e({active:!0,item:t,loaded:n,total:r,progress:(n-Q)/(r-Q)*100||100})},{errors:[],active:!1,progress:0,item:``,loaded:0,total:0})),me=class extends T{parse(e){let t=super.parse(e),n=new Set(t.surfaceMeshes.map(e=>e.material.resourcePath));for(let e of n)ee.preload(C(e));return t}};`Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec`.split(` `);var he=`MapGenius`;function ge(e){return e?.toLowerCase()===he.toLowerCase()}var _e={targetId:null,frozen:!1,handOff:!1,jump:0,active:!1},ve={shot:``,panState:``,panActive:!1,travel:`idle`,travelT:0};export{te as _,$ as a,de as c,fe as d,K as f,ne as g,D as h,me as i,le as l,re as m,_e as n,pe as o,z as p,ge as r,ue as s,ve as t,Y as u};