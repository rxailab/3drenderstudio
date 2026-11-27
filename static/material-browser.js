/* Local catalog browser for the project's left rail. Only a drop or a deliberate
   second click commits. Hovering changes materials on the current WebGL model. */
function createMaterialBrowser({state,load,openLive,apply,onError}) {
  const byId=id=>document.getElementById(id);
  const grid=byId('localMaterialGrid'),host=byId('realtimeViewport'),search=byId('localMaterialSearch'),categories=byId('localMaterialCategory');
  const hint=byId('localMaterialHint'),live=byId('localMaterialLive'),retry=byId('localMaterialRetry');
  const categoriesZh={Architectural:'建筑','Axalta Paint':'艾仕得车漆','Cloth and Leather':'织物与皮革',Contour:'轮廓',Cutaway:'剖切',Fuzz:'绒毛','Gem Stones':'宝石',Glass:'玻璃',Light:'灯光',Liquids:'液体',Measured:'实测材质',Metal:'金属',Miscellaneous:'其他','Mold-Tech':'模具纹理','Multi-Layer Optics':'多层光学',Packaging:'包装',Paint:'油漆',Plastic:'塑料',RealCloth:'真实织物','Scattering Medium':'散射介质',Stone:'石材',Toon:'卡通',Translucent:'半透明','Twinbru Fabrics':'Twinbru 面料',Wood:'木材'};
  const pageSize=30;
  let materials=null,byKey=new Map(),filtered=[],page=0,loading=false,error='',session='',activeInstance=null;
  let picked=null,mode='',hover=null,pressed=null,submitting=false;
  let gesture=null,ghost=null,dragFrame=0,lastPoint=null,suppressClick=false,pendingProject='';
  const pointerDrag=typeof window.PointerEvent==='function';

  function paintPage(){
    const cards=filtered.slice(page*pageSize,(page+1)*pageSize).map((item,index)=>{
      const button=document.createElement('button'),img=document.createElement('img'),label=document.createElement('span');
      button.type='button';button.className='local-material-tile';button.draggable=!pointerDrag;button.tabIndex=index===0?0:-1;
      button.dataset.id=item.id;button.setAttribute('aria-pressed',String(picked===item));
      button.title=[categoriesZh[item.category] || item.category,item.subcategoryZh,item.name].filter(Boolean).join(' / ');
      if(item.thumbnail_is_swatch)button.title+='\n基础色示意';
      if(item.missing_textures)button.title+='\n部分本地贴图未安装，对应通道使用材质参数。';
      img.src=item.thumbnail_url;img.alt='';img.loading='lazy';img.decoding='async';img.draggable=false;
      label.textContent=item.name;button.append(img,label);return button;
    });
    grid.replaceChildren(...cards);grid.scrollTop=0;
    byId('localMaterialCount').textContent=materials ? `${filtered.length} / ${materials.length} 款` : '';
    const pages=Math.ceil(filtered.length/pageSize);
    byId('localMaterialPages').hidden=pages<2;
    byId('localMaterialPage').textContent=pages?`${page+1} / ${pages}`:'';
    byId('localMaterialPrev').disabled=page===0;byId('localMaterialNext').disabled=page+1>=pages;
  }
  function filter(){
    cancel();
    const terms=search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    filtered=(materials || []).filter(item=>{
      if(categories.value && item.category!==categories.value)return false;
      const label=[item.name,item.sourceName,item.category,categoriesZh[item.category],item.subcategory,item.subcategoryZh].join(' ').toLowerCase();
      return terms.every(term=>label.includes(term));
    });
    page=0;paintPage();refresh();
  }
  async function fetchCatalog(){
    if(loading || materials || error)return;
    loading=true;
    try{
      const result=await load();
      if(!Array.isArray(result.materials))throw new Error('材质目录格式无效');
      const translations=window.WuyingMaterialLabels;
      materials=result.materials.map(item=>({...item,sourceName:item.name,name:translations.names[item.name] || item.name,
        subcategoryZh:(item.subcategory || '').split('/').map(key=>translations.subcategories[key] || key).join(' / ')}));
      byKey=new Map(materials.map(item=>[item.id,item]));
      for(const name of result.categories){
        const option=document.createElement('option');option.value=name;
        option.textContent=categoriesZh[name] || name;categories.append(option);
      }
      filter();
    }catch(e){error='读取本地材质库失败。'+e.message;}
    finally{loading=false;refresh();}
  }
  function setPressed(item){
    const old=grid.querySelector('[aria-pressed="true"]');if(old)old.setAttribute('aria-pressed','false');
    if(item)for(const button of grid.children)if(button.dataset.id===item.id)button.setAttribute('aria-pressed','true');
  }
  function cancel(){
    if(submitting)return;
    endGesture();pendingProject='';
    if(activeInstance && !activeInstance.disposed && picked)activeInstance.previewLibraryMaterial(null,null);
    if(picked)setPressed(null);
    picked=null;mode='';hover=null;pressed=null;
    activeInstance?.renderer.domElement.classList.remove('material-targeting');
  }
  function refresh(){
    const ctx=state();
    const awaitingView=pendingProject && pendingProject===ctx.project && ctx.visible && ctx.active;
    if(!ctx.visible || ctx.blocked || (!awaitingView && (session!==ctx.key || activeInstance!==ctx.instance)))cancel();
    session=ctx.key;activeInstance=ctx.instance;
    if(awaitingView && activeInstance && picked){pendingProject='';activeInstance.renderer.domElement.classList.add('material-targeting');}
    if(!ctx.visible)return;
    if(!materials && !loading && !error)void fetchCatalog();
    live.hidden=!ctx.canOpenLive || ctx.active;
    retry.hidden=!error;
    const note=error || (loading?'正在读取本地材质库。'
      : !filtered.length?'没有匹配的材质。'
      : submitting?'正在保存材质替换。'
      : !ctx.active?(ctx.canOpenLive?'在主图 3D 中可以拖动试材质。':'选择并分析模型后，可以在主图 3D 中试材质。')
      : !ctx.instance?'实时模型就绪后即可试材质。'
      : ctx.blocked || (hover?`${picked.name} → ${hover.object_name} · ${ctx.commitBlocked || (mode==='drag'?'松开应用':'点击应用')}`
      : picked?`已选 ${picked.name}，点击模型表面应用，Esc 取消。${picked.missing_textures?'部分贴图未安装，使用材质参数预览和渲染。':''}`
      : window.matchMedia?.('(pointer: coarse)').matches?'点选材质，再点模型部件应用。'
      :ctx.commitBlocked?`可拖动试材质。${ctx.commitBlocked}`:'拖到部件上预览，松开应用。也可先选材质，再点部件。'));
    if(hint.textContent!==note)hint.textContent=note;
  }
  function select(item,nextMode){
    if(submitting || !item)return false;
    let ctx=state();
    if(ctx.visible && !ctx.active && ctx.canOpenLive){openLive();ctx=state();}
    if(!ctx.visible || !ctx.active || ctx.blocked){refresh();return false;}
    cancel();activeInstance=ctx.instance;session=ctx.key;
    picked=item;mode=nextMode;setPressed(item);
    if(!activeInstance)pendingProject=ctx.project;
    activeInstance?.renderer.domElement.classList.add('material-targeting');refresh();return true;
  }
  function target(event){
    const ctx=state();
    if(pendingProject===ctx.project && !activeInstance)return null;
    if(!picked || submitting || !ctx.visible || ctx.blocked || ctx.key!==session || ctx.instance!==activeInstance){cancel();return null;}
    const hit=activeInstance.materialTargetAt(event.clientX,event.clientY);
    if(hover!==hit){
      hover=hit;activeInstance.previewLibraryMaterial(hit,picked);refresh();
    }
    return hit;
  }
  function leave(){
    if(!hover || submitting)return;
    hover=null;activeInstance?.previewLibraryMaterial(null,picked);refresh();
  }
  async function submit(event){
    if(pendingProject && !activeInstance){mode='click';refresh();return;}
    const hit=target(event);
    if(!hit){cancel();refresh();return;}
    const ctx=state(),item=picked;
    if(ctx.commitBlocked){onError(ctx.commitBlocked);cancel();refresh();return;}
    submitting=true;refresh();
    const rect=ctx.instance.frame?.getBoundingClientRect();
    const point=rect?.width && rect?.height?[(event.clientX-rect.left)/rect.width,(event.clientY-rect.top)/rect.height]:null;
    try{await apply(ctx,item,hit,point);}
    catch(e){onError(e.message);}
    finally{submitting=false;cancel();refresh();}
  }
  search.addEventListener('input',filter);categories.addEventListener('change',filter);
  live.onclick=openLive;retry.onclick=()=>{error='';refresh();};
  byId('localMaterialPrev').onclick=()=>{if(page>0){cancel();page--;paintPage();refresh();}};
  byId('localMaterialNext').onclick=()=>{if((page+1)*pageSize<filtered.length){cancel();page++;paintPage();refresh();}};
  const card=event=>event.target.closest('.local-material-tile');
  function endGesture(){
    cancelAnimationFrame(dragFrame);dragFrame=0;lastPoint=null;
    const finished=gesture;gesture=null;
    if(finished?.button.hasPointerCapture?.(finished.id))finished.button.releasePointerCapture(finished.id);
    ghost?.remove();ghost=null;
  }
  if(pointerDrag){
    grid.addEventListener('pointerdown',event=>{
      const button=card(event);
      if(!button || event.button!==0 || !event.isPrimary || submitting)return;
      endGesture();suppressClick=false;
      gesture={id:event.pointerId,button,x:event.clientX,y:event.clientY,dragging:false};
    });
    window.addEventListener('pointermove',event=>{
      const candidate=gesture;
      if(!candidate || event.pointerId!==candidate.id)return;
      if(!event.buttons){cancel();refresh();return;}
      if(!candidate.dragging){
        if(Math.hypot(event.clientX-candidate.x,event.clientY-candidate.y)<5)return;
        if(!select(byKey.get(candidate.button.dataset.id),'drag')){endGesture();return;}
        gesture=candidate;gesture.dragging=true;suppressClick=true;
        candidate.button.setPointerCapture?.(event.pointerId);
        ghost=candidate.button.querySelector('img').cloneNode();ghost.className='material-drag-preview';ghost.setAttribute('aria-hidden','true');document.body.append(ghost);
      }
      event.preventDefault();event.stopPropagation();
      lastPoint={clientX:event.clientX,clientY:event.clientY};
      ghost.style.transform=`translate(${event.clientX+12}px,${event.clientY+12}px)`;
      if(!dragFrame)dragFrame=requestAnimationFrame(()=>{dragFrame=0;if(lastPoint && activeInstance)target(lastPoint);});
    },{capture:true,passive:false});
    window.addEventListener('pointerup',event=>{
      if(!gesture || event.pointerId!==gesture.id)return;
      const dragged=gesture.dragging;endGesture();
      if(!dragged)return;
      event.preventDefault();event.stopPropagation();
      const rect=host.getBoundingClientRect();
      if(event.clientX>=rect.left && event.clientX<rect.right && event.clientY>=rect.top && event.clientY<rect.bottom)void submit(event);
      else{cancel();refresh();}
    },true);
    window.addEventListener('pointercancel',event=>{if(gesture?.id===event.pointerId){cancel();refresh();}},true);
    grid.addEventListener('lostpointercapture',event=>{
      if(gesture?.id===event.pointerId && event.target===gesture.button && !gesture.button.hasPointerCapture(event.pointerId)){cancel();refresh();}
    });
  }
  grid.addEventListener('focusin',event=>{
    const button=card(event);if(!button)return;
    const prior=grid.querySelector('[tabindex="0"]');
    if(prior!==button){if(prior)prior.tabIndex=-1;button.tabIndex=0;}
  });
  grid.addEventListener('keydown',event=>{
    const button=card(event);if(!button)return;
    const cards=Array.from(grid.children),index=cards.indexOf(button);
    const nextRow=cards.findIndex(other=>other.offsetTop>cards[0].offsetTop);
    const columns=nextRow>0?nextRow:cards.length;
    const steps={ArrowLeft:-1,ArrowRight:1,ArrowUp:-columns,ArrowDown:columns,Home:-index,End:cards.length-1-index};
    if(!Object.hasOwn(steps,event.key))return;
    event.preventDefault();cards[Math.max(0,Math.min(cards.length-1,index+steps[event.key]))].focus();
  });
  grid.addEventListener('click',event=>{
    if(suppressClick && event.detail){suppressClick=false;event.preventDefault();return;}
    const button=card(event);if(!button)return;
    const item=byKey.get(button.dataset.id);
    if(picked===item && mode==='click'){cancel();refresh();}else select(item,'click');
  });
  grid.addEventListener('dragstart',event=>{
    const button=card(event);
    if(!button || !event.dataTransfer || !select(byKey.get(button.dataset.id),'drag')){event.preventDefault();return;}
    event.dataTransfer.effectAllowed='copy';event.dataTransfer.setData('application/x-wuying-material',picked.id);
  });
  host.addEventListener('dragover',event=>{
    if(mode!=='drag')return;
    event.preventDefault();const hit=target(event);if(event.dataTransfer)event.dataTransfer.dropEffect=hit?'copy':'none';
  });
  host.addEventListener('dragleave',event=>{if(!host.contains(event.relatedTarget))leave();});
  host.addEventListener('drop',event=>{
    if(mode!=='drag')return;
    event.preventDefault();event.stopPropagation();void submit(event);
  });
  document.addEventListener('dragend',()=>{if(mode==='drag'){cancel();refresh();}});
  host.addEventListener('pointermove',event=>{if(mode==='click')target(event);});
  host.addEventListener('pointerleave',leave);
  host.addEventListener('pointerdown',event=>{
    if(mode==='click' && event.button===0 && target(event)){
      pressed={x:event.clientX,y:event.clientY};event.stopPropagation();
    }
  },true);
  host.addEventListener('pointerup',event=>{
    if(pressed && mode==='click'){
      const distance=Math.hypot(event.clientX-pressed.x,event.clientY-pressed.y);pressed=null;
      if(event.button===0 && distance<=5){event.stopPropagation();void submit(event);}
    }
  },true);
  host.addEventListener('pointercancel',()=>{pressed=null;leave();});
  document.addEventListener('keydown',event=>{if(event.key==='Escape' && picked){cancel();refresh();}});
  window.addEventListener('blur',()=>{if(picked || gesture){cancel();refresh();}});
  return {refresh,cancel};
}
