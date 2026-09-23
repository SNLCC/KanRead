// Authored interaction adapters. No external scripts or user document modifications.
const zoomNumber=el('input');zoomNumber.id='zoom-label';zoomNumber.type='number';zoomNumber.min=ZOOM_MIN_PERCENT;zoomNumber.max=ZOOM_MAX_PERCENT;zoomNumber.step=ZOOM_STEP_PERCENT;zoomNumber.value=ZOOM_DEFAULT_PERCENT;zoomNumber.setAttribute('aria-label','文献缩放百分比');$('#zoom-label').replaceWith(zoomNumber);zoomNumber.after(el('span','','%'));zoomNumber.onchange=()=>{const value=Number(zoomNumber.value);if(Number.isFinite(value)){zoomNumber.value=zoomPercent(value);zoomAt(Number(zoomNumber.value));}else{zoomNumber.value=Math.round(state.zoom*100);toast(`请输入 ${ZOOM_MIN_PERCENT} 至 ${ZOOM_MAX_PERCENT} 的缩放比例`);}};zoomNumber.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();zoomNumber.blur();}};
for(const node of document.querySelectorAll('textarea,input:not([type=password])')){node.setAttribute('spellcheck','false');node.setAttribute('autocomplete','off');node.setAttribute('autocorrect','off');node.setAttribute('writingsuggestions','false');}

// Native selection is consumed after capture to avoid Edge's selection mini-toolbar.
const selectedOverlay=el('div','selected-overlay');$('#paper').append(selectedOverlay);
// 这个包装必须把第三个参数（meta）**原样转发**：setSelection 用它记"选文是从哪儿来的"
// （meta.origin，例如 'translation'）。上一版只转发 text/rects，于是 origin 永远是空的，
// 从译文栏进来的批注就没法在弹窗里说明"锚点已换成原文"。实测踩过。
const selectionBeforeOverlay=setSelection;setSelection=function(text,rects=[],meta={}){selectionBeforeOverlay(text,rects,meta);selectedOverlay.replaceChildren();for(const r of rects){const mark=el('div');Object.assign(mark.style,{left:r[0]*100+'%',top:r[1]*100+'%',width:(r[2]-r[0])*100+'%',height:(r[3]-r[1])*100+'%'});selectedOverlay.append(mark);}};
$('#paper').addEventListener('pointerup',()=>{if(state.crop)return;requestAnimationFrame(()=>{if(state.selection&&window.getSelection()?.toString().trim()){window.getSelection().removeAllRanges();}});});
$('#paper').addEventListener('pointerdown',e=>{if(!state.crop&&e.button===0&&!e.target.closest('button'))setSelection('');});

const floatingAsk=el('div','floating-ask');floatingAsk.hidden=true;floatingAsk.setAttribute('role','dialog');floatingAsk.setAttribute('aria-label','对选文提问');const floatQuote=el('blockquote'),floatInput=el('textarea'),floatSend=el('button','primary','发送'),floatClose=el('button','','关闭');floatInput.rows=3;floatInput.placeholder='输入问题，Enter 发送';floatInput.setAttribute('aria-label','浮窗提问');floatInput.setAttribute('writingsuggestions','false');floatInput.spellcheck=false;floatingAsk.append(floatQuote,floatInput,floatClose,floatSend);document.body.append(floatingAsk);
const floatAction=el('button','','浮窗提问');floatAction.onpointerdown=e=>e.preventDefault();floatAction.onclick=()=>{floatQuote.textContent=state.selection;floatingAsk.hidden=false;const r=selectionTools.getBoundingClientRect();floatingAsk.style.left=Math.max(8,Math.min(innerWidth-340,r.left))+'px';floatingAsk.style.top=Math.max(8,Math.min(innerHeight-240,r.bottom+6))+'px';selectionTools.hidden=true;floatInput.focus();};selectionTools.prepend(floatAction);
floatClose.onclick=()=>floatingAsk.hidden=true;floatSend.onclick=()=>{if(!floatInput.value.trim())return;$('#question').value=floatInput.value;floatInput.value='';floatingAsk.hidden=true;tab('chat');updateSendButton();$('#chat-form').requestSubmit($('#send'));};floatInput.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.ctrlKey&&!e.isComposing){e.preventDefault();floatSend.click();}};

