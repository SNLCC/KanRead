// Reading interaction feature: all visual assets below are authored DOM/CSS.
const annotationColors={yellow:'黄色',green:'绿色',blue:'蓝色',pink:'粉色',purple:'紫色',orange:'橙色'};
const annotationStyles={highlight:'高亮',underline:'下划线',margin:'段落侧线',region:'区域框选',page:'整页批注'};
for(const [value,label] of Object.entries(annotationColors))if(!Array.from($('#annotation-color').options).some(o=>o.value===value))$('#annotation-color').append(new Option(label,value));
const annotationStyle=el('select');annotationStyle.id='annotation-style';annotationStyle.setAttribute('aria-label','批注标记样式');for(const [v,t] of Object.entries(annotationStyles))annotationStyle.append(new Option(t,v));$('#annotation-color').parentElement.after(annotationStyle);
const annotationFilterBar=el('div','annotation-filters'),annotationQuery=el('input'),annotationColorFilter=el('select'),annotationTypeFilter=el('select'),annotationContentFilter=el('select');annotationQuery.placeholder='搜索原文或批注';annotationQuery.setAttribute('aria-label','搜索批注');
annotationColorFilter.setAttribute('aria-label','批注颜色筛选');annotationColorFilter.append(new Option('全部颜色',''),...Object.entries(annotationColors).map(([v,t])=>new Option(t,v)));
annotationTypeFilter.setAttribute('aria-label','批注类型筛选');annotationTypeFilter.append(new Option('全部类型',''),...Object.entries(annotationStyles).map(([v,t])=>new Option(t,v)));
annotationContentFilter.setAttribute('aria-label','批注内容筛选');annotationContentFilter.append(new Option('全部内容',''),new Option('有文字批注','with'),new Option('仅标记','without'));
annotationFilterBar.append(annotationQuery,annotationColorFilter,annotationTypeFilter,annotationContentFilter);$('#annotations').before(annotationFilterBar);for(const n of [annotationQuery,annotationColorFilter,annotationTypeFilter,annotationContentFilter])n.addEventListener('input',()=>drawAnnotationList());
function annotationKind(a){return a.rects.length?(a.style||'highlight'):'page';}
const previousOpenAnnotation=openAnnotation;
openAnnotation=function(existing){previousOpenAnnotation(existing);const kind=annotationKind(annotationDraft);annotationStyle.value=kind;annotationStyle.hidden=kind==='page';annotationStyle.querySelector('option[value=page]').disabled=kind!=='page';$('#annotation-color').parentElement.hidden=kind==='page';$('#annotation-quote').textContent=annotationDraft.quote||(kind==='page'?'整页批注 · 不在页面上涂色':'框选区域 · 无需识别文字');};
$('#new-annotation').onclick=()=>{window.getSelection()?.removeAllRanges();setSelection('');openAnnotation();};
$('#annotation-form').onsubmit=async e=>{e.preventDefault();const b=e.submitter;b.disabled=true;try{const a=annotationDraft,body={page:a.page,quote:a.quote,rects:a.rects,content:$('#annotation-content').value,color:$('#annotation-color').value,style:a.rects.length?annotationStyle.value:'page'};await post(`/documents/${a.doc}/annotations${a.id?'/'+a.id:''}`,body,a.id?'PATCH':'POST');$('#annotation-dialog').close();window.getSelection()?.removeAllRanges();setSelection('');await loadAnnotations();toast('批注已保存');}catch(err){toast(err.message);}finally{b.disabled=false;}};
drawAnnotations=function(){const layer=$('#annotation-layer');layer.replaceChildren();for(const a of state.annotations||[]){if(a.page!==state.page||!a.rects.length)continue;const kind=annotationKind(a);let rects=a.rects;if(kind==='margin')rects=[[Math.min(...rects.map(r=>r[0])),Math.min(...rects.map(r=>r[1])),Math.max(...rects.map(r=>r[2])),Math.max(...rects.map(r=>r[3]))]];
 for(const [x0,y0,x1,y1] of rects){const mark=el('div','annotation-mark '+a.color+' mark-'+kind);mark.dataset.annotationId=a.id;Object.assign(mark.style,{left:100*x0+'%',top:100*y0+'%',width:100*(x1-x0)+'%',height:100*(y1-y0)+'%'});layer.append(mark);}}};
async function jumpAnnotation(a){await go(a.page);if(a.rects.length){const r=a.rects[0],d=state.pageData;if(d)showHighlight({bbox:[r[0]*d.width,r[1]*d.height,r[2]*d.width,r[3]*d.height],bbox_space:'visual'});}showAnnotationPopover([a]);}
drawAnnotationList=function(){const list=$('#annotations');list.replaceChildren();const q=annotationQuery.value.trim().toLowerCase();const shown=state.annotations.filter(a=>(!q||(a.quote+'\n'+a.content).toLowerCase().includes(q))&&(!annotationColorFilter.value||(a.rects.length&&a.color===annotationColorFilter.value))&&(!annotationTypeFilter.value||annotationKind(a)===annotationTypeFilter.value)&&(!annotationContentFilter.value||(annotationContentFilter.value==='with')===!!a.content.trim()));
 for(const a of shown){const item=el('article','note annotation-card');item.dataset.annotationId=a.id;if(a.rects.length)item.classList.add('color-'+a.color);const jump=el('button','',`第 ${a.page} 页 · ${annotationStyles[annotationKind(a)]}${a.rects.length?' · '+annotationColors[a.color]:''}`);jump.onclick=()=>jumpAnnotation(a);item.append(jump,el('blockquote','',a.quote),el('p','',a.content||'仅标记，无文字批注'));const edit=el('button','secondary','编辑'),ask=el('button','secondary','引用到提问'),remove=el('button','secondary danger','删除');edit.onclick=()=>openAnnotation(a);ask.onclick=()=>askAboutAnnotation(a);remove.onclick=()=>deleteAnnotation(a);item.append(edit,ask,remove);list.append(item);}if(!shown.length)list.append(el('p','hint','没有符合条件的批注。'));};
