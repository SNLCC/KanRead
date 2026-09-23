// Internal model catalog UI. Metadata is labelled by evidence, never silently trusted.
const capabilityLabels={chat:'大语言模型',image_input:'图片输入',video_input:'视频输入',embedding:'Embedding',rerank:'Rerank',asr:'ASR 语音识别',tts:'TTS 语音播报'};
let catalogConnections=[];
const catalogPanel=el('section','model-profile');catalogPanel.id='catalog-settings-panel';catalogPanel.hidden=true;$('.settings-panels').prepend(catalogPanel);
const catalogNav=el('button','','模型服务');catalogNav.type='button';catalogNav.dataset.settingTab='catalog';$('.settings-nav').prepend(catalogNav);catalogNav.onclick=()=>switchModelTab('catalog');
let selectedConnection='';
async function buildCatalogSettings(){catalogConnections=await api('/connections');if(!selectedConnection&&catalogConnections.length)selectedConnection=catalogConnections[0].id;catalogPanel.replaceChildren(el('h3','','模型服务'),el('p','hint','添加多个平台，在各用途选择模型。优先采用平台元数据；平台未公开的能力需要核实。'));
 const layout=el('div','provider-layout'),side=el('nav','provider-list'),right=el('div','provider-detail');catalogPanel.append(layout);layout.append(side,right);
 for(const c of catalogConnections){const b=el('button',c.id===selectedConnection?'active':'',c.name);b.onclick=()=>{selectedConnection=c.id;buildCatalogSettings();};side.append(b);}const add=el('button','secondary','＋ 添加平台');add.onclick=()=>{selectedConnection='new';buildCatalogSettings();};side.append(add);
 const current=catalogConnections.find(c=>c.id===selectedConnection),form=el('form');right.append(form);
 const name=connectionField(form,'连接名称',current?.name||''),provider=connectionField(form,'平台',current?.provider||'deepseek','text',modelSettings.providers.map(p=>[p.id,p.name]).concat([['groq','Groq']])),url=connectionField(form,'服务根地址',current?.base_url||'https://api.deepseek.com'),key=connectionField(form,current?.has_key?'API Key（已保存，留空保留）':'API Key','','password'),clear=connectionField(form,'清除保存的密钥',false,'checkbox');
 // 连接名称只在"自定义平台"时才需要用户填写：内置平台的名称已经确定了，
 // 再让用户起名属于重复劳动。新建时自动采用平台名并禁用该输入框；
 // 已有连接允许改名（用户可能想区分同一平台的两把密钥，例如"百炼 · 个人"与"百炼 · 团队"）。
 function syncName(){
  const preset=provider.value!=='custom';
  const platform=provider.selectedOptions[0]?.textContent||'';
  if(preset&&!current)name.value=platform;          // 新建内置平台：直接采用平台名
  name.disabled=preset&&!current;                   // 已有连接不禁用，便于改名
  nameHelp.textContent=name.disabled
    ? '内置平台的名称已自动采用平台名；如需自定义名称，请把「平台」改为「自定义」。'
    : '自定义平台请填写一个便于识别的名称。';
 }
 // 提示放在输入框所在的 label 之后（放进 label 内会改变其结构）。
 const nameHelp=el('p','model-help','');
 name.parentElement.after(nameHelp);
 provider.onchange=()=>{url.value=provider.value==='groq'?'https://api.groq.com/openai/v1':modelSettings.providers.find(p=>p.id===provider.value)?.base_url||'';syncName();key.value='';};
 syncName();
 const save=el('button','primary','保存设置'),refresh=el('button','secondary','获取模型'),feedback=el('p','hint');refresh.type='button';
 // 保存 / 获取模型 / 删除平台放在同一行，彼此留出间距（用户反馈：贴在一起且删除另起一行）。
 const actions=el('div','provider-actions');actions.append(save,refresh);
 if(current){const remove=el('button','danger','删除此平台');remove.type='button';remove.onclick=()=>confirmConnectionRemoval(current);actions.append(remove);}
 form.append(actions,feedback);
 async function persist(fetchModels){save.disabled=refresh.disabled=true;try{const c=await post('/connections'+(current?'/'+current.id:''),{name:name.value||provider.selectedOptions[0].textContent,provider:provider.value,base_url:url.value,api_key:key.value,clear_key:clear.checked},current?'PUT':'POST');selectedConnection=c.id;key.value='';if(fetchModels)await post(`/connections/${c.id}/refresh`,{});await refreshCatalogPickers();toast('服务设置已保存');}catch(e){feedback.textContent=e.message;}finally{save.disabled=refresh.disabled=false;}}
 form.onsubmit=e=>{e.preventDefault();persist(false);};refresh.onclick=()=>persist(true);
 if(!current){right.append(el('p','hint','选择预设平台，填写 API Key 后获取模型。本地平台通常无需密钥。'));return;}
 const filter=connectionField(right,'搜索模型',''),grid=el('div','catalog-models'),manual=el('button','secondary','＋ 添加模型');manual.onclick=()=>editCatalogModel(current);right.append(manual,grid);
 function draw(){grid.replaceChildren();for(const m of current.models||[]){if(!m.id.toLowerCase().includes(filter.value.toLowerCase()))continue;const row=el('div','catalog-model'),head=el('div','catalog-model-head'),edit=el('button','model-edit','编辑');edit.title='修改模型能力与容量';edit.onclick=()=>editCatalogModel(current,m);
  // 「测试连接」跟着模型走：它验证的是这个模型能不能按该用途真的调用成功。
  const testable=['chat','embedding','rerank'].find(kind=>(m.capabilities||[]).includes(kind));
  if(testable){const test=el('button','model-edit','测试');test.title='按「'+capabilityLabels[testable]+'」发送一次最小请求，只发送测试文本，不发送文献';test.onclick=()=>testConnectionModel(current.id,m.id,testable,test);head.append(el('strong','',m.id),test,edit);}
  else head.append(el('strong','',m.id),edit);
  row.append(head);const badges=el('div','capability-badges');for(const cap of m.capabilities)badges.append(el('span','',capabilityLabels[cap]));row.append(badges,el('small','',m.source+(m.context_window?' · 上下文 '+m.context_window.toLocaleString()+' token':'')));if(m.metadata_url){const link=el('a','','官方来源');link.href=m.metadata_url;link.target='_blank';link.rel='noopener noreferrer';row.append(link);}grid.append(row);}if(!current.models?.length)grid.append(el('p','hint','点击获取模型，或按平台文档手动添加。'));}filter.oninput=draw;draw();}
