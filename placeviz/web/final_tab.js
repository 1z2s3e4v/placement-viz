// ---- final tab: final placement + routing (render/final.py). View FV = {d, cx, cy (DBU), span = visible die width (DBU)}.
const FIN=typeof FINAL!=="undefined"?FINAL:{};
const FCOL=["#e41a1c","#377eb8","#4daf4a","#984ea3","#ff7f00"],CELLC=["#c8d3e0","#9aa5b1","#f4a259"],fcol=i=>FCOL[i%FCOL.length];
const NM=Math.max(1,...Object.values(FIN).map(r=>r.metal?r.metal.length:0));  // metal chips M1..M<NM>
const FL={pins:false,nets:false,gr:false,dr:false,drv:false,big:false},FM=Array(NM).fill(true);
const TC=new Map(),TP=new Set(),IMG=new Map(),TCAP=900;  // decoded tiles (LRU by Map order), pending tile ids, overview images
let FV=null,FZ=null,fq=0;const FOVW=4;  // overview PNGs while visible span > die/FOVW
function FINAL_TILE(key,layer,t,b64){const id=key+"|"+layer+"|"+t,u=Uint8Array.from(atob(b64),c=>c.charCodeAt(0));
 new Response(new Blob([u]).stream().pipeThrough(new DecompressionStream("gzip"))).arrayBuffer().then(ab=>{
  TP.delete(id);TC.set(id,new Int32Array(ab));while(TC.size>TCAP)TC.delete(TC.keys().next().value);fdraw();});}
function ftile(key,layer,t){const id=key+"|"+layer+"|"+t,d=TC.get(id);
 if(d){TC.delete(id);TC.set(id,d);return d;}
 if(!TP.has(id)){TP.add(id);const s=document.createElement("script");s.src="final/"+key+"/"+layer+"/"+t+".js";
  s.onload=()=>s.remove();s.onerror=()=>s.remove();document.head.appendChild(s);}
 return null;}
function fimg(key,layer){const id=key+"|"+layer;let i=IMG.get(id);
 if(!i){i=new Image();i.onload=fdraw;i.src="final/"+key+"/overview/"+layer+".png";IMG.set(id,i);}
 return i.complete&&i.naturalWidth?i:null;}
function fdie(){for(const m of MD)for(const [p] of PL){const r=FIN[p+"/"+D+"_"+m];if(r&&r.die)return r.die;}return null;}
function freset(){const d=fdie()||[0,0,1,1];FV={d:D,cx:(d[0]+d[2])/2,cy:(d[1]+d[3])/2,span:d[2]-d[0]};}
function fchanged(){const d=fdie();if(d)history.replaceState(null,"","#d="+LBL[D]+"&v=final&z="+((d[2]-d[0])/FV.span).toFixed(2)+
 "&cx="+Math.round(FV.cx)+"&cy="+Math.round(FV.cy));fdraw();}
function buildFinal(g){
 ["",...MD.map(m=>MN[m])].forEach(t=>{const h=document.createElement("div");h.className="hd";h.textContent=t;g.appendChild(h);});
 if(!FV||FV.d!==D){freset();if(FZ){const d=fdie();if(d){FV.span=(d[2]-d[0])/FZ.z;if(FZ.cx!=null)FV.cx=FZ.cx;if(FZ.cy!=null)FV.cy=FZ.cy;}FZ=null;}}
 const d=fdie();
 PL.forEach(([p,name])=>{const l=document.createElement("div");l.className="pl";l.textContent=name;g.appendChild(l);
  MD.forEach(m=>{const c=document.createElement("canvas");c.className="fc";c.dataset.key=p+"/"+D+"_"+m;
   if(d)c.style.aspectRatio=(d[2]-d[0])+"/"+(d[3]-d[1]);fwire(c);g.appendChild(c);});});
 fopts();fchanged();}
function fopts(){const el=document.getElementById("fopts");if(el.childElementCount)return;
 [["pins","pins"],["nets","nets"],["gr","GR"],["dr","DR"],["drv","DRV"],["big","nets deg>64"]].forEach(([k,t])=>{
  const l=document.createElement("label"),i=document.createElement("input");i.type="checkbox";i.id="f_"+k;i.checked=FL[k];
  i.onchange=()=>{FL[k]=i.checked;fdraw();};l.append(i,t);el.appendChild(l);});
 FM.forEach((_,j)=>{const b=document.createElement("button");b.className="chip on";b.id="f_m"+(j+1);b.textContent="M"+(j+1);
  b.style.borderColor=fcol(j);b.title="metal"+(j+1)+" filter for pins / GR / DR / DRV (vector view; overview images are unfiltered)";
  b.onclick=()=>{FM[j]=!FM[j];b.classList.toggle("on",FM[j]);fdraw();};el.appendChild(b);});}
