const storageDescription=d=>d.storage_kind==='zotero'?'Zotero 本地只读链接 · 不复制附件 · 关闭 Zotero 后仍可读取磁盘文件':d.storage_kind==='zotero_cache'?'Zotero 云端附件 · 应用缓存副本':'应用保存的 PDF 副本 · 原文件不变';
 async function showWorkbench(view='library'){
  if(state.busy){toast('请等待当前操作完成');return;}
  document.body.classList.add('managing');$('#workbench').hidden=false;
  $('#show-trash').checked=view==='trash';$('#doc-title').textContent=view==='trash'?'应用回收站':'文献工作台';
  $('#zotero-panel').hidden=view!=='zotero';$('#library-panel').hidden=view==='zotero';
  document.querySelectorAll('[data-library-view]').forEach(b=>b.classList.toggle('active',b.dataset.libraryView===view));
  // 侧边栏的「全部文献」不是 [data-library-view] 按钮（它另有 id 与计数标记），
  // 因此上面的循环永远轮不到它：切到应用回收站或 Zotero 时它一直保留 active，
  // 看起来像是"选中了全部文献"。这里显式同步它的选中态。
  $('#library-home').classList.toggle('active',view==='library');
  if(view==='zotero'){await loadZoteroCollections();await loadZoteroItems(true);}else await drawWorkbench();
 }
