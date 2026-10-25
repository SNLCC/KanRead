// 文档导航层（导航索引试点，默认关闭）：阅读工具栏入口 + 状态/开关对话框。
//
// 产品口径（改这里之前先读一遍）：
// - 导航索引**只用于**建议"该读哪几页"，不参与回答内容、证据与核验；
// - 本版标签由本机规则（章节标题 / 页面首行）生成：不调用模型、不花 token、不编造；
// - 默认关闭；文件或无关区域规则一变就过期，界面必须如实说"已过期"并回退；
// - 生成失败如实显示原因；用户可随时删除，删除后规划回到改造前的路径。
//
// 纯函数单独放在下面一段里：tests/doc_nav.test.cjs 用 node:vm 抽取这一段做断言，
// 因此这一段不得引用 DOM 或全局状态。

// ===== 纯函数（tests/doc_nav.test.cjs 抽取此段） =====
function navStateText(status){
  if(!status)return '读取导航索引状态失败。';
  const state=status.state||'missing';
  const count=Number(status.node_count)||0;
  const enabled=!!status.enabled;
  if(state==='building'){
    return '正在本机生成导航索引：'+(Number(status.done)||0)+'/'+(Number(status.total)||0)
      +' 页。标签来自章节标题与页面首行，不调用模型。';
  }
  if(state==='failed'){
    return '生成失败：'+(status.error||'未知原因')+'。规划会回退为逐页读取原文。';
  }
  if(state==='stale'){
    return '已过期：'+(status.stale_reason||'文献或无关区域规则已变化')
      +'。请重建后再启用；规划目前仍按逐页读取。';
  }
  if(state==='ready'){
    const usage=enabled
      ?'已启用：规划时会把下面的页码当作"该读哪几页"的参考。'
      :'已生成但未启用（默认关闭）。';
    return '已生成 '+count+' 条导航标签。'+usage;
  }
  if(enabled)return '已启用，但还没有生成导航索引：请先点「生成索引」。';
  return '未生成（默认关闭）。生成后仍需启用，才会影响阅读范围建议。';
}

// 标签来源只用于展示/审计：章节标题比"页面首行"更可靠，用户需要知道自己在看哪一种。
function navKindText(kind){
  return kind==='section'?'章节':'页面首行';
}

// 节点预览是否被截断：生成很多条时只显示前 N 条，界面要说清"不是丢了"。
function navNodesNote(status,nodes){
  const total=Number(status&&status.node_count)||0;
  const shown=Array.isArray(nodes)?nodes.length:0;
  if(!shown)return '';
  return total>shown?('预览前 '+shown+' 条，共 '+total+' 条。'):('共 '+shown+' 条。');
}
// ===== 纯函数结束 =====