function fwire(c){let drag=null;
 const pt=e=>{const W=c.clientWidth,H=c.clientHeight,s=FV.span/W;return [FV.cx+(e.offsetX-W/2)*s,FV.cy-(e.offsetY-H/2)*s];};
 c.onwheel=e=>{e.preventDefault();const d=fdie();if(!d)return;const [u,v]=pt(e),W=c.clientWidth,H=c.clientHeight;
  FV.span=Math.min((d[2]-d[0])*2,Math.max((d[2]-d[0])/4000,FV.span*Math.pow(1.0015,e.deltaY)));
  const s=FV.span/W;FV.cx=u-(e.offsetX-W/2)*s;FV.cy=v+(e.offsetY-H/2)*s;fchanged();};
 c.onpointerdown=e=>{drag=[e.clientX,e.clientY];c.setPointerCapture(e.pointerId);c.style.cursor="grabbing";};
 c.onpointermove=e=>{if(!drag)return;const s=FV.span/c.clientWidth;FV.cx-=(e.clientX-drag[0])*s;FV.cy+=(e.clientY-drag[1])*s;
  drag=[e.clientX,e.clientY];fchanged();};
 c.onpointerup=c.onpointercancel=()=>{drag=null;c.style.cursor="";};
 c.ondblclick=()=>{freset();fchanged();};}
function fdraw(){if(!fq)fq=requestAnimationFrame(()=>{fq=0;const d=fdie(),ovw=d&&FV.span>(d[2]-d[0])/FOVW;
 document.querySelectorAll("#fopts .chip").forEach(b=>b.style.opacity=ovw?.4:"");  // chips only filter the vector view
 document.querySelectorAll("canvas.fc").forEach(fpaint);});}
function fpaint(c){const key=c.dataset.key,r=FIN[key],dpr=window.devicePixelRatio||1,W=c.clientWidth,H=c.clientHeight;
 c.width=W*dpr;c.height=H*dpr;const x=c.getContext("2d");x.scale(dpr,dpr);x.fillStyle="#fff";x.fillRect(0,0,W,H);
 x.font="13px system-ui";x.fillStyle="#777";x.textAlign="center";
 if(!r){x.fillText("not exported",W/2,H/2);return;}
 if(r.status.placement!=="ok"){x.fillText(r.status.placement,W/2,H/2);return;}
 const q=r.q,k=W/FV.span*q,ox=W/2-FV.cx/q*k,oy=H/2+FV.cy/q*k;  // screen = (ox + u*k, oy - v*k), u,v in q units
 const ovw=FV.span>(r.die[2]-r.die[0])/FOVW,notes=[];
 for(const L of ["cells","gr","pins","dr","nets","drv"]){if(L!=="cells"&&!FL[L])continue;
  if(!r.layers[L]){notes.push(L.toUpperCase()+": "+(r.status[L]&&r.status[L]!=="ok"?r.status[L]:"n/a"));continue;}
  if(ovw){const i=fimg(key,L),d=r.die;if(i)x.drawImage(i,ox+d[0]/q*k,oy-d[3]/q*k,(d[2]-d[0])/q*k,(d[3]-d[1])/q*k);}
  else fvec(x,r,key,L,k,ox,oy,W,H);}
 // scale bar: largest 1/2/5 x 10^n um not wider than 120 px
 const upx=FV.span/W/r.dbu;let um=Math.pow(10,Math.floor(Math.log10(120*upx)));
 for(const f of [5,2])if(um*f<=120*upx){um*=f;break;}const bw=um/upx;
 x.fillStyle="rgba(255,255,255,.8)";x.fillRect(6,H-24,bw+60,18);x.fillStyle="#222";x.fillRect(10,H-12,bw,3);
 x.textAlign="left";x.font="11px system-ui";x.fillText((um>=1?um:+um.toPrecision(1))+" µm",bw+16,H-9);
 if(notes.length){x.fillStyle="#b00";x.fillText(notes.join("   "),8,16);}}