async function renameDoc(d){const name=window.prompt('文献名称（只修改本应用显示名称）',d.name);if(name===null||!name.trim())return;await post(`/documents/${d.id}`,{name:name.trim()},'PATCH');await loadLibrary();if(state.doc?.id===d.id){state.doc.name=name.trim();$('#doc-title').textContent=name.trim();}if(document.body.classList.contains('managing'))await drawWorkbench();}
async function trashDoc(d){if(state.busy)return toast('请等待当前操作完成');await api(`/documents/${d.id}`,{method:'DELETE'});if(state.doc?.id===d.id){resetReaderInteraction();state.doc=null;state.pageData=null;state.annotations=[];++state.renderId;$('#annotation-layer').replaceChildren();$('#messages').replaceChildren();$('#notes').replaceChildren();$('#annotations').replaceChildren();$('#context span').textContent='等待打开文献';}await loadLibrary();await showWorkbench();toast('已移入应用回收站；原文件、Zotero 和云端附件均未移动');}
let purgeTarget=null;
function requestPurge(d){purgeTarget=d;$('#purge-name').value='';$('#purge-description').textContent=`将永久删除「${d.name}」在本应用中的索引、对话、笔记和批注，${d.storage_kind==='zotero'?'解除只读链接，不删除 Zotero 原附件。':'并删除应用内的 PDF 副本或缓存；不删除导入前原文件、Zotero 或云端附件。'} 不会进入 Windows 回收站，此操作不能撤销。请输入完整文献名称确认。`;$('#purge-dialog').showModal();}
$('#purge-cancel').onclick=()=>$('#purge-dialog').close();
$('#purge-form').onsubmit=async e=>{e.preventDefault();const b=e.submitter;b.disabled=true;try{await post(`/documents/${purgeTarget.id}/purge`,{confirm_name:$('#purge-name').value});$('#purge-dialog').close();await loadLibrary();await drawWorkbench();toast('应用内文献数据已彻底删除；原文件未改动');}catch(err){toast(err.message);}finally{b.disabled=false;}};
async function drawWorkbench(){
 try{
 const trash=$('#show-trash').checked,docs=trash?await api('/documents?trash=true'):state.docs;
 drawTagFilter();
 // 文献或标签变了，跨文献范围的具体项也要跟着更新（否则会留着已删除的文献）。
 drawScopeList();
 // 检索范围：名称、标签、作者——与输入框里的提示文案必须一致（否则就是文案在骗人）。
 const query=$('#library-query').value.trim().toLocaleLowerCase();
 const haystack=d=>(d.name+' '+(d.tags||[]).join(' ')+' '+(d.author||'')).toLocaleLowerCase();
 // 标签下拉只按标签过滤（作者不在下拉里，见 drawTagFilter）。
 const filtered=docs.filter(d=>haystack(d).includes(query)&&(!libraryTag||(d.tags||[]).some(t=>t.toLocaleLowerCase()===libraryTag)));
 const sort=$('#library-sort').value;if(sort==='name')filtered.sort((a,b)=>a.name.localeCompare(b.name,'zh-CN'));if(sort==='progress')filtered.sort((a,b)=>b.current_page/b.pages-a.current_page/a.pages);
 $('#library-summary').textContent=`${trash?'应用回收站':'全部文献'} · ${filtered.length} 份`;$('#document-grid').replaceChildren();
 if(!filtered.length)$('#document-grid').append(el('p','hint',trash?'应用回收站为空':'暂无匹配文献，试试导入 PDF、连接 Zotero 或更换搜索词。'));
 for(const d of filtered){
 const card=el('article','document-card');card.dataset.docId=d.id;card.tabIndex=0;card.setAttribute('aria-label',d.name+(trash?' · 已移入应用回收站':' · 点击继续阅读'));
 if(!trash){card.onclick=e=>{if(!e.target.closest('button,a,input'))openDoc(d);};card.onkeydown=e=>{if(e.target===card&&['Enter',' '].includes(e.key)){e.preventDefault();openDoc(d);}};}
 card.append(el('p','document-tags',[(d.author?'@'+d.author:''),...(d.tags||[]).map(t=>'#'+t)].filter(Boolean).join('  ')));
 card.append(el('h2','',d.name),el('p','hint',`${d.pages} 页 · 读至第 ${d.current_page} 页`),el('p','storage-label',storageDescription(d)));
 const progress=el('progress');progress.max=d.pages;progress.value=d.current_page;progress.setAttribute('aria-label','阅读进度');card.append(progress);
 const actions=el('div','document-actions');const action=(label,fn)=>{const b=el('button','secondary',label);b.onclick=async e=>{e.stopPropagation();b.disabled=true;try{await fn();}catch(err){toast(err.message);}finally{b.disabled=false;}};actions.append(b);return b;};
 if(trash){action('恢复到文献库',async()=>{await post(`/documents/${d.id}/restore`,{});await loadLibrary();await drawWorkbench();});action('彻底删除应用数据',()=>requestPurge(d));}
 else{action('继续阅读',()=>openDoc(d));action('重命名',()=>renameDoc(d));action('编辑作者',()=>editAuthor(d));action('编辑标签',()=>editTags(d));if(typeof translationAction==='function')translationAction(action,d);action('移入应用回收站',()=>trashDoc(d));}
 card.append(actions);$('#document-grid').append(card);
 }
 if(typeof translationDecorations==='function')translationDecorations();
 }catch(e){toast(e.message);}
}
$('#workbench-import').onclick=()=>$('#file').click();$('#library-query').oninput=drawWorkbench;$('#library-sort').onchange=drawWorkbench;
document.querySelectorAll('[data-library-view]').forEach(b=>b.onclick=()=>showWorkbench(b.dataset.libraryView));
let zoteroStart=0,zoteroNext=null;
async function loadZoteroItems(reset=false){if(reset){zoteroStart=0;$('#zotero-items').replaceChildren();}$('#zotero-feedback').textContent='正在只读获取 Zotero PDF 附件…';try{const result=await api('/zotero/items?q='+encodeURIComponent($('#zotero-query').value)+'&start='+zoteroStart+'&collection='+encodeURIComponent($('#zotero-collection').value));zoteroNext=result.next;for(const item of result.items){const b=el('button','zotero-item');b.append(el('strong','',item.title),el('small','',`${item.filename} · 点击读取（${result.mode==='local'?'本地只读链接':'下载应用缓存'}）`));b.onclick=async()=>{if(state.busy)return;state.busy=true;b.disabled=true;toast('正在读取附件并建立本地索引…');try{const doc=await post(`/zotero/items/${item.key}/open`,{});await loadLibrary();state.busy=false;await openDoc(doc);}catch(e){toast(e.message);}finally{state.busy=false;b.disabled=false;}};$('#zotero-items').append(b);}$('#zotero-feedback').textContent=result.items.length?'只读连接，不向 Zotero 或 WebDAV 写入任何修改。':'暂无 PDF 附件。可更换搜索词或检查同步状态。';$('#zotero-more').hidden=zoteroNext===null;}catch(e){$('#zotero-feedback').textContent=e.message+' 请在设置的 Zotero 页配置。';}}
$('#zotero-search').onsubmit=e=>{e.preventDefault();loadZoteroItems(true);};$('#zotero-more').onclick=()=>{zoteroStart=zoteroNext;loadZoteroItems();};