function regionPreview(a){if(!a.rects?.length)return null;const r=a.rects[0],frame=el('div','region-preview'),img=el('img');img.src=`/api/documents/${a.document_id||a.doc||state.doc.id}/pages/${a.page}/image`;img.alt='第 '+a.page+' 页框选区域';img.onload=()=>{const width=Math.max(1,r[2]-r[0]),height=Math.max(1e-4,r[3]-r[1]);frame.style.aspectRatio=(img.naturalWidth*(r[2]-r[0]))/(img.naturalHeight*height);Object.assign(img.style,{width:100/(r[2]-r[0])+'%',left:-100*r[0]/(r[2]-r[0])+'%',top:-100*r[1]/height+'%'});};frame.append(img);return frame;}
const drawListBeforePreview=drawAnnotationList;drawAnnotationList=function(){drawListBeforePreview();for(const card of $('#annotations').querySelectorAll('.annotation-card')){const button=card.querySelector('button');const a=state.annotations.find(a=>a.id===card.dataset.annotationId);if(a&&annotationKind(a)==='region')card.insertBefore(regionPreview(a),card.querySelector('blockquote'));}};
const quoteEditor=el('textarea');quoteEditor.rows=3;quoteEditor.maxLength=12000;quoteEditor.setAttribute('aria-label','框选识别文字，可校订');quoteEditor.placeholder='可识别框选文字并校订';const recognizeRegion=el('button','secondary','识别框选文字'),previewContainer=el('div');recognizeRegion.type='button';$('#annotation-quote').after(previewContainer,quoteEditor,recognizeRegion);
const openBeforePreview=openAnnotation;openAnnotation=function(a){openBeforePreview(a);const region=annotationStyle.value==='region'||(!annotationDraft.quote&&annotationDraft.rects.length);previewContainer.replaceChildren();quoteEditor.hidden=recognizeRegion.hidden=!region;quoteEditor.value=annotationDraft.quote;quoteEditor.oninput=()=>annotationDraft.quote=quoteEditor.value;if(region)previewContainer.append(regionPreview(annotationDraft));};
recognizeRegion.onclick=async()=>{const a=annotationDraft,r=a.rects[0];if(!r)return;recognizeRegion.disabled=true;try{const result=await post(`/documents/${a.doc}/ocr`,{page:a.page,x0:r[0],y0:r[1],x1:r[2],y1:r[3]});if(a!==annotationDraft)return;const text=result.text_reflowed||result.text;quoteEditor.value=text;annotationDraft.quote=text;$('#annotation-quote').textContent=text;}catch(e){toast(e.message);}finally{recognizeRegion.disabled=false;}};
const drawBeforePageBadge=drawAnnotations;drawAnnotations=function(){drawBeforePageBadge();const notes=state.annotations.filter(a=>a.page===state.page&&!a.rects.length);if(notes.length){const b=el('button','page-note-badge','▣ '+notes.length);b.title='此页有 '+notes.length+' 条整页批注';b.setAttribute('aria-label',b.title);b.onclick=()=>showAnnotationPopover(notes);$('#annotation-layer').append(b);}};