function fvec(x,r,key,L,k,ox,oy,W,H){
 const q=r.q,m=6/k,u0=(-ox)/k-m,u1=(W-ox)/k+m,v0=(oy-H)/k-m,v1=oy/k+m;  // visible box (q units) + 6 px margin
 const xl=r.die[0]/q,yl=r.die[1]/q,tw=(r.die[2]-r.die[0])/q/r.nt,th=(r.die[3]-r.die[1])/q/r.nt;
 const ti=(u,o,t)=>Math.min(r.nt-1,Math.max(0,Math.floor((u-o)/t)));  // same tile rule as final.tile_masks
 const a0=ti(u0,xl,tw),a1=ti(u1,xl,tw),b0=ti(v0,yl,th),b1=ti(v1,yl,th),have=new Set(r.layers[L].tiles);
 const VI=r.metal.length;  // path index of DR vias (after one path per metal)
 const P=[],path=g=>P[g]||(P[g]=new Path2D()),X=u=>ox+u*k,Y=v=>oy-v*k,wpx=r.metal.map(n=>Math.max(1,(r.width[n]||q)/q*k));
 const box=(g,a,b,c,d,mn)=>{let w=(c-a)*k,h=(d-b)*k,sx=X(a),sy=Y(d);
  if(w<mn){sx-=(mn-w)/2;w=mn;}if(h<mn){sy-=(mn-h)/2;h=mn;}path(g).rect(sx,sy,w,h);};
 for(let ix=a0;ix<=a1;ix++)for(let iy=b0;iy<=b1;iy++){const t=ix+"_"+iy;if(!have.has(t))continue;const R=ftile(key,L,t);if(!R)continue;
  for(let j=0;j<R.length;j+=5){const p0=R[j],p1=R[j+1],p2=R[j+2],p3=R[j+3],c=R[j+4];
   const a=Math.min(p0,p2),c2=Math.max(p0,p2),b=Math.min(p1,p3),d=Math.max(p1,p3);
   if(c2<u0||a>u1||d<v0||b>v1)continue;
   if(Math.max(ti(a,xl,tw),a0)!==ix||Math.max(ti(b,yl,th),b0)!==iy)continue;  // record is in every tile it touches: draw once
   if(L==="cells")box(c,a,b,c2,d,0);
   else if(L==="gr"||L==="pins"){if(FM[c])box(L==="pins"?0:c,a,b,c2,d,1);}
   else if(L==="nets"){if(FL.big||c<=64){const g=path(0);g.moveTo(X(p0),Y(p1));g.lineTo(X(p2),Y(p3));}}
   else if(L==="dr"){if(c<100){if(FM[c]){const g=path(c);g.moveTo(X(p0),Y(p1));g.lineTo(X(p2),Y(p3));}}
    else if(FM[c-100]||FM[c-99]){const s=Math.max(2,wpx[0]);path(VI).rect(X(p0)-s/2,Y(p1)-s/2,s,s);}}
   else{const li=c&63;if(li<32?FM[li]:(FM[li-32]||FM[li-31]))box(0,a,b,c2,d,5);}}}  // drv: metal, or 32 + cut
 x.save();
 if(L==="cells"){P.forEach((g,i)=>{if(g){x.fillStyle=CELLC[i];x.fill(g);}});x.strokeStyle="rgba(60,75,95,.45)";x.lineWidth=.5;P.forEach(g=>g&&x.stroke(g));}
 else if(L==="gr"){x.globalAlpha=.12;P.forEach((g,i)=>{if(g){x.fillStyle=fcol(i);x.fill(g);}});}
 else if(L==="pins"){if(P[0]){x.fillStyle="#000";x.fill(P[0]);}}
 else if(L==="nets"){if(P[0]){x.globalAlpha=.35;x.strokeStyle="#00a0a0";x.lineWidth=1;x.stroke(P[0]);}}
 else if(L==="dr"){x.lineCap="square";P.forEach((g,i)=>{if(g&&i<VI){x.strokeStyle=fcol(i);x.lineWidth=wpx[i];x.stroke(g);}});
  if(P[VI]){x.fillStyle="#222";x.fill(P[VI]);}}
 else if(P[0]){x.strokeStyle="#fff";x.lineWidth=4;x.stroke(P[0]);x.strokeStyle="#ff00ff";x.lineWidth=2;x.stroke(P[0]);}
 x.restore();}