// 批注弹窗：可以被拖动，也可以拖四条边/右下角改大小。
// 用户要求："批注弹窗应当可以挪动位置和通过拖拽边缘调节大小。"
// 位置与大小记在本机（localStorage），下次打开还在原处；拖动只改弹窗自身，不触碰批注数据与原文。
// 抓手做成一条**标题栏**（含"批注"标题与复位按钮），而不是一条 12px 的细缝：
// 第一版只有细缝可拖，用户按在弹窗其它地方时毫无反应（用户反馈"仍旧无法拖动"）。
const annotationPopover=el('div','annotation-popover');annotationPopover.hidden=true;annotationPopover.setAttribute('role','dialog');annotationPopover.setAttribute('aria-label','阅读区批注');
const annotationPopoverHead=el('div','annotation-popover-head');
const annotationPopoverTitle=el('span','annotation-popover-title','批注');
const annotationPopoverGrip=el('div','annotation-popover-grip');
const annotationPopoverClose=el('button','close','×');annotationPopoverClose.setAttribute('aria-label','关闭批注');annotationPopoverClose.onclick=()=>{annotationPopover.hidden=true;};
const annotationPopoverReset=el('button','annotation-popover-reset','复位');annotationPopoverReset.type='button';annotationPopoverReset.title='把批注窗口放回默认位置与大小';
const annotationPopoverBody=el('div','annotation-popover-body');
annotationPopoverHead.append(annotationPopoverTitle,annotationPopoverGrip,annotationPopoverReset,annotationPopoverClose);
annotationPopover.append(annotationPopoverHead,annotationPopoverBody);
// 边缘与右下角的缩放手柄：都是普通 div（不是按钮），不参与"弹窗里有哪几个动作"的断言。
// 手柄贴在弹窗内沿（负偏移会被 overflow 裁掉，第一版就是这么失效的）。
for(const dir of ['n','s','e','w','se']){const handle=el('div','annotation-resize');handle.dataset.dir=dir;handle.title='拖动这里可以改大小';handle.setAttribute('aria-hidden','true');annotationPopover.append(handle);}
$('.reader').append(annotationPopover);
// —— 可拖动 / 可缩放的浮层：阅读区批注弹窗与"添加批注"表单窗口共用这一份实现（只有一处真相）——
// 拖头部（或浮层自身的留白）移动，拖四条边与右下角改大小；位置与大小记在本机，越界自动夹回。
const FLOATING_BOX_MIN={width:220,height:140};
function floatingBoxBounds(element){
 // 普通定位的浮层按包含块算；固定定位的模态 <dialog> 没有 offsetParent，按窗口算——
 // 两种情况下 offsetLeft/offsetTop 都以各自的原点为基准，所以夹取口径一致。
 const parent=element.offsetParent;
 if(parent&&parent.clientWidth&&parent.clientHeight)return {width:parent.clientWidth,height:parent.clientHeight};
 if(typeof window!=='undefined'&&window.innerWidth)return {width:window.innerWidth,height:window.innerHeight};
 return {width:1024,height:768};
}
function readFloatingBox(key){try{return JSON.parse(localStorage.getItem(key)||'null');}catch(error){return null;}}
function saveFloatingBox(key,box){try{localStorage.setItem(key,JSON.stringify(box));}catch(error){}}
function clearFloatingBox(key){try{localStorage.removeItem(key);}catch(error){}}
function clampFloatingBox(element,box){
 const bounds=floatingBoxBounds(element);
 const width=Math.max(FLOATING_BOX_MIN.width,Math.min(box.width,bounds.width-8));
 const height=Math.max(FLOATING_BOX_MIN.height,Math.min(box.height,bounds.height-8));
 return {left:Math.max(0,Math.min(box.left,bounds.width-width)),top:Math.max(0,Math.min(box.top,bounds.height-height)),width,height};
}
// 一旦被拖动/缩放，浮层就脱离原来的锚点（right/bottom/margin），否则它们会和 left/width 打架。
function pinFloatingBox(element,box){Object.assign(element.style,{right:'auto',bottom:'auto',margin:'0',maxWidth:'none',maxHeight:'none',left:box.left+'px',top:box.top+'px',width:box.width+'px',height:box.height+'px'});}
// 只在浏览器里生效；测试替身没有 document/localStorage 时直接跳过（内容逻辑照常被测）。
function placeFloatingBox(element,key){
 if(typeof document==='undefined'||typeof localStorage==='undefined'||typeof element.getBoundingClientRect!=='function')return false;
 const box=readFloatingBox(key);
 if(!box)return false;
 pinFloatingBox(element,clampFloatingBox(element,box));
 return true;
}
function resetFloatingBox(element,key){
 clearFloatingBox(key);
 for(const name of ['left','top','right','bottom','width','height','maxWidth','maxHeight','margin'])element.style[name]='';
}
// 接线：head（整条标题栏可拖）、surfaces（浮层自身留白可拖）、.annotation-resize（四边 + 右下角）、复位按钮。
function wireFloatingBox(element,options){
 if(typeof document==='undefined'||typeof document.addEventListener!=='function'||!element.addEventListener)return false;
 const key=options.key,head=options.head,surfaces=options.surfaces||[];
 const persist=()=>saveFloatingBox(key,{left:element.offsetLeft,top:element.offsetTop,width:element.offsetWidth,height:element.offsetHeight});
 const start=(event,dir)=>{
  if(event.button!==undefined&&event.button!==0)return;
  const begin={x:event.clientX,y:event.clientY,left:element.offsetLeft,top:element.offsetTop,width:element.offsetWidth,height:element.offsetHeight};
  pinFloatingBox(element,begin);
  element.classList.add('dragging');
  const move=moveEvent=>{
   const dx=moveEvent.clientX-begin.x,dy=moveEvent.clientY-begin.y;
   let left=begin.left,top=begin.top,width=begin.width,height=begin.height;
   if(!dir){left=begin.left+dx;top=begin.top+dy;}
   else{
    if(dir.includes('e'))width=begin.width+dx;
    if(dir.includes('s'))height=begin.height+dy;
    if(dir.includes('w')){width=begin.width-dx;left=begin.left+dx;}
    if(dir.includes('n')){height=begin.height-dy;top=begin.top+dy;}
   }
   const fitted=clampFloatingBox(element,{left,top,width,height});
   Object.assign(element.style,{left:fitted.left+'px',top:fitted.top+'px',width:fitted.width+'px',height:fitted.height+'px'});
   moveEvent.preventDefault();
  };
  const finish=()=>{document.removeEventListener('pointermove',move);document.removeEventListener('pointerup',finish);document.removeEventListener('pointercancel',finish);element.classList.remove('dragging');persist();};
  document.addEventListener('pointermove',move);document.addEventListener('pointerup',finish);document.addEventListener('pointercancel',finish);
  event.preventDefault();event.stopPropagation();
 };
 // 表单控件与标签不触发拖动；正文（段落/摘引）留给文字选择与点击。
 const draggable=target=>!target.closest('button,a,input,textarea,select,label');
 if(head)head.addEventListener('pointerdown',event=>{if(draggable(event.target))start(event,'');});
 for(const surface of surfaces)surface.addEventListener('pointerdown',event=>{if(event.target===surface&&draggable(event.target))start(event,'');});
 for(const handle of element.querySelectorAll('.annotation-resize'))handle.addEventListener('pointerdown',event=>start(event,handle.dataset.dir));
 if(options.resetButton)options.resetButton.onclick=()=>{resetFloatingBox(element,key);if(options.onReset)options.onReset();};
 if(typeof window!=='undefined'&&window.addEventListener)window.addEventListener('resize',()=>{if(readFloatingBox(key))placeFloatingBox(element,key);});
 return true;
}
// 阅读区批注弹窗：下面这些旧函数名保留（它们就是这段行为的既有接口，测试与其它代码都在用）。
const ANNOTATION_POPOVER_MIN=FLOATING_BOX_MIN;
function annotationPopoverBounds(){return floatingBoxBounds(annotationPopover);}
function readAnnotationPopoverBox(){return readFloatingBox('reader-annotation-popover');}
function saveAnnotationPopoverBox(box){saveFloatingBox('reader-annotation-popover',box);}
function clampAnnotationPopover(box){return clampFloatingBox(annotationPopover,box);}
function applyAnnotationPopoverPlacement(){return placeFloatingBox(annotationPopover,'reader-annotation-popover');}
// 阅读区点批注后的弹窗：以前只能看内容和"编辑批注"，删不掉、也带不进提问。
// 现在每条批注给出三个动作，并且都走与批注列表相同的实现（删除可撤销）。
function showAnnotationPopover(items){
 annotationPopoverBody.replaceChildren();
 for(const a of items){
  annotationPopoverBody.append(el('strong','',annotationStyles[annotationKind(a)]+' · 第 '+a.page+' 页'));
  if(a.quote)annotationPopoverBody.append(el('blockquote','annotation-quote',a.quote));
  annotationPopoverBody.append(el('p','',a.content||'仅标记，无文字批注'));
  const actions=el('div','annotation-actions');
  const ask=el('button','secondary','引用到提问');ask.title='把这条批注的原文带入选文，并把批注写进问题框';
  ask.onclick=()=>{annotationPopover.hidden=true;askAboutAnnotation(a);};
  const edit=el('button','secondary','编辑批注');
  edit.onclick=()=>{annotationPopover.hidden=true;openAnnotation(a);};
  const remove=el('button','secondary danger','删除批注');
  remove.onclick=async()=>{
   // 删除后刷新弹窗：同一处可能叠着多条批注，只移除被删掉的那一条。
   const removed=await deleteAnnotation(a);
   if(!removed)return;
   const alive=items.filter(item=>state.annotations.some(x=>x.id===item.id));
   if(alive.length)showAnnotationPopover(alive);else annotationPopover.hidden=true;
  };
  actions.append(ask,edit,remove);annotationPopoverBody.append(actions);
 }
 annotationPopover.hidden=false;
 applyAnnotationPopoverPlacement();
}
let annotationClickStart=null;
// ——「添加批注 / 编辑批注」表单窗口 ——
// 用户报的"批注弹窗无法拖动、无法缩放"，截图里就是这张表单（标题是「添加批注 · 第 N 页」）。
// 上一轮只给阅读区那个小弹窗接了拖动/缩放，这张表单没接，所以无论怎么刷新都拖不动。
// 现在两者共用上面的 wireFloatingBox：同一份实现、同样的手柄、同样记在本机。
function annotationDialogNode(){return typeof $==='function'?$('#annotation-dialog'):null;}
function placeAnnotationDialog(){const dialog=annotationDialogNode();return dialog?placeFloatingBox(dialog,'reader-annotation-dialog'):false;}
function wireAnnotationDialogGestures(){
 const dialog=annotationDialogNode();
 if(!dialog||!dialog.addEventListener||typeof dialog.querySelectorAll!=='function'||typeof el!=='function')return false;
 // 缩放手柄与阅读区弹窗同款（普通 div，不是按钮，不参与"窗口里有哪几个动作"的断言）。
 if(!dialog.querySelectorAll('.annotation-resize').length)for(const dir of ['n','s','e','w','se']){const handle=el('div','annotation-resize');handle.dataset.dir=dir;handle.title='拖动这里可以改大小';handle.setAttribute('aria-hidden','true');dialog.append(handle);}
 const surfaces=[dialog],form=$('#annotation-form');
 if(form)surfaces.push(form);
 return wireFloatingBox(dialog,{key:'reader-annotation-dialog',head:$('#annotation-dialog-head'),surfaces,resetButton:$('#annotation-dialog-reset')});
}
wireAnnotationDialogGestures();
// 拖动与缩放的实际接线。放在 showAnnotationPopover 之后：弹窗内容逻辑不依赖浏览器环境，
// 而这里要用到 document / window / classList。位置与大小都夹在阅读区内，不会拖出可视区。
function wireAnnotationPopoverGestures(){
 return wireFloatingBox(annotationPopover,{key:'reader-annotation-popover',head:annotationPopoverHead,surfaces:[annotationPopover],resetButton:annotationPopoverReset,onReset:()=>{annotationPopover.style.right='18px';}});
}
wireAnnotationPopoverGestures();
$('#paper').addEventListener('pointerdown',e=>{annotationClickStart={x:e.clientX,y:e.clientY};});
$('#paper').addEventListener('pointerup',e=>{if(!annotationClickStart||state.crop||Math.hypot(e.clientX-annotationClickStart.x,e.clientY-annotationClickStart.y)>4||window.getSelection()?.toString().trim())return;const box=$('#paper').getBoundingClientRect(),x=(e.clientX-box.left)/box.width,y=(e.clientY-box.top)/box.height;const hits=state.annotations.filter(a=>a.page===state.page&&a.rects.some(r=>x>=r[0]-.015&&x<=r[2]&&y>=r[1]&&y<=r[3]));if(hits.length)showAnnotationPopover(hits);else annotationPopover.hidden=true;});
// Direct scan annotation shares the existing crop coordinates without invoking OCR.
let regionAnnotation=false;const regionButton=el('button','','框选批注');regionButton.type='button';regionButton.title='扫描页也可直接框选添加批注';$('#crop').after(regionButton);
const originalCropToggle=$('#crop').onclick;$('#crop').onclick=()=>{regionAnnotation=false;originalCropToggle();};
const originalReaderReset=resetReaderInteraction;resetReaderInteraction=function(){regionAnnotation=false;annotationPopover.hidden=true;originalReaderReset();};
regionButton.onclick=()=>{if(!state.pageData||state.toolBusy)return;$('#crop').click();regionAnnotation=state.crop;if(regionAnnotation)toast('拖动框选批注区域，无需 OCR');};
const ocrPointerUp=$('#paper').onpointerup;
$('#paper').onpointerup=async e=>{if(!regionAnnotation)return ocrPointerUp(e);regionAnnotation=false;if(!startPoint)return;const end=point(e),start=startPoint;startPoint=null;$('#crop-box').style.display='none';state.crop=false;$('#paper').classList.remove('cropping');$('#crop').classList.remove('active');if(Math.abs(start.x-end.x)<.005||Math.abs(start.y-end.y)<.005)return;setSelection('',[[Math.min(start.x,end.x),Math.min(start.y,end.y),Math.max(start.x,end.x),Math.max(start.y,end.y)]]);openAnnotation();annotationStyle.value='region';};
// Inline citation controls are placed only into text nodes, never code or links.
// 引用与摘引的配对**必须自证**：候选摘引得真的出现在它标注的那个来源的原文里，否则宁可不配。
// 背景（用户反馈"回答中的内容和标识、跳转的参考不同"）：以前按"第几个引用标记"数着取绑定，
// 段号与出现顺序一旦不一致就会错位，把 A 段的证据挂到 B 段的引用上——用户点开就看到另一段原文。
// 现在按段号取（后端给出 binding.line），并逐条校验"摘引确实在本文献这一页里"，两道都不通过就退回
// 来源自己的摘引/定位锚点，绝不显示一段对不上的原文。
function normalizedText(value){return String(value==null?'':value).replace(/\s+/g,'');}
function citationBindingFor(bindings,sources,lineNumber,index){
 const source=sources[index-1];if(!source)return '';
 const flat=normalizedText(source.text);
 for(const item of (bindings||[])){
  if(item.line!==lineNumber||item.index!==index||!item.quote)continue;
  if(flat&&!flat.includes(normalizedText(item.quote)))continue;   // 对不上就当作没有这条绑定
  return item.quote;
 }
 return '';
}
function linkCitations(node,sources,meta){const web=meta.sections?.find(s=>s.kind==='web')?.results||[];const bindings=meta.coverage?.citation_bindings||[];let lineNumber=0;
 const sections=[...node.querySelectorAll('.answer-section')],roots=sections.length?sections:[node];
 for(const root of roots){
  const bound=root.classList.contains('source-document');
  const walk=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);const texts=[];while(walk.nextNode())if(!walk.currentNode.parentElement.closest('a,button,code,pre,.sources,.role,.source-label,.reading-report'))texts.push(walk.currentNode);
  for(const text of texts){const matches=[...text.textContent.matchAll(/\[(W?)(\d+)\]/g)];if(!matches.length)continue;if(bound)lineNumber++;const fragment=document.createDocumentFragment();let offset=0;for(const m of matches){fragment.append(document.createTextNode(text.textContent.slice(offset,m.index)));const index=Number(m[2])-1,source=m[1]?web[index]:sources[index];if(!source)fragment.append(document.createTextNode(m[0]));else{
    let quote='';
    if(!m[1]&&bound)quote=citationBindingFor(bindings,sources,lineNumber,index+1);
    // 没有可信绑定（非文献小节、编号/段号对不上、摘引不在该来源里）时依次退回：来源自己的摘引 → 定位锚点。
    if(!quote&&!m[1])quote=(source.quotes||[])[0]||source.anchor||'';
    const b=el('button','inline-citation',m[0]);b.title=m[1]?source.title:(source.name+' · 第 '+source.page+' 页'+(quote?'\n引用原文：'+quote:'\n这一处没有可逐字核验的摘引，只能定位到该页'));
    b.onclick=()=>{if(m[1]){try{const url=new URL(source.url);if(['http:','https:'].includes(url.protocol))window.open(url.href,'_blank','noopener,noreferrer');}catch{}}else cite(source,quote,b);};fragment.append(b);}offset=m.index+m[0].length;}fragment.append(document.createTextNode(text.textContent.slice(offset)));text.replaceWith(fragment);}}
}
// Phase 7：来源路由的显示口径（**纯文本函数，便于单独测**）。
// 它要说清三件事，而且三件事必须分开说：
//   ① 这条论断需要多硬的证据、当前文献答到什么程度（决定"该不该去找外部资料"）；
//   ② 已经获授权、本轮也确实用了外部资料 —— 那是"已经查过"；
//   ③ 需要外部资料但**没有授权** —— 只能说"没有执行"，绝不能写成"查过了"。
// 未获授权时等级一律取下限（见 review.route_claims），因此这里的 LIGHT 只可能来自
// "模型逐条判定只需解释现有材料、且该段确有原文支持"这一种情形。
const SOURCE_KIND_NAMES={CURRENT_DOCUMENT:'本篇文献',LIBRARY_DOCUMENT:'本机文献库',PRIMARY_EXTERNAL_SOURCE:'一手外部资料',
 SCHOLARLY_REFERENCE:'学术参考资料',OFFICIAL_SOURCE:'官方来源',GENERAL_WEB:'一般网页',MODEL_BACKGROUND:'模型背景知识',SYSTEM_INFERENCE:'系统推断'};
