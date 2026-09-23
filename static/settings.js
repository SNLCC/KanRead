const modelKinds = ['chat', 'embedding', 'rerank'];
const modelCopy = {
  chat: {title:'会话模型', description:'按问题规划阅读范围，直接阅读连续原文；必要时分批读完再综合。', off:'关闭后展示检索原文，不生成 AI 回答。', model:'会话模型名称'},
  embedding: {title:'Embedding · 语义检索', description:'把文档和问题转换为向量，找到措辞不同但含义相关的原文。', off:'关闭外部服务后使用内置 TF-IDF 稀疏向量与关键词检索，无需密钥；不具备神经语义模型的同义理解能力。', model:'Embedding 模型名称'},
  rerank: {title:'Rerank · 检索重排序', description:'对已召回的原文再次排序，优先保留与问题更相关的内容。', off:'关闭外部服务后使用内置词项覆盖与短语重排序，无需密钥。', model:'Rerank 模型名称'},
};
let modelSettings = null;
let settingsWorking = false;
const profileNode = (kind, field) => document.querySelector(`#profile-${kind} [data-field="${field}"]`);

function fieldLabel(text, input) {
  const label = el('label', 'model-field');
  label.append(el('span', '', text), input);
  return label;
}

function profileMessage(kind, text, error=false) {
  const n = profileNode(kind, 'feedback');
  n.textContent = text;
  n.classList.toggle('error', error);
}

function readProfile(kind) {
  // 平台、密钥、地址、模型全部由「模型服务」统一配置，用途面板不再重复一套输入。
  // 因此这里只读取本面板真正负责的字段（启用开关与阅读容量），
  // 其余值原样回传已保存的配置：PUT /api/settings 是整体覆盖语义，
  // 漏传任何一项都会把用户已经选好的模型清掉。
  const saved=modelSettings[kind]||{};
  const node=field=>profileNode(kind,field);
  const number=(field,fallback)=>{const input=node(field);const value=Number(input?.value);return Number.isFinite(value)&&input?.value!==''?value:(Number(saved[field])||fallback);};
  const capacity=node('capacity_mode')?.value||saved.capacity_mode||'auto';
  return {
    capacity_mode:capacity,
    context_window:number('context_window',65536),
    output_reserve:number('output_reserve',4096),
    max_read_batches:number('max_read_batches',64),
    tokenizer_id:(node('tokenizer_id')?.value)??saved.tokenizer_id??'',
    visual_reading:node('visual_reading')?node('visual_reading').checked:!!saved.visual_reading,
    task_thinking:node('task_thinking')?node('task_thinking').checked:!!saved.task_thinking,
    max_parallel:number('max_parallel',0),
    connection_id:saved.connection_id||'',
    enabled: node('enabled')?node('enabled').checked:!!saved.enabled,
    provider:saved.provider||'custom',
    base_url:saved.base_url||'',
    model:saved.model||'',
    api_key:'',
    clear_key:false,
  };
}

function catalogConnectionName(id){
  try{return (catalogConnections||[]).find(c=>c.id===id)?.name||'';}catch{return '';}
}

function updateProfileHelp(kind) {
  const saved=modelSettings[kind]||{};
  const enabled=profileNode(kind,'enabled')?.checked;
  profileNode(kind,'disabled-hint').hidden=!!enabled;
  const provider=modelSettings.providers.find(p=>p.id===saved.provider);
  const node=profileNode(kind,'assignment');
  if(node){
    const connection=catalogConnectionName(saved.connection_id);
    if(saved.connection_id&&saved.model)node.textContent=`当前用途使用：${connection||'已连接的平台'} · ${saved.model}`;
    else if(saved.model&&saved.base_url)node.textContent=`当前用途使用：${provider?provider.name:saved.provider} · ${saved.model}（直接配置，未登记在「模型服务」；如需统一管理可到模型服务添加该平台）`;
    else node.textContent='当前用途还没有选择模型：请先在「模型服务」添加平台并获取模型，再回到这里选择。';
  }
  profileNode(kind,'privacy').textContent=kind==='chat'?'调用时发送：问题、选中文字、相关原文与最近对话。':kind==='embedding'?'启用后发送：待索引的文档文本与检索问题。切换模型后自动建立新索引，首次检索可能较慢。':'调用时发送：问题与召回的原文片段。需支持 /rerank 协议；会话模型不能直接作为重排序模型。';
}

