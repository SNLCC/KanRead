// Presentation only: stored claim numbers, gaps and source bindings stay intact.
function compactEvidenceGaps(text){
 const marker='此处结论缺少本轮证据支持，暂不作确定回答。';
 let count=0;
 const content=String(text||'').split('\n').filter(line=>{if(line.trim()===marker){count++;return false;}return true;}).join('\n').replace(/\n{3,}/g,'\n\n').trim();
 return {content,count};
}
function renderCheckedAnswer(text,report){
 const result=compactEvidenceGaps(text),root=el('div');
 if(result.content)root.append(renderMarkdown(result.content));
 if(result.count){
  root.append(el('p','hint',`另有 ${result.count} 处候选内容尚未通过核验，完整保留在下方，未作为确定结论展示。`));
  const details=el('details','evidence-gaps');details.append(el('summary','','查看候选内容与核验原因'));
  const candidates=(report?.claims||[]).filter(row=>row.verdict==='unsupported'&&row.candidate_text);
  for(const row of candidates){
   const item=el('div','unverified-candidate');
   item.append(el('p','hint',`段落 ${row.claim} · ${row.review_problem==='classification_uncertain'?'结构分类待确认':'证据支持待确认'} · 模型候选内容，不是文献原文`),el('blockquote','',row.candidate_text),el('p','hint',row.reason||'尚未确认支持依据。'));
   details.append(item);
  }
  // 段落号由核对时的原文行序决定，而上面压掉了缺证占位行；编号因此**可能**与当前
  // 段落位置不完全一致，这一点必须写在展开处，否则用户会按编号去数错段。
  if(candidates.length)details.append(el('p','hint','段落号按核对时的原文行序编号；上面的缺证占位行已折叠，当前段落位置可能与编号相差若干段。'),
   el('p','hint','候选内容中的引用是待核对线索，不代表已获支持；可对照本节原文引用或网页依据核对。'));
  else for(const issue of report?.issues||[])details.append(el('p','hint',issue));
  // 没拿到逐段记录（例如边生成边显示的草稿）时不能留一个点开是空的折叠区：
  // 要么说明去哪看，要么连折叠区一起省掉。
  if(candidates.length||report?.issues?.length)root.append(details);
  else root.append(el('p','hint','逐段候选内容与原因在本轮核对完成后显示；未支持不等于结论错误，也不能据此确认结论。'));
 }
 return root;
}
function renderMessage(role,text,sources,meta){
 const node=el('div','message '+role);node.append(el('span','role',role==='user'?'你':'◇ 阅读伙伴'));
 if(meta.voice_original){const original=el('details');original.append(el('summary','','语音原始识别'),el('p','',meta.voice_original));node.append(original);}
 if(meta.quote){const quote=el('blockquote','message-quote');quote.append(el('small','',`${meta.name} · 第 ${meta.page} 页`),el('p','',meta.quote));const jump=el('button','','返回引用页');jump.onclick=async()=>{const doc=state.docs.find(d=>d.id===meta.document_id);if(!doc)return toast('引用文献已移入回收站');if(state.doc?.id!==doc.id)await openDoc(doc);await go(meta.page);};quote.append(jump);node.append(quote);}
 if(meta.sections){for(const section of meta.sections){const part=el('section','answer-section source-'+section.kind);part.append(el('strong','source-label',section.title),renderCheckedAnswer(section.content,section.kind==='document'?meta.coverage?.support_review:section.kind==='web'?meta.coverage?.web_support_review:null));
  if(section.kind==='web'){
   if(!section.results?.length)part.append(el('p','','未获取到网页结果。'));
   const details=el('details','web-evidence');details.append(el('summary','',`查看网页依据（${section.results?.length||0}）`));part.append(details);for(const [index,item] of (section.results||[]).entries()){try{const url=new URL(item.url);if(!['https:','http:'].includes(url.protocol))continue;const link=el('a','',`[W${index+1}] ${item.title}`);link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';details.append(link,el('p','',item.snippet),el('small','',`${url.hostname}${item.tier&&item.tier!=='GENERAL_WEB'?'（'+item.tier_label+'）':''} · 获取于 ${new Date(item.retrieved_at).toLocaleString()}`));}catch{}}
    // 来源分层（Phase 7 的 Web 分层）：只按**域名归属**分组，用于排序与显示。
    // 边界必须写在界面上（尤其"本应用只有通用网页搜索"），否则用户会以为标了"预印本平台"
    // 就等于已经查过学术资料库了。
    if(section.tiers?.groups?.length){details.append(el('p','hint','来源分层（按域名归属，仅用于排序与显示）：'+section.tiers.groups.map(group=>`${group.label} ${group.count}`).join('、')));
     details.append(el('p','hint',section.tiers.note));}
  }
  if(section.kind==='document')appendSources(part,sources,meta);node.append(part);
 }}else{node.append(role==='assistant'?renderCheckedAnswer(text,meta.coverage?.support_review):el('div','',text));appendSources(node,sources,meta);}
 if(role==='assistant'&&meta.sections){const actions=el('div','message-actions'),read=el('button','','朗读');read.onclick=()=>speakAnswer(text,node);actions.append(read);node.append(actions);}
 $('#messages').append(node);$('#messages').scrollTop=$('#messages').scrollHeight;return node;
}
// 引用落到"内容"：chip 以原文位置为主、页码为辅，点击后高亮对应文字。
// 摘引分三档，界面必须如实区分：
//   1. 逐字核验过的摘引（复核阶段确认过）——就是原文支持；
//   2. 定位锚点：正文那一句与原文的逐字重合片段——只用于定位，不代表原文支持该句；
//   3. 只有页码：正文用了改写表述，找不到逐字重合，诚实说明。
function citationTarget(source,index,meta){
 const own=(source.quotes||[])[0];
 if(own)return {quote:own,verified:true};
 const bindings=meta&&meta.coverage&&meta.coverage.citation_bindings;
 const bound=(bindings||[]).find(item=>item.index===index&&item.quote);
 if(bound&&bound.quote)return {quote:bound.quote,verified:true};
 if(source.anchor)return {quote:source.anchor,verified:false};
 return {quote:'',verified:false};
}
function citationQuote(source,index,meta){return citationTarget(source,index,meta).quote;}
function citationLabel(quote){return quote.length>120?quote.slice(0,120)+'…':quote;}
// 正文里实际引用到的来源编号（含复核绑定）。来源列表只列这些——用户看到的就是"参考引用"，
// 而不是"这轮读过的全部页面"（后者在阅读报告的"查看实际读取的原文"里）。
function citedIndexes(meta){
 const found=new Set();
 for(const section of (meta?.sections||[])){
  for(const match of String(section.content||'').matchAll(/\[(\d+)\]/g))found.add(Number(match[1]));
 }
 for(const binding of (meta?.coverage?.citation_bindings||[]))if(binding.index)found.add(binding.index);
 return found;
}
function sourceButton(index,source,target){
 const quote=target.quote;
 const button=el('button',quote?'source-with-quote':'');
 button.dataset.index=String(index);              // 页码被更正时按编号改写标签
 button.dataset.name=source.name||'文献';       // 更正文案时用，避免从显示文本反解
 // 原文在前、页码在后：参考引用说的是"哪一段文字"，页码只是附带信息。
 if(quote)button.append(el('q','',`“${citationLabel(quote)}”`));
 button.append(el('span','',target.verified
   ? `[${index}] ${source.name||'文献'} · 第 ${source.page} 页 · 已逐字核验`
   : quote?`[${index}] ${source.name||'文献'} · 第 ${source.page} 页 · 按正文用词定位（未经逐字核验）`
          : `[${index}] ${source.name||'文献'} · 第 ${source.page} 页 · 只有页码：这段回答用了改写表述，原文里找不到逐字重合`));
 button.title=quote?`${source.name} · 第 ${source.page} 页\n${target.verified?'引用原文（复核阶段逐字核验）：':'定位锚点（正文用词与原文的逐字重合，未经核验）：'}\n${quote}\n点击跳转到原文位置`
                   :`${source.name} · 第 ${source.page} 页\n这一处没有可逐字核验的摘引，只能定位到该页`;
 button.onclick=()=>cite(source,quote,button);
 return button;
}
function appendSources(node,sources,meta){
 if(!sources?.length)return;
 const cited=citedIndexes(meta);
 const list=el('div','sources');let offset=0;
 const picked=sources.map((source,index)=>({source,index:index+1})).filter(item=>cited.has(item.index));
 const rows=picked.length?picked:sources.map((source,index)=>({source,index:index+1}));
 const next=el('button','','更多引用');
 function more(){for(const item of rows.slice(offset,offset+20)){list.append(sourceButton(item.index,item.source,citationTarget(item.source,item.index,meta)));}offset+=20;next.hidden=offset>=rows.length;}
 next.onclick=more;
 if(rows.length>12){const details=el('details');details.append(el('summary','',`参考引用（${rows.length} 处）`),list,next);let loaded=false;details.ontoggle=()=>{if(details.open&&!loaded){loaded=true;more();}};node.append(details);}
 else{more();node.append(list);}
 // 引用只能给页码时把原因写在旁边：多半是后台仍是旧版本（新版本会给逐字摘引或定位锚点）。
 const support=meta?.coverage?.citation_support;
 if(support&&support.page_only?.length&&!support.verified&&!support.anchor_only){
  list.append(el('small','hint','这些引用目前只有页码：本机后台可能仍是旧版本，请用新的启动器（勘读.exe）打开一次。'));
 }
}


let annotationDraft=null,lastDeletedAnnotation=null;
async function loadAnnotations(){if(!state.doc)return;const id=state.doc.id;const annotations=await api(`/documents/${id}/annotations`);if(state.doc?.id!==id)return;state.annotations=annotations;drawAnnotations();drawAnnotationList();}
function drawAnnotations(){const layer=$('#annotation-layer');layer.replaceChildren();for(const a of state.annotations||[]){if(a.page!==state.page)continue;for(const [x0,y0,x1,y1] of a.rects){const mark=el('div','annotation-mark '+a.color);Object.assign(mark.style,{left:100*x0+'%',top:100*y0+'%',width:100*(x1-x0)+'%',height:100*(y1-y0)+'%'});layer.append(mark);}}}
function drawAnnotationList(){const list=$('#annotations');list.replaceChildren();for(const a of state.annotations){const item=el('article','note');const jump=el('button','',`第 ${a.page} 页 · ${a.rects.length?'原文高亮':'整页批注'}`);jump.onclick=()=>go(a.page);item.append(jump,el('blockquote','',a.quote),el('p','',a.content));const edit=el('button','secondary','编辑');edit.onclick=()=>openAnnotation(a);const ask=el('button','secondary','引用到提问');ask.onclick=()=>askAboutAnnotation(a);const remove=el('button','secondary danger','删除');remove.onclick=()=>deleteAnnotation(a);item.append(edit,ask,remove);list.append(item);}if(!state.annotations.length)list.append(el('p','hint','暂无批注。选中文字后右键添加，或点击上方按钮。'));}
// 删除批注：批注列表与阅读区弹窗共用这一条路径，删除后可撤销（#undo-annotation 调 restore）。
async function deleteAnnotation(a,options={}){
 if(!a||!state.doc)return false;
 try{
  await api(`/documents/${state.doc.id}/annotations/${a.id}`,{method:'DELETE'});
  lastDeletedAnnotation={doc:state.doc.id,id:a.id};
  $('#undo-annotation').hidden=false;
  await loadAnnotations();
  if(!options.silent)toast('批注已删除，可点击撤销删除');
  return true;
 }catch(e){toast(e.message);return false;}
}
// 把批注带进会话提问：批注引用的原文进「选文」，批注文字进问题框——两者都随这一轮提问发给模型。
// 只读地引用，不改批注、不改原文；问题框里已有草稿时不覆盖，只把原文带进选文。
function askAboutAnnotation(a){
 if(!a||!requireDoc())return;
 const quote=(a.quote||'').trim(),content=(a.content||'').trim();
 if(quote)setSelection(quote,a.rects||[]);
 tab('chat');
 const box=$('#question');
 if(!box.value.trim())box.value=content
  ?`关于我在第 ${a.page} 页的批注「${content}」：请结合${quote?'上面引用的原文':'这一页的原文'}说明。`
  :`请结合上下文解释我在第 ${a.page} 页标记的这段原文。`;
 box.scrollIntoView({block:'center',behavior:'smooth'});box.focus();
 toast(quote?'已把批注引用的原文带入选文，问题已写好，可修改后发送':'已把这条批注带入提问，可修改后发送');
}
function openAnnotation(existing){if(!requireDoc())return;captureSelection();annotationDraft=existing?{...existing,doc:state.doc.id}:{doc:state.doc.id,page:state.page,quote:state.selection,rects:state.selectionRects.map(r=>[...r]),color:'yellow',content:''};$('#annotation-title').textContent=existing?'编辑批注':`添加批注 · 第 ${state.page} 页`;$('#annotation-quote').textContent=annotationDraft.quote||'整页批注';$('#annotation-content').value=annotationDraft.content;$('#annotation-color').value=annotationDraft.color;$('#annotation-dialog').showModal();if(typeof placeAnnotationDialog==='function')placeAnnotationDialog();$('#annotation-content').focus();}
$('#annotate-selection').onclick=$('#new-annotation').onclick=()=>openAnnotation();
$('#annotation-cancel').onclick=()=>$('#annotation-dialog').close();
$('#annotation-form').onsubmit=async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{const a=annotationDraft;const body={page:a.page,quote:a.quote,rects:a.rects,content:$('#annotation-content').value,color:$('#annotation-color').value};await post(`/documents/${a.doc}/annotations${a.id?'/'+a.id:''}`,body,a.id?'PATCH':'POST');$('#annotation-dialog').close();window.getSelection()?.removeAllRanges();setSelection('');await loadAnnotations();tab('annotations');toast('批注已保存，原 PDF 保持不变');}catch(err){toast(err.message);}finally{button.disabled=false;}};
$('#undo-annotation').onclick=async()=>{if(!lastDeletedAnnotation)return;try{await post(`/documents/${lastDeletedAnnotation.doc}/annotations/${lastDeletedAnnotation.id}/restore`,{});lastDeletedAnnotation=null;$('#undo-annotation').hidden=true;await loadAnnotations();}catch(e){toast(e.message);}};

const menu=$('#context-menu');
function closeMenu(){menu.hidden=true;}
function showMenu(event,actions){event.preventDefault();menu.replaceChildren();for(const [label,action] of actions){const b=el('button','',label);b.setAttribute('role','menuitem');b.onclick=async()=>{closeMenu();try{await action();}catch(e){toast(e.message);}};menu.append(b);}menu.hidden=false;menu.style.left=Math.max(4,Math.min(event.clientX,window.innerWidth-220))+'px';menu.style.top=Math.max(4,Math.min(event.clientY,window.innerHeight-menu.offsetHeight-8))+'px';menu.querySelector('button')?.focus();}
$('#paper').addEventListener('contextmenu',event=>{captureSelection();if(!state.doc)return;const actions=[];if(state.selection)actions.push(['复制选中文字',()=>navigator.clipboard.writeText(state.selection)],['对选中文字提问',()=>$('#ask-selection').click()]);actions.push(['添加批注',()=>openAnnotation()],['框选识别',()=>$('#crop').click()],['适应宽度',()=>{state.zoom=1;resizePage();}]);if(typeof translationPanel==='function'&&state.selection)actions.push(['翻译选中文字',()=>translateSelection()]);if(typeof translationPanel==='function')actions.push(['原文/译文对照',()=>translationPanel().open()]);showMenu(event,actions);});
$('#document-grid').addEventListener('contextmenu',event=>{const card=event.target.closest('.document-card');if(!card)return;const actions=Array.from(card.querySelectorAll('.document-actions button')).map(b=>[b.textContent,()=>b.click()]);const id=card.dataset.docId;if(id&&typeof translateDocument==='function')actions.push(['翻译整篇文献（后台）',()=>translateDocument(id)]);showMenu(event,actions);});
document.addEventListener('pointerdown',event=>{if(!menu.contains(event.target))closeMenu();});
document.addEventListener('keydown',event=>{if(menu.hidden)return;if(event.key==='Escape'){closeMenu();return;}if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();const buttons=Array.from(menu.querySelectorAll('button')),i=buttons.indexOf(document.activeElement);buttons[(i+(event.key==='ArrowDown'?1:buttons.length-1))%buttons.length].focus();}});
window.addEventListener('resize',closeMenu);document.addEventListener('scroll',closeMenu,true);
$('#mode').onchange=()=>{$('#mode-help').textContent=$('#mode').value==='close'?'精读：优先选段与邻页，解释概念、论据和推理。':'泛读：结合全文抽样和检索片段，概括主题、结构与主要发现；并非逐页通读。';};
// Phase 8：原来这里监听「联网搜索」勾选框来展开那段说明；那个勾选框已经收进「智能来源」，
// 说明文字也随之取消（平台的隐私权限在设置里有完整说明，不必在这里重复一段）。
// 保留 `#web-options` 元素本身不做任何操作：删掉它会让历史页面里的引用落空。

