// 附件容错界面：文献卡片上的状态标签 + 处理对话框。
//
// 口径与 app/file_health.py 一一对应（不要在这里另创一套说法）：
//   · copy / zotero_cache = 应用内副本：原始文件删了/移了、Zotero 卸了都不影响使用；
//   · zotero = Zotero 本机只读链接：不复制字节，依赖 Zotero 数据目录里的那个 PDF。
// 这里只如实显示状态并给处理入口，不自动修复、不隐藏失败；文件位置只显示文件名和
// data/ 内的相对位置，不把完整用户路径摆到界面上。
const FILE_STATE_LABELS={missing:'附件缺失',changed:'附件已变化',unknown:'附件状态未知'};

function fileHealthBadge(doc){
 const info=doc&&doc.file_state;
 if(!info||!FILE_STATE_LABELS[info.state])return null;
 const badge=el('button','filehealth-badge '+info.state,FILE_STATE_LABELS[info.state]);
 badge.type='button';
 badge.title=info.detail||'';
 badge.setAttribute('aria-label','附件状态：'+FILE_STATE_LABELS[info.state]+'，点击查看处理方式');
 badge.onclick=event=>{event.stopPropagation();openFileHealth(doc);};
 return badge;
}

function fileHealthFileName(path){
 return String(path||'').split(/[\\/]/).filter(Boolean).pop()||'';
}

function fileHealthStorageText(doc){
 if(doc.storage_kind==='zotero')return '这份文献是 Zotero 本地只读链接：应用没有复制附件，只记住 Zotero 数据目录里的文件位置。Zotero 被卸载、附件被移动或删除后，阅读、检索与引用定位会不可用；可以重新连接一份 PDF，或先保存为本机副本。';
 if(doc.storage_kind==='zotero_cache')return '这份文献是 Zotero 云端附件下载到应用里的缓存副本：之后删除 Zotero、移动原附件都不影响使用。';
 return '这份文献是应用内副本（data 目录里的 PDF）：导入后原文件被删除、移动，或导入来源不可用，都不影响本应用里的阅读、检索与批注。';
}

function fileHealthLocationText(doc){
 if(doc.storage_kind==='zotero'){
  const name=fileHealthFileName(doc.external_path);
  return 'Zotero 记录的文件名：'+(name||'未知')+'（完整路径不显示，避免暴露本机用户名）。';
 }
 if(doc.storage_kind==='zotero_cache')return '应用缓存副本：data/'+doc.id+'.pdf（由 Zotero 云端附件下载）。';
 return '应用内副本：data/'+doc.id+'.pdf。';
}

function fileHealthIsTrashed(doc){return Number(doc&&doc.deleted)===1;}

let fileHealthDialog=null,fileHealthDoc=null,fileHealthTitle=null,fileHealthDetail=null,
    fileHealthStorage=null,fileHealthLocation=null,fileHealthActions=null,fileHealthFeedback=null,
    fileHealthInput=null;

function fileHealthSheet(){
 if(fileHealthDialog)return fileHealthDialog;
 fileHealthDialog=el('dialog','filehealth-dialog');
 fileHealthDialog.id='filehealth-dialog';
 fileHealthDialog.setAttribute('aria-label','附件状态与处理');
 fileHealthTitle=el('h3','','附件状态');
 fileHealthDetail=el('p','filehealth-detail','');
 fileHealthStorage=el('p','hint filehealth-storage','');
 fileHealthLocation=el('p','hint filehealth-location','');
 fileHealthFeedback=el('p','filehealth-feedback','');
 fileHealthFeedback.setAttribute('aria-live','polite');
 fileHealthActions=el('div','dialog-actions filehealth-actions');
 fileHealthInput=document.createElement('input');
 fileHealthInput.type='file';
 fileHealthInput.accept='.pdf,application/pdf';
 fileHealthInput.hidden=true;
 fileHealthInput.addEventListener('change',()=>{
  const file=fileHealthInput.files&&fileHealthInput.files[0];
  fileHealthInput.value='';
  if(file)relinkWithPdf(file);
 });
 fileHealthDialog.append(fileHealthTitle,fileHealthDetail,fileHealthStorage,fileHealthLocation,
                          fileHealthFeedback,fileHealthActions,fileHealthInput);
 // 点遮罩关闭（与其它对话框一致的浏览器行为），不额外拦截。
 fileHealthDialog.addEventListener('click',event=>{if(event.target===fileHealthDialog)fileHealthDialog.close();});
 document.body.append(fileHealthDialog);
 return fileHealthDialog;
}

function fileHealthButton(label,cls,fn){
 const button=el('button',cls,label);
 button.type='button';
 button.onclick=fn;
 return button;
}

function fileHealthBusy(flag,text){
 if(fileHealthFeedback&&text!==undefined)fileHealthFeedback.textContent=text;
 if(fileHealthActions)fileHealthActions.querySelectorAll('button').forEach(b=>{b.disabled=flag;});
 if(fileHealthInput)fileHealthInput.disabled=flag;
}

async function refreshFileHealthWorkbench(){
 if(typeof loadLibrary==='function')await loadLibrary();
 if(document.body.classList.contains('managing')&&typeof drawWorkbench==='function')await drawWorkbench();
}