// 每个设置栏目对应的表单 id。页脚只保留一个保存按钮，按当前栏目改指对应表单，
// 这样既保留“点一下就能存”的明确操作，也不会出现同一面板里两个保存按钮、
// 或各面板按钮文案不一（保存设置 / 保存全部设置 / 保存语音设置）的割裂。
// 说明：不能用 display:none 隐藏 #model-settings-form —— 非 model 栏目下它一直是隐藏的，
// 而隐藏表单的默认提交按钮在浏览器中不会触发；因此这里显式改指当前表单。
const saveForms={chat:'model-settings-form',embedding:'model-settings-form',rerank:'model-settings-form',
  speech:'speech-settings-form',shortcuts:'shortcut-settings-form',web:'web-settings-form',zotero:'zotero-settings-form',
  parsing:'parsing-settings-form',catalog:''};
// 文案必须说明"保存的是什么"：此前三个模型栏目一律显示「保存全部设置」，
// 用户无法判断这一下会保存哪些内容。模型表单实际同时承载三个用途。
const saveLabels={chat:'保存模型设置（三项用途）',embedding:'保存模型设置（三项用途）',rerank:'保存模型设置（三项用途）',
  speech:'保存语音设置',shortcuts:'保存快捷键与字号',web:'保存联网与隐私设置',zotero:'保存 Zotero 设置',
  parsing:'保存识别设置',catalog:''};
// 光有按钮文案还不够：把"本页保存什么"同时写进页脚说明，用户不必猜。
const saveHints={chat:'本页保存启用状态与阅读容量；平台、密钥与模型在「模型服务」配置。',embedding:'本页保存启用状态；平台、密钥与模型在「模型服务」配置。',
  rerank:'本页保存启用状态；平台、密钥与模型在「模型服务」配置。',speech:'本页保存语音输入与语音播报设置。',
  shortcuts:'本页保存快捷键、滚轮行为与界面字号。',web:'本页保存联网搜索与隐私权限设置。',
  zotero:'本页保存 Zotero 与 WebDAV 连接设置。',parsing:'本页保存 PDF 识别方式与服务设置。'};

function syncSaveEntry(kind){
  const button=$('#settings-save');if(!button)return;
  const formId=saveForms[kind]||'';
  // 只在目标表单确实存在时才改指，避免按钮变成不起作用的死按钮。
  const usable=formId&&document.getElementById(formId)?formId:'';
  button.hidden=!usable;
  if(usable){button.setAttribute('form',usable);button.textContent=saveLabels[kind]||'保存设置';}
  const feedback=$('#settings-feedback');
  if(feedback&&!feedback.classList.contains('error')&&kind!=='catalog')feedback.textContent=(saveHints[kind]||'')+'设置仅保存到此设备，保存后立即生效。';
}

function switchModelTab(kind) {
  $('#catalog-settings-panel').hidden=kind!=='catalog';
  $('#settings-dialog').classList.toggle('connection-view',['web','zotero','shortcuts','speech','catalog'].includes(kind));
  $('#model-settings-form').hidden=['web','zotero','shortcuts','speech','catalog'].includes(kind);$('#speech-settings-panel').hidden=kind!=='speech';$('#shortcut-settings-panel').hidden=kind!=='shortcuts';$('#web-settings-panel').hidden=kind!=='web';$('#zotero-settings-panel').hidden=kind!=='zotero';
  syncSaveEntry(kind);
  document.querySelectorAll('[data-setting-tab]').forEach(b=>{
    const active=b.dataset.settingTab===kind;
    b.classList.toggle('active',active);
    b.setAttribute('aria-current',active?'true':'false');
  });
  modelKinds.forEach(k=>document.querySelector(`#profile-${k}`).hidden=k!==kind);
}

