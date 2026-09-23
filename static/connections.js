function connectionField(form,label,value,type='text',options){const row=el('label','model-field');const caption=el('span','',label);row.append(caption);const input=el(options?'select':'input');if(options){for(const [v,t] of options){const option=el('option','',t);option.value=v;input.append(option);}}else input.type=type;if(type==='checkbox')input.checked=!!value;else input.value=value||'';row.append(input);form.append(row);input.row=row;input.caption=caption;return input;}
async function buildConnectionSettings(){
 const [web,z]=await Promise.all([api('/search-settings'),api('/zotero/settings')]);
 const webPanel=$('#web-settings-panel');webPanel.replaceChildren(el('h3','','联网搜索与隐私'));
 const form=el('form');form.id='web-settings-form';webPanel.append(form);
 const providerNames={brave:'Brave Search',tavily:'Tavily',searxng:'SearXNG · 自托管'};
 const providerName=value=>providerNames[value]||value||'（未知）';
 const provider=connectionField(form,'搜索平台',web.provider,'text',Object.entries(providerNames));
 const base=connectionField(form,'SearXNG 服务根地址',web.base_url);
 const key=connectionField(form,'API Key','','password');
 // 密钥**只有一份**，属于"上一次保存的那个平台"。所以不能只按 web.has_key 说"已保存"：
 // 选到另一个平台时那句话会让人以为本平台已经配好了（用户报的就是这个）。
 // 这里一律说清"这份密钥属于谁"，本平台没配就直说本平台要另填。
 function searchKeyCaption(){
  if(!web.has_key)return provider.value==='searxng'?'API Key（SearXNG 可留空）':'API Key';
  if(provider.value===web.provider)return 'API Key（已保存，留空保留）';
  return 'API Key（已保存的密钥属于 '+providerName(web.provider)+'，'+providerName(provider.value)+' 需要另填）';
 }
 // 服务地址只有自托管 SearXNG 才用得上：选别的平台时把这一行**整行隐藏**，
 // 而不是留一个写着"其他平台忽略"的输入框让用户猜（用户明确要求）。
 // 隐藏不等于丢弃：已经填过的地址仍会随保存提交，服务端也不会因为换平台就把它抹掉。
 function syncSearchProvider(){
  const selfHosted=provider.value==='searxng';
  if(base.row)base.row.hidden=!selfHosted;
  if(key.caption)key.caption.textContent=searchKeyCaption();
 }
 syncSearchProvider();
 const clear=connectionField(form,'移除搜索密钥',false,'checkbox');
 const permission=connectionField(form,'搜索引擎发送权限',web.permission,'text',[['off','禁止联网搜索'],['review','每次确认搜索主题（默认）'],['auto','自动发送规划后的主题（无需逐次确认）']]);
 const planner=connectionField(form,'允许当前会话模型规划搜索（可能是云端模型）',web.planner,'checkbox');
 const followup=connectionField(form,'回答过程中允许提出补充搜索（最多两轮，仍遵循上面的确认权限）',web.followup,'checkbox');
 const fetchPages=connectionField(form,'允许直接访问搜索结果网页获取正文（网站可见网络地址，不发送文献或笔记）',web.fetch_pages,'checkbox');
 form.append(el('p','hint','关闭模型规划时只在本机提取关键词。启用后，下面获准的内容会发送给当前会话模型以识别知识缺口，最多生成 3 个互补搜索主题；不读取或展示模型内部思考。'));
 const inputs={};for(const [name,label] of [['question','当前问题'],['history','最近提问与回答'],['selection','选中原文'],['notes','当前文献的笔记'],['annotations','当前文献的批注'],['answer','本轮回答草稿（可能含文献私密内容）'],['document','本轮文献阅读材料']])inputs[name]=connectionField(form,'允许用于搜索规划：'+label,web['allow_'+name],'checkbox');
 form.append(el('p','hint','默认不使用历史、原文、笔记或批注。邮箱、长数字、网址会尝试移除，但不保证匿名。自托管 SearXNG 也可能把关键词转交上游搜索引擎。上述权限只控制搜索流程，不改变普通会话模型的文献上下文。'));
 form.onsubmit=async e=>{e.preventDefault();settingsBusy(true);try{const saved=await post('/search-settings',{provider:provider.value,base_url:base.value,api_key:key.value,clear_key:clear.checked,permission:permission.value,planner:planner.checked,followup:followup.checked,fetch_pages:fetchPages.checked,...Object.fromEntries(Object.entries(inputs).map(([k,v])=>['allow_'+k,v.checked]))},'PUT');key.value='';clear.checked=false;if(saved&&typeof saved==='object')Object.assign(web,saved);syncSearchProvider();toast('联网与隐私设置已保存');}catch(err){toast(err.message);}finally{settingsBusy(false);}};
 provider.onchange=()=>{key.value='';clear.checked=false;key.placeholder='切换平台不会复用其他平台密钥';syncSearchProvider();};
 const panel=$('#zotero-settings-panel');panel.replaceChildren(el('h3','','Zotero 只读连接'));const zform=el('form');zform.id='zotero-settings-form';panel.append(zform);
 const mode=connectionField(zform,'连接方式',z.mode,'text',[['local','本机 Zotero（无需密钥）'],['cloud','Zotero 云端存储'],['webdav','Zotero 云端 + 坚果云 / WebDAV 附件']]);
 const libraryType=connectionField(zform,'资料库类型',z.library_type,'text',[['users','个人资料库'],['groups','群组（WebDAV 不支持）']]);
 const id=connectionField(zform,'Zotero 数字用户 / 群组 ID（本机个人库填 0）',z.library_id);
 const apiKey=connectionField(zform,z.has_key?'Zotero API Key（已保存，留空保留）':'Zotero API Key（云端需要资料库 / 文件读取权限）','','password');
 const clearKey=connectionField(zform,'移除 Zotero 密钥',false,'checkbox');
 const url=connectionField(zform,'WebDAV 的 zotero 文件夹完整地址',z.webdav_url);
 const user=connectionField(zform,'WebDAV 账号',z.webdav_user);
 const password=connectionField(zform,z.has_password?'WebDAV 应用密码（已保存）':'WebDAV 应用密码（不是网页登录密码）','','password');
 const clearPass=connectionField(zform,'移除 WebDAV 应用密码',false,'checkbox');
 // 「检查连接」是只读诊断：Zotero 云端读取失败时，最需要知道的是"密钥属于哪个账户、
 // 有没有资料库读取权限、填的 ID 对不对"——这些不能靠一句笼统的报错让用户自己猜。
 // 诊断只做 GET，不写 Zotero 数据、不上传任何内容。
 const diagnose=el('button','secondary','检查连接（只读）'),diagnoseStatus=el('p','hint');diagnose.type='button';
 zform.append(diagnose,diagnoseStatus);
 diagnose.onclick=async()=>{diagnose.disabled=true;diagnoseStatus.textContent='正在按当前已保存的设置探测…';
  try{
   const r=await api('/zotero/diagnose');
   const lines=[r.message].concat(r.detail||[]);
   diagnoseStatus.textContent=lines.filter(Boolean).join('\n');
   if(r.suggested_library_id){
    const apply=el('button','secondary','把用户 ID 改成 '+r.suggested_library_id);apply.type='button';
    apply.onclick=()=>{id.value=r.suggested_library_id;apply.remove();diagnoseStatus.textContent+='\n已填入输入框，请点页脚「保存 Zotero 连接」后再检查一次。';};
    diagnoseStatus.after(apply);
   }
  }catch(err){diagnoseStatus.textContent=err.message;}finally{diagnose.disabled=false;}};
 zform.append(el('p','hint','本机：启动 Zotero，并在高级设置允许其他本机应用通信。云端：读取 Zotero 元数据，附件按需下载为应用缓存。坚果云默认附件目录为 https://dav.jianguoyun.com/dav/zotero；按实际目录修改。仅使用 GET，不修改 Zotero 数据库，不上传批注，不删除原附件。云端与 WebDAV 模式都要填"数字"用户 / 群组 ID；「检查连接」会用 API Key 查出正确的数字 ID 与读取权限。'));
 zform.onsubmit=async e=>{e.preventDefault();settingsBusy(true);try{await post('/zotero/settings',{mode:mode.value,library_type:libraryType.value,library_id:id.value,api_key:apiKey.value,clear_key:clearKey.checked,webdav_url:url.value,webdav_user:user.value,webdav_password:password.value,clear_password:clearPass.checked},'PUT');apiKey.value='';password.value='';toast('Zotero 连接已保存，可从文献工作台进入 Zotero');}catch(err){toast(err.message);}finally{settingsBusy(false);}};
}
function approveSearchPlan(plan){return new Promise(resolve=>{const dialog=$('#search-plan-dialog');$('#plan-description').textContent=`平台：${plan.provider}；规划方式：${plan.method}；规划输入：${plan.inputs.join('、')}。规划处理位置：${plan.planner_destination}。${plan.warning}`;$('#plan-queries').replaceChildren(...plan.queries.map(q=>el('li','',q)));let finished=false;const finish=value=>{if(finished)return;finished=true;dialog.close();resolve(value);};$('#plan-cancel').onclick=()=>finish(false);$('#plan-approve').onclick=()=>finish(true);dialog.oncancel=e=>{e.preventDefault();finish(false);};dialog.onclose=()=>{if(!finished){finished=true;resolve(false);}};dialog.showModal();});}