$('#documents').addEventListener('contextmenu',event=>{const button=event.target.closest('[data-doc-id]');const d=state.docs.find(d=>d.id===button?.dataset.docId);if(!d)return;showMenu(event,[['继续阅读',()=>openDoc(d)],['重命名',()=>renameDoc(d)],['移入应用回收站',()=>trashDoc(d)],['打开应用回收站',()=>showWorkbench('trash')]]);});

// Use application actions outside editing fields; retain native text editing menus.
document.addEventListener('contextmenu',event=>{
 if(event.defaultPrevented||event.target.closest('input,textarea,[contenteditable=true]'))return;
 const text=window.getSelection()?.toString().trim(),link=event.target.closest('a');
 const actions=[];
 if(text)actions.push(['复制选中文字',()=>navigator.clipboard.writeText(text)]);
 if(link)actions.push(['复制链接',()=>navigator.clipboard.writeText(link.href)],['打开链接',()=>link.click()]);
 if(event.target.closest('.message'))actions.push(['复制这条消息',()=>navigator.clipboard.writeText(event.target.closest('.message').innerText)]);
 const note=event.target.closest('.note');if(note){actions.push(['复制笔记或批注',()=>navigator.clipboard.writeText(note.innerText)]);for(const b of note.querySelectorAll('button'))actions.push([b.textContent,()=>b.click()]);}
 const attachment=event.target.closest('.zotero-item');if(attachment)actions.push(['打开这篇文献',()=>attachment.click()]);
 if(actions.length)showMenu(event,actions);else{event.preventDefault();closeMenu();}
});
const splitter=el('div','panel-splitter');splitter.tabIndex=0;splitter.setAttribute('role','separator');splitter.setAttribute('aria-label','调整会话窗口宽度');splitter.setAttribute('aria-orientation','vertical');$('.assistant-panel').before(splitter);
function setPanelWidth(width){const max=Math.max(280,$('.workspace').clientWidth-260);width=Math.round(Math.max(280,Math.min(max,width)));document.documentElement.style.setProperty('--chat-width',width+'px');splitter.setAttribute('aria-valuemin','280');splitter.setAttribute('aria-valuemax',max);splitter.setAttribute('aria-valuenow',width);localStorage.setItem('reader-chat-width',width);}
const savedWidth=Number(localStorage.getItem('reader-chat-width'));if(savedWidth)setPanelWidth(savedWidth);
splitter.onpointerdown=e=>{if(e.button!==0)return;e.preventDefault();splitter.setPointerCapture(e.pointerId);splitter.dataset.dragging='1';document.body.classList.add('resizing-panel');};
splitter.onpointermove=e=>{if(splitter.dataset.dragging){setPanelWidth($('.workspace').getBoundingClientRect().right-e.clientX);scheduleReaderResize();}};
function finishResize(){delete splitter.dataset.dragging;document.body.classList.remove('resizing-panel');resizePage();}
splitter.onpointerup=splitter.onpointercancel=finishResize;
splitter.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home'].includes(e.key)){e.preventDefault();setPanelWidth(e.key==='Home'?365:$('.assistant-panel').clientWidth+(e.key==='ArrowLeft'?20:-20));resizePage();}};

