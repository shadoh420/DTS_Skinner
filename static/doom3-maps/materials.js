/* Data-only id material expressions, UV matrices and conservative portal visibility. */
(function(root) {
  'use strict';
  function evaluate(node, time, parms, tables, globals=[], sound=0) {
    if (typeof node === 'number') return node;
    if (typeof node === 'string') return node === 'sound' ? sound : node === 'time' ? time : node.startsWith('parm') ? (parms[+node.slice(4)] || 0) : node.startsWith('global') ? (globals[+node.slice(6)] || 0) : 0;
    if (!node) return 0;
    const [op,a,b]=node, x=evaluate(a,time,parms,tables,globals,sound);
    if (op==='table') {
      const table=tables[a]; if (!table || !table.values.length) return 0;
      const values=table.values, n=values.length;if(n<=1)return 1;
      let f=evaluate(b,time,parms,tables,globals,sound);
      f=table.clamp ? Math.max(0,Math.min(1,f))*(n-1) : ((f%1+1)%1)*n;
      const i=Math.floor(f); return table.snap ? values[i%n] : values[i%n]*(1-f+i)+values[Math.min(i+1,table.clamp?n-1:n)%n]*(f-i);
    }
    const y=evaluate(b,time,parms,tables,globals,sound);
    switch(op) {
      case '+':return x+y; case '-':return x-y; case '*':return x*y; case '/':return y?x/y:0; case '%':return y?x%y:0;
      case '-u':return -x; case '+u':return x; case '!u':return +!x; case '<':return +(x<y);case '>':return +(x>y);
      case '<=':return +(x<=y);case '>=':return +(x>=y);case '==':return +(x===y);case '!=':return +(x!==y);
      case '&&':return +(!!x&&!!y);case '||':return +(!!x||!!y);default:return 0;
    }
  }
  function uvMatrix(stage,time,parms,tables,globals=[],sound=0) {
    // Row-major affine UV transform. Doom post-multiplies each modifier (Material.cpp).
    let m=[1,0,0,0,1,0];
    for(const [op,...args] of stage.uv || []) {
      const [x,y=x]=args.map(a=>evaluate(a,time,parms,tables,globals,sound));let n;
      if(op==='scroll'||op==='translate') n=[1,0,x,0,1,y];
      else if(op==='scale') n=[x,0,0,0,y,0];
      else if(op==='centerscale') n=[x,0,.5-.5*x,0,y,.5-.5*y];
      else {const c=tables.costable?evaluate(['table','costable',x],time,parms,tables,globals,sound):Math.cos(x*2*Math.PI),s=tables.sintable?evaluate(['table','sintable',x],time,parms,tables,globals,sound):Math.sin(x*2*Math.PI);n=[c,-s,.5-.5*c+.5*s,s,c,.5-.5*s-.5*c];}
      m=[m[0]*n[0]+m[1]*n[3],m[0]*n[1]+m[1]*n[4],m[0]*n[2]+m[1]*n[5]+m[2],
         m[3]*n[0]+m[4]*n[3],m[3]*n[1]+m[4]*n[4],m[3]*n[2]+m[4]*n[5]+m[5]];
    }
    return m;
  }
  function soundEnvelope(samples) {
    // snd_cache.cpp / FindAmplitude: min/max over 512 samples at 44.1 kHz,
    // atan((high-low)/32767)/(pi/4). Sound volume and distance do not enter it.
    const values=new Float32Array(Math.ceil(samples.length/512));
    for(let i=0;i<values.length;i++) {
      let low=32767,high=-32767;
      for(let j=i*512;j<Math.min(samples.length,(i+1)*512);j++) {
        const v=Math.max(-32768,Math.min(32767,Math.round(samples[j]*32768)));
        low=Math.min(low,v);high=Math.max(high,v);
      }
      values[i]=Math.atan((high-low)/32767)/(Math.PI/4);
    }
    return {values,duration:samples.length/44100};
  }
  function soundAmplitude(source,seconds,override=null) {
    if(!source||source.waitForTrigger||!source.envelope)return 0;
    if(override!==null)return override; // diagnostic equivalent of s_constantAmplitude
    const e=source.envelope;if(!e.duration||seconds<0||(!source.looping&&seconds>=e.duration))return 0;
    if(source.noFlicker)return Math.atan(2)/(Math.PI/4);
    return e.values[Math.min(e.values.length-1,Math.floor((seconds%e.duration)*44100/512))];
  }
  function ambientBlend(stage) {
    // GLS_*MASK disables writes, not the source channel used by blending.
    // Separate alpha factors preserve destination alpha for subsequent stages.
    const rgb=stage.blend?.length===2?stage.blend:['gl_one','gl_zero'];
    const alpha=rgb.map(f=>f.replace('_color','_alpha'));
    return [...(stage.maskcolor?['gl_zero','gl_one']:rgb),...(stage.maskalpha?['gl_zero','gl_one']:alpha)];
  }
  function areaAt(p,nodes) {
    if(!nodes.length)return 0;
    let i=0;
    for(let n=0;n<=nodes.length;n++) {
      const node=nodes[i],v=node.plane,c=node.children[v[0]*p[0]+v[1]*p[1]+v[2]*p[2]+v[3]>=0?0:1];
      if(c<=0)return -1-c;i=c;
    }
    return -1;
  }
  function clipPolygon(points,plane) {
    const result=[]; if(!points.length)return result;
    let a=points.at(-1), da=plane(a);
    for(const b of points) {const db=plane(b);if((da>=0)!==(db>=0)) {const f=da/(da-db);result.push(a.map((x,i)=>x+f*(b[i]-x)));}if(db>=0)result.push(b);a=b;da=db;}
    return result;
  }
  function portalRect(points,matrix) {
    let p=points.map(v=>[0,1,2,3].map(r=>matrix[r]*v[0]+matrix[r+4]*v[1]+matrix[r+8]*v[2]+matrix[r+12]));
    // Clip in homogeneous coordinates: no false rejection when a portal crosses the near plane.
    for(const plane of [v=>v[3]+v[2],v=>v[3]+v[0],v=>v[3]-v[0],v=>v[3]+v[1],v=>v[3]-v[1]])p=clipPolygon(p,plane);
    if(!p.length)return null;
    const x=p.map(v=>v[0]/Math.max(v[3],1e-8)),y=p.map(v=>v[1]/Math.max(v[3],1e-8));
    return [Math.min(...x),Math.min(...y),Math.max(...x),Math.max(...y)];
  }
  function visibleAreas(origin,data,matrix) {
    const start=areaAt(origin,data.nodes);
    if(start<0)return new Set(Array.from({length:data.areaCount},(_,i)=>i));
    const adjacency=data.portalAdjacency || (data.portalAdjacency=Array.from({length:data.areaCount},()=>[]));
    if(!data.portalIndexed){data.portals.forEach((p,i)=>p.areas.forEach(a=>adjacency[a].push(i)));data.portalIndexed=true;}
    const rects=new Map([[start,[-1,-1,1,1]]]), queue=[start],projected=new Map();
    for(let next=0;next<queue.length;next++) {
      const a=queue[next],from=rects.get(a);
      for(const index of adjacency[a] || []) {
        const portal=data.portals[index],b=portal.areas[0]===a?portal.areas[1]:portal.areas[0];
        if(!projected.has(index))projected.set(index,portalRect(portal.points,matrix));
        const p=projected.get(index);if(!p)continue;
        const r=[Math.max(from[0],p[0]),Math.max(from[1],p[1]),Math.min(from[2],p[2]),Math.min(from[3],p[3])];
        if(r[2]<r[0]||r[3]<r[1])continue;
        const old=rects.get(b);
        if(old&&r[0]>=old[0]-1e-6&&r[1]>=old[1]-1e-6&&r[2]<=old[2]+1e-6&&r[3]<=old[3]+1e-6)continue;
        rects.set(b,old?[Math.min(old[0],r[0]),Math.min(old[1],r[1]),Math.max(old[2],r[2]),Math.max(old[3],r[3])]:r);queue.push(b);
      }
    }
    return new Set(rects.keys());
  }
  function tangents(position,uv,normal,index,dominantRanges=[]) {
    // Simd_Generic.cpp: normalize each triangle's S/T vectors, accumulate,
    // then project both independently onto the vertex normal plane.
    const result=[new Float32Array(position.length),new Float32Array(position.length)];
    for(let i=0;i<index.length;i+=3) {
      const ids=[index[i],index[i+1],index[i+2]],a=ids[0],b=ids[1],c=ids[2];
      const d0=[0,1,2].map(k=>position[b*3+k]-position[a*3+k]),d1=[0,1,2].map(k=>position[c*3+k]-position[a*3+k]);
      const s0=uv[b*2]-uv[a*2],t0=uv[b*2+1]-uv[a*2+1],s1=uv[c*2]-uv[a*2],t1=uv[c*2+1]-uv[a*2+1];
      const sign=s0*t1-t0*s1<0?-1:1;
      const ts=[d0.map((v,k)=>v*t1-t0*d1[k]),d1.map((v,k)=>s0*v-d0[k]*s1)];
      ts.forEach((t,j)=>{const f=sign/Math.max(Math.hypot(...t),1e-20);for(const id of ids)for(let k=0;k<3;k++)result[j][id*3+k]+=t[k]*f;});
    }
    for(let i=0;i<position.length;i+=3)for(const t of result) {
      const dot=t[i]*normal[i]+t[i+1]*normal[i+1]+t[i+2]*normal[i+2];
      for(let k=0;k<3;k++)t[i+k]-=dot*normal[i+k];
      const length=Math.max(Math.hypot(t[i],t[i+1],t[i+2]),1e-20);for(let k=0;k<3;k++)t[i+k]/=length;
    }
    // R_BuildDominantTris: unsmoothed materials use each vertex's largest face.
    if(dominantRanges.length) {
      const largest=new Float32Array(position.length/3);
      const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
      for(const range of dominantRanges)for(let i=range.start;i<range.start+range.count;i+=3) {
        const ids=[index[i],index[i+1],index[i+2]],a=ids[0],b=ids[1],c=ids[2];
        const d0=[0,1,2].map(k=>position[b*3+k]-position[a*3+k]),d1=[0,1,2].map(k=>position[c*3+k]-position[a*3+k]);
        const s0=uv[b*2]-uv[a*2],t0=uv[b*2+1]-uv[a*2+1],s1=uv[c*2]-uv[a*2],t1=uv[c*2+1]-uv[a*2+1];
        const raw=cross(d0,d1),area=Math.hypot(...raw),n=raw.map(v=>v/Math.max(area,.001)),sign=s0*t1-t0*s1<0?-1:1;
        let t=d0.map((v,k)=>v*t1-t0*d1[k]);const scale=sign/Math.max(Math.hypot(...t),.001);t=t.map(v=>v*scale);
        const bitangent=cross(n,t).map(v=>v*sign);
        for(const id of ids)if(area>=largest[id]) {
          largest[id]=area;for(let k=0;k<3;k++){normal[id*3+k]=n[k];result[0][id*3+k]=t[k];result[1][id*3+k]=bitangent[k];}
        }
      }
    }
    return result;
  }
  function ambientOrder(sort,sequence) {
    // RB_STD_DrawShaderPasses: after all interactions, by material sort; stage chains keep import order.
    return 100+((sort??0)+3)*1e6+sequence;
  }
  function fogPlane(light) {
    // R_SetLightFrustum frustum[5] = vLight->fogPlane: the falloff f=1 plane, positive outside (above) the fog.
    const [a,b,c,d]=light.projection[3],l=Math.hypot(a,b,c);return [a/l,b/l,c/l,(d-1)/l];
  }
  function portalSkyOrigin(data,areas) {
    return data.portalSky&&[...areas].some(a=>data.areaSky?.['_area'+a])?data.portalSky.origin:null;
  }
  function skyMatrix(wobble,seconds,axis=[1,0,0,0,1,0,0,0,1]) {
    if(!wobble)return axis;
    // R_WobbleskyTexGen: degrees, wobble rpm, rotation rpm. Row-major.
    const [degrees,speed,rotate]=wobble,angle=degrees*Math.PI/180,a=seconds*speed*Math.PI/30;
    const sine=Math.sin(angle),s=Math.sin(a)*sine,c=Math.cos(a)*sine,z=Math.cos(angle);
    const up=[c,s,z],y=[-Math.sin(a*2)*sine,0,-s*sine];y[1]=Math.sqrt(Math.max(0,1-y[0]*y[0]-y[2]*y[2]));
    const dot=y.reduce((n,v,i)=>n+v*up[i],0);for(let i=0;i<3;i++)y[i]-=dot*up[i];
    const length=Math.hypot(...y);for(let i=0;i<3;i++)y[i]/=length;
    const x=[y[1]*up[2]-y[2]*up[1],y[2]*up[0]-y[0]*up[2],y[0]*up[1]-y[1]*up[0]];
    const rs=Math.sin(seconds*rotate*Math.PI/30),rc=Math.cos(seconds*rotate*Math.PI/30);
    const rows=[x.map((v,i)=>v*rc+y[i]*rs),y.map((v,i)=>v*rc-x[i]*rs),up];
    return rows.flatMap(row=>[0,1,2].map(j=>row.reduce((n,v,k)=>n+v*axis[k*3+j],0)));
  }
  // tr_deform.cpp view-dependent deforms, rebuilt on the CPU for visible surfaces only.
  // base: {position,uv,color,normal,index} of one surface; out: same-size typed arrays.
  const sub=(a,b)=>[a[0]-b[0],a[1]-b[1],a[2]-b[2]],dot3=(a,b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
  const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
  const unit3=a=>{const l=Math.hypot(...a)||1;return a.map(v=>v/l);};
  const vert=(p,i)=>[p[i*3],p[i*3+1],p[i*3+2]];
  const DEFORMS={flare:1,sprite:1,rectsprite:1,tube:1,turbulent:1,expand:1,move:1};
  function cyclicQuad(p,start) {
    // Corner order around the centre; PROC/model quads are not guaranteed cyclic.
    const v=[0,1,2,3].map(k=>vert(p,start+k)),c=v.reduce((s,x)=>s.map((y,i)=>y+x[i]/4),[0,0,0]);
    const n=unit3(cross(sub(v[1],v[0]),sub(v[2],v[0]))),a=unit3(sub(v[0],c)),b=cross(n,a);
    return [0,1,2,3].map(k=>[k,Math.atan2(dot3(sub(v[k],c),b),dot3(sub(v[k],c),a))]).sort((x,y)=>x[1]-y[1]).map(x=>start+x[0]);
  }
  function deformLayout(kind,base) {
    // Returns {vertices,index} for the rebuilt surface, or null when the deform can't apply (drawn unchanged, like the engine).
    const n=base.position.length/3;
    if(kind==='flare') {
      if(n!==4||base.index.length!==6)return null;
      const q=cyclicQuad(base.position,0),index=[0,1,2,0,2,3];
      for(let j=0;j<4;j++){const k=(j+1)%4,o=j=>4+j*3;index.push(j,k,o(k)+2,j,o(k)+2,o(j),j,o(j),o(j)+1,j,o(j)+1,o(j)+2);}
      return {vertices:16,index,order:q};
    }
    if(kind==='sprite'||kind==='rectsprite') {
      if(n%4)return null;
      const index=[];for(let i=0;i<n;i+=4)index.push(i,i+1,i+2,i,i+2,i+3);
      return {vertices:n,index};
    }
    if(kind==='tube') {
      if(n%4)return null;
      return {vertices:n,index:Array.from(base.index),order:Array.from({length:n/4},(_,i)=>cyclicQuad(base.position,i*4)).flat()};
    }
    return {vertices:n,index:Array.from(base.index)};
  }
  function deform(kind,base,layout,out,eye,left,up,values=[],lookup=null) {
    // Fills out.position/uv/color; returns false when the surface draws nothing this view.
    const p=base.position,n=p.length/3;
    if(kind==='flare') {
      // R_FlareDeform: invisible from behind, dimmed edge-on (dot*8), halo of `spread` units in _quadratic falloff.
      const q=layout.order.map(i=>vert(p,i)),c=q.reduce((s,x)=>s.map((y,i)=>y+x[i]/4),[0,0,0]);
      const normal=unit3([0,1,2,3].reduce((s,i)=>s.map((y,k)=>y+base.normal[i*3+k]),[0,0,0]));
      const toEye=sub(eye,c);if(dot3(toEye,normal)<=0)return false;
      const shade=Math.min(1,Math.floor(dot3(unit3(toEye),normal)*8*256)/255),spread=values[0]||0;
      for(let j=0;j<4;j++) {
        const v=q[j],next=q[(j+1)%4],prev=q[(j+3)%4],e=unit3(sub(eye,v));
        const outward=(d,edgeMid)=>{d=unit3(d);return dot3(d,sub(edgeMid,c))<0?d.map(x=>-x):d;};
        const e0=outward(cross(e,sub(next,v)),v.map((x,i)=>(x+next[i])/2)),e2=outward(cross(e,sub(v,prev)),v.map((x,i)=>(x+prev[i])/2));
        const e1=unit3(e0.map((x,i)=>x+e2[i]));
        out.position.set(v,j*3);out.uv.set([.5,.5],j*2);
        [e0,e1,e2].forEach((d,k)=>{
          // Pull each halo point back along its view ray onto the flare plane: same screen spot, nearer depth.
          let o=v.map((x,i)=>x+d[i]*spread);const ray=sub(o,eye),len=Math.hypot(...ray),u=ray.map(x=>x/len);
          const along=-dot3(toEye,normal)/dot3(u,normal);if(along>0&&along<len)o=eye.map((x,i)=>x+u[i]*along);
          const at=4+j*3+k;out.position.set(o,at*3);out.uv.set([0,.5],at*2);});
      }
      out.color.fill(shade);return true;
    }
    if(kind==='sprite'||kind==='rectsprite') {
      // R_AutospriteDeform: each 4-vertex group becomes a view-aligned square of radius |v0-mid|/sqrt(2).
      const st=[[0,0],[1,0],[1,1],[0,1]],corner=[[1,1],[-1,1],[-1,-1],[1,-1]];
      for(let i=0;i<n;i+=4) {
        const v=[0,1,2,3].map(k=>vert(p,i+k)),mid=v.reduce((s,x)=>s.map((y,k)=>y+x[k]/4),[0,0,0]);
        const r=Math.hypot(...sub(v[0],mid))*.707;
        for(let k=0;k<4;k++){out.position.set(mid.map((x,a)=>x+(left[a]*corner[k][0]+up[a]*corner[k][1])*r),(i+k)*3);out.uv.set(st[k],(i+k)*2);}
      }
      return true;
    }
    out.position.set(p);out.uv.set(base.uv);
    if(kind==='tube') {
      // R_TubeDeform: keep the long axis, turn the short ends to face the viewer.
      for(let i=0;i<n;i+=4) {
        const o=layout.order.slice(i,i+4),v=o.map(k=>vert(p,k)),len=k=>Math.hypot(...sub(v[k],v[(k+1)%4]));
        const s=len(0)+len(2)<=len(1)+len(3)?0:1,ends=[[o[s],o[s+1]],[o[s+2],o[(s+3)%4]]];
        const mids=ends.map(([a,b])=>vert(p,a).map((x,k)=>(x+vert(p,b)[k])/2));
        // Cyclic order: the second end starts beside the first end's last corner, so its signs flip (no twist).
        ends.forEach(([a,b],e)=>{const h=Math.hypot(...sub(vert(p,a),vert(p,b)))/2*(e?-1:1);
          const minor=unit3(cross(sub(mids[1],mids[0]),sub(eye,mids[e])));
          out.position.set(mids[e].map((x,k)=>x+minor[k]*h),a*3);out.position.set(mids[e].map((x,k)=>x-minor[k]*h),b*3);});
      }
    } else if(kind==='turbulent') {
      // R_TurbulentDeform, including its doubled time offset.
      const [range,offset,domain]=values;
      for(let i=0;i<n;i++){const f=offset+domain*(p[i*3]*.003+p[i*3+1]*.007+p[i*3+2]*.011)+offset;
        out.uv[i*2]+=range*lookup(f);out.uv[i*2+1]+=range*lookup(f+.5);}
    } else if(kind==='expand') {for(let i=0;i<n*3;i++)out.position[i]+=base.normal[i]*values[0];}
    else if(kind==='move') {for(let i=0;i<n;i++)out.position[i*3]+=values[0];}
    return true;
  }
  const api={evaluate,uvMatrix,soundEnvelope,soundAmplitude,ambientBlend,areaAt,portalRect,visibleAreas,tangents,ambientOrder,fogPlane,portalSkyOrigin,skyMatrix,DEFORMS,deformLayout,deform};
  if(typeof module!=='undefined')module.exports=api;else root.doom3Materials=api;
})(typeof window!=='undefined'?window:globalThis);
