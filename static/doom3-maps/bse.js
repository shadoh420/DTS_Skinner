/* Quake 4 BSE spawner/emitter segments with sprite, line and oriented particles, in effect-local space.
 * Stateless: every particle alive at time t is rebuilt from seeded randoms (segment, cycle, index).
 * Semantics follow the GPL Quake 4 BSE recreation (rvSegment::Check, rvParticle spawn/evaluate/render). */
(function(root) {
  'use strict';
  function rng(...keys) {
    // mulberry32 over a key hash: stable per particle, independent of frame rate.
    let h=2166136261;for(const k of keys){h=Math.imul(h^(k|0),16777619);h^=h>>>13;}
    return ()=>{h=(h+0x6D2B79F5)|0;let t=Math.imul(h^(h>>>15),1|h);t=(t+Math.imul(t^(t>>>7),61|t))^t;return ((t^(t>>>14))>>>0)/4294967296;};
  }
  const between=(r,[a,b])=>a+(b-a)*r();
  function sample(d,n,r,index=0,count=1) {
    // BSE_SpawnDomains: point, line (one shared fraction; linearSpacing by index), box (surface: one face),
    // sphere (box-bounded; surface: shell), cylinder (along x; cone tapers), spiral treated as cylinder.
    if(!d)return null;
    const v=d.values,mins=Array.from({length:n},(_,i)=>v[i]??0),maxs=Array.from({length:n},(_,i)=>v[n+i]??mins[i]);
    const surface=d.flags.includes('surface');
    if(d.type==='point')return mins;
    if(d.type==='line'){const f=d.flags.includes('linearspacing')?(count>1?index/(count-1):0):r();return mins.map((m,i)=>m+f*(maxs[i]-m));}
    if(d.type==='box'){const out=mins.map((m,i)=>m+r()*(maxs[i]-m));if(surface&&n===3){const face=Math.min(5,Math.floor(r()*6)),axis=face>>1;out[axis]=face&1?maxs[axis]:mins[axis];}return out;}
    if(d.type==='sphere'){
      const dir=mins.map(()=>r()*2-1),l=Math.hypot(...dir)||1;
      return mins.map((m,i)=>(m+maxs[i])/2+(surface?1:r())*(maxs[i]-m)/2*dir[i]/l);
    }
    if(n<3)return mins.map((m,i)=>m+r()*(maxs[i]-m));
    const t=r()*(maxs[0]-mins[0]),taper=d.flags.includes('cone')&&maxs[0]!==mins[0]?t/(maxs[0]-mins[0]):1;
    let dy=r()*2-1,dz=r()*2-1;const l=Math.hypot(dy,dz)||1;dy/=l;dz/=l;const k=surface?1:r();
    return [mins[0]+t,(maxs[1]+mins[1])/2+(maxs[1]-mins[1])/2*taper*dy*k,(maxs[2]+mins[2])/2+(maxs[2]-mins[2])/2*taper*dz*k];
  }
  const SIZE={sprite:2,line:1,oriented:2},ROTATE={sprite:1,line:0,oriented:3};
  function effectPeriod(effect) {
    // An effect lasts until its last non-constant segment ends; looping effects restart then.
    return Math.max(.05,...effect.segments.filter(s=>!s.constant).map(s=>s.start[1]+s.duration[1]+(s.kind==='spawner'?s.particle.duration[1]:0)));
  }
  function capacity(segment) {
    const p=segment.particle,count=Math.max(...segment.count,1);
    const quads=segment.kind==='spawner'?Math.ceil(count)*3:Math.ceil(count*p.duration[1])+2;
    return Math.min(4096,quads)*4;
  }
  function simulate(effect,index,seconds,loop,view,out,table) {
    out.count=0;const s=effect.segments[index],p=s.particle,P=effectPeriod(effect),life=p.duration[1];
    const cycles=s.constant||!loop?[0]:Array.from({length:Math.min(4,Math.ceil(life/P)+1)},(_,i)=>Math.floor(seconds/P)-i).filter(c=>c>=0);
    for(const c of cycles) {
      const r0=rng(index,c,-1),start=c*P+between(r0,s.start),end=s.constant?Infinity:start+between(r0,s.duration);
      if(s.kind==='spawner') {
        const n=Math.round(between(r0,s.count));
        for(let k=0;k<n;k++)emit(s,p,start,seconds,rng(index,c,k),k,n,view,out,table);
      } else {
        const interval=1/Math.max(.001,(s.count[0]+s.count[1])/2),first=Math.max(0,Math.ceil((seconds-life-start)/interval)),last=Math.floor((Math.min(end,seconds)-start)/interval);
        for(let k=first;k<=last&&out.count+4<=out.position.length/3;k++)emit(s,p,start+k*interval,seconds,rng(index,c,k),k,0,view,out,table);
      }
      if(!loop&&seconds>=P+life)break;
    }
    return out.count;
  }
  function emit(s,p,birth,seconds,r,k,n,view,out,table) {
    if(out.count+4>out.position.length/3)return;
    const life=between(r,p.duration),age=seconds-birth;if(age<0||age>=life)return;
    const value=(name,size)=>{
      // Start domain, end domain (relative adds the start), motion envelope over the life (count -> rate).
      const a=sample(p.start[name],size,r,k,n);if(!a)return null;
      let b=sample(p.end[name],size,r,k,n);if(b&&p.end[name].flags.includes('relative'))b=b.map((v,i)=>v+a[i]);
      const env=p.motion[name];if(!b||!env&&!p.end[name])return a;
      const tableName=env?.table||'linear',rate=env?.rate?env.rate[0]:(env?.count?env.count[0]:1)/life,offset=env?.offset?.[0]||0;
      const f=table(tableName,age*rate+offset);return a.map((v,i)=>v+(b[i]-v)*f);
    };
    const kind=p.kind,size=value('size',SIZE[kind])||[1,1],tint=value('tint',3)||[1,1,1],fade=(value('fade',1)||[1])[0];
    const pos=sample(p.start.position,3,r,k,n)||[0,0,0],vel=sample(p.start.velocity,3,r,k,n)||[0,0,0],acc=sample(p.start.acceleration,3,r,k,n)||[0,0,0];
    const g=between(r,p.gravity)*1066,down=view.down||[0,0,-1];
    for(let i=0;i<3;i++)pos[i]+=vel[i]*age+.5*(acc[i]+down[i]*g)*age*age;
    const alpha=Math.max(0,Math.min(1,fade)),rgb=tint.map(v=>Math.max(0,Math.min(1,v))*(p.additive?alpha:1)),color=[...rgb,p.additive?alpha*alpha:alpha];
    const put=(x,uv)=>{out.position.set(x,out.count*3);out.uv.set(uv,out.count*2);out.color.set(color,out.count*4);out.count++;};
    const add=(a,b,f=1)=>a.map((v,i)=>v+b[i]*f);
    if(kind==='line') {
      const length=value('length',3)||[0,0,0],tip=add(pos,length),w=size[0];
      const side=[length[1]*view.forward[2]-length[2]*view.forward[1],length[2]*view.forward[0]-length[0]*view.forward[2],length[0]*view.forward[1]-length[1]*view.forward[0]];
      const l=Math.hypot(...side)||1,off=side.map(v=>v/l*w);
      put(add(pos,off,-1),[0,0]);put(add(pos,off),[1,0]);put(add(tip,off,-1),[0,1]);put(add(tip,off),[1,1]);
      return;
    }
    const turn=(value('rotate',ROTATE[kind]||1)||[0]).map(v=>v*Math.PI*2);
    if(kind==='oriented') {
      // Quad in the plane of the particle's angles (pitch, yaw, roll in turns).
      const [pi,ya,ro]=[turn[0]||0,turn[1]||0,turn[2]||0],sp=Math.sin(pi),cp=Math.cos(pi),sy=Math.sin(ya),cy=Math.cos(ya),sr=Math.sin(ro),cr=Math.cos(ro);
      const left=[sr*sp*cy-cr*sy,sr*sp*sy+cr*cy,sr*cp].map(v=>v*size[0]),up=[cr*sp*cy+sr*sy,cr*sp*sy-sr*cy,cr*cp].map(v=>v*size[1]);
      put(add(add(pos,left),up),[0,0]);put(add(add(pos,left,-1),up),[1,0]);put(add(add(pos,left),up,-1),[0,1]);put(add(add(pos,left,-1),up,-1),[1,1]);
      return;
    }
    // rvSpriteParticle::Render: a diamond whose corners sit at +-left*size.x and +-up*size.y, rotated by angle.
    const c=Math.cos(turn[0]),sn=Math.sin(turn[0]);
    const left=view.left.map((v,i)=>(v*c-view.up[i]*sn)*size[0]),up=view.up.map((v,i)=>(v*c+view.left[i]*sn)*size[1]);
    // Slots for quadIndex (0,2,3)(0,3,1): corners -left, -up, +left, +up in st order (0,0),(1,0),(1,1),(0,1).
    put(add(pos,left,-1),[0,0]);put(add(pos,up),[0,1]);put(add(pos,up,-1),[1,0]);put(add(pos,left),[1,1]);
  }
  const api={rng,sample,effectPeriod,capacity,simulate};
  if(typeof module!=='undefined')module.exports=api;else root.quake4BSE=api;
})(typeof window!=='undefined'?window:globalThis);