const CLAIM_TYPE_NAMES={DOCUMENT_INTERPRETATION:'本文作者的解读',CONCEPT_DEFINITION:'概念界定',SCHOLARLY_POSITION:'学术立场',
 HISTORICAL_FACT:'史实',BIBLIOGRAPHIC_FACT:'文献事实',CONTESTED_INTERPRETATION:'有争议的解读',
 CURRENT_INFORMATION:'需要时效性的信息',BACKGROUND_EXPLANATION:'背景解释'};
const EVIDENCE_STANDARD_NAMES={LIGHT:'只需解释现有材料',GROUNDED:'需要可核验依据',SCHOLARLY:'需要学术研究级依据'};
function countPhrase(counts,names){return Object.entries(counts||{}).filter(([,n])=>n>0)
 .map(([key,n])=>`${names[key]||key} ${n} 段`).join('、');}
function sourceRoutingNote(routing){
 if(!routing)return '';
 const summary=routing.summary||{},kinds=(routing.blocked_kinds||[]).map(kind=>SOURCE_KIND_NAMES[kind]||kind);
 const parts=[];
 if(summary.claims)parts.push(`本轮回答按论断性质逐段核对来源：共 ${summary.claims} 段`
  +(countPhrase(summary.claim_types,CLAIM_TYPE_NAMES)?`（${countPhrase(summary.claim_types,CLAIM_TYPE_NAMES)}）`:'')
  +(countPhrase(summary.standards,EVIDENCE_STANDARD_NAMES)?`；所需依据：${countPhrase(summary.standards,EVIDENCE_STANDARD_NAMES)}`:'')+'。');
 if(summary.external)parts.push(`其中 ${summary.external} 段已经用外部资料（联网网页）作为依据。`);
 if(kinds.length)parts.push(`另有论断按性质需要${kinds.join('、')}，但本轮没有获授权，因此没有执行任何外部查证——报告里不会把它写成已经查过。`
  +'若确实需要，可在设置的联网搜索里授权后重问。');
 return parts.join('');
}
// Phase 7（A）：把一个"证据缺口"变成一次**定向外部查证**。
//   - 它复用完全现成的联网路径：勾选 web → /search-plan 规划关键词 → 用户批准 → /api/chat；
//     这里不新增任何外发点，"什么时候发"仍然由用户在批准框上决定；
//   - gap_* 三项只是把**已经发给过模型**的那一小段再指认一次（段号、该段自报的性质、它的摘引），
//     因此没有扩大外发面；没有摘引时就只报段号与性质，绝不为此重新读一遍文献。
//   - 没有授权（permission='off'）时按钮改成"去设置里授权"，不发任何请求——不能点一下却什么都没发生。
function sourceKindNames(kinds){return (kinds||[]).map(kind=>SOURCE_KIND_NAMES[kind]||kind);}
function gapQuoteOf(plan){
 const evidence=(plan&&plan.evidence)||[];
 const quote=evidence.map(item=>item&&item.quote).find(text=>typeof text==='string'&&text.trim());
 return (quote||'').trim().slice(0,600);
}
function startGapCheck(claim,plan){
 if(typeof state==='undefined'||!state.doc)return toast('请先打开一篇文献。');
 const kinds=sourceKindNames((plan||{}).blocked_external);
 const type=(plan&&plan.claim_type)||'';
 const label='第 '+claim+' 段';
 const question=`请为${label}的论断补上外部依据：${CLAIM_TYPE_NAMES[type]||'该论断'}`
  +(kinds.length?`（需要${kinds.join('、')}）`:'')+'。只说明这一点，不要把文献分析重讲一遍。';
 const body={...payload(question),web:true,gap_claim:claim,gap_type:CLAIM_TYPE_NAMES[type]?type:'',gap_quote:gapQuoteOf(plan)};
 if(!startProgrammaticChat(body))return;
 toast('已按这一处缺口发起查证；联网关键词仍需你在批准框里确认。');
}
// 某个论断"值得去查外部依据"吗：需要外部资料、但**还没有**用上（`blocked_external` 非空）。
// 没有权限时按钮改成去设置（不发请求）；已经用上外部资料的论断不再给按钮（免得重复查证）。
function gapPlans(routing){
 // Phase 8：来源策略选"仅文献"时后端不会发出任何外部请求，界面因此也不该给"去查证"的入口
 // ——点了只会拿到一句 403。要查证就先把它改成"自动（按隐私权限）"。
 if($('#source-policy')?.value==='document_only')return [];
 return ((routing||{}).plans||[]).filter(plan=>plan.claim&&plan.blocked_external&&plan.blocked_external.length);
}
function gapCheckButtons(routing){
 const plans=gapPlans(routing);if(!plans.length)return null;
 const box=el('p','hint');box.append(document.createTextNode('可以针对某一处缺口去查外部资料：'));
 for(const plan of plans){
  const kinds=sourceKindNames(plan.blocked_external);
  const button=el('button','secondary',`查证第 ${plan.claim} 段（${kinds.join('、')}）`);
  button.type='button';
  button.onclick=()=>startGapCheck(plan.claim,plan);
  box.append(button);
 }
 return box;
}
const originalRenderMessage=renderMessage;
renderMessage=function(role,text,sources=[],meta={}){const node=originalRenderMessage(role,text,sources,meta);if(role==='assistant'){linkCitations(node,sources,meta);
 // 没读文献时说明"已经核对过什么"：用户不必猜是不是判断错了。
 if(meta.route_note)node.prepend(el('small','coverage',meta.route_note));
 if(meta.coverage){const c=meta.coverage;node.prepend(el('small','coverage',`${c.strategy} · 已读取文字 ${c.indexed_pages}/${c.total_pages} 页${c.batches?' · '+c.batches+' 批':''}`));
 if(c.documents){const details=el('details','reading-report');details.append(el('summary','','本次阅读范围与原文'));details.append(el('p','',c.reason+'（'+c.planner+'）'),el('p','',`范围内 ${c.candidate_documents} 篇；实际选择 ${c.selected_documents} 篇。${c.multi_pass?'原文已逐批读取；最终综合使用派生阅读记录与可核验摘引，仍可能存在压缩损失。':'最终模型直接接收以下连续原文。'}`));
  // "为什么这一轮又要读一遍"：页面文字与视觉解读命中本机缓存，模型阅读才是按本轮问题重来的。
  if(c.reading_reuse)details.append(el('p','hint',c.reading_reuse));
 for(const d of c.documents){details.append(el('p','',`${d.name}：读取 ${d.read_pages.length}/${d.total_pages} 页${d.whole_document?'（完整文献范围）':'（局部范围）'}${d.missing_pages.length?'；未提取文字页：'+d.missing_pages.join('、'):''}${d.ocr_pages.length?'；OCR 页：'+d.ocr_pages.join('、'):''}`));}
 details.append(el('p','hint','按章节范围读取，进行文本质量检查和双栏重排；复杂页面可启用视觉读取。已读取或自动核对不等于模型必然正确。'+(c.estimator||'')));
 if(c.resumed_batches)details.append(el('p','',`从本机恢复 ${c.resumed_batches} 批，未重复调用这些批次。`));
 // 模型有时会把自己分析里的一句话当成原文摘引（例如"审视其理论框架是否契合……"）。
 // 那些句子无法逐字核验，因此被丢弃——必须说出来，否则用户会以为它们本来就在文献里。
 // Phase 6：这篇文献**对这个问题**答到什么程度（只根据已核验证据的角色判断）。
 // "只是提到"与"给出定义/解释"必须分开说，否则回答会显得有据可依、实际答非所问。
 if(c.answerability){const names={SUFFICIENT:'文献给出了界定或解释',PARTIAL:'文献提供了相关材料但没有直接界定或解释',MENTION_ONLY:'文献只是提到该概念，没有进一步解释',ABSENT:'在本次阅读范围内没有发现相关内容',CONFLICTING:'文献内部存在不同表述',UNCERTAIN:'本轮没有查全，无法判断文献里有没有'};
  const answer=names[c.answerability]||c.answerability;
  details.append(el('p',c.answerability==='ABSENT'?'hint':'',`文献对这个问题：${answer}${c.answerability==='MENTION_ONLY'||c.answerability==='PARTIAL'?'（如需权威解释，可让系统去查专业参考资料）':''}。`));}
 if(c.quotes_dropped)details.append(el('p','hint',`有 ${c.quotes_dropped} 条"摘引"在原文里找不到逐字对应的句子，已丢弃、未作为文献原文使用${c.quotes_dropped_samples?.length?'（例如「'+c.quotes_dropped_samples[0].slice(0,60)+'…」）':''}。这多半是模型用自己的话转述而非照抄原文。`));
 // 证据银行：各批已逐字核验的摘引原样带进最终综合，**不参与压缩**。
 // 只有真的因为容量上限截断时才需要提示，而且必须说清少了几条。
 if(c.evidence_bank?.kept)details.append(el('p','',`各批已逐字核验的原文摘引 ${c.evidence_bank.kept} 条已完整带给最终综合（压缩只压缩分析，不动摘引）。`));
 if(c.evidence_bank?.dropped)details.append(el('p','error',`有 ${c.evidence_bank.dropped} 条已逐字核验的摘引因容量上限未能进入最终综合（已保留 ${c.evidence_bank.kept}/${c.evidence_bank.available} 条）。这是本次容量不够，不是文献里没有。`));
 // Phase 7：来源路由。**建议与执行必须分开说**——没有授权时只说明"本该去找什么"，
 // 绝不让人以为已经查过（外部来源也不能改写上面那句"文献对这个问题"的口径）。
 const routingNote=sourceRoutingNote(c.source_routing);
 if(routingNote)details.append(el('p','hint',routingNote));
 // 逐处缺口给一个"查证"入口：点了走完全现成的联网批准路径（见 startGapCheck）。
 const gapButtons=gapCheckButtons(c.source_routing);
 if(gapButtons)details.append(gapButtons);
 // 阅读范围是模型猜的、正文说了算：本机核对补读了哪几页、缺口检索有没有查全，都要如实写出来。
 if(c.lexical_sweep?.added_pages?.length)details.append(el('p','',`本机按问题术语在全文里核对后，把计划范围之外命中相关内容的 ${c.lexical_sweep.added_pages.length} 页（第 ${c.lexical_sweep.added_pages.join('、')} 页）一并读入。`));
 if(c.discovery)details.append(el('p','',`跨文献顺序：按本机命中数先读相关的 ${c.discovery.hit_documents}/${c.discovery.candidates} 篇（当前文献优先）。${c.discovery.note}`));
 // 语义宽召回（Phase 3 后半）：词面找不到的同义表达由它补。
 // 三件事必须说清：语义找到的那几页是**另外**哪些页、这一路跑没跑、没跑是为什么。
 // 证伪检索（§36）：系统**主动**去找过"反对 / 限制 / 修正"的那几页。
 // 必须说清三件事：查了多深、在哪几页看到反面迹象、以及那几页里说的是什么（逐句原文）。
 // Phase 7 的 B：**链路自己**发现缺口并发起的外部查证。
 // 必须说清三件事：系统凭什么认为缺依据、去查了没有、结果如何。
 if(c.auto_source_search){
  const auto=c.auto_source_search;
  if(auto.claims?.length)details.append(el('p','',`系统主动核对：本轮有 ${auto.claims.length} 处论断按性质需要外部依据（${auto.reason}）。${auto.status}`));
  if(auto.queries?.length)details.append(el('p','hint','为这些缺口规划的主题词：'+auto.queries.map(q=>`「${q}」`).join('、')));
  if(auto.approved===false)details.append(el('p','hint','你未同意这次追加搜索，文献结论保持不变。'));
 }
 if(c.lexical_sweep?.falsification){
  const fal=c.lexical_sweep.falsification;
  if(fal.contrast_pages?.length){
   details.append(el('p','',`已主动核对反面：在 ${fal.scanned_pages} 页里查"然而/反例/据此修正"这类转折与自省表述，第 ${fal.contrast_pages.join('、')} 页有反面迹象${fal.added_pages?.length?`（其中第 ${fal.added_pages.join('、')} 页已补入本轮阅读）`:''}。`));
   const lines=[];
   for(const [page,sentences] of Object.entries(fal.evidence||{}))lines.push(`第 ${page} 页：「${sentences[0]}」`);
   if(lines.length)details.append(el('p','hint','反面迹象的原文：'+lines.join('；')));
   else details.append(el('p','hint','这几页含转折或自省表述，但没能切出完整的反面句；可点开原文自行核对。'));
   if(fal.truncated)details.append(el('p','hint',`还有 ${fal.truncated} 页有反面迹象但超出本轮补读上限，未读入。`));
  }else details.append(el('p','hint',`已主动核对反面：在 ${fal.scanned_pages} 页里查过转折与自省表述，本轮没有发现反面迹象——这是对**本机已索引文字**的陈述，不等于文献里一定没有。`));
 }
 if(c.lexical_sweep?.semantic){
  const sem=c.lexical_sweep.semantic;
  if(sem.pages?.length)details.append(el('p','',`语义比对又找到 ${sem.pages.length} 页可能相关的位置（第 ${sem.pages.join('、')} 页）；这些页词面上没有任何匹配，因此只可能是换了一种说法。`));
  if(sem.ran)details.append(el('p','hint',`语义比对比对了 ${sem.scanned_pages} 页文字${sem.pages?.length?'':'，没有达到阈值的页'}。`));
  else details.append(el('p','hint',`语义比对这一路没有运行：${sem.note}`));
 }
 if(c.gap_search&&c.gap_search.terms?.length)details.append(el('p','hint',`缺口检索：在本机索引到的 ${c.gap_search.scanned_pages} 页文字里核对 ${c.gap_search.terms.length} 个问题术语`
  +(c.gap_search.missing_terms?.length?`（其中 ${c.gap_search.missing_terms.length} 个一次都没命中）`:'（全部有命中）')
  +(c.gap_search.read_now?.length?`；补读了之前没读到的第 ${c.gap_search.read_now.join('、')} 页`:'')
  +(c.gap_search.ran?'；命中页都已读过，"未见到"可以按全文口径说明。':'；**这次没有查全**，"未见到"只按更弱的范围说明。')));
 // 覆盖台账：这一轮**检查了哪些方面**、哪些方面本批没读到，逐行写清楚。
 // 状态必须照实显示："本批未见到"只说明分给模型的这一批原文里没有，不等于文献里没有；
 // 有页读不了时只能说"不确定"，并把页码列出来——用户要能判断是"作者没写"还是"没读到"。
 if(c.coverage_ledger?.length){const ledger=el('details');ledger.append(el('summary','','本次回答的覆盖台账'));
  const ledgerNames={FOUND:'已找到',NOT_FOUND_IN_BATCH:'本批未见到',NOT_RECORDED:'未逐批记账',UNCERTAIN:'不确定（有页读不了）',
   NOT_SEARCHABLE:'整篇没有可检索的文字',NOT_FOUND_IN_READ_SCOPE:'本次阅读范围内未见到',
   NOT_FOUND_AFTER_INDEXED_TEXT_SEARCH:'已索引文字里未见到（整篇索引都查过）',
   NOT_FOUND_AFTER_FULL_DOCUMENT_REVIEW:'全文检查未见到（整篇读完且全文核对过）'};
  for(const row of c.coverage_ledger){ledger.append(el('p','',`${row.label}：${ledgerNames[row.status]||row.status}`
   +(row.evidence?.length?`（${row.evidence.length} 条已核验摘引）`:'')
   +(row.unreadable_pages?.length?`；读不了的页：${row.unreadable_pages.join('、')}`:'')
   +(row.reason?`；${row.reason}`:'')));}
  ledger.append(el('p','hint','这几档说的话**不一样**："本批未见到"只说明分给模型的这一批原文里没有；'
   +'"本次阅读范围内未见到"只覆盖实际读到的页；"已索引文字里未见到"要真的把本机索引到的文字查过一遍；'
   +'只有"整篇读完 + 全文核对过 + 没有读不了的页"才允许说"全文检查未见到"。有页读不了时，'
   +'任何一档都只会是"不确定"，并列出是哪几页。'));
  if(c.coverage_note)ledger.append(el('p','hint',c.coverage_note));
  details.append(ledger);}
 if(c.reread_status)details.append(el('p','hint',c.reread_status));
 if(c.reread_sources?.length)details.append(el('p','','补充回读原文：'+c.reread_sources.map(n=>'['+n+']').join('、')));
 // 正文里有据可查的页码保留，凭印象写的页码会被删除；删了什么必须说清楚，否则等于静默改稿。
 if(c.removed_page_mentions?.length)details.append(el('p','hint',`已删除 ${c.removed_page_mentions.length} 处与引用不符的页码表述（${c.removed_page_mentions.map(item=>'第'+item.claim+'段「'+item.text+'」').slice(0,6).join('、')}）。页码请以引用标记与原文为准。`));
 // 引用能不能落到具体文字，直接在报告里说清楚（含"只有页码"的编号）。
 if(c.citation_support){const s=c.citation_support;
  details.append(el('p','hint',`本段共 ${s.citations} 处引用：${s.verified} 处有逐字核验的摘引，${s.anchor_only} 处按正文用词定位，`
   +(s.page_only?.length?`${s.page_only.length} 处只有页码（${s.page_only.map(n=>'['+n+']').join('、')}，正文用的是改写表述）`:'没有只有页码的引用')+'。'));}
 // 部分阅读（第 96 轮）：这一轮读到哪、还剩哪些页没读。以前这种情形整轮失败、什么都不给；
 // 现在给答案，但必须把"结论只基于已读部分"写在最显眼的地方（挂在报告标题那一行）。
 const partialDoc=(c.documents||[]).find(d=>d.partial);
 if(partialDoc){const read=partialDoc.read_pages||[],unread=partialDoc.unread_pages||[];
  node.prepend(el('small','coverage',
   `本轮已完整读取 ${read.length} 页（共 ${partialDoc.total_pages} 页，`
   +`未读 ${unread.length} 页）：以下结论只基于已读部分；点「继续阅读」接着读，已读部分不会重读。`));}
 for(const warning of c.warnings||[])details.append(el('p','hint',warning));
 if(typeof c.support_review?.reused_claims==='number')details.append(el('p','hint',`核验分组 ${c.support_review.audit_groups||1} 组；复用 ${c.support_review.reused_claims} 条已核验论断（摘引已重新比对当前原文）。`));
 if(c.web_focus)details.append(el('p','hint',`联网补充围绕 ${c.web_focus.items?.length||0} 个待补问题${c.web_focus.deferred?'，另有 '+c.web_focus.deferred+' 个缺口尚未处理':''}。`));
 if(c.support_review?.evidence_scope)details.append(el('p','hint',c.support_review.evidence_scope.mode));
 if(c.support_review){details.append(el('p','',c.support_review.status),...c.support_review.issues.map(t=>el('p','hint',t)));node.prepend(el('small','coverage','证据核对：'+c.support_review.status));
  if(c.support_review.claims?.length){const checks=el('details');checks.append(el('summary','','逐段支持依据'));for(const claim of c.support_review.claims){const row=el('div');
   // 论断性质与所需依据按段显示：用户才知道"这一段本来就该有权威依据、而现在只有模型的理解"。
   const meta=[claim.claim_type?CLAIM_TYPE_NAMES[claim.claim_type]||claim.claim_type:'',
    claim.evidence_standard?EVIDENCE_STANDARD_NAMES[claim.evidence_standard]||claim.evidence_standard:''].filter(Boolean);
   row.append(el('p','',`段落 ${claim.claim} · ${{supported:'有原文支持',inference:'有依据的推断',unsupported:'支持不足',heading:'标题',background:'背景知识'}[claim.verdict]||claim.verdict}${meta.length?'（'+meta.join('、')+'）':''}`));for(const evidence of claim.evidence||[]){const jump=el('button','secondary','['+evidence.source+']');jump.type='button';jump.onclick=()=>sources[evidence.source-1]&&cite(sources[evidence.source-1]);row.append(jump,el('blockquote','',evidence.quote));}checks.append(row);}details.append(checks);}}
 if(c.web_support_review)details.append(el('p','','联网补充核对：'+c.web_support_review.status));
 if(c.web_followup)details.append(el('p','','补充搜索：'+c.web_followup.status));
  // 「为什么后面没有重写整篇」：核对提的问题如果回读救不了（摘引与论断不匹配之类），
  // 就不该再花一次完整生成把同一份材料重讲一遍——这里如实说明，避免用户以为被省掉了。
  if(c.reread_skipped)details.append(el('p','hint',c.reread_skipped));
  if(c.web_reading)details.append(el('p','hint',
   `网页材料：共 ${c.web_reading.results} 条结果；给了正文的有 ${(c.web_reading.with_body||[]).length} 条`
   +`（每条最多 ${c.web_reading.body_chars} 字，其中 ${c.web_reading.truncated} 条被截断），其余只给搜索摘要。`));
 if(c.invalid_web_citations?.length)details.append(el('p','error','未匹配的网页引用：'+c.invalid_web_citations.map(n=>'W'+n).join('、')));
 if(c.visual_reading?.enabled)details.append(el('p','',`已对 ${c.visual_reading.pages.length} 页进行图像解读；派生解读与原文分别标注。`));
 for(const d of c.documents)if(d.quality_pages?.length)details.append(el('p','error',d.name+' 仍有解析疑点的页：'+d.quality_pages.join('、')));
 if(c.invalid_citations?.length)details.append(el('p','error','存在未能匹配的引用编号：'+c.invalid_citations.join('、')+'，请核对原文。'));
 const originals=el('details');originals.append(el('summary','','查看实际读取的原文（按需展开）'));let built=false;originals.ontoggle=()=>{if(!originals.open||built)return;built=true;let offset=0;const more=el('button','','继续加载原文');more.type='button';function next(){for(const [index,source] of sources.slice(offset,offset+30).entries()){const item=el('details');item.append(el('summary','',`[${offset+index+1}] ${source.name} · 第 ${source.page} 页`));let shown=false;item.ontoggle=()=>{if(item.open&&!shown){shown=true;const jump=el('button','','返回原页');jump.onclick=()=>cite(source);item.append(jump,el('pre','source-original',source.text));}};originals.insertBefore(item,more);}offset+=30;more.hidden=offset>=sources.length;}originals.append(more);more.onclick=next;next();};details.append(originals);
 if(c.multi_pass){const final=el('details');final.append(el('summary','','最终综合接收的阅读记录与摘引'));let shown=false;final.ontoggle=()=>{if(final.open&&!shown){shown=true;final.append(el('pre','source-original',c.final_context||''));}};details.append(final);}
 node.prepend(details);}}}return node;};
