// 「数据与更新」维护面板：日志、备份、诊断包与一键更新入口。
//
// 为什么单独成一个功能文件：这些能力与阅读无关，却都需要"看见真实数字"——
// 数据目录占多少、日志多大、上次备份是什么时候、新版怎么装。放进设置对话框的
// 独立栏目（而不是塞进某个已有面板），用户排查磁盘与更新问题时只用看这一页。
// DOM 全部动态创建：不改 index.html；本文件在 boot 清单里排在 settings 之后。
(function(){
  const panel=el('section','model-profile maintenance-panel');panel.id='maintenance-settings-panel';panel.hidden=true;
  const heading=el('div','profile-heading'),headingCopy=el('div');
  headingCopy.append(el('h3','','数据与更新'),
    el('p','','查看数据目录与占用、导出诊断包、立即备份，或按说明一键更新。更新永远不替换 data/ 与 .venv/，并且会先自动备份数据。'));
  heading.append(headingCopy);panel.append(heading);

  // 事实区只展示后端返回的数字，不在前端猜：对不上时用户能直接看到差异。
  const facts=el('div','maintenance-facts');panel.append(facts);
  const live=el('p','maintenance-status','');
  live.setAttribute('role','status');live.setAttribute('aria-live','polite');panel.append(live);
  const actions=el('div','maintenance-actions');
  const exportButton=el('button','secondary','导出诊断包');exportButton.type='button';
  const backupButton=el('button','secondary','立即备份数据');backupButton.type='button';
  const openButton=el('button','secondary','打开数据目录');openButton.type='button';
  actions.append(exportButton,backupButton,openButton);panel.append(actions);

  const logNote=el('p','hint','日志会按 2MB 轮转、最多保留 5 份历史（约 12MB 封顶），不需要手动清理。'
    +'「导出诊断包」会把版本、依赖、数据库表行数、最近日志与失败摘要打成 zip；包内不含文献正文、页面文本、批注内容、API Key 或完整绝对路径。');
  panel.append(logNote);
  const packages=el('div','maintenance-list');panel.append(packages);

  const updateBox=el('div','maintenance-update');panel.append(updateBox);
  // 状态接口失败时也要能看到更新说明：先放一条不依赖后端的通用指引，
  // 读到状态后再换成带真实路径的一键复制命令。
  updateBox.append(el('h4','','更新'),
    el('p','','更新只替换程序文件，不会动数据目录 data/ 与运行环境 .venv/；替换前会自动备份 data/，失败会回滚已替换的文件。'),
    el('p','hint','在程序目录打开 PowerShell 运行：scripts/update.ps1 -Zip "<新版本压缩包路径>"（建议先加 -DryRun 看计划）。'));

  $('.settings-panels').append(panel);
  const nav=el('button');nav.type='button';nav.dataset.settingTab='maintenance';
  nav.append(el('span','','数据与更新'),el('small','','日志 · 备份 · 诊断 · 更新'));
  $('.settings-nav').append(nav);

  let busy=false,lastStatus=null,updateCommand='';
  const setMessage=(text,error)=>{live.textContent=text;live.classList.toggle('error',!!error);};
  const formatBytes=value=>{
    let number=Number(value)||0,unit=0;
    const units=['B','KB','MB','GB'];
    while(number>=1024&&unit<units.length-1){number/=1024;unit+=1;}
    return (unit===0?String(Math.round(number)):number.toFixed(1))+' '+units[unit];
  };
  const setBusy=value=>{
    busy=value;
    for(const button of [exportButton,backupButton,openButton])button.disabled=value;
    panel.setAttribute('aria-busy',value?'true':'false');
  };
  async function copyText(text,label){
    try{
      if(navigator.clipboard&&navigator.clipboard.writeText){await navigator.clipboard.writeText(text);}
      else{
        // 非安全上下文/旧浏览器的退路：临时 textarea + execCommand。
        const area=el('textarea');area.value=text;area.style.position='fixed';area.style.opacity='0';
        document.body.append(area);area.select();
        if(!document.execCommand('copy'))throw new Error('浏览器拒绝复制');
        area.remove();
      }
      toast(label+'已复制到剪贴板');
    }catch(error){
      toast('复制失败，请手动选择文本复制：'+error.message);
    }
  }
  function fact(label,valueNode){
    const row=el('div','maintenance-fact'),name=el('span','maintenance-fact-label',label),value=el('span','maintenance-fact-value');
    if(typeof valueNode==='string')value.textContent=valueNode;else value.append(valueNode);
    row.append(name,value);facts.append(row);
  }
  function renderStatus(status){
    facts.replaceChildren();
    const version=`接口 v${status.api_version} · 程序 ${status.git_describe||'未知'}`;
    fact('当前版本',version);
    const dataPath=el('code','maintenance-path',status.data_dir);fact('数据目录',dataPath);
    const usage=`${formatBytes(status.data_usage.bytes)}（${status.data_usage.files} 个文件）`;
    fact('数据目录占用',usage);
    fact('数据库大小',formatBytes(status.database.size_bytes));
    const logInfo=`${formatBytes(status.log.total_size)}；单文件上限 ${formatBytes(status.log.rotation.max_bytes)}，保留 ${status.log.rotation.backup_count} 份历史`;
    fact('日志',logInfo);
    const copies=el('span','maintenance-inline');
    const copyPath=el('button','link-button','复制路径');copyPath.type='button';
    copyPath.onclick=()=>copyText(status.data_dir,'数据目录路径');
    copies.append(copyPath);
    if(status.backups&&status.backups.length){
      copies.append(el('span','hint','上次备份：'+status.backups[0].name+'（'+formatBytes(status.backups[0].size_bytes)+'）'));
    }else copies.append(el('span','hint','还没有备份过：建议在导入重要文献后备份一次。'));
    fact('备份',copies);
    lastStatus=status;
    buildUpdate(status);
  }
  function buildUpdate(status){
    const base=String(status.app_dir||'').replace(/[\\/]+$/,'');
    updateCommand=`powershell -NoProfile -ExecutionPolicy Bypass -File "${base}/scripts/update.ps1" -Zip "<新版本压缩包路径>"`;
    updateBox.replaceChildren(el('h4','','更新'));
    updateBox.append(el('p','','更新只替换程序文件，不会动数据目录 data/ 与运行环境 .venv/；脚本在替换前会自动备份 data/，'
      +'依赖文件 requirements.lock.txt 有变化时才重装依赖，任何一步失败都会回滚已替换的文件。'));
    updateBox.append(el('p','','步骤：1）关闭勘读窗口；2）把新版本压缩包下载到任意位置（不要解压覆盖到程序目录）；3）在 PowerShell 里运行下面的命令——'
      +'建议先加 -DryRun 看一遍计划，确认无误再去掉它。更新完成后会自动重新启动勘读。'));
    const commandRow=el('div','maintenance-command-row');
    const command=el('code','maintenance-command',updateCommand);command.tabIndex=0;command.title='点击选中';
    command.onclick=()=>{const range=document.createRange();range.selectNodeContents(command);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);};
    const copyButton=el('button','secondary','复制更新命令');copyButton.type='button';
    copyButton.onclick=()=>copyText(updateCommand,'更新命令');
    commandRow.append(command,copyButton);updateBox.append(commandRow);
    updateBox.append(el('p','hint','源码仓库版本也可以用 -Git 模式更新（git fetch + reset --hard，data/ 与 .venv/ 不在版本控制里，因此不会被覆盖）。'));
  }
  async function refreshStatus(){
    try{renderStatus(await api('/maintenance/status'));}
    catch(error){setMessage('读取维护状态失败：'+error.message,true);}
  }
  async function refreshPackages(){
    try{
      const result=await api('/maintenance/diagnostics');
      packages.replaceChildren(el('h4','','已导出的诊断包'));
      if(!result.packages||!result.packages.length){packages.append(el('p','hint','还没有导出过诊断包。'));return;}
      const list=el('ul','maintenance-package-list');
      for(const item of result.packages.slice(0,5)){
        const row=el('li');
        row.append(el('span','',item.name+'（'+formatBytes(item.size_bytes)+'）'),el('span','hint',' '+item.created.replace('T',' ')));
        list.append(row);
      }
      packages.append(list,el('p','hint','诊断包保存在：'+result.directory));
    }catch(error){setMessage('读取诊断包列表失败：'+error.message,true);}
  }
  async function refreshMaintenance(){
    setMessage('正在读取维护状态…');
    await refreshStatus();
    await refreshPackages();
  }
  exportButton.onclick=async()=>{
    if(busy)return;
    setBusy(true);setMessage('正在生成诊断包…（只收集运行环境事实与计数，不读取文献内容）');
    try{
      const result=await post('/maintenance/diagnostics',{});
      setMessage(`诊断包已生成：${result.name}（${formatBytes(result.size_bytes)}，${result.entries.length} 个文件）`);
      toast('诊断包已导出');
      await refreshPackages();
    }catch(error){setMessage('导出诊断包失败：'+error.message,true);}
    finally{setBusy(false);}
  };
  backupButton.onclick=async()=>{
    if(busy)return;
    setBusy(true);setMessage('正在备份数据目录…（数据库使用 SQLite 一致性快照，运行中也可以备份）');
    try{
      const result=await post('/maintenance/backup',{});
      setMessage(`备份完成：${result.name}（${formatBytes(result.size_bytes)}，${result.entries_count} 个条目）。旧备份不会被自动删除。`);
      toast('数据备份完成');
      await refreshStatus();
    }catch(error){setMessage('备份失败：'+error.message,true);}
    finally{setBusy(false);}
  };
  openButton.onclick=async()=>{
    if(busy)return;
    setBusy(true);
    try{
      const result=await post('/maintenance/open-data-dir',{});
      if(result.opened)setMessage('已在本机文件管理器打开数据目录：'+result.path);
      else setMessage(result.message+'：'+result.path,true);
    }catch(error){setMessage('打开数据目录失败：'+error.message,true);}
    finally{setBusy(false);}
  };
  nav.onclick=()=>switchModelTab('maintenance');

  // 设置页原有的切换函数不认识这个栏目：包一层，切到本页时显示自己、
  // 隐藏模型表单与页脚保存按钮（本页没有任何需要保存的设置）。
  function patchMaintenanceSwitcher(){
    if(typeof switchModelTab!=='function')return;
    const base=switchModelTab;
    window.switchModelTab=function(kind){
      base(kind);
      const mine=kind==='maintenance';
      panel.hidden=!mine;
      if(mine){
        const form=$('#model-settings-form');if(form)form.hidden=true;
        const feedback=$('#settings-feedback');
        if(feedback){feedback.textContent='本页只做维护：日志、备份、诊断与更新，没有需要保存的设置。';feedback.classList.remove('error');}
        refreshMaintenance();
      }
      const button=$('#settings-save');if(button&&mine)button.hidden=true;
    };
  }
  patchMaintenanceSwitcher();
})();