// Typography applies only to the app, never the PDF text geometry.
// 问答字号可以在对话区直接调（用户反馈：应该能自己调对话框里问答的字号），
// 与「设置 → 快捷键与阅读 → 字号」共用同一份状态和同一个 CSS 变量，只有一处真相。
const UI_FONT_MIN=12,UI_FONT_MAX=22,CHAT_FONT_MIN=12,CHAT_FONT_MAX=32,CHAT_FONT_DEFAULT=15;
let typography={ui:14,chat:CHAT_FONT_DEFAULT};try{typography={...typography,...JSON.parse(localStorage.getItem('reader-typography')||'{}')};}catch{}
function persistTypography(){try{localStorage.setItem('reader-typography',JSON.stringify(typography));}catch{}}
function clampChatFont(value){const number=Math.round(Number(value));return Number.isFinite(number)?Math.max(CHAT_FONT_MIN,Math.min(CHAT_FONT_MAX,number)):typography.chat;}
function applyTypography(){document.documentElement.style.setProperty('--ui-font-size',Math.max(UI_FONT_MIN,Math.min(UI_FONT_MAX,typography.ui))+'px');document.documentElement.style.setProperty('--chat-font-size',clampChatFont(typography.chat)+'px');}
applyTypography();
let chatFontInput=null;
function setChatFontSize(value){typography={...typography,chat:clampChatFont(value)};applyTypography();persistTypography();const shown=String(typography.chat);const output=$('#chat-font-value');if(output)output.textContent=shown;if(chatFontInput&&String(chatFontInput.value)!==shown)chatFontInput.value=shown;}
const chatFontOutput=$('#chat-font-value');if(chatFontOutput)chatFontOutput.textContent=String(clampChatFont(typography.chat));
const chatFontDown=$('#chat-font-down'),chatFontUp=$('#chat-font-up'),chatFontReset=$('#chat-font-reset');
if(chatFontDown&&chatFontUp){chatFontDown.onclick=()=>setChatFontSize(typography.chat-1);chatFontUp.onclick=()=>setChatFontSize(typography.chat+1);if(chatFontReset)chatFontReset.onclick=()=>setChatFontSize(CHAT_FONT_DEFAULT);}
const buildShortcutsBeforeFonts=buildShortcutSettings;buildShortcutSettings=function(){buildShortcutsBeforeFonts();const panel=$('#shortcut-settings-panel'),form=$('#shortcut-settings-form'),group=el('div','typography-settings');group.append(el('h4','','字号'),el('p','hint','问答字号也可以在对话区右上角直接调整；两处共用同一份设置。'));const ui=connectionField(group,'界面字号',typography.ui,'number'),chat=connectionField(group,'问答字号（对话框里的提问与回答）',typography.chat,'number');ui.min=UI_FONT_MIN;ui.max=UI_FONT_MAX;chat.min=CHAT_FONT_MIN;chat.max=CHAT_FONT_MAX;chatFontInput=chat;
 // 字号并入「快捷键与阅读」的同一个表单：页脚保存按钮提交时会一并保存，
 // 不再另建第二个保存按钮（同一面板出现两个保存入口正是用户反映的割裂）。
 // 用 addEventListener 而非改写 onsubmit，避免覆盖快捷键本身的保存逻辑。
 form.prepend(group);form.addEventListener('submit',()=>{typography={...typography,ui:Number(ui.value)||typography.ui};applyTypography();persistTypography();setChatFontSize(chat.value);});};