function trackReadingProgress(task,node){let done=false,timer,lastReview;const status=el('p','reading-progress');status.setAttribute('role','status');node.append(status);
 // 「边生成边显示」：正文那一次生成是整轮里最慢的一步，后台会把它边写边发出来（见 cancellation.draft）。
 // 这里把这段文字显示在**同一个气泡**里，并标注"尚未核对"——核对完成后后台会把整段换成核对后的文本，
 // 整轮结束时这个临时气泡会被最终回答整体替换（app.js 里的 `pending.remove()`）。
 let draft=null,draftNote='',draftText='',draftVerified=false;
 function drawDraft(text,note,verified=false,report=null){if(!text)return;if(!draft){draft=el('div','answer-draft');node.append(draft);}if(note&&note!==draftNote){draftNote=note;draft.dataset.note=note;}if(text!==draftText||verified!==draftVerified){draftText=text;draftVerified=verified;if(verified)draft.replaceChildren(renderCheckedAnswer(text,report));else draft.textContent=text;}}
 async function poll(){if(done||task.stopped)return;try{const p=await api(`/chat/${task.id}/status`,{signal:task.controller.signal});if(!done&&p.phase)status.textContent=p.phase+(p.total?` · ${p.current}/${p.total}`:'');
  if(!done&&p.draft&&p.draft.text)drawDraft(p.draft.text,p.draft.note||'',p.draft.verified===true,p.draft.report||null);
 if(!done&&p.search_review&&p.search_review.nonce!==lastReview){lastReview=p.search_review.nonce;status.textContent='等待确认新增搜索主题；拒绝后继续使用已有资料。';const approved=await approveSearchPlan(p.search_review);if(!done&&!task.stopped)await post(`/chat/${task.id}/search-approval`,{nonce:lastReview,approved});}
 }catch{}if(!done)timer=setTimeout(poll,1200);}timer=setTimeout(poll,800);return()=>{done=true;clearTimeout(timer);};}
