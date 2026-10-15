/* Doom 3 parametric particles: idRenderModelPrt::InstantiateDynamicModel + idParticleStage, in emitter-local space. */
(function(root) {
  'use strict';
  const MAX_RAND=0x7fff;
  class Random {
    // idRandom: 69069 LCG, 15-bit results.
    constructor(seed=0){this.seed=seed>>>0;}
    int(){this.seed=(Math.imul(69069,this.seed)+1)>>>0;return this.seed&MAX_RAND;}
    float(){return this.int()/(MAX_RAND+1);}
    cfloat(){return 2*(this.float()-.5);}
    copy(){return new Random(this.seed);}
  }
  const evalParm=(p,frac,table)=>p.length>2?table(p[2],frac):p[0]+frac*(p[1]-p[0]);
  const integrate=(p,frac)=>p.length>2?0:(p[0]+frac*(p[1]-p[0])*.5)*frac;
  function quadsPerParticle(s) {
    return (s.orientation==='aimed'?1+Math.trunc(s.orientationParms[0]):1)*(s.animationFrames>1?2:1);
  }
  function origin(s,g) {
    // idParticleStage::ParticleOrigin.
    const r=g.random,o=[0,0,0],d=s.distributionParms;
    if(s.customPath==='standard') {
      const rnd=()=>s.randomDistribution?r.cfloat():1;
      let radiusSqr;
      if(s.distribution==='cylinder') {
        const a=rnd()*Math.PI*2;o[0]=Math.sin(a);o[1]=Math.cos(a);o[2]=rnd();
        if(d[3]>0){radiusSqr=o[0]*o[0]+o[1]*o[1];if(radiusSqr<d[3]*d[3]){const f=Math.sqrt(radiusSqr)/d[3],k=(d[3]+f*(1-d[3]))/f;o[0]*=k;o[1]*=k;}}
      } else if(s.distribution==='sphere') {
        if(s.randomDistribution){do{o[0]=r.cfloat();o[1]=r.cfloat();o[2]=r.cfloat();radiusSqr=o[0]*o[0]+o[1]*o[1]+o[2]*o[2];}while(radiusSqr>1);}
        else{o.fill(1);radiusSqr=3;}
        if(d[3]>0&&radiusSqr<d[3]*d[3]){const f=Math.sqrt(radiusSqr)/d[3],k=(d[3]+f*(1-d[3]))/f;for(let i=0;i<3;i++)o[i]*=k;}
      } else {for(let i=0;i<3;i++)o[i]=rnd();}
      for(let i=0;i<3;i++)o[i]=o[i]*d[i]+s.offset[i];
      let dir;
      if(s.direction==='outward'){const l=Math.hypot(...o)||1;dir=o.map(v=>v/l);dir[2]+=s.directionParms[0];}
      else{const a1=r.cfloat()*s.directionParms[0]*Math.PI/180,a2=r.cfloat()*Math.PI;dir=[Math.sin(a1)*Math.cos(a2),Math.sin(a1)*Math.sin(a2),Math.cos(a1)];}
      const speed=integrate(s.speed,g.frac);for(let i=0;i<3;i++)o[i]+=dir[i]*speed*s.time;
    } else {
      const p=s.customPathParms;
      if(s.customPath==='helix'){const s1=r.cfloat(),s2=r.cfloat(),a=r.float()*Math.PI*2+p[3]*s1*g.age;o[0]=Math.cos(a)*p[0];o[1]=Math.sin(a)*p[1];o[2]=r.float()*p[2]+p[4]*s2*g.age;}
      else if(s.customPath==='flies'){const s1=Math.min(1,Math.max(.4,r.cfloat()));Math.min(1,Math.max(.4,r.cfloat()));
        const a1=r.float()*Math.PI*2+p[0]*s1*g.age,a2=r.float()*Math.PI*2+p[1]*s1*g.age;
        o[0]=Math.cos(a1)*Math.cos(a2)*p[2];o[1]=Math.sin(a1)*Math.cos(a2)*p[2];o[2]=-Math.sin(a2)*p[2];}
      else if(s.customPath==='spherical'){const a=r.float()*Math.PI*2+p[1]*g.age;o[0]=Math.cos(a)*p[0];o[1]=Math.sin(a)*p[0];
        const l=Math.hypot(...o)||1;for(let i=0;i<3;i++)o[i]*=p[0]/l;}
      else if(s.customPath==='drip'){o[2]=-g.age*p[0];}
      for(let i=0;i<3;i++)o[i]+=s.offset[i];
    }
    // Emitter-local gravity, or world -Z expressed in emitter space (view.down).
    if(s.worldGravity)for(let i=0;i<3;i++)o[i]+=g.down[i]*s.gravity*g.age*g.age;else o[2]-=s.gravity*g.age*g.age;
    return o;
  }
  function emit(s,g,view,out,table,parms) {
    // idParticleStage::CreateParticle. view: emitter-local {left,up,forward}. Returns vertices written.
    let fade=1;
    if(g.frac<s.fadeIn)fade*=g.frac/s.fadeIn;
    if(1-g.frac<s.fadeOut)fade*=(1-g.frac)/s.fadeOut;
    if(s.fadeIndex){const f=(s.count-g.index)/s.count;if(f<s.fadeIndex)fade*=f/s.fadeIndex;}
    const color=[0,1,2,3].map(i=>Math.max(0,Math.min(255,Math.trunc(((s.entityColor?parms[i]:s.color[i])*fade+s.fadeColor[i]*(1-fade))*255)))/255);
    if(color.every(c=>c===0))return 0;
    const o=origin(s,g);
    let st0=0,width=1,frameFrac=0;
    if(s.animationFrames>1){width=1/s.animationFrames;const f=s.animationRate?g.age*s.animationRate:g.frac*s.animationFrames;st0=width*Math.trunc(f);frameFrac=f-Math.trunc(f);}
    const st=[[st0,0],[st0+width,0],[st0,1],[st0+width,1]];
    const size=evalParm(s.size,g.frac,table),aspect=evalParm(s.aspect,g.frac,table),start=out.count;
    const put=(p,uv)=>{out.position.set(p,out.count*3);out.uv.set(uv,out.count*2);out.color.set(color,out.count*4);out.count++;};
    if(s.orientation==='aimed') {
      const trails=Math.trunc(s.orientationParms[0]),trailTime=s.orientationParms[1]||.5,h=1/(1+trails),age=g.age;
      let step=o,stepLeft=[0,0,0],t=0;
      for(let i=0;i<=trails;i++) {
        g.random=g.originalRandom.copy();g.age=age-(i+1)*trailTime/(trails+1);g.frac=g.age/s.time;
        const old=origin(s,g);let up=step.map((v,k)=>v-old[k]);const f=view.forward,dd=up[0]*f[0]+up[1]*f[1]+up[2]*f[2];
        up=up.map((v,k)=>v-dd*f[k]);const l=Math.hypot(...up)||1;up=up.map(v=>v/l);
        const left=[up[1]*f[2]-up[2]*f[1],up[2]*f[0]-up[0]*f[2],up[0]*f[1]-up[1]*f[0]].map(v=>v*size);
        const a=i?stepLeft:left;
        put(step.map((v,k)=>v-a[k]),[st[0][0],t]);put(step.map((v,k)=>v+a[k]),[st[1][0],t]);
        put(old.map((v,k)=>v-left[k]),[st[2][0],t+h]);put(old.map((v,k)=>v+left[k]),[st[3][0],t+h]);
        t+=h;step=old;stepLeft=left;
      }
      g.age=age;g.frac=age/s.time;
    } else {
      let angle=s.angle||360*g.random.float();
      const move=integrate(s.rotation,g.frac)*s.time;angle+=g.index&1?move:-move;angle*=Math.PI/180;
      const c=Math.cos(angle),n=Math.sin(angle);let left,up;
      if(s.orientation==='z'){left=[n,c,0];up=[c,-n,0];}
      else if(s.orientation==='x'){left=[0,c,n];up=[0,-n,c];}
      else if(s.orientation==='y'){left=[c,0,n];up=[-n,0,c];}
      else{left=view.left.map((v,k)=>v*c+view.up[k]*n);up=view.up.map((v,k)=>v*c-view.left[k]*n);}
      left=left.map(v=>v*size);up=up.map(v=>v*size*aspect);
      put(o.map((v,k)=>v-left[k]+up[k]),st[0]);put(o.map((v,k)=>v+left[k]+up[k]),st[1]);
      put(o.map((v,k)=>v-left[k]-up[k]),st[2]);put(o.map((v,k)=>v+left[k]-up[k]),st[3]);
    }
    if(s.animationFrames>1) {
      // Cross-fade into the next frame with a second copy of the quads.
      const n=out.count-start;
      for(let i=0;i<n;i++){const from=start+i,to=out.count;
        out.position.copyWithin(to*3,from*3,from*3+3);out.uv.set([out.uv[from*2]+width,out.uv[from*2+1]],to*2);
        for(let k=0;k<4;k++){out.color[to*4+k]=out.color[from*4+k]*frameFrac;out.color[from*4+k]*=1-frameFrac;}out.count++;}
    }
    return out.count-start;
  }
  function stageCapacity(s){return s.count*quadsPerParticle(s)*4;}
  function simulate(s,ms,parms,view,out,table) {
    // One stage of idRenderModelPrt::InstantiateDynamicModel at renderView.time = ms.
    out.count=0;if(!s.cycleMsec)return 0;
    const stageAge=Math.trunc(ms+(parms[4]||0)*1000-s.timeOffset*1000),stageCycle=Math.trunc(stageAge/s.cycleMsec);
    const diversity=Math.trunc((parms[5]||0)*MAX_RAND),stop=parms[8]||0;
    const stepping=new Random(((stageCycle<<10)&MAX_RAND)^diversity),stepping2=new Random((((stageCycle-1)<<10)&MAX_RAND)^diversity);
    for(let index=0;index<s.count;index++) {
      stepping.int();stepping2.int();
      const particleAge=stageAge-Math.trunc(s.time*1000*s.bunching*index/s.count),cycle=Math.trunc(particleAge/s.cycleMsec);
      if(particleAge<0||cycle<0||(s.cycles&&cycle>=s.cycles))continue;
      const inCycle=particleAge-cycle*s.cycleMsec;
      if(stop&&ms-inCycle>=stop*1000)continue;
      const frac=inCycle/(s.time*1000);if(frac<0||frac>1)continue;
      const random=(cycle===stageCycle?stepping:stepping2).copy();
      emit(s,{index,random,originalRandom:random.copy(),frac,age:frac*s.time,down:view.down||[0,0,-1]},view,out,table,parms);
    }
    return out.count;
  }
  function quadIndex(vertices){const index=[];for(let i=0;i<vertices;i+=4)index.push(i,i+2,i+3,i,i+3,i+1);return index;}
  const api={Random,simulate,stageCapacity,quadIndex,quadsPerParticle};
  if(typeof module!=='undefined')module.exports=api;else root.doom3Particles=api;
})(typeof window!=='undefined'?window:globalThis);