const parsePanel=el('section','model-profile');parsePanel.id='parsing-settings-panel';parsePanel.hidden=true;$('.settings-panels').append(parsePanel);const parseNav=el('button','','PDF 与文字识别');parseNav.dataset.settingTab='parsing';$('.settings-nav').append(parseNav);const switchBeforeParsing=switchModelTab;switchModelTab=function(kind){switchBeforeParsing(kind);parsePanel.hidden=kind!=='parsing';if(kind==='parsing'){$('#model-settings-form').hidden=true;buildParsingSettings();}};parseNav.onclick=()=>switchModelTab('parsing');
async function buildParsingSettings(){try{const cfg=await api('/parsing-settings');parsePanel.replaceChildren(el('h3','','PDF 与文字识别'));const form=el('form');form.id='parsing-settings-form';parsePanel.append(form);
 // 一个下拉直接表达"这一页交给谁识别"。取值编码：
 //   local                 本机内置 OCR
 //   vision:<连接>:<模型>   「模型服务」里已连接、带图像能力的模型（这类能力确实由平台提供）
 //   mineru / custom        独立服务商，各自填自己的服务地址
 // 取值的第三类以前写成 "mineru:<平台连接>" / "custom:<平台连接>"，于是任何平台连接都会被
 // 列成「MinerU 服务：<平台名>」——MinerU 是单独的服务商，与聊天平台连接不是一回事。
 const source=el('select');source.setAttribute('aria-label','识别方式与服务');
 source.append(new Option('本机内置 OCR（默认，不上传文献）','local'));
 for(const c of catalogConnections){
  for(const m of c.models||[])if((m.capabilities||[]).includes('image_input')){
    const option=new Option(`已连接图像模型：${c.name} / ${m.id}`,`vision:${c.id}:${m.id}`);
    // 模型 id 可能含 "/"，挂到选项上比回头解析文案更稳。
    option.dataset.model=m.id;source.append(option);
  }
 }
 // MinerU 有四条路（官方自己就把在线服务分成"精准解析"与"轻量解析"两套接口）：
 //   mineru         自建 4.x 的 V1 API（服务根地址即 /v1 之前的部分）
 //   mineru_legacy  自建 3.x 及更早的旧 /file_parse（4.0 的 V1 服务已不再提供）
 //   mineru_online  mineru.net 官方·精准解析（/api/v4，需要 Token）
 //   mineru_agent   mineru.net 官方·轻量解析 Agent（/api/v1/agent，无需 Token，按 IP 限频）
 source.append(new Option('MinerU 4.x（自建 V1 API：上传 → 任务 → 取回 Markdown）','mineru'));
 source.append(new Option('MinerU 3.x 及更早（自建旧 /file_parse 接口）','mineru_legacy'));
 source.append(new Option('MinerU 在线 · 精准解析（mineru.net，需要 Token）','mineru_online'));
 source.append(new Option('MinerU 在线 · 轻量解析 Agent（mineru.net，无需 Token）','mineru_agent'));
 source.append(new Option('自定义解析服务（POST /parse，填写服务地址）','custom'));
 // 回填当前选择：vision 需要「连接 + 模型」，其余独立服务商只看模式。
 const standaloneModes=['mineru','mineru_legacy','mineru_online','mineru_agent','custom'];
 const currentValue=cfg.mode==='vision'?`vision:${cfg.connection_id}:${cfg.model}`
   :standaloneModes.includes(cfg.mode)?cfg.mode:'local';
 // 已保存的 vision 模型若不在列表里（连接改名、模型重新标注等），仍要能保持原选择。
 if(cfg.mode==='vision'&&![...source.options].some(o=>o.value===currentValue)){
  const option=new Option(`已保存的图像模型：${cfg.model}`,`vision:${cfg.connection_id}:${cfg.model}`);
  option.dataset.model=cfg.model;source.insertBefore(option,source.options[2]);
 }
 source.value=[...source.options].some(o=>o.value===currentValue)?currentValue:'local';
 const sourceRow=el('label','model-field');sourceRow.append(el('span','','识别方式与服务'),source);form.append(sourceRow);
 // 只保留一条**随模式变化**的说明：用户明确要求去掉堆积的小字，只留下必须知道/必须暴露的内容
 // （地址含义、MinerU 是独立项目、在线服务会把页面传出去）。
 const modeNote=el('p','hint','');form.append(modeNote);
 const url=el('input');url.type='url';url.setAttribute('aria-label','解析服务地址');url.value=cfg.service_url||'';url.placeholder='例如 http://localhost:8000';
 const urlRow=el('label','model-field');urlRow.append(el('span','','解析服务地址（MinerU / 自定义解析服务）'),url);form.append(urlRow);
 const key=el('input');key.type='password';key.autocomplete='new-password';key.setAttribute('aria-label','解析服务访问令牌');
 const keyRow=el('label','model-field');const keyLabel=el('span','','访问令牌（可选，按服务要求填写）');keyRow.append(keyLabel,key);
 const clearKey=el('input');clearKey.type='checkbox';
 const clearRow=el('label','model-field');clearRow.append(el('span','','移除已保存的访问令牌'),clearKey);
 form.append(keyRow,clearRow);
 const model=el('input');model.setAttribute('aria-label','图像模型 ID');model.value=cfg.model||'';model.placeholder='从「模型服务」的图像模型中选择后自动填入';
 const modelRow=el('label','model-field');modelRow.append(el('span','','图像模型 ID（来自「模型服务」）'),model);form.append(modelRow);
 const remote=connectionField(form,'允许向所选远程服务发送页面图像',cfg.allow_remote,'checkbox');
 remote.setAttribute('aria-label','允许向所选远程服务发送页面图像');
 const remoteRow=remote.parentElement;
 // 在线服务的地址是固定的官方入口：展示出来（只读）让用户清楚这一页会被送到哪里。
 const MINERU_ONLINE_URL='https://mineru.net/api/v4';
 const MINERU_AGENT_URL='https://mineru.net/api/v1/agent';
 const onlineModel=connectionField(form,'在线解析模型',cfg.mineru_online_model||'vlm','text',
   [['vlm','vlm · 官方推荐，版面与公式更准'],['pipeline','pipeline · 在线默认档，通常更快']]);
 onlineModel.setAttribute('aria-label','在线解析模型');
 const onlineModelRow=onlineModel.parentElement;
 // 在线服务**必须**把这一页发到 MinerU 的服务器，所以不再给一个可以取消的勾选框，
 // 改成红字提醒：选择了这条路，就是同意把这一页发出去（用户明确要求这样处理）。
 const remoteWarn=el('p','hint warn','');form.append(remoteWarn);
 // 档位只对自建 4.x 有意义：服务端启动时固定的档位不含所请求的档位时，任务会明确报错。
 const tier=connectionField(form,'MinerU 解析档位',cfg.mineru_tier||'standard','text',
   [['flash','flash · 原生文本与快速预览'],['basic','basic · 基础 OCR'],['standard','standard · 小模型 + VLM（PDF 默认）'],['advanced','advanced · 更高质量']]);
 tier.setAttribute('aria-label','MinerU 解析档位');
 const tierRow=tier.parentElement;
 const checkService=el('button','secondary','检查已保存的服务地址'),checkStatus=el('p','hint');checkService.type='button';
 form.append(checkService,checkStatus);
 checkService.onclick=async()=>{checkService.disabled=true;checkStatus.textContent='正在探测（只做只读能力查询，不发送任何页面内容）…';
  try{
   const r=await post('/parsing-settings/check',{});
   const typed=url.value.trim().replace(/\/+$/,'');
   const stale=r.url&&typed&&r.url!==typed?'当前输入框里的地址还没保存，下面的结果来自已保存的 '+r.url+'。':'';
   checkStatus.textContent=(stale?stale+' ':'')+r.message+(r.detail&&r.detail.length?'（'+r.detail.join('；')+'）':'');
  }catch(err){checkStatus.textContent=err.message;}finally{checkService.disabled=false;}};
 // 目标服务不在本机时，才会需要"允许发送页面图像"这个授权；本机/回环地址永远不需要，
 // 在线服务则必然需要（改用红字提醒）。这样这个小字说明只在该出现的时候出现。
 const isLocalTarget=value=>{
  let host='';try{host=new URL(String(value||'')).hostname.toLowerCase().replace(/^\[|\]$/g,'');}catch(error){return false;}
  return host==='localhost'||host==='::1'||host.startsWith('127.');
 };
 const refreshPermission=()=>{
  const value=source.value;
  const vision=value.startsWith('vision:');
  const online=value==='mineru_online',agent=value==='mineru_agent',hosted=online||agent;
  const standalone=standaloneModes.includes(value);
  // 本机识别不涉及任何外部服务：两个提示都不该出现。
  if(!standalone&&!vision){remoteRow.hidden=true;remoteWarn.hidden=true;remoteWarn.textContent='';return;}
  const target=vision?(source.selectedOptions[0]?.dataset?.url||''):url.value;
  const needsPermission=!hosted&&!isLocalTarget(target);
  remoteRow.hidden=!needsPermission;
  remoteWarn.hidden=!hosted;
  remoteWarn.textContent=hosted
   ?'⚠ 在线服务必须把这一页作为单页 PDF 发送到 MinerU 的服务器（'+(online?MINERU_ONLINE_URL:MINERU_AGENT_URL)+'）；选择这条路并点击识别，即表示同意发送。'
   :'';
 };
 url.oninput=refreshPermission;
 const pick=()=>{
  const value=source.value;
  const vision=value.startsWith('vision:');
  const online=value==='mineru_online',agent=value==='mineru_agent',hosted=online||agent;
  const standalone=standaloneModes.includes(value);
  modelRow.hidden=!vision;urlRow.hidden=!standalone;keyRow.hidden=!standalone||agent;clearRow.hidden=!standalone||agent;
  tierRow.hidden=value!=='mineru';onlineModelRow.hidden=!online;
  // 在线服务的地址是固定的，所以只读展示；探测按钮对它们没有意义。
  checkService.hidden=!standalone||hosted;checkStatus.hidden=!standalone||hosted;
  url.disabled=hosted;
  keyLabel.textContent=online?'mineru.net API Token（精准解析必填，官网申请）':'访问令牌（可选，按服务要求填写）';
  if(vision)model.value=source.selectedOptions[0]?.dataset?.model||model.value;
  if(value==='mineru'&&!url.value.trim())url.value='http://localhost:8000';
  if(online)url.value=MINERU_ONLINE_URL;
  else if(agent)url.value=MINERU_AGENT_URL;
  else if(url.value.trim().replace(/\/+$/,'')===MINERU_ONLINE_URL||url.value.trim().replace(/\/+$/,'')===MINERU_AGENT_URL)url.value='';
  modeNote.textContent={
   local:'本机内置 OCR：这一页在本机识别，不联网、不上传文献。',
   vision:'图像模型识别：地址与密钥在「模型服务」里统一维护，这里只选用哪个模型。',
   mineru:'MinerU 4.x 自建服务：服务根地址是 /v1 之前的部分，mineru-kit api-server 默认监听 127.0.0.1:8000（localhost 只适用于服务跑在这台电脑上）。MinerU 是独立开源项目，由你自行部署或订阅，本程序不捆绑、不代申请账号。',
   mineru_legacy:'MinerU 3.x 及更早：一次 POST /file_parse；4.0 的服务已不再提供该路由。由你自行部署，本程序不捆绑、不代申请账号。',
   mineru_online:'MinerU 官方·精准解析：需要 mineru.net 的 API Token（只保存在本机并加密），可选模型版本。',
   mineru_agent:'MinerU 官方·轻量解析 Agent：无需 Token（按 IP 限频），单文件、体积与页数上限更小，只返回 Markdown。',
   custom:'自定义解析服务：以 PNG multipart file 请求 /parse，返回 {"text":"..."}。'
  }[value]||'外部识别只在你点击识别时调用，识别结果可校订并保存在本机，原 PDF 不变。';
  refreshPermission();
 };
 source.onchange=pick;pick();
 const readSource=()=>{
  const value=source.value;
  const base={mineru_tier:tier.value,mineru_online_model:onlineModel.value};
  if(value==='local')return {...base,mode:'local',connection_id:'',model:'',service_url:''};
  if(value.startsWith('vision:')){const [_,id,mdl]=value.split(':');return {...base,mode:'vision',connection_id:id||'',model:mdl||model.value||'',service_url:''};}
  if(value==='mineru_online')return {...base,mode:'mineru_online',connection_id:'',model:'',service_url:MINERU_ONLINE_URL};
  if(value==='mineru_agent')return {...base,mode:'mineru_agent',connection_id:'',model:'',service_url:MINERU_AGENT_URL};
  return {...base,mode:value,connection_id:'',model:'',service_url:url.value.trim()};
 };
 // 页脚保存：在线服务必然要发送页面图像，直接按"已同意"提交（界面上有红字提醒）；
 // 其余模式用那个只在需要时才出现的勾选框。
 form.onsubmit=async e=>{e.preventDefault();settingsBusy(true);try{const picked=readSource();const hosted=picked.mode==='mineru_online'||picked.mode==='mineru_agent';await post('/parsing-settings',{...picked,service_key:key.value,clear_service_key:clearKey.checked,allow_remote:hosted?true:remote.checked},'PUT');key.value='';clearKey.checked=false;toast('识别设置已保存');}catch(err){toast(err.message);}finally{settingsBusy(false);pick();}};
 // 表单是异步建好后才存在的，需要重新同步页脚保存按钮的指向。
 syncSaveEntry('parsing');}catch(e){toast(e.message);}}