function renderFileHealth(doc){
 fileHealthDoc=doc;
 fileHealthSheet();
 const info=doc.file_state||{state:'unknown',detail:'没有拿到附件状态信息。'};
 fileHealthTitle.textContent='附件状态：'+(FILE_STATE_LABELS[info.state]||'未知');
 fileHealthDetail.textContent=info.detail||'';
 fileHealthStorage.textContent=fileHealthStorageText(doc);
 fileHealthLocation.textContent=fileHealthLocationText(doc);
 fileHealthFeedback.textContent='';
 const actions=[];
 if(fileHealthIsTrashed(doc)){
  fileHealthFeedback.textContent='这份文献在应用回收站里：恢复到文献库之后才能重新连接附件。';
  actions.push(fileHealthButton('恢复到文献库','primary',()=>restoreTrashedDoc()));
 }else{
  actions.push(fileHealthButton('选择新 PDF 重新连接','primary',()=>{
   fileHealthFeedback.textContent='选一份新的 PDF：会用它重建正文索引；批注、笔记与对话按文献 id 保留。';
   fileHealthInput.click();
  }));
  if(doc.storage_kind==='zotero'){
   const snapshot=fileHealthButton('保存为本机副本','secondary',()=>snapshotZoteroLink());
   snapshot.title=info.state==='missing'
    ?'Zotero 附件当前不存在，无法复制'
    :'把 Zotero 附件复制进应用，之后不再依赖 Zotero 文件位置';
   if(info.state==='missing')snapshot.disabled=true;
   actions.push(snapshot);
  }
  actions.push(fileHealthButton('移入应用回收站','secondary',()=>trashFromHealthDialog()));
 }
 actions.push(fileHealthButton('关闭','secondary',()=>fileHealthDialog.close()));
 fileHealthActions.replaceChildren(...actions);
}

function openFileHealth(doc){
 renderFileHealth(doc);
 if(!fileHealthDialog.open)fileHealthDialog.showModal();
}

function renderFileHealthResult(result,title){
 fileHealthTitle.textContent=title;
 fileHealthDetail.textContent=result.warning||'页数没有变化，批注页码通常仍可用；建议抽查核对。';
 fileHealthStorage.textContent='页数：'+result.page_count_before+' → '+result.page_count_after
  +'；超出新页数的批注：'+Number(result.annotations_out_of_range||0)+' 条。';
 fileHealthLocation.textContent=result.previous_copy_backup?'旧副本已改名保留：'+result.previous_copy_backup+'。':'';
 fileHealthFeedback.textContent='批注、笔记与对话没有删除；页码可能错位时请逐条核对。';
 fileHealthActions.replaceChildren(
  fileHealthButton('知道了','secondary',()=>fileHealthDialog.close()));
}

async function relinkWithPdf(file){
 const doc=fileHealthDoc;
 if(!doc)return;
 if(state.busy){toast('请等待当前操作完成');return;}
 state.busy=true;
 fileHealthBusy(true,'正在读取 PDF 并重建索引，请稍候…');
 try{
  const form=new FormData();
  form.append('file',file,file.name||'reconnected.pdf');
  const result=await api('/documents/'+encodeURIComponent(doc.id)+'/relink',{method:'POST',body:form});
  await refreshFileHealthWorkbench();
  if(result.page_count_before!==result.page_count_after||Number(result.annotations_out_of_range)>0){
   renderFileHealthResult(result,'索引已重建，请核对批注页码');
  }else{
   fileHealthDialog.close();
   toast(result.warning||'已重新连接这份 PDF 并重建索引；批注与笔记已保留。');
  }
 }catch(error){
  fileHealthFeedback.textContent='重新连接失败：'+error.message;
  fileHealthBusy(false);
  toast(error.message);
 }finally{
  state.busy=false;
 }
}

async function snapshotZoteroLink(){
 const doc=fileHealthDoc;
 if(!doc)return;
 if(state.busy){toast('请等待当前操作完成');return;}
 state.busy=true;
 fileHealthBusy(true,'正在把 Zotero 附件复制到应用并重建索引，请稍候…');
 try{
  const result=await api('/documents/'+encodeURIComponent(doc.id)+'/snapshot',{method:'POST'});
  await refreshFileHealthWorkbench();
  if(result.page_count_before!==result.page_count_after){
   renderFileHealthResult(result,'已保存为本机副本，请核对批注页码');
  }else{
   fileHealthDialog.close();
   toast('已保存为本机副本：之后删除、移动 Zotero 附件或卸载 Zotero 都不影响这份文献。');
  }
 }catch(error){
  fileHealthFeedback.textContent='保存失败：'+error.message;
  fileHealthBusy(false);
  toast(error.message);
 }finally{
  state.busy=false;
 }
}

async function trashFromHealthDialog(){
 const doc=fileHealthDoc;
 if(!doc)return;
 if(typeof trashDoc!=='function'){toast('回收站功能未加载，请刷新页面重试');return;}
 fileHealthDialog.close();
 try{
  await trashDoc(doc);
 }catch(error){
  toast(error.message);
 }
}

async function restoreTrashedDoc(){
 const doc=fileHealthDoc;
 if(!doc)return;
 if(state.busy){toast('请等待当前操作完成');return;}
 fileHealthBusy(true,'正在恢复到文献库…');
 try{
  await post('/documents/'+encodeURIComponent(doc.id)+'/restore',{});
  await refreshFileHealthWorkbench();
  fileHealthDialog.close();
  toast('已恢复到文献库；附件仍缺失的话，可以在卡片上点「附件缺失」重新连接一份 PDF。');
 }catch(error){
  fileHealthFeedback.textContent='恢复失败：'+error.message;
  fileHealthBusy(false);
  toast(error.message);
 }
}