async function loadZoteroCollections(){
 try{const result=await api('/zotero/collections'),select=$('#zotero-collection'),previous=select.value;select.replaceChildren(new Option('全部文献',''));const seen=new Set();
 function append(parent,depth){for(const c of result.collections.filter(c=>c.parent===parent)){if(seen.has(c.key))continue;seen.add(c.key);select.append(new Option('　'.repeat(Math.min(depth,12))+c.name,c.key));append(c.key,depth+1);}}
 append('',0);for(const c of result.collections)if(!seen.has(c.key))select.append(new Option(c.name,c.key));select.value=previous;if(select.selectedIndex<0)select.value='';
 }catch(e){toast('分类读取失败：'+e.message);}
}
$('#zotero-collection').onchange=()=>loadZoteroItems(true);

let libraryTag='';
const tagFilter=el('select');tagFilter.setAttribute('aria-label','按标签筛选文献');$('.workbench-controls').append(tagFilter);tagFilter.onchange=()=>{libraryTag=tagFilter.value;drawWorkbench();};
// 标签筛选：**下拉选择**（不是输入搜索），选项只有标签——作者不在下拉里，
// 作者的检索走上面的搜索框（用户明确区分了这两件事）。
function drawTagFilter(){
 const tags=[...new Set(state.docs.flatMap(d=>d.tags||[]))].filter(Boolean).sort((a,b)=>a.localeCompare(b,'zh-CN'));
 const options=[new Option(tags.length?'全部标签':'还没有标签','')];
 for(const tag of tags)options.push(new Option('#'+tag,tag));
 tagFilter.replaceChildren(...options);
 tagFilter.value=tags.includes(libraryTag)?libraryTag:'';
 if(!tagFilter.value)libraryTag='';
}
async function editTags(d){const value=window.prompt('编辑标签，用中文或英文逗号分隔；留空清除。仅保存在本应用。',(d.tags||[]).join('，'));if(value===null)return;await post(`/documents/${d.id}/tags`,{tags:value.split(/[,，]/)},'PUT');await loadLibrary();await drawWorkbench();}
// 作者：导入时先取 PDF 元数据，取不到就由用户在这里填（只影响检索与筛选）。
async function editAuthor(d){const value=window.prompt('作者（用于检索与筛选，可留空）：',d.author||'');if(value===null)return;const saved=await post(`/documents/${d.id}/author`,{author:value.trim()},'PUT');d.author=saved.author||'';await loadLibrary();await drawWorkbench();}
// 跨文献范围：**弹窗选择**（第 90 轮按用户反馈改）。此前是行内多选，占掉输入框一行；
// 现在选中「跨文献」时弹出对话框选"全部文献 / 指定文献 / 标签"，选完在输入框那一行只留
// 一个可点击的小标签（"跨文献 · 已选 2 项"），随时能再点开改。
// 语义没变：类型与后端 scope 一一对应，不存在"什么都不选＝全部"这种要靠用户猜的隐式状态。
const scopeDialog=el('dialog','scope-dialog');scopeDialog.id='scope-dialog';
scopeDialog.setAttribute('aria-label','跨文献范围');
scopeDialog.append(el('h3','','跨文献范围'));
scopeDialog.append(el('p','hint','选择这次一起检索哪些文献。先选范围类型，再选具体文献或标签（可多选）。'));
const scopeKind=el('select');scopeKind.id='cross-scope-kind';
scopeKind.setAttribute('aria-label','跨文献范围类型');
for(const [value,text] of [['all','全部文献'],['documents','指定文献'],['tags','标签']])scopeKind.append(new Option(text,value));
// 初始状态写成显式赋值，不依赖"下拉默认选第一项"这种浏览器行为。
scopeKind.value='all';
const scopeList=el('select','scope-list');scopeList.multiple=true;scopeList.size=6;scopeList.id='cross-scope-list';
scopeList.setAttribute('aria-label','跨文献范围里具体要一起检索的文献或标签，按住 Ctrl 可多选');
const scopeCount=el('span','scope-count','');
const scopePicker=el('div','scope-picker');
scopePicker.append(el('span','scope-picker-label','一起检索：'),scopeKind,scopeCount,scopeList);
scopeDialog.append(scopePicker);
const scopeActions=el('div','dialog-actions');
const scopeCancel=el('button','secondary','取消');scopeCancel.type='button';
const scopeConfirm=el('button','primary','确定');scopeConfirm.type='button';
scopeActions.append(scopeCancel,scopeConfirm);scopeDialog.append(scopeActions);
document.body.append(scopeDialog);
// 输入框那一行的小标签：只有选了「跨文献」才出现，点它重新打开对话框。
const scopeSummary=el('button','scope-summary');scopeSummary.type='button';scopeSummary.id='scope-summary';
scopeSummary.hidden=true;scopeSummary.title='点击修改跨文献范围';
scopeSummary.onclick=()=>openScopeDialog();
$('#reading-help')?.after(scopeSummary);
let crossScope={scope:'all',scope_tags:[],scope_documents:[]};
// 两个类型各自保留已选，来回切换时不丢。
function drawScopeList(){
 const kind=scopeKind.value;
 scopeList.hidden=kind==='all';
 scopeList.replaceChildren();
 if(kind==='documents')for(const doc of state.docs){const option=el('option','',doc.name);option.value=doc.id;option.selected=crossScope.scope_documents.includes(doc.id);scopeList.append(option);}
 if(kind==='tags')for(const tag of [...new Set(state.docs.flatMap(d=>d.tags||[]))].sort()){const option=el('option','',tag);option.value=tag;option.selected=crossScope.scope_tags.includes(tag);scopeList.append(option);}
 if(!scopeList.hidden&&!scopeList.options.length)scopeList.append(new Option(kind==='tags'?'还没有标签：可在文献工作台给文献加标签':'还没有文献：先导入 PDF 或连接 Zotero',''));
 updateScope();
}
function updateScope(){
 const kind=scopeKind.value;
 const chosen=kind==='all'?[]:[...scopeList.selectedOptions].map(o=>o.value).filter(Boolean);
 crossScope={scope:kind,scope_tags:kind==='tags'?chosen:crossScope.scope_tags,scope_documents:kind==='documents'?chosen:crossScope.scope_documents};
 scopeCount.textContent=kind==='all'?'全部文献':chosen.length?`已选 ${chosen.length} 项`:'请至少选择 1 项';
 scopeCount.classList.toggle('warn',kind!=='all'&&!chosen.length);
 drawScopeSummary();
}
function drawScopeSummary(){
 const label=crossScope.scope==='all'?'全部文献':crossScope.scope==='tags'?'标签':'指定文献';
 const count=crossScope.scope==='tags'?crossScope.scope_tags.length:crossScope.scope_documents.length;
 const ready=crossScope.scope==='all'||count>0;
 scopeSummary.textContent=`跨文献 · ${label}`+(crossScope.scope==='all'?'':`（${count} 项）`);
 scopeSummary.classList.toggle('warn',!ready);
 scopeSummary.title=ready?'点击修改跨文献范围':'还没有选择具体文献或标签：点击选择，否则这一轮不会检索任何文献';
}
scopeKind.onchange=drawScopeList;scopeList.onchange=updateScope;
function openScopeDialog(){drawScopeList();$('#scope-dialog').showModal();}
scopeCancel.onclick=()=>$('#scope-dialog').close();
scopeConfirm.onclick=()=>{updateScope();$('#scope-dialog').close();};
// 跨文献时显示小标签，并在**刚选中时自动弹窗**让用户选具体范围。**必须由这个函数统一处理**：
// 第 89 轮之前只有勾选框的 change 事件会显示行内选择器，而范围下拉是**代码**把 #cross-book
// 勾上的（选「跨文献」时），change 事件不会触发——于是用户选了跨文献，却看不到选文献的地方。
let scopeAsked=false;
function syncScopePicker(){
 const on=$('#cross-book').checked;
 scopeSummary.hidden=!on;
 if(!on){scopeAsked=false;if($('#scope-dialog').open)$('#scope-dialog').close();return;}
 drawScopeSummary();
 if(!scopeAsked){scopeAsked=true;openScopeDialog();}
}
$('#cross-book').addEventListener('change',syncScopePicker);
// 点对话框外的遮罩也能关（原生 dialog 的行为），关掉时保留当前选择。
$('#scope-dialog').addEventListener('click',event=>{if(event.target===$('#scope-dialog'))$('#scope-dialog').close();});