const pageTextButton=el('button','','校订文字');$('#toolbar').append(pageTextButton);const pageTextDialog=el('dialog','page-text-dialog'),pageTextInput=el('textarea');pageTextInput.rows=18;pageTextInput.setAttribute('aria-label','当前页提取文字');const pageTextStatus=el('p','hint'),recognizePage=el('button','secondary','使用所选服务重新识别'),savePageText=el('button','primary','保存校订'),closePageText=el('button','','关闭');pageTextDialog.append(el('h2','','页面文字 · 可校订'),pageTextStatus,pageTextInput,recognizePage,closePageText,savePageText);document.body.append(pageTextDialog);let editingPage=null;
pageTextButton.onclick=async()=>{if(!state.doc)return;editingPage={id:state.doc.id,page:state.page};try{const r=await api(`/documents/${editingPage.id}/pages/${editingPage.page}/text`);pageTextInput.value=r.text;pageTextStatus.textContent='第 '+editingPage.page+' 页 · '+r.origin+' · 保存后用于阅读与检索，原文件不变';pageTextDialog.showModal();}catch(e){toast(e.message);}};closePageText.onclick=()=>pageTextDialog.close();recognizePage.onclick=async()=>{recognizePage.disabled=true;try{const r=await post(`/documents/${editingPage.id}/pages/${editingPage.page}/recognize`,{});pageTextInput.value=r.text;pageTextStatus.textContent='识别完成，请校订后保存';}catch(e){toast(e.message);}finally{recognizePage.disabled=false;}};savePageText.onclick=async()=>{try{await post(`/documents/${editingPage.id}/pages/${editingPage.page}/text`,{text:pageTextInput.value},'PUT');pageTextDialog.close();toast('校订已保存，后续阅读与检索使用新文字');}catch(e){toast(e.message);}};

