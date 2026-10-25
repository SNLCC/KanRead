// 「网络」设置栏目：代理三态 + 连接自检（实际走向与耗时）。
//
// 产品口径（改这里之前先读一遍）：
// - 代理只影响**模型与解析服务**的请求（会话模型 / Embedding / Rerank / PDF 识别 /
//   翻译 / 语音）；Zotero、联网搜索与网页读取仍是各自的旧逻辑，不受这一页影响。
// - 本机、回环与私有网段地址一律直连，不走代理。
// - 代理凭据只保存在本机；不写日志，界面与接口只显示打码形式。
// - 「跟随系统」= 有环境变量代理就用它，没有才读 Windows 系统代理注册表。
//
// DOM 全部动态创建：不改 index.html；本文件在 boot 清单里排在 settings 之后，
// 因此可以直接用 el()/api()/post()/toast()/fieldLabel() 与 switchModelTab()。
(function(){
  const panel=el('section','model-profile network-panel');
  panel.id='network-settings-panel';panel.hidden=true;

  const heading=el('div','profile-heading'),headingCopy=el('div');
  headingCopy.append(el('h3','','网络'),
    el('p','','代理影响所有需要联网的外部服务请求（模型、翻译、语音、解析、Zotero 云端与联网搜索）；公开网页读取为防 SSRF 故意直连；本机、回环与私有网段地址自动直连。'
      +'代理凭据只保存在本机：不写日志、界面只显示打码形式。'));
  heading.append(headingCopy);panel.append(heading);

  // 检测结果只展示后端返回的事实（系统 / 环境变量 / 当前实际走向），前端不猜。
  const detected=el('div','network-detected');panel.append(detected);

  const form=el('form','network-form');
  const modeSelect=el('select');modeSelect.dataset.field='mode';
  const modeHint=el('p','hint','');
  form.append(fieldLabel('代理模式',modeSelect),modeHint);

  const manualBox=el('div','network-manual');
  const proxyInput=el('input');proxyInput.type='text';proxyInput.dataset.field='proxy_url';
  proxyInput.placeholder='127.0.0.1:7890';
  proxyInput.autocomplete='off';proxyInput.spellcheck=false;
  const proxyNote=el('p','hint','支持账号密码（user:password@host:port），但界面与接口只显示打码形式，'
    +'也不会写进日志。留空保存不会覆盖已保存的密码。');
  const noProxyInput=el('input');noProxyInput.type='text';noProxyInput.dataset.field='no_proxy';
  noProxyInput.placeholder='localhost,127.0.0.1,.internal';
  noProxyInput.autocomplete='off';noProxyInput.spellcheck=false;
  manualBox.append(fieldLabel('手动代理地址',proxyInput),proxyNote,
    fieldLabel('不走代理的地址（逗号分隔，可空）',noProxyInput),
    el('p','hint','这些目标会按直连处理；本机与局域网地址不受此列表影响，始终直连。'));
  form.append(manualBox);

  const actions=el('div','network-actions');
  const saveButton=el('button','primary','保存网络设置');saveButton.type='button';
  const reloadButton=el('button','secondary','重新检测');reloadButton.type='button';
  actions.append(saveButton,reloadButton);
  const formMessage=el('p','network-status','');formMessage.setAttribute('role','status');formMessage.setAttribute('aria-live','polite');
  form.append(actions,formMessage);
  panel.append(form);

  // 连接自检：默认测会话模型的服务地址，也可以填任意 http(s) 地址。
  const probeBox=el('div','network-probe');
  probeBox.append(el('h4','','连接自检'));
  const probeTarget=el('input');probeTarget.type='text';probeTarget.dataset.field='probe_target';
  probeTarget.placeholder='留空＝使用会话模型的服务地址';probeTarget.autocomplete='off';probeTarget.spellcheck=false;
  const probeActions=el('div','network-actions');
  const probeButton=el('button','secondary','测试连接');probeButton.type='button';
  probeActions.append(probeButton);
  const probeResult=el('div','network-probe-result','');
  probeResult.setAttribute('role','status');probeResult.setAttribute('aria-live','polite');
  probeBox.append(fieldLabel('测试目标（可留空）',probeTarget),probeActions,
    el('p','hint','自检只发一次 GET，不携带 API Key，也不发送任何用户数据；'
      +'401/403/404 都说明网络路径通了。'),
    probeResult);
  panel.append(probeBox);

  panel.append(el('p','hint','影响范围：会话模型、Embedding、Rerank、PDF 识别、翻译与语音调用会按本页设置走；'
    +'Zotero、联网搜索与网页读取仍按各自的旧逻辑只跟随环境变量，不受本页三态设置影响。'
    +'非 Windows 系统只能读取环境变量代理；SOCKS 代理与 PAC 脚本本版不支持。'));

  $('.settings-panels').append(panel);
  const nav=el('button');nav.type='button';nav.dataset.settingTab='network';
  nav.append(el('span','','网络'),el('small','','代理 · 连接自检'));
  $('.settings-nav').append(nav);
  nav.onclick=()=>switchModelTab('network');

  let busy=false,loaded=null;
  const setMessage=(text,error)=>{formMessage.textContent=text;formMessage.classList.toggle('error',!!error);};
  const setBusy=value=>{
    busy=value;
    for(const node of [saveButton,reloadButton,probeButton])node.disabled=value;
    panel.setAttribute('aria-busy',value?'true':'false');
  };
  const setProbeResult=(text,error)=>{
    probeResult.textContent=text;
    probeResult.classList.toggle('error',!!error);
  };
  function syncMode(){
    const mode=modeSelect.value;
    manualBox.hidden=mode!=='manual';
    const selected=(loaded&&loaded.modes||[]).find(item=>item.id===mode);
    modeHint.textContent=selected?selected.hint:'';
  }
  modeSelect.onchange=syncMode;

  function factRow(label,value){
    const row=el('div','network-fact'),name=el('span','network-fact-label',label),text=el('span','network-fact-value');
    text.textContent=value;
    row.append(name,text);detected.append(row);
  }
  function proxyLine(info){
    const parts=[];
    if(info.http)parts.push('HTTP '+info.http);
    if(info.https)parts.push('HTTPS '+info.https);
    if(info.all)parts.push('ALL '+info.all);
    return parts.join('；');
  }
  function renderDetected(data){
    detected.replaceChildren();
    const info=data.detected||{};
    factRow('检测结论',info.summary||'未知（后端没有返回检测结果）。');
    if(info.system&&info.system.enabled){
      const systemLine=proxyLine(info.system)||'已启用（未提供地址）';
      factRow('Windows 系统代理',systemLine+(info.system.no_proxy?'；绕过：'+info.system.no_proxy:''));
    }else{
      factRow('Windows 系统代理',(info.platform_label==='Windows')
        ?'未启用（或注册表里没有可用的 HTTP/HTTPS 代理）'
        :'当前系统不是 Windows：无法读取系统代理注册表，只能读环境变量。');
    }
    const env=info.env||{};
    factRow('环境变量代理',env.detected?(proxyLine(env)||'已设置'):'未检测到');
    const effective=data.effective||{};
    if(effective.available){
      factRow('当前实际走向',effective.route+' · 目标 '+effective.target
        +(effective.proxy?' · 代理 '+effective.proxy:'')
        +(effective.bypassed?'（已绕过代理）':'')+'。'+(effective.reason||''));
    }else{
      factRow('当前实际走向',effective.reason||'会话模型还没有服务地址。');
    }
  }
  function render(data){
    loaded=data;
    modeSelect.replaceChildren();
    for(const item of data.modes||[]){const option=new Option(item.label,item.id);modeSelect.append(option);}
    if(!modeSelect.options.length){
      modeSelect.append(new Option('跟随系统（自动）','auto'),new Option('手动指定代理','manual'),new Option('直连（不使用代理）','direct'));
    }
    modeSelect.value=data.mode||'auto';
    proxyInput.value=data.proxy_url||'';
    noProxyInput.value=data.no_proxy||'';
    if(data.has_credentials){
      proxyNote.textContent='已保存的代理地址包含凭据：界面只显示打码形式，直接点保存不会覆盖原密码；'
        +'要更换密码请整条重新填写。凭据不写日志。';
    }else{
      proxyNote.textContent='支持账号密码（user:password@host:port），但界面与接口只显示打码形式，'
        +'也不会写进日志。留空保存不会覆盖已保存的密码。';
    }
    if(!probeTarget.value&&data.target)probeTarget.value=data.target;
    renderDetected(data);
    syncMode();
  }
  async function refresh(){
    if(busy)return;
    setBusy(true);setMessage('正在读取网络设置…');
    try{
      const data=await api('/network/settings');
      render(data);
      setMessage('已读取当前网络设置与实际走向。');
    }catch(error){setMessage('读取网络设置失败：'+error.message,true);}
    finally{setBusy(false);}
  }
  saveButton.onclick=async()=>{
    if(busy)return;
    const payload={mode:modeSelect.value,proxy_url:proxyInput.value.trim(),no_proxy:noProxyInput.value.trim()};
    if(payload.mode==='manual'&&!payload.proxy_url){
      setMessage('「手动指定」需要填写代理地址，例如 127.0.0.1:7890。',true);proxyInput.focus();return;
    }
    setBusy(true);setMessage('正在保存网络设置…');
    try{
      const data=await post('/network/settings',payload,'PUT');
      render(data);
      setMessage('网络设置已保存，随后的模型与解析调用按新设置生效。');
      toast('网络设置已保存');
    }catch(error){setMessage('保存失败：'+error.message,true);}
    finally{setBusy(false);}
  };
  reloadButton.onclick=()=>refresh();
  probeButton.onclick=async()=>{
    if(busy)return;
    const target=probeTarget.value.trim();
    setBusy(true);setProbeResult('正在测试连接…（可能会用到刚保存的代理设置；未保存的修改不会生效）');
    try{
      const result=await post('/network/probe',{target_url:target});
      const routeText='路线：'+result.route+(result.proxy?'（'+result.proxy+'）':'')
        +(result.bypassed?'（已绕过代理）':'');
      if(result.reachable){
        setProbeResult('可达。'+(result.message||('HTTP '+result.status_code))
          +routeText+'；耗时 '+result.latency_ms+' 毫秒。',false);
      }else{
        setProbeResult('不可达。'+routeText
          +'；耗时 '+result.latency_ms+' 毫秒。'+(result.error||''),true);
      }
    }catch(error){setProbeResult('测试连接失败：'+error.message,true);}
    finally{setBusy(false);}
  };

  // 设置页原有的切换函数不认识这个栏目：包一层，切到本页时显示自己、
  // 隐藏模型表单与页脚保存按钮（本页有自己的保存按钮，避免两处保存同一件事）。
  function patchNetworkSwitcher(){
    if(typeof switchModelTab!=='function')return;
    const base=switchModelTab;
    window.switchModelTab=function(kind){
      base(kind);
      const mine=kind==='network';
      panel.hidden=!mine;
      if(mine){
        const modelForm=$('#model-settings-form');if(modelForm)modelForm.hidden=true;
        const feedback=$('#settings-feedback');
        if(feedback){feedback.textContent='本页保存网络与代理设置；保存与测试连接都在本页完成。';feedback.classList.remove('error');}
        const footerSave=$('#settings-save');if(footerSave)footerSave.hidden=true;
        refresh();
      }
    };
  }
  patchNetworkSwitcher();
})();
