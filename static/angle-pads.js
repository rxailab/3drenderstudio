// Visual controls for the 参数 panel: a camera orbit diagram (top view and side
// view) and a light-position pad with a shaded ball. They only read and write
// the existing form fields, so the plan logic in app.js stays the single owner
// of the values.
(function () {
  const NS='http://www.w3.org/2000/svg';
  const $=id=>document.getElementById(id);
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
  const rad=d=>d*Math.PI/180,deg=r=>r*180/Math.PI;
  const wrap=a=>((a+180)%360+360)%360-180;
  const snap=(v,step,reach)=>{const near=Math.round(v/step)*step;return Math.abs(v-near)<=reach?near:Math.round(v);};
  const still=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  function svg(tag,attrs,parent) {
    const node=document.createElementNS(NS,tag);
    for(const [key,value] of Object.entries(attrs || {}))node.setAttribute(key,value);
    parent?.appendChild(node);return node;
  }
  function point(event,node) {
    const box=node.getBoundingClientRect(),view=node.viewBox.baseVal;
    return [view.x+(event.clientX-box.left)/box.width*view.width,view.y+(event.clientY-box.top)/box.height*view.height];
  }
  function write(id,value,events=['input']) {
    const field=$(id);if(!field || field.disabled)return;
    field.value=value;
    for(const name of events)field.dispatchEvent(new Event(name,{bubbles:true}));
  }
  function drag(node,move,end) {
    node.addEventListener('pointerdown',event=>{
      if(event.button)return;
      const handle=event.currentTarget;handle.setPointerCapture?.(event.pointerId);
      handle.closest('svg').classList.add('dragging');event.preventDefault();handle.focus?.();
      let frame=0,last=event;
      const step=()=>{frame=0;move(last);};
      const onMove=e=>{last=e;if(!frame)frame=requestAnimationFrame(step);};
      const onUp=()=>{
        handle.removeEventListener('pointermove',onMove);handle.removeEventListener('pointerup',onUp);handle.removeEventListener('pointercancel',onUp);
        if(frame){cancelAnimationFrame(frame);step();}
        handle.closest('svg').classList.remove('dragging');end?.();
      };
      handle.addEventListener('pointermove',onMove);handle.addEventListener('pointerup',onUp);handle.addEventListener('pointercancel',onUp);
      move(event);
    });
  }

  // ---- Camera ----
  const RING=40,SIDE={x:-20,y:2,r:46};
  let topCam,topCone,topBand,sideCam,shownTurn=0;
  function cameraGlyph(parent,facing) {
    // Drawn at the origin with the lens pointing along `facing` (degrees, screen clockwise from up).
    const glyph=svg('g',{class:'cam-glyph',transform:'rotate('+facing+')'},parent);
    svg('rect',{x:-6,y:-3.5,width:12,height:8,rx:1.6},glyph);
    svg('rect',{x:-2.6,y:-7,width:5.2,height:4,rx:.8},glyph);
    return glyph;
  }
  function buildCamera() {
    const top=$('orbitTop'),side=$('orbitSide');if(!top || !side)return;
    svg('circle',{class:'pad-ring',r:RING},top);
    for(let a=0;a<360;a+=45)svg('line',{class:'pad-tick',x1:0,y1:RING-2.5,x2:0,y2:RING+2.5,transform:'rotate('+a+')'},top);
    for(const [text,x,y] of [['前',0,57],['后',0,-51],['左',-54,2.5],['右',54,2.5]])svg('text',{class:'pad-label',x,y,'text-anchor':'middle'},top).textContent=text;
    topCam=svg('g',{class:'cam'},top);
    topCone=svg('path',{class:'cam-cone',d:'M0 '+RING+' L-'+RING+' 0 L'+RING+' 0 Z'},topCam);
    topBand=svg('rect',{class:'cam-cone',x:-12,y:0,width:24,height:RING},topCam);
    svg('g',{transform:'translate(0 '+RING+')'},topCam).appendChild(cameraGlyph(null,0));
    const product=svg('g',{class:'pad-product'},top);
    svg('rect',{x:-9,y:-9,width:18,height:18,rx:3},product);
    svg('path',{class:'pad-front',d:'M-4 9 L0 14 L4 9 Z'},product);
    drag(top,event=>{
      const [x,y]=point(event,top);if(Math.hypot(x,y)<3)return;
      write('azimuth',snap(deg(Math.atan2(x,y)),15,3),['input','change']);
    });
    top.addEventListener('keydown',event=>{
      const turn={ArrowLeft:-5,ArrowRight:5}[event.key];if(!turn)return;
      event.preventDefault();write('azimuth',wrap(Math.round(Number($('azimuth').value) || 0)+turn),['input','change']);
    });

    const arc=a=>[SIDE.x+SIDE.r*Math.cos(rad(a)),SIDE.y-SIDE.r*Math.sin(rad(a))];
    const [ax,ay]=arc(-80),[bx,by]=arc(90);
    svg('line',{class:'pad-ground',x1:-56,y1:SIDE.y+10,x2:56,y2:SIDE.y+10},side);
    svg('path',{class:'pad-ring',d:'M'+ax+' '+ay+' A'+SIDE.r+' '+SIDE.r+' 0 0 0 '+bx+' '+by},side);
    for(const a of [-60,-30,0,30,60,90]){const [x,y]=arc(a);svg('circle',{class:'pad-dot',cx:x,cy:y,r:a?1:1.6},side);}
    for(const [text,a] of [['平视',0],['俯拍',62],['仰拍',-52]]){const x=SIDE.x+(SIDE.r+9)*Math.cos(rad(a)),y=SIDE.y-(SIDE.r+9)*Math.sin(rad(a));svg('text',{class:'pad-label',x:Math.min(x,50),y:y+2.5,'text-anchor':'middle'},side).textContent=text;}
    sideCam=svg('g',{class:'cam'},side);
    svg('line',{class:'cam-sight',x1:6,y1:0,x2:SIDE.r-8,y2:0},sideCam);
    svg('g',{transform:'translate('+SIDE.r+' 0)'},sideCam).appendChild(cameraGlyph(null,-90));
    svg('rect',{class:'pad-product-solid',x:SIDE.x-9,y:SIDE.y-8,width:18,height:18,rx:3},side);
    drag(side,event=>{
      const [x,y]=point(event,side),dx=x-SIDE.x,dy=SIDE.y-y;if(Math.hypot(dx,dy)<3)return;
      let angle=deg(Math.atan2(dy,dx));if(dx<0)angle=dy>=0?90:-80;
      write('elevation',clamp(snap(angle,15,3),-80,90),['input','change']);
    });
    side.addEventListener('keydown',event=>{
      const lift={ArrowUp:5,ArrowDown:-5}[event.key];if(!lift)return;
      event.preventDefault();write('elevation',clamp(Math.round(Number($('elevation').value) || 0)+lift,-80,90),['input','change']);
    });

    $('cameraPresets')?.addEventListener('click',event=>{
      const button=event.target.closest('[data-view]');if(!button)return;
      const [azimuth,elevation]=button.dataset.view.split(',').map(Number);
      write('elevation',elevation);write('azimuth',azimuth,['input','change']);
    });
    $('projectionToggle')?.addEventListener('click',event=>{
      const button=event.target.closest('[data-projection]');if(!button)return;
      write('cameraProjection',button.dataset.projection,['input','change']);
    });
  }
  function syncCamera() {
    if(!topCam)return;
    const azimuth=Number($('azimuth').value),elevation=Number($('elevation').value);
    const perspective=$('cameraProjection').value==='perspective',focal=clamp(Number($('focalLength').value) || 70,20,200);
    const valid=Number.isFinite(azimuth) && Number.isFinite(elevation) && $('azimuth').value!=='' && $('elevation').value!=='';
    if(valid){
      // Follow the shortest way round so a preset never spins the long way.
      shownTurn+=wrap(-azimuth-shownTurn);
      topCam.style.transform='rotate('+shownTurn+'deg)';
      sideCam.style.transform='translate('+SIDE.x+'px,'+SIDE.y+'px) rotate('+(-clamp(elevation,-80,90))+'deg)';
      $('orbitTop').setAttribute('aria-valuenow',wrap(azimuth));$('orbitTop').setAttribute('aria-valuetext','水平角 '+Math.round(azimuth)+' 度');
      $('orbitSide').setAttribute('aria-valuenow',elevation);$('orbitSide').setAttribute('aria-valuetext','俯仰角 '+Math.round(elevation)+' 度');
    }
    topCone.style.transform='scaleX('+Math.tan(Math.atan(18/focal))+')';
    topCone.style.opacity=perspective?1:0;topBand.style.opacity=perspective?0:1;
    for(const button of document.querySelectorAll('#projectionToggle [data-projection]'))button.setAttribute('aria-pressed',String((button.dataset.projection==='perspective')===perspective));
    for(const button of document.querySelectorAll('#cameraPresets [data-view]')){
      const [a,e]=button.dataset.view.split(',').map(Number);
      button.setAttribute('aria-pressed',String(valid && Math.abs(wrap(a-azimuth))<.5 && Math.abs(e-elevation)<.5));
    }
    if($('focalLengthValue'))$('focalLengthValue').textContent=Math.round(focal)+' mm';
    if($('cameraSummary'))$('cameraSummary').textContent=valid?Math.round(azimuth)+'° / '+Math.round(elevation)+'° · '+(perspective?'透视 '+Math.round(focal)+'mm':'正交'):'';
  }

  // ---- Lights ----
  const DOME=50;
  const ROLES=[
    {role:'key',name:'主光',mark:'主',azimuth:'keyAzimuth',elevation:'keyElevation',rest:[-35,45],weight:1,tint:[1,.93,.82]},
    {role:'fill',name:'补光',mark:'补',azimuth:'fillAzimuth',elevation:'fillElevation',rest:[40,45],weight:.38,tint:[.8,.9,1]},
    {role:'rim',name:'轮廓光',mark:'轮',azimuth:'rimAzimuth',elevation:'rimElevation',rest:[145,45],weight:.75,tint:[.95,.92,1]}
  ];
  const LOOKS={
    three:{key:[-35,45],fill:[40,30],rim:[145,50]},
    side:{key:[-80,30],fill:[60,20],rim:[150,45]},
    top:{key:[0,80],fill:[0,20],rim:[180,60]},
    back:{key:[150,40],fill:[-20,25],rim:[-150,45]}
  };
  const shown={},dots={};let tween=0,ball;
  function target(light) {
    // A blank field follows the project value when there is one, else the stock position.
    const read=(id,fallback)=>{const field=$(id),own=field.value.trim(),shared=field.dataset.shared;return own!==''&&Number.isFinite(Number(own))?Number(own):shared?Number(shared):fallback;};
    return {azimuth:read(light.azimuth,light.rest[0]),elevation:clamp(read(light.elevation,light.rest[1]),0,90),
      set:$(light.azimuth).value.trim()!=='' || $(light.elevation).value.trim()!==''};
  }
  const place=(azimuth,elevation)=>{const r=DOME*(1-elevation/90);return [r*Math.sin(rad(azimuth)),r*Math.cos(rad(azimuth))];};
  function buildLights() {
    const pad=$('lightPad');if(!pad)return;
    svg('circle',{class:'pad-dome',r:DOME},pad);
    for(const e of [30,60])svg('circle',{class:'pad-ring dashed',r:DOME*(1-e/90)},pad);
    svg('line',{class:'pad-axis',x1:-DOME,y1:0,x2:DOME,y2:0},pad);svg('line',{class:'pad-axis',x1:0,y1:-DOME,x2:0,y2:DOME},pad);
    for(const e of [0,30,60])svg('text',{class:'pad-label small',x:2,y:-DOME*(1-e/90)+6.5},pad).textContent=e+'°';
    svg('text',{class:'pad-label',x:0,y:-DOME-5,'text-anchor':'middle'},pad).textContent='后';
    svg('text',{class:'pad-label',x:-DOME-9,y:2.5,'text-anchor':'middle'},pad).textContent='左';
    svg('text',{class:'pad-label',x:DOME+9,y:2.5,'text-anchor':'middle'},pad).textContent='右';
    svg('g',{transform:'translate(0 '+(DOME+11)+')'},pad).appendChild(cameraGlyph(null,0));
    const beams=svg('g',{},pad);
    svg('rect',{class:'pad-product-solid',x:-6,y:-6,width:12,height:12,rx:2.4},pad);
    for(const light of ROLES){
      const beam=svg('line',{class:'light-beam light-'+light.role,x1:0,y1:0},beams);
      const dot=svg('g',{class:'light-handle light-'+light.role,tabindex:0,role:'slider','aria-label':light.name+'灯位，方向键微调，Delete 恢复默认'},pad);
      svg('circle',{r:8.5},dot);svg('text',{y:3,'text-anchor':'middle'},dot).textContent=light.mark;
      dots[light.role]={dot,beam};
      drag(dot,event=>{
        const [x,y]=point(event,pad),r=Math.hypot(x,y);
        const elevation=clamp(snap(90*(1-r/DOME),15,2.5),0,85);
        write(light.azimuth,r<1?0:snap(deg(Math.atan2(x,y)),15,3));write(light.elevation,elevation);
      });
      dot.addEventListener('keydown',event=>{
        if(event.key==='Delete' || event.key==='Backspace'){event.preventDefault();write(light.azimuth,'');write(light.elevation,'');return;}
        const move={ArrowLeft:[-5,0],ArrowRight:[5,0],ArrowUp:[0,5],ArrowDown:[0,-5]}[event.key];if(!move)return;
        event.preventDefault();const now=target(light);
        write(light.azimuth,wrap(Math.round(now.azimuth)+move[0]));write(light.elevation,clamp(Math.round(now.elevation)+move[1],0,85));
      });
    }
    document.querySelector('.light-rows')?.addEventListener('click',event=>{
      const button=event.target.closest('[data-light-reset]');if(!button)return;
      const light=ROLES.find(item=>item.role===button.dataset.lightReset);write(light.azimuth,'');write(light.elevation,'');
    });
    $('lightLooks')?.addEventListener('click',event=>{
      const button=event.target.closest('[data-look]');if(!button)return;
      const look=LOOKS[button.dataset.look];
      for(const light of ROLES){write(light.azimuth,look?look[light.role][0]:'');write(light.elevation,look?look[light.role][1]:'');}
    });
    ball=$('lightBall');
  }
  function drawLights() {
    for(const light of ROLES){
      const state=shown[light.role],[x,y]=place(state.azimuth,state.elevation),{dot,beam}=dots[light.role];
      dot.setAttribute('transform','translate('+x.toFixed(2)+' '+y.toFixed(2)+')');dot.classList.toggle('unset',!state.set);
      dot.setAttribute('aria-valuetext','方位 '+Math.round(state.azimuth)+' 度，高度 '+Math.round(state.elevation)+' 度'+(state.set?'':'（默认）'));
      beam.setAttribute('x2',x.toFixed(2));beam.setAttribute('y2',y.toFixed(2));beam.classList.toggle('unset',!state.set);
    }
    drawBall();
  }
  function drawBall() {
    let context=null;try{context=ball?.getContext?.('2d');}catch{context=null;}
    if(!context)return;
    const size=ball.width,image=context.createImageData(size,size),half=size/2,radius=half-3;
    const lights=ROLES.map(light=>{
      const a=rad(shown[light.role].azimuth),e=rad(shown[light.role].elevation);
      // Camera frame: x to the right, y up, z toward the camera.
      const l=[Math.sin(a)*Math.cos(e),Math.sin(e),Math.cos(a)*Math.cos(e)],h=[l[0],l[1],l[2]+1],n=Math.hypot(...h) || 1;
      return {l,h:h.map(v=>v/n),weight:light.weight,tint:light.tint};
    });
    for(let py=0;py<size;py++)for(let px=0;px<size;px++){
      const x=(px+.5-half)/radius,y=-(py+.5-half)/radius,d=x*x+y*y,i=(py*size+px)*4;
      if(d>1.03)continue;
      const z=Math.sqrt(Math.max(0,1-d)),color=[.035,.036,.04];
      for(const light of lights){
        const lambert=Math.max(0,x*light.l[0]+y*light.l[1]+z*light.l[2]);
        const gloss=Math.pow(Math.max(0,x*light.h[0]+y*light.h[1]+z*light.h[2]),48)*.55;
        // A light behind the ball still catches its edge, which is what a rim light is for.
        const edge=light.l[2]<0?Math.pow(1-z,3)*Math.max(0,x*light.l[0]+y*light.l[1]+.35)*1.6:0;
        const amount=light.weight*(lambert*.78+gloss+edge);
        for(let c=0;c<3;c++)color[c]+=amount*light.tint[c];
      }
      for(let c=0;c<3;c++)image.data[i+c]=255*Math.pow(clamp(color[c]/(1+color[c]*.35),0,1),1/2.2);
      image.data[i+3]=255*clamp((1.03-d)/.06,0,1);
    }
    context.putImageData(image,0,0);
  }
  function syncLights(jump) {
    if(!$('lightPad') || !dots.key)return;
    const goal={},from={};let moved=false;
    for(const light of ROLES){
      goal[light.role]=target(light);from[light.role]=shown[light.role] || goal[light.role];
      if(Math.abs(wrap(goal[light.role].azimuth-from[light.role].azimuth))>.01 || Math.abs(goal[light.role].elevation-from[light.role].elevation)>.01)moved=true;
    }
    cancelAnimationFrame(tween);
    const dragging=$('lightPad').classList.contains('dragging');
    if(jump || still || dragging || !moved || !shown.key){Object.assign(shown,goal);drawLights();}
    else {
      const started=performance.now();
      const step=now=>{
        const t=clamp((now-started)/260,0,1),k=1-Math.pow(1-t,3);
        for(const light of ROLES){const a=from[light.role],b=goal[light.role];
          shown[light.role]={azimuth:a.azimuth+wrap(b.azimuth-a.azimuth)*k,elevation:a.elevation+(b.elevation-a.elevation)*k,set:b.set};}
        drawLights();if(t<1)tween=requestAnimationFrame(step);
      };
      tween=requestAnimationFrame(step);
    }
    const changed=ROLES.filter(light=>goal[light.role].set);
    for(const light of ROLES)document.querySelector('.light-row[data-light="'+light.role+'"]')?.classList.toggle('unset',!goal[light.role].set);
    if($('lightSummary'))$('lightSummary').textContent=changed.length?'已调 '+changed.map(light=>light.name).join('·'):'默认布光';
    for(const button of document.querySelectorAll('#lightLooks [data-look]')){
      const look=LOOKS[button.dataset.look];
      button.setAttribute('aria-pressed',String(look?ROLES.every(light=>Number($(light.azimuth).value)===look[light.role][0] && Number($(light.elevation).value)===look[light.role][1] && $(light.azimuth).value!==''):!changed.length));
    }
  }

  function sync(jump) { syncCamera();syncLights(jump===true); }
  function start() {
    buildCamera();buildLights();
    const form=$('settingsForm');
    $('focalLength')?.addEventListener('input',event=>{event.target.value=Math.round(event.target.value);});
    form?.addEventListener('input',()=>sync());form?.addEventListener('change',()=>sync());
    sync(true);
  }
  window.anglePads={sync:()=>sync(true)};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