// Searchable tag chips and an app-owned editor.
const tagDialog=el('dialog');document.body.append(tagDialog);editTags=async function(d){tagDialog.replaceChildren(el('h2','','编辑标签'));const tags=new Set(d.tags||[]),chips=el('div','tag-chips'),form=el('form'),input=el('input');input.placeholder='输入标签，按 Enter 添加';input.setAttribute('aria-label','添加文献标签');const list=el('datalist');list.id='existing-tags';list.append(...[...new Set(state.docs.flatMap(x=>x.tags||[]))].map(t=>new Option(t,t)));input.setAttribute('list',list.id);function draw(){chips.replaceChildren();for(const t of tags){const b=el('button','tag-chip',t+' ×');b.onclick=()=>{tags.delete(t);draw();};chips.append(b);}}form.append(input,el('button','secondary','添加'));form.onsubmit=e=>{e.preventDefault();for(const t of input.value.split(/[,，]/).map(t=>t.trim()).filter(Boolean))tags.add(t);input.value='';draw();};const save=el('button','primary','保存标签'),close=el('button','','取消');close.onclick=()=>tagDialog.close();save.onclick=async()=>{if(input.value.trim())form.requestSubmit();try{await post(`/documents/${d.id}/tags`,{tags:[...tags]},'PUT');tagDialog.close();await loadLibrary();await drawWorkbench();}catch(e){toast(e.message);}};tagDialog.append(chips,form,list,close,save);draw();tagDialog.showModal();input.focus();};
// 标签筛选：用户明确要求「直接下拉选择，而不是输入搜索」，所以这里不再插入搜索框，
// 只把 library.js 画好的下拉刷新一次（下拉里的选项由现有文献的标签与作者组成）。
// 注意：不要再往 .workbench-controls 里追加输入框——那正是被用户否掉的做法。
const refreshTagFilter=drawTagFilter;drawTagFilter=function(){refreshTagFilter();};
$('#library-query').placeholder='搜索文献名称、标签或作者';$('#library-query').setAttribute('aria-label','搜索文献名称、标签或作者');
const heading=$('.workbench-heading'),libraryNav=$('.library-views');heading.append(libraryNav);libraryNav.append($('#workbench-import'));$('#workbench').addEventListener('scroll',()=>$('#workbench').classList.toggle('compact-workbench',$('#workbench').scrollTop>32),{passive:true});
const renderBeforeUsage=renderMessage;renderMessage=function(role,text,sources=[],meta={}){const node=renderBeforeUsage(role,text,sources,meta);if(meta.usage){const u=meta.usage,rows=u.requests||[],sum=k=>rows.reduce((n,r)=>n+(Number(r[k])||0),0),details=el('details','reading-report');
 // 缓存命中**必须按平台实际返回的字段读**（第 89 轮修）：OpenAI 兼容平台把命中数放在
 // `prompt_tokens_details.cached_tokens`，而这里此前只读 `prompt_cache_hit_tokens`——
 // 于是真实数据里明明有 512/1024/3904 的命中（用户那份记录里一轮 5,440），界面上却全是 0。
 const hit=row=>{const v=row.prompt_cache_hit_tokens;if(v!==undefined&&v!==null)return Number(v)||0;const d=row.prompt_tokens_details;return d&&d.cached_tokens!==undefined&&d.cached_tokens!==null?Number(d.cached_tokens)||0:null;};
 const reported=rows.filter(r=>hit(r)!==null).length,hitSum=rows.reduce((n,r)=>n+(hit(r)||0),0),inputSum=sum('prompt_tokens');
 const seconds=rows.reduce((n,r)=>n+(Number(r.seconds)||0),0);
 const cacheLine=reported?`其中缓存命中 ${hitSum}（占输入 ${inputSum?Math.round(hitSum/inputSum*100):0}%，${reported}/${rows.length} 次调用平台给出了该字段）`
  :'平台没有返回缓存命中字段，因此这里不显示命中数（不把"未返回"当成 0）';
 // 耗时按**阶段**汇总：用户问"慢在哪"时，这张表要能直接回答（第 92 轮）。
 const byPhase=new Map();
 for(const row of rows){const key=row.phase||'模型调用';byPhase.set(key,(byPhase.get(key)||0)+(Number(row.seconds)||0));}
 const slowest=[...byPhase.entries()].sort((a,b)=>b[1]-a[1]).slice(0,2).filter(([,value])=>value>0);
 const timeLine=seconds?`；模型调用合计耗时 ${seconds.toFixed(1)} 秒`
   +(slowest.length?`（最慢：${slowest.map(([name,value])=>`${name} ${value.toFixed(1)}s`).join('、')}）`:'')
   :'（平台未返回分段耗时）';
 // 「即时回复」是否真的生效：只有流式成功的那一次才会边写边显示。
 // 平台不支持流式时会自动退回普通请求（结果一样、但看不到边写边出），这里如实标出来。
 const streamRow=rows.find(r=>'streamed' in r);
 const streamLine=streamRow?(streamRow.streamed?'正文是流式生成（边写边显示已生效）。'
   :'正文**没有**流式生成：平台未接受流式请求，已自动退回普通请求（内容不变，但看不到边写边出）。'):'';
 details.append(el('summary','',`模型调用 ${u.calls} 次 · 本轮用量`),el('p','',`输入 ${inputSum} / 输出 ${sum('completion_tokens')} token；${cacheLine}${timeLine}。搜索规划调用另计。`));
 // **口径声明**（第 94 轮）：这些数字是本应用在调用时自己数的，不是服务商的账单，
 // 也不代表任何平台的计费口径。不同来源的统计出现差异是正常的，避免用户拿它去对账。
 details.append(el('p','hint','以上数字由本应用在调用过程中自行统计，**仅供参考**；'
  +'它与模型服务商、其它客户端或接口给出的用量/计费统计可能不一致，'
  +'**不构成任何计费依据**，请以服务商的账单与官方数据为准。缓存命中与耗时同样取决于该服务是否返回相应字段。'));
 if(streamLine)details.append(el('p','hint',streamLine));
 if(rows.length){const table=el('table','usage-table');const head=el('tr');for(const label of ['调用阶段','输入','缓存命中','输出','耗时'])head.append(el('th','',label));table.append(head);
  for(const row of rows){const value=hit(row);const tr=el('tr');tr.append(el('td','',row.phase||'模型调用'),el('td','',String(Number(row.prompt_tokens)||0)),el('td','',value===null?'—':String(value)),el('td','',String(Number(row.completion_tokens)||0)),el('td','',row.seconds?Number(row.seconds).toFixed(1)+'s':'—'));table.append(tr);}
  details.append(table,el('p','hint','耗时是**单次调用的墙钟时间**（含等待与生成）；同一批并发的调用会重叠，因此各行相加不等于整轮等待时间。同一份原文只在"形成分析"与"核对论断"两次调用中发送；两次使用完全相同的前缀，平台的上下文缓存命中时按缓存价计费。并发发出的几批**互相之间**不可能命中缓存（它们同时在飞），缓存收益只出现在先后发生的调用之间。'));}
 node.append(details);}return node;};