(function(){
  let dialog=null,timer=null,polls=0,docId='',busy=false,lastData=null;

  const button=el('button','','▦ 导航索引（试点）');
  button.id='doc-navigation';
  button.title='为这篇文献生成"页 → 标签"导航索引：只用于建议该读哪几页，不参与回答内容与证据；本版不调用模型；默认关闭';
  // 与「框选识别」「屏蔽无关区域」并排：放进工具栏最后一个分组（不要用 > div:last-child，
  // 其它功能会往 #toolbar 末尾直接追加按钮）。应用启动时工具栏是隐藏的，但 DOM 已存在。
  const groups=$('#toolbar')?Array.from($('#toolbar').children).filter(node=>node.tagName==='DIV'):[];
  const group=groups[groups.length-1]||$('#toolbar');
  if(group)group.append(button);

  function ensureDialog(){
    if(dialog)return dialog;
    dialog=el('dialog','doc-nav-dialog');
    dialog.id='doc-nav-dialog';
    dialog.setAttribute('aria-labelledby','doc-nav-title');
    const title=el('h2','','导航索引（试点）');
    title.id='doc-nav-title';
    const hint=el('p','doc-nav-hint',
      '导航索引只用于建议「该读哪几页」，不参与回答内容与证据，也不参与引用核验。'
      +'本版标签由本机规则（章节标题 / 页面首行）生成，不调用模型、不花 token、不伪造摘要；'
      +'文件或「无关区域」规则变化后会自动过期，可随时删除。');
    const statusLine=el('p','doc-nav-status');
    statusLine.id='doc-nav-status';
    statusLine.setAttribute('role','status');
    statusLine.setAttribute('aria-live','polite');
    const toggleLabel=el('label','doc-nav-toggle');
    const toggle=document.createElement('input');
    toggle.type='checkbox';toggle.id='doc-nav-enabled';
    toggleLabel.append(toggle,el('span','','启用导航索引（默认关闭；保存后下次提问生效）'));
    const note=el('p','doc-nav-note',
      '说明：启用后规划器只会多一个"还应该看哪几页"的参考，不会减少原有的大纲、检索或整篇读取范围；'
      +'过期或生成失败时自动回退逐页读取。');
    const list=el('div','doc-nav-nodes');
    list.id='doc-nav-nodes';
    const actions=el('div','doc-nav-actions');
    const generate=el('button','primary','生成索引');
    generate.type='button';generate.id='doc-nav-generate';
    const rebuild=el('button','','重建');
    rebuild.type='button';rebuild.id='doc-nav-rebuild';
    const remove=el('button','danger','删除');
    remove.type='button';remove.id='doc-nav-remove';
    const close=el('button','','关闭');
    close.type='button';close.id='doc-nav-close';
    actions.append(generate,rebuild,remove,close);
    dialog.append(title,hint,statusLine,toggleLabel,note,list,actions);
    document.body.append(dialog);

    close.onclick=()=>dialog.close();
    dialog.addEventListener('close',stopPolling);

    toggle.onchange=async()=>{
      const value=!!toggle.checked;
      toggle.disabled=true;
      try{
        render(await api('/documents/'+encodeURIComponent(docId)+'/navigation/enabled',
          {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:value})}));
        toast(value?'导航索引已启用：重新提问时生效':'导航索引已关闭：规划回到原有路径');
      }catch(error){
        toggle.checked=!value;
        statusLine.textContent='保存开关失败：'+error.message;
      }finally{
        toggle.disabled=false;
      }
    };

    generate.onclick=()=>submit({});
    rebuild.onclick=()=>submit({rebuild:true});
    remove.onclick=async()=>{
      if(!confirm('删除这篇文献的导航索引？删除后规划立即回到逐页读取，可随时重新生成。'))return;
      await submit(null,'DELETE');
    };
    return dialog;
  }

  function setBusy(value){
    busy=value;
    if(!dialog)return;
    for(const selector of ('#doc-nav-generate,#doc-nav-rebuild,#doc-nav-remove,#doc-nav-enabled').split(',')){
      const node=dialog.querySelector(selector);
      if(node)node.disabled=value;
    }
  }

  function render(data){
    if(!dialog)return;
    lastData=data||{};
    const statusLine=dialog.querySelector('#doc-nav-status');
    const toggle=dialog.querySelector('#doc-nav-enabled');
    const list=dialog.querySelector('#doc-nav-nodes');
    statusLine.textContent=navStateText(lastData);
    toggle.checked=!!lastData.enabled;
    toggle.disabled=!!busy||lastData.state==='building';
    dialog.querySelector('#doc-nav-generate').disabled=!!busy||lastData.state==='building';
    dialog.querySelector('#doc-nav-rebuild').disabled=!!busy||lastData.state==='building';
    dialog.querySelector('#doc-nav-remove').disabled=!!busy||(!lastData.built_at&&!lastData.source_hash);
    list.replaceChildren();
    const nodes=Array.isArray(lastData.nodes)?lastData.nodes:[];
    if(!nodes.length){
      list.append(el('p','doc-nav-empty',
        '还没有导航节点。点「生成索引」用本机规则（章节标题 / 页面首行）生成，不调用模型。'));
      return;
    }
    list.append(el('p','doc-nav-list-head','节点预览（'+navNodesNote(lastData,nodes)+'）'));
    for(const node of nodes){
      const row=el('div','doc-nav-node');
      row.append(el('span','doc-nav-node-page','第 '+(Number(node.page)||'?')+' 页'),
        el('span','doc-nav-node-label',String(node.label||'')),
        el('span','doc-nav-node-kind',navKindText(node.kind)));
      list.append(row);
    }
    if(lastData.nodes_truncated)list.append(el('p','doc-nav-note','预览已截断，只显示前 '+nodes.length+' 条。'));
  }

  function stopPolling(){
    if(timer){clearTimeout(timer);timer=null;}
    polls=0;
  }

  function schedulePoll(){
    stopPolling();
    const tick=async()=>{
      if(!dialog||!dialog.open)return;
      try{
        const data=await api('/documents/'+encodeURIComponent(docId)+'/navigation');
        render(data);
        if(data.state==='building'){
          polls+=1;
          if(polls<60){
            timer=setTimeout(tick,2000);
          }else{
            dialog.querySelector('#doc-nav-status').textContent=
              navStateText(data)+' 已等待较久，可关闭对话框稍后再打开查看。';
          }
          return;
        }
        toast(data.state==='ready'?'导航索引已生成：可启用后重新提问'
          :'导航索引生成结束：'+(data.state==='failed'?'失败':'未完成'));
      }catch(error){
        dialog.querySelector('#doc-nav-status').textContent='读取生成进度失败：'+error.message;
      }
    };
    timer=setTimeout(tick,1200);
  }

  async function submit(payload,method='POST'){
    if(!docId||busy)return;
    setBusy(true);
    try{
      const path='/documents/'+encodeURIComponent(docId)+'/navigation';
      const data=method==='DELETE'?await api(path,{method:'DELETE'}):await post(path,payload);
      render(data);
      if(data.state==='building')schedulePoll();
      else if(data.state==='failed')toast('导航索引生成失败：'+(data.error||'未知原因')+'；规划会回退逐页读取');
      else if(method==='DELETE')toast('导航索引已删除：规划回到原有路径');
      else toast('导航索引已生成'+(data.enabled?'并已启用':'（默认未启用）')+'；重新提问时生效');
    }catch(error){
      const statusLine=dialog.querySelector('#doc-nav-status');
      if(statusLine)statusLine.textContent='操作失败：'+error.message;
    }finally{
      setBusy(false);
      if(lastData)render(lastData);
    }
  }

  async function load(){
    try{
      const data=await api('/documents/'+encodeURIComponent(docId)+'/navigation');
      render(data);
      if(data.state==='building')schedulePoll();
    }catch(error){
      const statusLine=dialog.querySelector('#doc-nav-status');
      if(statusLine)statusLine.textContent='读取导航索引失败：'+error.message;
    }
  }

  button.onclick=async()=>{
    if(!state.doc){toast('请先打开一份文献');return;}
    docId=state.doc.id;
    ensureDialog();
    stopPolling();
    if(!dialog.open)dialog.showModal();
    await load();
  };
})();