const capabilityDialog=el('dialog');document.body.append(capabilityDialog);
function editCatalogModel(connection,model){capabilityDialog.replaceChildren(el('h2','','模型能力'));const form=el('form');capabilityDialog.append(form);const id=connectionField(form,'模型 ID',model?.id||''),checks={};for(const [k,label] of Object.entries(capabilityLabels))checks[k]=connectionField(form,label,model?.capabilities.includes(k)||false,'checkbox');form.append(el('p','hint','依据平台说明或实际测试确认。名称推测不是能力保证；勾选用途不会自动调用或测试模型。'));const cancel=el('button','','取消'),save=el('button','primary','保存能力');cancel.type='button';cancel.onclick=()=>capabilityDialog.close();form.append(cancel,save);form.onsubmit=async e=>{e.preventDefault();try{await post(`/connections/${connection.id}/models`,{model:id.value,capabilities:Object.keys(checks).filter(k=>checks[k].checked)},'PUT');capabilityDialog.close();await refreshCatalogPickers();}catch(error){toast(error.message);}};capabilityDialog.showModal();}

// 删除平台连接：先问后端"谁在用它"，把后果逐条写进对话框，
// 再执行删除。删除只影响本机保存的地址与密钥，不触碰平台账户与文献。
const connectionRemoveDialog=el('dialog');connectionRemoveDialog.id='connection-remove-dialog';document.body.append(connectionRemoveDialog);
async function confirmConnectionRemoval(connection){
 const impact=el('p','hint','正在检查受影响的用途…');
 connectionRemoveDialog.replaceChildren(el('h2','','删除平台连接'),
  el('p','',`将从此设备删除「${connection.name}」的服务地址与已保存密钥。不会删除原始文献、Zotero / 云端附件，也不会改动平台账户。`),impact);
 const cancel=el('button','','取消'),confirm=el('button','danger','删除此平台');cancel.type=confirm.type='button';
 const actions=el('div','dialog-actions');actions.append(cancel,confirm);connectionRemoveDialog.append(actions);
 cancel.onclick=()=>connectionRemoveDialog.close();
 confirm.onclick=async()=>{
  confirm.disabled=true;
  try{
   const result=await api(`/connections/${connection.id}`,{method:'DELETE'});
   if(selectedConnection===connection.id)selectedConnection='';
   connectionRemoveDialog.close();
   await refreshCatalogPickers();await refreshModelStatus();
   const detail=(result.unbound||[]).map(item=>item.label+'：'+item.result).join('；');
   toast(detail?`已删除「${result.name}」；${detail}`:`已删除「${result.name}」；原文件、Zotero 与平台账户未受影响`);
  }catch(e){impact.textContent=e.message;impact.classList.add('error');confirm.disabled=false;}
 };
 connectionRemoveDialog.showModal();
 try{
  const impactData=await api(`/connections/${connection.id}/impact`);
  const affected=impactData.affected||[];
  impact.replaceChildren();
  if(affected.length){impact.append(el('p','','以下用途正在使用它，删除后会按说明调整：'));const list=el('ul');for(const item of affected)list.append(el('li','',`${item.label}：${item.result}`));impact.append(list);}
  else impact.append(el('p','hint','当前没有用途引用这个平台连接。'));
 }catch(e){impact.textContent=e.message;}
}
function modelPicker(kind,current,onApplied){const box=el('div','catalog-picker');box.append(el('strong','','从已连接平台选择'));const select=el('select');select.setAttribute('aria-label',capabilityLabels[kind]+'已连接模型');select.append(new Option('请选择模型',''));const values=[];for(const c of catalogConnections)for(const m of c.models||[])if(m.capabilities.includes(kind)){const index=values.push({connection_id:c.id,model:m.id})-1;select.append(new Option(c.name+' / '+m.id+' ['+m.source+']',String(index)));if(current?.connection_id===c.id&&current?.model===m.id)select.value=String(index);}box.append(select);
 const apply=el('button','secondary','使用此模型');apply.type='button';apply.onclick=async()=>{if(!select.value)return toast('请先选择模型');apply.disabled=true;try{await post('/model-assignments/'+kind,{...values[Number(select.value)]},'PUT');await onApplied();await refreshModelStatus();toast('当前用途已切换到所选模型');}catch(e){toast(e.message);}finally{apply.disabled=false;}};box.append(apply);
 if(!values.length)box.append(el('p','hint','还没有已连接的模型支持这一用途：请先在「模型服务」添加平台并获取（或手动标注）模型。'));
 return box;}