function buildProfile(kind) {
  const copy=modelCopy[kind], saved=modelSettings[kind];
  const panel=el('section','model-profile'); panel.id=`profile-${kind}`;
  const heading=el('div','profile-heading');
  const title=el('div');title.append(el('h3','',copy.title),el('p','',copy.description));
  const toggle=el('label','model-toggle');
  const enabled=el('input');enabled.type='checkbox';enabled.dataset.field='enabled';enabled.checked=saved.enabled;
  toggle.append(enabled,el('span','','启用'));heading.append(title,toggle);panel.append(heading);
  const off=el('div','profile-off',copy.off);off.dataset.field='disabled-hint';panel.append(off);
  // 这里不再放平台 / API Key / 服务地址 / 模型输入：那些只在「模型服务」配置一次，
  // 本面板只负责"这一项用途用哪个已连接的模型"。重复两套配置正是用户反馈的问题：
  // 同一件事要填两遍，而且两处的值可能不一致。
  const assignment=el('p','model-help');assignment.dataset.field='assignment';panel.append(assignment);
  const feedback=el('div','profile-feedback');feedback.dataset.field='feedback';feedback.setAttribute('role','status');panel.append(feedback);
  const privacy=el('p','model-privacy');privacy.dataset.field='privacy';panel.append(privacy);
  $('#model-profile-panels').append(panel);
  const changed=()=>{profileMessage(kind,'');updateProfileHelp(kind);$('#settings-feedback').textContent='有未保存的修改。点击「保存设置」后生效。';};
  if(kind==='chat'){
    // 第 89 轮：把用户**真的要动**的三项从折叠区挪出来（用户反馈"这几项不应当隐藏"）：
    // 并发上限、是否允许发送页面图像、任务型调用是否关闭深度思考。
    // 留在这里仍然折叠的只有"容量与批次上限"这类平台相关的保守参数——
    // 它们有合理默认值，写错反而会让整轮提问失败。
    const common=el('div','model-common');
    // 并发上限：0/留空 = 按服务地址给默认（本机 1、云端 4）。**实测过**：
    // 23 批的用例 workers=1 → 8.7s、2 → 4.8s、4 → 2.7s、8 → 2.7s，收益到 4 就吃完了。
    const parallel=el('input');parallel.type='number';parallel.min=0;parallel.max=8;parallel.step=1;
    parallel.dataset.field='max_parallel';parallel.value=saved.max_parallel||0;parallel.oninput=changed;
    common.append(fieldLabel('同时进行的模型调用数上限（0 = 自动：本机 1、云端 4）',parallel),
      el('p','hint','只影响"逐批阅读"这类同层独立调用的并发，不会减少调用次数、批数或证据。'
        +'实测（23 批的合成用例）：1 → 8.7s、2 → 4.8s、4 → 2.7s、8 → 2.7s，'
        +'也就是**收益到 4 就吃完了**，再高只会更容易被平台限流；'
        +'报错里若出现"请求受限或额度不足"，就把它调低。'));
    const visual=el('input');visual.type='checkbox';visual.dataset.field='visual_reading';visual.checked=!!saved.visual_reading;visual.oninput=changed;
    common.append(fieldLabel('允许将复杂页面图像发送给当前会话模型（该模型须支持图片）',visual),
      el('p','hint','视觉读取用于扫描页、图表、公式和双栏页面；使用当前会话平台，可能增加调用费用。未开启时只做本机文本解析与 OCR。'));
    // 任务型调用关闭深度思考：默认关闭＝不发任何厂商私有参数（行为与之前一致）。
    // 打开后只对**这个服务地址**下发，而且只对"读这一批 / 合并 / 核对"这类任务调用；
    // 最终回答那一次仍然保留思考。实测这类调用占一轮输出的绝大多数 token。
    const taskThinking=el('input');taskThinking.type='checkbox';taskThinking.dataset.field='task_thinking';taskThinking.checked=!!saved.task_thinking;taskThinking.oninput=changed;
    common.append(fieldLabel('任务型调用关闭深度思考（只对上面这个服务地址生效）',taskThinking),
      el('p','hint','分批阅读、合并与核对属于确定性工作，思考的耗时往往远大于收益。打开后这些调用会带上 '
        +'thinking={"type":"disabled"}；最终回答那一次不受影响。若平台不认识这个参数会直接报错，'
        +'那就保持关闭——本应用默认不给任何平台发厂商私有参数。'));
    panel.append(common);
    const limits=el('details','reading-limits');
    limits.append(el('summary','','阅读容量与批次上限（一般不用改）'));
    const capacityMode=el('select');capacityMode.dataset.field='capacity_mode';capacityMode.append(new Option('自动（优先官方数据）','auto'),new Option('手动指定','manual'));capacityMode.value=saved.capacity_mode||'auto';limits.append(fieldLabel('容量设置方式',capacityMode),el('p','hint',saved.capacity_source||'平台未提供容量时使用保守值'));capacityMode.onchange=()=>{profileNode(kind,'context_window').disabled=capacityMode.value==='auto';changed();};
    for(const [field,label,fallback,min,max] of [['context_window','模型上下文容量（token，按平台实际设置）',65536,8192,4000000],['output_reserve','每次输出预留（token）',4096,512,32768],['max_read_batches','每次继续最多新增阅读批次',64,1,512]]){const input=el('input');input.type='number';input.min=min;input.max=max;input.step=1;input.dataset.field=field;input.value=saved[field]||fallback;if(field==='context_window')input.disabled=capacityMode.value==='auto';input.oninput=changed;limits.append(fieldLabel(label,input));}
    // tokenizer 导入入口在第 89 轮**从界面上移除**：用户明确说它没用，而且它要求用户自己去
    // 找一份与模型匹配的 tokenizer.json，配错了反而让容量估算失真。后端能力与已保存的值保留——
    // 界面上不再出现这个文件选择框，已配置过的取值原样带回去（见 read() 的回退）。
    limits.append(el('p','hint','需要全文时会分批完整阅读。达到本次工作预算会保存进度，点击继续即可接着阅读。检查点保存在本机，最长保留 7 天。容量未实测时按保守估算，不自动下载模型文件。'));
    panel.append(limits);
  }
  enabled.addEventListener('input',changed);
  updateProfileHelp(kind);
}

