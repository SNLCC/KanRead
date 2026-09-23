let speechConfig=null,voiceSession=null,speechPlayback=null,voiceOriginal='';
const voiceControls=el('div','speech-controls'),voiceButton=el('button','','按住说话'),voiceStop=el('button','','取消录音');voiceButton.type=voiceStop.type='button';voiceStop.hidden=true;voiceControls.append(voiceButton,voiceStop);$('#chat-form').before(voiceControls);
const voiceFeedback=el('div');voiceFeedback.id='voice-feedback';voiceControls.after(voiceFeedback);
function applySpeechVisibility(){voiceControls.hidden=!speechConfig?.input_enabled;document.body.classList.toggle('speech-output-enabled',!!speechConfig?.output_enabled);}
api('/speech/settings').then(cfg=>{speechConfig=cfg;applySpeechVisibility();}).catch(()=>{});
function encodeWave(samples,rate){let length=samples.reduce((n,a)=>n+a.length,0);const buffer=new ArrayBuffer(44+length*2),v=new DataView(buffer);const write=(offset,s)=>{for(let i=0;i<s.length;i++)v.setUint8(offset+i,s.charCodeAt(i));};write(0,'RIFF');v.setUint32(4,36+length*2,true);write(8,'WAVE');write(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,rate,true);v.setUint32(28,rate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);write(36,'data');v.setUint32(40,length*2,true);let i=44;for(const a of samples)for(const x of a){v.setInt16(i,Math.max(-1,Math.min(1,x))*(x<0?32768:32767),true);i+=2;}return new Blob([buffer],{type:'audio/wav'});}
async function releaseMicrophone(session){clearTimeout(session.timer);session.processor?.disconnect();session.source?.disconnect();session.gain?.disconnect();session.stream?.getTracks().forEach(t=>t.stop());if(session.audio&&session.audio.state!=='closed')await session.audio.close();}
async function startVoice(){
 if(voiceSession||state.busy||!requireDoc())return;
 if(!speechConfig?.input_enabled){toast('请先在「语音输入与播报」设置中开启语音输入');return;}
 stopSpeaking();const session={controller:new AbortController(),samples:[],doc:state.doc.id,page:state.page,draft:$('#question').value,released:false,cancelled:false};voiceSession=session;state.busy=true;voiceStop.hidden=false;voiceStop.textContent='取消录音';voiceButton.textContent='正在请求麦克风…';
 try{
  session.stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true}});
  if(session.cancelled||session.released){await releaseMicrophone(session);return;}
  session.audio=new AudioContext({sampleRate:16000});await session.audio.resume();if(session.cancelled||session.released){await releaseMicrophone(session);return;}session.rate=session.audio.sampleRate;
  session.source=session.audio.createMediaStreamSource(session.stream);session.processor=session.audio.createScriptProcessor(512,1,1);session.gain=session.audio.createGain();session.gain.gain.value=0;
  session.processor.onaudioprocess=e=>{if(!session.released&&!session.cancelled){const data=new Float32Array(e.inputBuffer.getChannelData(0));session.samples.push(data);const rms=Math.sqrt(data.reduce((sum,v)=>sum+v*v,0)/data.length);session.peak=Math.max(session.peak||0,rms);if(!session.lastMeter||Date.now()-session.lastMeter>200){voiceButton.textContent=rms>.003?'正在录音 · 检测到声音':'正在录音 · 音量很低';session.lastMeter=Date.now();}}};
  session.source.connect(session.processor);session.processor.connect(session.gain);session.gain.connect(session.audio.destination);
  voiceButton.textContent='正在录音，松开结束';voiceFeedback.textContent='只在按住期间录音。识别位置：'+(speechConfig.asr_mode==='system'?'本机 Windows':speechConfig.asr_url)+'；点击取消录音可放弃。先等按钮显示正在录音，再开始说话。';
  session.timer=setTimeout(()=>{session.forceReview=true;finishVoice();},60000);
 }catch(err){toast('无法录音：'+err.message);session.cancelled=true;await releaseMicrophone(session);}
 finally{if(session.cancelled||session.released){if(voiceSession===session){voiceSession=null;state.busy=false;voiceButton.textContent='按住说话';voiceStop.hidden=true;}}}
}
async function cancelVoice(){const session=voiceSession;if(!session)return;session.cancelled=true;session.controller.abort();await releaseMicrophone(session);if(voiceSession===session){voiceSession=null;state.busy=false;voiceButton.textContent='按住说话';voiceStop.hidden=true;voiceFeedback.textContent='语音已取消，未发送问题';}}
async function finishVoice(){
 const session=voiceSession;if(!session||session.released)return;session.released=true;
 if(!session.audio){voiceFeedback.textContent='麦克风尚未就绪，此次未录音。请按住直到显示正在录音后再说话。';return;}
 await releaseMicrophone(session);if(session.cancelled)return;voiceButton.textContent='正在识别…';voiceStop.textContent='取消识别';
 try{
  if(session.samples.reduce((n,a)=>n+a.length,0)<session.rate*.25)throw new Error('录音太短，请按住说完整的问题');
  if((session.peak||0)<.001)throw new Error('麦克风没有采集到有效声音。请检查 Windows 输入设备、麦克风静音和应用权限。');
  const form=new FormData();form.append('file',encodeWave(session.samples,session.rate),'speech.wav');session.samples=[];
  const result=await api('/speech/transcribe',{method:'POST',body:form,signal:session.controller.signal});
  if(session.cancelled)return;voiceOriginal=result.text;voiceFeedback.textContent='原始识别：'+result.text+'\n正在检查识别文本…';
  const corrected=await api('/speech/correct',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:result.text,document_id:session.doc,page:session.page}),signal:session.controller.signal});
  if(session.cancelled)return;
  session.draft=$('#question').value;const hasDraft=!!session.draft.trim(),lowConfidence=typeof result.confidence==='number'&&result.confidence<.65;
  const review=corrected.needs_review||lowConfidence||session.forceReview||hasDraft;
  $('#question').value=(hasDraft?session.draft+'\n':'')+corrected.text;$('#question').dispatchEvent(new Event('input',{bubbles:true}));
  voiceFeedback.textContent='原始识别：'+result.text+(corrected.corrected?'\n纠错后：'+corrected.text:'')+'\n'+(review?(corrected.reason||'请检查识别文本，再点击发送。'):speechConfig.auto_send?'识别完成，正在发送…':'已填入草稿，请检查后发送。');
  voiceSession=null;state.busy=false;voiceButton.textContent='按住说话';voiceStop.hidden=true;
  if(speechConfig.auto_send&&!review)$('#chat-form').requestSubmit($('#send'));else $('#question').focus();
 }catch(err){if(!session.cancelled){voiceFeedback.textContent=err.message;toast(err.message);}}
 finally{if(voiceSession===session){voiceSession=null;state.busy=false;voiceButton.textContent='按住说话';voiceStop.hidden=true;}}
}
voiceButton.onpointerdown=e=>{if(e.button!==0)return;e.preventDefault();voiceButton.setPointerCapture(e.pointerId);startVoice();};voiceButton.onpointerup=e=>{e.preventDefault();finishVoice();};voiceButton.onpointercancel=()=>cancelVoice();voiceStop.onclick=cancelVoice;
function stopSpeaking(){if(!speechPlayback)return;const play=speechPlayback;speechPlayback=null;play.controller.abort();play.audio?.pause();play.finish?.();if(play.url)URL.revokeObjectURL(play.url);}
async function speakAnswer(text){
 if(!speechConfig?.output_enabled)return toast('请在「语音输入与播报」设置中开启播报');stopSpeaking();const play={controller:new AbortController()};speechPlayback=play;
 try{for(const part of text.match(/[\s\S]{1,1500}/g)||[]){if(speechPlayback!==play)break;const response=await fetch('/api/speech/synthesize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:part}),signal:play.controller.signal});if(!response.ok){let data=await response.json();throw new Error(data.detail||'语音生成失败');}play.url=URL.createObjectURL(await response.blob());play.audio=new Audio(play.url);await new Promise((resolve,reject)=>{play.finish=resolve;play.audio.onended=resolve;play.audio.onerror=()=>reject(new Error('语音无法播放'));play.audio.play().catch(reject);});URL.revokeObjectURL(play.url);play.url=null;}}
 catch(err){if(speechPlayback===play)toast('播报未完成：'+err.message);}finally{if(speechPlayback===play)stopSpeaking();}
}
function autoSpeakAnswer(text){if(speechConfig?.output_enabled&&speechConfig.auto_read)speakAnswer(text);}
function readLastAnswer(){const messages=$('#messages').querySelectorAll('.message.assistant');const last=messages[messages.length-1];if(last)speakAnswer(last.querySelectorAll('.answer-section').length?Array.from(last.querySelectorAll('.answer-section')).map(n=>n.innerText).join('\n'):last.innerText);}
// 来源下拉里"已连接平台模型"这一项的取值编码。抽成函数是为了能单独测试：
// 模型 id 可能含 "/"，所以模型名不从这里解析，而是挂在 option.dataset.model 上。
function speechSourceValue(connectionId,modelId){return `conn:${connectionId}:${modelId}`;}
// 音色不是一个跨平台通用的字段：MiniMax 的播报接口必须给音色 ID（留空会被平台拒绝），
// MiMo 可以留空用默认音色、也可填预置音色名，而其中的"音色设计"模型这一栏填的是描述文字。
// 这里只按所选平台说清"要填什么"，最终取值仍由后端按平台校验并给出明确报错。
function speechVoiceNote(url,value){
 if(value==='system')return '本机 Windows 播报：音色名称可留空，使用系统默认音色。';
 if(value==='legacy')return '沿用此前保存的自定义服务：音色按该服务自己的要求填写。';
 let host='';try{host=new URL(url).hostname.toLowerCase();}catch(error){}
 if(host==='api.xiaomimimo.com')return 'MiMo 播报：可留空（使用默认音色），也可填预置音色 mimo_default、冰糖、茉莉、苏打、白桦、Mia、Chloe、Milo、Dean；选到 mimo-v2.5-tts-voicedesign 时，这一栏填的是音色描述文字。';
 if(host==='api.minimax.io'||host==='api.minimaxi.com')return 'MiniMax 播报必须填写音色 ID（voice_id），留空会被平台拒绝；请从平台音色列表中复制，例如 Chinese (Mandarin)_Lyrical_Voice。';
 return '云端播报的音色名称由所选平台规定，请按平台文档填写；留空时程序按 default 发送。';
}
// 语音来源只有两类：本机 Windows，或「模型服务」里已连接的模型。
// 不再内置平台预设（硅基流动 / Groq / 本地兼容服务…）：那等于在模型服务之外又维护
// 一份平台、地址与密钥，与此前用途面板被指出的重复配置是同一个问题。
// 需要某个平台的语音模型时，在「模型服务」添加该平台并获取（或手动标注）ASR / TTS 模型，
// 再回到这里选择即可；本机 Windows 仍然单独保留，因为它不是平台模型。
async function buildSpeechSettings(){
 speechConfig=await api('/speech/settings');const cfg=speechConfig,panel=$('#speech-settings-panel');panel.replaceChildren(el('h3','','语音'));
 panel.append(el('p','speech-note','先选择在哪里识别或播报，再开启对应功能。本机 Windows 使用系统已安装的语音组件；云端识别会上传录音，云端播报会上传回答文本。不会自动下载模型。'));
 const form=el('form');form.id='speech-settings-form';const fields={};panel.append(form);
 // 记录每个用途的来源是否被用户改过、以及改成了哪个平台连接；保存时据此决定是否改写 connection_id。
 const touched={asr:false,tts:false},assignedId={asr:'',tts:''};
 let voiceNote=null;
 for(const kind of ['asr','tts']){
  const group=el('fieldset');group.append(el('legend','',kind==='asr'?'说话 → 文字':'回答 → 朗读'));form.append(group);
  const enabled=kind==='asr'?'input_enabled':'output_enabled';fields[enabled]=connectionField(group,kind==='asr'?'开启按住说话（默认 F8）':'开启回答朗读',cfg[enabled],'checkbox');
  // 一个下拉表达"在哪里识别/播报"：本机 Windows，或「模型服务」里已连接的语音模型。
  // 选中平台模型即指派该用途：地址与密钥都取自模型服务，这里不再重复填写。
  const platform=el('select');platform.setAttribute('aria-label',kind==='asr'?'语音识别来源':'语音播报来源');
  platform.append(new Option('本机 Windows（系统语音，无需 API Key）','system'));
  const connectedOption=value=>[...platform.options].some(o=>o.value===value);
  let connectedCount=0;
  for(const c of catalogConnections)for(const m of c.models||[])if((m.capabilities||[]).includes(kind)){
    const option=new Option('模型服务：'+c.name+' / '+m.id,speechSourceValue(c.id,m.id));
    // 模型 id 可能含 "/"（例如 BAAI/bge-m3），所以把 id 直接挂在选项上，
    // 而不是回头去解析显示文案——按分隔符拆字符串会在这种 id 上取错。
    option.dataset.model=m.id;option.dataset.url=c.base_url||'';platform.append(option);connectedCount++;
  }
  // 旧版直接填地址保存的自定义服务：不静默丢弃，列出来供用户继续使用或主动改选；
  // 但不再提供新的"手填地址"入口——那正是与模型服务重复的那套配置。
  const legacy=cfg[kind+'_mode']==='custom'&&!cfg[kind+'_connection_id']&&!!cfg[kind+'_url'];
  if(legacy)platform.append(new Option('此前保存的自定义服务：'+(cfg[kind+'_model']||cfg[kind+'_url']),'legacy'));
  const currentOption=cfg[kind+'_connection_id']
    ? speechSourceValue(cfg[kind+'_connection_id'],cfg[kind+'_model'])
    : cfg[kind+'_mode']==='system'?'system':legacy?'legacy':'system';
  // 连接已被删除或模型已改名时退回到本机，不留一个选不中的空值。
  platform.value=connectedOption(currentOption)?currentOption:(legacy?'legacy':'system');
  group.append(fieldLabel(kind==='asr'?'语音识别来源':'语音播报来源',platform));
  if(!connectedCount)group.append(el('p','hint','还没有可选的云端语音模型：可在「模型服务」添加平台，获取模型后把它的能力标注为 '+(kind==='asr'?'ASR 语音识别':'TTS 语音播报')+'。'));
  fields[kind+'_mode']={value:cfg[kind+'_mode']};
  fields[kind+'_model']={value:cfg[kind+'_model']||''};
  fields[kind+'_url']={value:cfg[kind+'_url']||''};
  let systemField;
  if(kind==='asr'){
   fields.language=connectionField(group,'Windows 识别语言',cfg.language,'text',[['zh-CN','普通话'],['en-US','英语']]);systemField=fields.language.parentElement;
   fields.auto_send=connectionField(group,'识别完成自动发送（有歧义或已有草稿时仍先确认）',cfg.auto_send,'checkbox');
   const correction=el('details');correction.append(el('summary','','识别纠错与隐私'));group.append(correction);
   fields.correction=connectionField(correction,'将识别文字交给会话模型检查错字',cfg.correction,'checkbox');
   fields.context=connectionField(correction,'同时允许发送当前页片段与最近对话用于纠错',cfg.context,'checkbox');
   correction.append(el('p','hint','纠错使用会话设置中的模型。关闭纠错后，不会为纠错发送文字；正式发送问题仍按会话设置处理。录音取消后不生成提问，已开始的云端请求可能仍有用量。'));
  }else{
   fields.voice=connectionField(group,'音色名称（本机留空使用系统默认）',cfg.voice);
   // 音色不该靠用户去翻平台文档：能查的按平台查（MiMo 是官方文档的固定清单，
   // MiniMax 有官方音色接口，含该账户自己克隆/生成的音色），查不到的如实说明。
   // 取回之后**在原本那个输入框的位置**换成选择框——用户明确要求不要出现两个作用重复的控件；
   // 选择框里保留一项「手动输入音色 ID」，需要自定义时能换回文字输入。
   const fetchVoices=el('button','secondary','获取该平台的音色'),voiceStatus=el('p','hint');fetchVoices.type='button';
   const voiceSelect=el('select','voice-picker');voiceSelect.hidden=true;
   voiceSelect.setAttribute('aria-label','音色');
   const MANUAL='__manual__';
   const restoreInput=()=>{if(voiceSelect.parentElement)voiceSelect.replaceWith(fields.voice);};
   voiceSelect.onchange=()=>{
    if(voiceSelect.value===MANUAL){restoreInput();fields.voice.focus();voiceStatus.textContent='已切回手动填写：可直接输入音色 ID。';return;}
    fields.voice.value=voiceSelect.value;
    voiceStatus.textContent='已选用音色 '+(voiceSelect.selectedOptions[0]?.textContent||voiceSelect.value)+'；保存后生效。';
   };
   fetchVoices.onclick=async()=>{
    const connectionId=selectedConnection();
    if(!connectionId){voiceStatus.textContent='这一项要先在「模型服务」里连接平台并获取语音模型，才能自动查询音色；本机 Windows 的音色请点下面的「检查 Windows 识别语言与音色」。';return;}
    fetchVoices.disabled=true;voiceStatus.textContent='正在向平台查询音色…';
    try{
     const model=selectedModel();
     const r=await api(`/speech/voices?connection_id=${encodeURIComponent(connectionId)}&model=${encodeURIComponent(model)}`);
     const rows=r.voices||[];
     if(!rows.length){voiceStatus.textContent=r.note||'这个平台没有返回可用音色，请直接填写音色名称。';return;}
     voiceSelect.replaceChildren(...rows.map(v=>new Option(v.label||v.id,v.id)),new Option('手动输入音色 ID…',MANUAL));
     if([...voiceSelect.options].some(o=>o.value===fields.voice.value))voiceSelect.value=fields.voice.value;
     fields.voice.replaceWith(voiceSelect);voiceSelect.hidden=false;
     voiceStatus.textContent=`已获取 ${rows.length} 个音色（来源：${r.source}）：就在这里选择即可。`+(r.note?' '+r.note:'');
    }catch(err){voiceStatus.textContent=err.message;}finally{fetchVoices.disabled=false;}
   };
   group.append(fetchVoices,voiceStatus);
   voiceNote=el('p','hint','');group.append(voiceNote);
   fields.auto_read=connectionField(group,'回答完成自动朗读',cfg.auto_read,'checkbox');
  }
  const selectedConnection=()=>platform.value.startsWith('conn:')?platform.value.split(':')[1]:'';
  const selectedModel=()=>platform.selectedOptions[0]?.dataset?.model||'';
  // 只有用户真的动过这个下拉才算"改过来源"：否则仅保存其它设置就会把已保存的
  // 平台连接静默清空（把来源改回"本机"时也应如实清空，所以必须区分两者）。
  const apply=(markTouched)=>{
    if(markTouched){touched[kind]=true;assignedId[kind]=selectedConnection();}
    const assigned=selectedConnection();
    fields[kind+'_mode'].value=platform.value==='system'?'system':'custom';
    if(assigned){
      // 后端要求自定义语音服务必须带地址与模型名；这里从平台连接回填，用户无需手填。
      fields[kind+'_model'].value=selectedModel();
      fields[kind+'_url'].value=platform.selectedOptions[0]?.dataset?.url||'';
    }else if(platform.value==='legacy'){
      fields[kind+'_model'].value=cfg[kind+'_model']||'';
      fields[kind+'_url'].value=cfg[kind+'_url']||'';
    }else{
      fields[kind+'_model'].value='';fields[kind+'_url'].value='';
    }
    if(systemField)systemField.hidden=platform.value!=='system';
    if(voiceNote)voiceNote.textContent=speechVoiceNote(platform.selectedOptions[0]?.dataset?.url||'',platform.value);
  };
  platform.onchange=()=>apply(true);apply(false);
 }
 const status=el('p','hint'),check=el('button','secondary','检查 Windows 识别语言与音色');check.type='button';check.onclick=async()=>{status.textContent='正在检查…';try{const r=await api('/speech/system');status.textContent='可识别语言：'+(r.recognizers.join('、')||'未安装')+'；可用音色：'+(r.voices.join('、')||'未安装');}catch(e){status.textContent=e.message;}};form.append(check,status);
 // 保存入口由设置页脚统一提供（按当前栏目指向对应表单），此面板不再放独立保存按钮。
 form.onsubmit=async e=>{e.preventDefault();settingsBusy(true);try{const body=Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,v.type==='checkbox'?v.checked:v.value]));
  // 后端 save() 会用请求体整体覆盖已存配置，因此未改动的用途必须回传原值，
  // 否则"顺手保存"就会把已绑定的平台连接清成空串。
  for(const kind of ['asr','tts'])body[kind+'_connection_id']=touched[kind]?assignedId[kind]:(cfg[kind+'_connection_id']||'');
  speechConfig=await post('/speech/settings',body,'PUT');applySpeechVisibility();if(!speechConfig.input_enabled)cancelVoice();if(!speechConfig.output_enabled)stopSpeaking();toast('语音设置已保存');}catch(err){toast(err.message);}finally{settingsBusy(false);}};
}

$('#question').addEventListener('input',()=>{if(!$('#question').value.trim()){voiceOriginal='';voiceFeedback.textContent='';}});