async function refreshCatalogPickers(){
 catalogConnections=await api('/connections');
 modelSettings=await api('/settings');
 $('#model-profile-panels').replaceChildren();modelKinds.forEach(buildProfile);decorateModelPickers();
 await buildCatalogSettings();await buildSpeechSettings();}
// 用途面板只保留一条主路径：从「模型服务」里已连接的平台选择模型。
//
// 此前每个用途面板还折叠着一份平台/密钥/地址/模型输入，与「模型服务」是两套重复配置：
// 同一件事要填两遍，两处还可能不一致。现在这些输入已从面板移除（见 settings.js 的
// buildProfile），平台、密钥与地址只在「模型服务」配置一次。
// 面板上保留的只有：启用开关、阅读容量等本用途专有的选项，以及这里的模型选择器。
//
// 曾经还有一个"还没有平台？添加一个内置预设"的下拉。用户指出它是多余的：它做的事
// （登记平台连接 + 标注模型能力 + 指派用途）与「模型服务」完全重合，只会让人怀疑
// 到底该在哪边配置。现在统一为：去「模型服务」添加平台，回到这里选择。
function decorateModelPickers(){for(const kind of modelKinds){
  const panel=$('#profile-'+kind);
  panel.querySelector('.profile-heading').after(modelPicker(kind,modelSettings[kind],async()=>{await refreshCatalogPickers();switchModelTab(kind);}));
  const picker=panel.querySelector('.catalog-picker');
  const manage=el('button','secondary','前往模型服务');manage.type='button';manage.onclick=()=>switchModelTab('catalog');
  picker.append(manage);
}}
// 语音面板的来源下拉直接读「模型服务」的已连接模型（见 speech.js），
// 因此这里不再向语音面板注入任何选择器，也不再需要旧版注入的清理逻辑。