function settingsBusy(value) {
  settingsWorking=value;
  // 统一禁用生成出来的全部设置表单（模型、语音、快捷键、联网、Zotero、识别）。
  for(const form of document.querySelectorAll('.settings-panels form'))form.querySelectorAll('input,select,textarea,button').forEach(n=>n.disabled=value);
  $('#settings-save').disabled=value;
  $('#settings-close').disabled=value;
}

// 「测试连接」跟着模型走：它验证的是"这个已连接的模型能不能按该用途调用"，
// 因此放在「模型服务」的模型列表里，而不是每个用途面板各放一个重复按钮。
async function testConnectionModel(connectionId,model,kind,button){
  if(settingsWorking)return;
  const label=button.textContent;button.disabled=true;button.textContent='测试中…';
  try{
    const result=await post('/settings/test',{kind,connection_id:connectionId,model,enabled:true});
    toast(result.message||'测试成功');
  }catch(e){toast('测试失败：'+e.message);}
  finally{button.disabled=false;button.textContent=label;}
}

async function openModelSettings() {
  if(settingsWorking)return;
  try{
    modelSettings=await api('/settings');
    $('#model-profile-panels').replaceChildren();modelKinds.forEach(buildProfile);await buildConnectionSettings();buildShortcutSettings();await buildCatalogSettings();decorateModelPickers();await buildSpeechSettings();switchModelTab('catalog');
    $('#settings-feedback').textContent='设置仅保存到此设备，保存后立即生效。';
    $('#settings-feedback').classList.remove('error');
    $('#settings-dialog').showModal();
  }catch(e){toast(e.message);}
}

$('#settings').onclick=openModelSettings;
$('#model-badge').onclick=openModelSettings;
$('#model-badge').setAttribute('role','button');$('#model-badge').tabIndex=0;
$('#model-badge').onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openModelSettings();}};
document.querySelectorAll('[data-setting-tab]').forEach(b=>b.onclick=()=>switchModelTab(b.dataset.settingTab));
$('#settings-close').onclick=()=>$('#settings-dialog').close();
$('#settings-dialog').addEventListener('cancel',e=>{if(settingsWorking)e.preventDefault();});
$('#settings-dialog').addEventListener('close',()=>{$('#model-profile-panels').replaceChildren();modelSettings=null;});
$('#model-settings-form').onsubmit=async e=>{
  e.preventDefault();if(settingsWorking)return;
  const draft=Object.fromEntries(modelKinds.map(k=>[k,readProfile(k)]));
  for(const kind of modelKinds){const p=draft[kind];if(p.enabled&&(!p.base_url||!p.model)){switchModelTab(kind);profileMessage(kind,'启用前请先在「模型服务」添加平台并获取模型，再在本页选择该用途使用的模型。',true);return;}}
  settingsBusy(true);$('#settings-feedback').textContent='正在加密保存…';$('#settings-feedback').classList.remove('error');
  try{
    await post('/settings',draft,'PUT');
    await refreshModelStatus();
    settingsBusy(false);modelSettings=await api('/settings');toast('设置已保存，即刻生效');
  }catch(err){$('#settings-feedback').textContent=err.message;$('#settings-feedback').classList.add('error');settingsBusy(false);}
};