// Whole-document and page notes, with Markdown editing controls.
const openDocumentBeforeRecovery=openDoc;
openDoc=async function(d){await openDocumentBeforeRecovery(d);if(state.doc?.id!==d.id||state.busy)return;try{const tasks=await api(`/documents/${d.id}/reading-tasks`);if(state.doc?.id!==d.id)return;for(const task of tasks){const card=el('div','reading-report');card.append(el('p','','尚未完成的阅读：'+task.payload.query));const button=el('button','secondary','继续阅读');button.type='button';button.onclick=()=>{card.remove();resumeReading(task.payload);};card.append(button);$('#messages').append(card);}}catch(e){toast(e.message);}};
let editingNote=null,lastDeletedNote=null;const noteScope=el('select');noteScope.setAttribute('aria-label','笔记范围');noteScope.append(new Option('当前页面','page'),new Option('整篇文献','document'));$('#note-content').before(noteScope);
const noteTools=el('div','note-tools');for(const [label,left,right] of [['粗体','**','**'],['标题','## ',''],['列表','- ',''],['引用','> ','']]){const b=el('button','',label);b.type='button';b.onclick=()=>{const n=$('#note-content'),text=n.value.slice(n.selectionStart,n.selectionEnd);n.setRangeText(left+(text||'文字')+right,n.selectionStart,n.selectionEnd,'select');n.focus();};noteTools.append(b);}$('#note-content').before(noteTools);
const cancelNote=el('button','','取消编辑');cancelNote.type='button';cancelNote.hidden=true;cancelNote.onclick=()=>{editingNote=null;$('#note-content').value='';cancelNote.hidden=true;};$('#note-form').append(cancelNote);
const undoNote=el('button','','撤销删除笔记');undoNote.hidden=true;undoNote.onclick=async()=>{if(!lastDeletedNote)return;await post(`/documents/${lastDeletedNote.doc}/notes/${lastDeletedNote.id}/restore`,{});undoNote.hidden=true;await loadNotes();};$('#notes').before(undoNote);
loadNotes=async function(){if(!state.doc)return;const id=state.doc.id,notes=await api(`/documents/${id}/notes`);if(state.doc?.id!==id)return;$('#notes').replaceChildren();for(const n of notes){const item=el('article','note'),jump=el('button','',n.page?'第 '+n.page+' 页':'整篇文献笔记');jump.onclick=()=>n.page?go(n.page):null;item.append(jump);if(n.quote)item.append(el('blockquote','',n.quote));item.append(renderMarkdown(n.content));const edit=el('button','','编辑'),remove=el('button','','删除');edit.onclick=()=>{editingNote={...n,doc:id};noteScope.value=n.page?'page':'document';$('#note-content').value=n.content;cancelNote.hidden=false;$('#note-content').focus();};remove.onclick=async()=>{try{await api(`/documents/${id}/notes/${n.id}`,{method:'DELETE'});lastDeletedNote={doc:id,id:n.id};undoNote.hidden=false;await loadNotes();}catch(e){toast(e.message);}};item.append(edit,remove);$('#notes').append(item);}if(!notes.length)$('#notes').append(el('p','hint','暂无笔记。可记录整篇文献或当前页的想法。'));};
$('#note-form').onsubmit=async e=>{e.preventDefault();if(!requireDoc())return;const content=$('#note-content').value.trim();if(!content)return;const n=editingNote;if(n&&n.doc!==state.doc.id)return toast('请回到原文献继续编辑笔记');const b=e.submitter;b.disabled=true;try{await post(`/documents/${state.doc.id}/notes${n?'/'+n.id:''}`,{page:noteScope.value==='document'?0:n?.page||state.page,quote:n?.quote||state.selection,content},n?'PATCH':'POST');editingNote=null;cancelNote.hidden=true;$('#note-content').value='';await loadNotes();toast('笔记已保存');}catch(err){toast(err.message);}finally{b.disabled=false;}};
// Search result matches without injecting user input into HTML.
new MutationObserver(()=>{const query=$('#search-query').value.trim();if(!query)return;for(const span of $('#search-results').querySelectorAll('.result>span:not([data-marked])')){span.dataset.marked='1';const text=span.textContent,lower=text.toLowerCase(),needle=query.toLowerCase();let start=0,index;span.replaceChildren();while((index=lower.indexOf(needle,start))>=0){span.append(document.createTextNode(text.slice(start,index)),el('mark','',text.slice(index,index+query.length)));start=index+query.length;}span.append(document.createTextNode(text.slice(start)));}}).observe($('#search-results'),{childList:true});
// Mouse-centered zoom keeps the pointed PDF location stationary.
function zoomAt(percent,clientX,clientY){const viewport=$('#viewport'),paper=$('#paper'),before=paper.getBoundingClientRect(),view=viewport.getBoundingClientRect();const x=clientX??view.left+view.width/2,y=clientY??view.top+view.height/2,rx=(x-before.left)/before.width,ry=(y-before.top)/before.height;state.zoom=zoomPercent(percent)/100;resizePage();const after=paper.getBoundingClientRect();viewport.scrollLeft+=after.left+rx*after.width-x;viewport.scrollTop+=after.top+ry*after.height-y;}
$('#viewport').addEventListener('wheel',e=>{if(!e.ctrlKey||readingPreferences.wheel!=='zoom')return;e.preventDefault();e.stopImmediatePropagation();if(state.pageData)zoomAt(zoomStep(e.deltaY>0?-1:1,currentZoomPercent()),e.clientX,e.clientY);},{capture:true,passive:false});
$('#zoom-in').onclick=()=>zoomAt(zoomStep(1,currentZoomPercent()));$('#zoom-out').onclick=()=>zoomAt(zoomStep(-1,currentZoomPercent()));
// Page previews use a separate low-resolution endpoint and native lazy loading.
const readerBody=el('div','reader-body'),previews=el('aside','page-previews');previews.hidden=true;previews.setAttribute('aria-label','页面缩略图');$('#viewport').before(readerBody);readerBody.append(previews,$('#viewport'));const previewToggle=el('button','','页面缩略图');previewToggle.setAttribute('aria-expanded','false');$('#toolbar').prepend(previewToggle);
// 目录：优先用 PDF 自带书签，没有就用标题规则识别，来源如实标注在面板顶部。
// 只在第一次点开时请求（后端与阅读规划共用同一份缓存，不会重复扫描整本）。
const outlinePanel=el('aside','outline-panel');outlinePanel.hidden=true;outlinePanel.setAttribute('aria-label','文献目录');previews.after(outlinePanel);
const outlineToggle=el('button','','目录');outlineToggle.setAttribute('aria-expanded','false');previewToggle.after(outlineToggle);
let outlineDoc=null,outlineSections=[];
function drawOutline(){
 outlinePanel.replaceChildren(el('strong','','目录'));
 const note=el('p','hint',outlineMessage||'');outlinePanel.append(note);
 if(!outlineSections.length){outlinePanel.append(el('p','hint','没有可跳转的目录条目；仍可用缩略图或页码跳转。'));return;}
 for(const section of outlineSections){
  const button=el('button','outline-item'+(state.page>=section.page&&state.page<=(section.end_page||section.page)?' active':''),section.title);
  button.style.paddingLeft=(10+Math.min(4,Math.max(0,(section.level||1)-1))*12)+'px';
  button.title=`第 ${section.page} 页${section.end_page&&section.end_page!==section.page?` – 第 ${section.end_page} 页`:''} · ${section.origin||''}`;
  button.onclick=()=>go(section.page);
  outlinePanel.append(button);
 }
}
let outlineMessage='';
function syncOutlineActive(){if(!outlinePanel.hidden&&outlineSections.length)drawOutline();}
async function loadOutline(){
 if(!state.doc)return;
 const id=state.doc.id;
 if(outlineDoc===id)return;
 outlinePanel.replaceChildren(el('p','hint','正在识别目录…'));
 try{
  const result=await api(`/documents/${id}/outline`);
  if(state.doc?.id!==id)return;
  outlineDoc=id;outlineSections=result.sections||[];outlineMessage=result.message||'';
  drawOutline();
 }catch(e){outlineMessage='目录读取失败：'+e.message+'（如果刚刚更新过程序，请关闭并重新打开应用让后台重启，只刷新页面不够）';outlineSections=[];drawOutline();}
}
outlineToggle.onclick=async()=>{
 outlinePanel.hidden=!outlinePanel.hidden;outlineToggle.setAttribute('aria-expanded',String(!outlinePanel.hidden));
 if(!outlinePanel.hidden){await loadOutline();drawOutline();}
 scheduleReaderResize();
};
let previewDoc=null;function updatePreviews(){if(!state.doc||previewDoc===state.doc.id)return;previewDoc=state.doc.id;previews.replaceChildren();for(let page=1;page<=state.doc.pages;page++){const b=el('button','',String(page));b.title='跳转第 '+page+' 页';const img=el('img');img.loading='lazy';img.alt='第 '+page+' 页';img.src=`/api/documents/${state.doc.id}/pages/${page}/thumbnail`;b.prepend(img);b.onclick=()=>go(page);previews.append(b);}}
previewToggle.onclick=()=>{previews.hidden=!previews.hidden;previewToggle.setAttribute('aria-expanded',String(!previews.hidden));if(!previews.hidden)updatePreviews();scheduleReaderResize();};
const headerNode=$('header'),headerControls=el('div','compact-controls');headerControls.append($('#toggle-library'),$('.breadcrumb'));headerNode.append(headerControls);
function syncReaderChrome(){const reading=state.doc&&!document.body.classList.contains('managing');if(reading){headerNode.prepend(headerControls);headerNode.hidden=false;if(!previews.hidden)updatePreviews();}else{headerNode.prepend(headerControls);headerNode.hidden=false;}annotationPopover.hidden=true;}
new MutationObserver(syncReaderChrome).observe(document.body,{attributes:true,attributeFilter:['class']});new MutationObserver(()=>{if(!previews.hidden)updatePreviews();}).observe($('#doc-title'),{childList:true});
// Dedicated selection actions, leaving the complete context menu available.
// 选文操作条跟随选区出现（在选区上方，靠近鼠标松开的位置）。
// 浏览器自带的选择文字迷你菜单属于**浏览器界面层**：网页既拦不住（对 contextmenu 调
// preventDefault() 对它无效），也没法用 z-index 盖在它上面；它出现时会压住"贴着选区"的
// 工具条。用户明确要求：**本应用不要去动浏览器的设置或策略**，那个菜单由用户自己在浏览器
// 里关，本应用只把工具条**向右让开一段**，别被它挡住。
// 向右让开的距离，按用户实测反馈调过两次：190px「太远」→ 96px → 再「往左约 1cm」。
// CSS 里 1cm = 96/2.54 ≈ 37.8px（浏览器把 cm 当物理单位），所以直接写成算式，方便按厘米微调。
const SELECTION_TOOLBAR_OFFSET=96-Math.round(96/2.54);   // ≈ 58px
function placeSelectionTools(event){
 const width=selectionTools.offsetWidth||0,height=selectionTools.offsetHeight||48;
 const x=event&&typeof event.clientX==='number'?event.clientX:window.innerWidth/2;
 const y=event&&typeof event.clientY==='number'?event.clientY:120;
 // 先向右让开；右边放不下就贴着右边缘（宁可贴边，也不回到会被挡住的左侧）。
 const room=window.innerWidth-width-8;
 selectionTools.style.left=Math.max(8,Math.min(room,x+SELECTION_TOOLBAR_OFFSET))+'px';
 selectionTools.style.top=Math.max(8,y-height-10)+'px';
}
const selectionTools=el('div','selection-tools');selectionTools.id='selection-tools';selectionTools.hidden=true;selectionTools.setAttribute('role','toolbar');selectionTools.setAttribute('aria-label','选文快捷操作');for(const [label,action] of [['提问',()=>$('#ask-selection').click()],['高亮',()=>{openAnnotation();annotationStyle.value='highlight';}],['下划线',()=>{openAnnotation();annotationStyle.value='underline';}],['段落批注',()=>{openAnnotation();annotationStyle.value='margin';}],['复制',()=>navigator.clipboard.writeText(state.selection)]]){const b=el('button','',label);b.onpointerdown=e=>e.preventDefault();b.onclick=()=>{action();selectionTools.hidden=true;};selectionTools.append(b);}
selectionTools.title='选中文字的快捷操作（已向右让开浏览器自带的选中菜单）；按 Esc 收起。';
document.body.append(selectionTools);
$('#paper').addEventListener('pointerup',e=>{if(state.crop)return;requestAnimationFrame(()=>{captureSelection();if(!state.selection||!window.getSelection()?.toString().trim()){selectionTools.hidden=true;return;}selectionTools.hidden=false;placeSelectionTools(e);});});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!selectionTools.hidden)selectionTools.hidden=true;});
document.addEventListener('pointerdown',e=>{if(!selectionTools.contains(e.target))selectionTools.hidden=true;});document.addEventListener('scroll',()=>selectionTools.hidden=true,true);
// Compact microphone and persistent playback stop control.
$('#send').before(voiceButton);voiceButton.className='microphone-button';voiceButton.setAttribute('aria-label','按住说话，松开识别');voiceButton.title='按住说话，松开识别';
const microphoneIcon=()=>{const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');for(const [tag,attrs] of [['rect',{x:9,y:3,width:6,height:12,rx:3}],['path',{d:'M6 10v2a6 6 0 0 0 12 0v-2M12 18v3M8 21h8'}]]){const n=document.createElementNS(svg.namespaceURI,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);svg.append(n);}return svg;};
function refreshMicrophone(){const label=voiceButton.textContent;if(label){voiceButton.title=label;voiceButton.setAttribute('aria-label',label);voiceButton.replaceChildren(microphoneIcon());}voiceButton.hidden=!speechConfig?.input_enabled;voiceButton.classList.toggle('recording',!!voiceSession);}
new MutationObserver(refreshMicrophone).observe(voiceButton,{childList:true});const originalApplySpeechVisibility=applySpeechVisibility;applySpeechVisibility=function(){originalApplySpeechVisibility();refreshMicrophone();};refreshMicrophone();
voiceControls.classList.add('voice-cancel-row');const playbackStop=el('button','playback-stop','■ 停止朗读');playbackStop.hidden=true;playbackStop.onclick=()=>stopSpeaking();$('#messages').append(playbackStop);
const originalSpeak=speakAnswer,originalStopSpeaking=stopSpeaking;stopSpeaking=function(){originalStopSpeaking();playbackStop.hidden=true;};speakAnswer=async function(text,node){if(node)node.append(playbackStop);else $('#messages .message.assistant:last-child')?.append(playbackStop);if(!speechConfig?.output_enabled)return originalSpeak(text);const pending=originalSpeak(text);playbackStop.hidden=!speechPlayback;try{return await pending;}finally{if(!speechPlayback)playbackStop.hidden=true;}};
syncReaderChrome();
