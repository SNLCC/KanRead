// 「无关区域」屏蔽（页眉、页脚、页码、水印、侧栏等排版噪声）。
//
// 产品口径（改这里之前先读一遍）：
// - 只是**读取侧的过滤**：规则存在本机，PDF 字节从不被改写；删掉规则就恢复原样。
// - 规则分两种：本页（某一页独有的水印/侧栏）与所有页（每页都有的页眉页脚）。
// - 目录界面的判断都必须如实：索引在后台重建期间，检索结果可能仍包含被屏蔽文字，
//   界面要说"正在重建索引"，不能假装已经生效。
//
// 纯函数单独放在下面一段里：tests/page_masks.test.cjs 用 node:vm 抽取这一段做断言，
// 因此这一段不得引用 DOM 或全局状态。

// ===== 纯函数（tests/page_masks.test.cjs 抽取此段） =====
function maskClamp(value){
  const number=Number(value);
  if(!Number.isFinite(number))return 0;
  return Math.min(1,Math.max(0,number));
}

// 两个拖拽点 → 归一化矩形（0..1，左上原点）。无论从哪个方向拖，结果都按左上/右下归位。
function maskNormalizeRect(start,end){
  const x0=maskClamp(Math.min(start.x,end.x)),x1=maskClamp(Math.max(start.x,end.x));
  const y0=maskClamp(Math.min(start.y,end.y)),y1=maskClamp(Math.max(start.y,end.y));
  return {x0,y0,x1,y1};
}

// 太小的框多半是误触：屏蔽一条 2px 的线既不解决问题，又会让规则列表变脏。
function maskValidRect(rect,minimum=0.005){
  return !!rect&&rect.x1-rect.x0>=minimum&&rect.y1-rect.y0>=minimum;
}

function maskRectsOverlap(a,b){
  return !!a&&!!b&&a.x0<b.x1&&a.x1>b.x0&&a.y0<b.y1&&a.y1>b.y0;
}

function maskPageLabel(mask){
  return !mask||mask.page===0?'所有页':('第 '+mask.page+' 页');
}

// 界面上的区域描述。百分比保留整数即可：它说明"画在版面的哪一块"，不承担精确回填。
function maskRegionText(mask){
  if(!mask)return '';
  const percent=value=>Math.round(maskClamp(value)*100);
  return percent(mask.x0)+'%–'+percent(mask.x1)+'% × '+percent(mask.y0)+'%–'+percent(mask.y1)+'%';
}

// 同一文献同一条规则可能因为并发/重复请求出现两次；展示前去重，但保留原顺序与 id。
function maskDedupe(masks){
  const seen=new Set(),result=[];
  for(const mask of masks||[]){
    if(!mask)continue;
    const key=[Number(mask.page)||0,Number(mask.x0).toFixed(4),Number(mask.y0).toFixed(4),
      Number(mask.x1).toFixed(4),Number(mask.y1).toFixed(4)].join('|');
    if(seen.has(key))continue;
    seen.add(key);result.push(mask);
  }
  return result;
}

// 索引重建状态 → 一句用户能看懂、且不会骗人的话。
function maskIndexStatusText(status){
  if(!status||!status.state||status.state==='idle')return '检索索引：没有正在进行的重建。';
  if(status.state==='running'){
    return '正在重建检索索引：'+(status.done||0)+'/'+(status.total||0)
      +' 页。完成前检索结果可能仍包含被屏蔽文字；阅读与页面文字层已经按规则生效。';
  }
  if(status.state==='failed'){
    return '检索索引重建失败：'+(status.error||'未知原因')
      +'。阅读已按规则生效，但检索可能仍包含被屏蔽文字；可删除后重新添加规则再试。';
  }
  return '检索索引已更新：被屏蔽文字不再出现在检索结果中。';
}
// ===== 纯函数结束 =====

(function(){
  let maskMode=false,maskStart=null,maskStatusTimer=null;

  // ---- 基础 DOM：按钮 / 遮罩层 / live 区域 ----

  const maskButton=el('button','','▩ 屏蔽无关区域');
  maskButton.id='page-masks';
  maskButton.title='在页面上拖出页眉、页脚、页码、水印等排版噪声，把它们从阅读原文、检索、引用定位与页面文字层中屏蔽；PDF 原文不会被改写，可随时删除恢复';
  const managerButton=el('button','','▤ 屏蔽规则');
  managerButton.id='mask-manager';
  managerButton.title='查看或删除这篇文献已设置的无关区域规则';
  // 放进工具栏的第二个分组（页码缩放组之后的工具组，与「框选识别」「翻译整页」并排）。
  // 不用 `> div:last-child`：usability.js 会往 #toolbar 末尾直接追加按钮，
  // 那时最后一个子元素已不是 div，选择器会落空。
  const groups=$('#toolbar')?Array.from($('#toolbar').children).filter(node=>node.tagName==='DIV'):[];
  const toolbarGroup=groups[groups.length-1]||$('#toolbar');
  if(toolbarGroup){toolbarGroup.append(maskButton,managerButton);}

  const live=el('div','mask-live');
  live.id='mask-live';
  live.setAttribute('role','status');
  live.setAttribute('aria-live','polite');
  document.body.append(live);

  function announce(text){live.textContent=String(text||'');}

  function maskLayer(){
    let layer=$('#mask-layer');
    if(layer)return layer;
    layer=el('div');
    layer.id='mask-layer';
    // 放在页面图像之上、批注层之下：遮罩要看得见，但不能盖住用户已有的高亮批注。
    const image=$('#page-image');
    if(image&&image.parentElement)image.after(layer);
    else $('#paper')?.append(layer);
    return layer;
  }

  // ---- 遮罩层绘制 ----

  function drawMaskLayer(){
    const layer=maskLayer();
    if(!layer)return;
    layer.replaceChildren();
    const masks=maskDedupe(state.pageData&&state.pageData.masks||[]);
    for(const mask of masks){
      const box=el('div','mask-region');
      Object.assign(box.style,{left:maskClamp(mask.x0)*100+'%',top:maskClamp(mask.y0)*100+'%',
        width:(maskClamp(mask.x1)-maskClamp(mask.x0))*100+'%',
        height:(maskClamp(mask.y1)-maskClamp(mask.y0))*100+'%'});
      box.title=maskPageLabel(mask)+' · '+maskRegionText(mask)+' 已屏蔽（不参与提问、检索与引用定位）';
      box.append(el('span','mask-region-label',mask.page===0?'已屏蔽 · 所有页':'已屏蔽 · 本页'));
      layer.append(box);
    }
    updateModeHint();
  }

  function updateModeHint(){
    // 工具栏按钮顺带显示这篇文献已有多少条规则：用户不必先点开管理对话框才知道设过什么。
    const rules=maskDedupe(state.pageData&&state.pageData.masks||[]).length;
    managerButton.textContent=rules?('▤ 屏蔽规则 ('+rules+')'):'▤ 屏蔽规则';
  }

  // ---- 拖拽选区 ----

  function paperPoint(event){
    const rect=$('#paper').getBoundingClientRect();
    if(!rect.width||!rect.height)return {x:0,y:0};
    return {x:maskClamp((event.clientX-rect.left)/rect.width),
            y:maskClamp((event.clientY-rect.top)/rect.height)};
  }

  function previewBox(){
    let node=$('#mask-drag');
    if(node)return node;
    node=el('div');
    node.id='mask-drag';
    maskLayer().append(node);
    return node;
  }

  function showPreview(start,end){
    const rect=maskNormalizeRect(start,end),node=previewBox();
    Object.assign(node.style,{display:'block',left:rect.x0*100+'%',top:rect.y0*100+'%',
      width:(rect.x1-rect.x0)*100+'%',height:(rect.y1-rect.y0)*100+'%'});
  }

  function clearPreview(){
    const node=$('#mask-drag');
    if(node)node.style.display='none';
  }

  function setMaskMode(enabled){
    if(enabled&&!state.pageData){toast('请先打开一份文献');return;}
    if(enabled&&state.crop){$('#crop').click();}  // 两个框选工具不能同时占用页面
    maskMode=enabled;
    maskButton.classList.toggle('active',enabled);
    $('#paper')?.classList.toggle('masking',enabled);
    if(!enabled)clearPreview();
    if(enabled){
      // 进入屏蔽模式时清掉当前选文：接下来在页面上的拖拽是"画区域"，不是"选文字"。
      window.getSelection()?.removeAllRanges();
      if(typeof setSelection==='function')setSelection('');
    }
    announce(enabled?'已进入屏蔽模式：在页面上拖出要屏蔽的区域，松开后选择作用于本页还是所有页。':'已退出屏蔽模式。');
    if(enabled)toast('在页面上拖出要屏蔽的区域（页眉、页脚、页码、水印等）');
  }

  maskButton.onclick=()=>setMaskMode(!maskMode);

  $('#paper').addEventListener('pointerdown',event=>{
    if(!maskMode||event.button!==0||!state.pageData||state.toolBusy)return;
    event.preventDefault();
    event.stopPropagation();  // 不能让"画遮罩"的拖拽继续变成选文/框选识别
    maskStart=paperPoint(event);
    $('#paper').setPointerCapture(event.pointerId);
    showPreview(maskStart,maskStart);
  });

  $('#paper').addEventListener('pointermove',event=>{
    if(!maskStart)return;
    event.preventDefault();
    event.stopPropagation();
    showPreview(maskStart,paperPoint(event));
  });

  $('#paper').addEventListener('pointerup',event=>{
    if(!maskStart)return;
    event.preventDefault();
    event.stopPropagation();
    const start=maskStart;maskStart=null;
    clearPreview();
    const rect=maskNormalizeRect(start,paperPoint(event));
    if(!maskValidRect(rect)){toast('拖出的区域太小，请重新框选完整的页眉/页脚或水印区域');return;}
    openChoiceDialog(rect);
  });

  $('#paper').addEventListener('pointercancel',()=>{maskStart=null;clearPreview();});

  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&maskMode){setMaskMode(false);}
  });

  // ---- 选择"本页 / 所有页" ----

  function ensureChoiceDialog(){
    let dialog=$('#mask-choice-dialog');
    if(dialog)return dialog;
    dialog=el('dialog','mask-dialog');
    dialog.id='mask-choice-dialog';
    dialog.setAttribute('aria-labelledby','mask-choice-title');
    const title=el('h2','','屏蔽这块区域');
    title.id='mask-choice-title';
    const describe=el('p','hint',
      '被屏蔽区域内的文字将不再出现在阅读原文、检索结果、引用定位与页面文字层中。'
      +'本操作不会改动 PDF 文件；删除规则即可恢复并重建索引。');
    const region=el('p','mask-region-summary');
    region.id='mask-choice-region';
    const choices=el('div','mask-scope-choices');
    const pageLabel=el('label');
    const pageRadio=document.createElement('input');pageRadio.type='radio';pageRadio.name='mask-scope';pageRadio.value='page';pageRadio.checked=true;
    pageLabel.append(pageRadio,el('span','','仅本页（第 '+'(当前页)'+' 页）'));
    const allLabel=el('label');
    const allRadio=document.createElement('input');allRadio.type='radio';allRadio.name='mask-scope';allRadio.value='all';
    allLabel.append(allRadio,el('span','','所有页（每一页的同一位置）'));
    choices.append(pageLabel,allLabel);
    const actions=el('div','dialog-actions');
    const cancel=el('button','','取消');cancel.type='button';
    const confirm=el('button','primary','确认屏蔽');confirm.type='button';
    actions.append(cancel,confirm);
    dialog.append(title,describe,region,choices,actions);
    document.body.append(dialog);
    dialog.__maskPageLabel=pageLabel;
    cancel.onclick=()=>dialog.close();
    confirm.onclick=()=>{
      const rect=dialog.__maskRect;
      const reuse=(dialog.querySelector('input[name="mask-scope"]:checked')||{}).value||'page';
      dialog.close();
      if(rect)submitMask(rect,reuse);
    };
    return dialog;
  }

  function openChoiceDialog(rect){
    const dialog=ensureChoiceDialog();
    dialog.__maskRect=rect;
    dialog.querySelector('#mask-choice-region').textContent=
      '区域：'+maskRegionText(rect)+'（相对于本页左上角）。';
    // 页码写进选项文案：用户要知道"本页"到底是哪一页。
    dialog.__maskPageLabel.querySelector('span').textContent='仅本页（第 '+state.page+' 页）';
    const pageRadio=dialog.querySelector('input[value="page"]');
    if(pageRadio)pageRadio.checked=true;
    if(!dialog.open)dialog.showModal();
  }

  function reportDecision(status){
    announce('屏蔽规则已保存。'+maskIndexStatusText(status));
    if(!status||status.state==='done'||status.state==='idle'){
      toast('屏蔽规则已保存，索引已更新：被屏蔽文字不再参与提问、检索与引用定位');
    }else if(status.state==='running'){
      toast('屏蔽规则已保存，正在后台重建检索索引（'+(status.done||0)+'/'+(status.total||0)+' 页）；完成前检索结果可能仍包含被屏蔽文字');
    }else{
      toast('屏蔽规则已保存，但检索索引重建失败：'+(status.error||'未知原因')+'；阅读已按规则生效');
    }
  }

  async function submitMask(rect,reuse){
    if(!state.doc)return;
    const docId=state.doc.id,page=state.page;
    try{
      const result=await post('/documents/'+docId+'/masks',
        {page,reuse,x0:rect.x0,y0:rect.y0,x1:rect.x1,y1:rect.y1});
      reportDecision(result.index_status);
      if(state.doc&&state.doc.id===docId&&state.page===page)await renderPage();
    }catch(error){
      toast('屏蔽失败：'+error.message);
    }
  }

  // ---- 管理规则（查看 / 逐条删除） ----

  function ensureManagerDialog(){
    let dialog=$('#mask-manager-dialog');
    if(dialog)return dialog;
    dialog=el('dialog','mask-dialog mask-manager-dialog');
    dialog.id='mask-manager-dialog';
    dialog.setAttribute('aria-labelledby','mask-manager-title');
    const title=el('h2','','屏蔽规则 · 无关区域');
    title.id='mask-manager-title';
    const describe=el('p','hint',
      '这些区域内的文字不参与提问、检索与引用定位，也不会出现在页面文字层。'
      +'PDF 原文没有被改写：删除任意一条规则会重建索引，阅读与检索随即恢复原文。');
    const status=el('p','mask-index-status');
    status.id='mask-manager-status';
    status.setAttribute('role','status');
    status.setAttribute('aria-live','polite');
    const list=el('div','mask-manager-list');
    list.id='mask-manager-list';
    const actions=el('div','dialog-actions');
    const close=el('button','','关闭');close.type='button';
    actions.append(close);
    dialog.append(title,describe,status,list,actions);
    document.body.append(dialog);
    close.onclick=()=>dialog.close();
    dialog.addEventListener('close',()=>{
      if(maskStatusTimer){clearTimeout(maskStatusTimer);maskStatusTimer=null;}
    });
    return dialog;
  }

  function renderManagerList(container,masks,docId){
    container.replaceChildren();
    const rules=maskDedupe(masks||[]);
    if(!rules.length){
      container.append(el('p','hint','这篇文献还没有屏蔽规则。点工具栏「▩ 屏蔽无关区域」，在页面上拖出页眉、页脚、页码或水印所在区域。'));
      return;
    }
    for(const mask of rules){
      const row=el('div','mask-rule');
      const text=el('div','mask-rule-text');
      text.append(el('strong','',maskPageLabel(mask)),el('span','',maskRegionText(mask)
        +(mask.created?' · 创建于 '+mask.created:'')));
      const remove=el('button','danger','删除');
      remove.type='button';
      remove.title='删除这条规则并重建索引；之后该区域恢复可阅读、可检索';
      remove.onclick=async()=>{
        remove.disabled=true;
        try{
          const result=await api('/documents/'+docId+'/masks/'+encodeURIComponent(mask.id),{method:'DELETE'});
          reportDecision(result.index_status);
          await refreshManager(docId);
          if(state.doc&&state.doc.id===docId)await renderPage();
        }catch(error){
          toast('删除失败：'+error.message);
          remove.disabled=false;
        }
      };
      row.append(text,remove);
      container.append(row);
    }
  }

  async function refreshManager(docId){
    const dialog=ensureManagerDialog();
    const status=dialog.querySelector('#mask-manager-status');
    try{
      const data=await api('/documents/'+docId+'/masks');
      if(!dialog.open)return;
      renderManagerList(dialog.querySelector('#mask-manager-list'),data.masks,docId);
      status.textContent='规则 '+maskDedupe(data.masks||[]).length+' 条。'+maskIndexStatusText(data.index_status);
      if(maskStatusTimer){clearTimeout(maskStatusTimer);maskStatusTimer=null;}
      if(data.index_status&&data.index_status.state==='running'){
        maskStatusTimer=setTimeout(()=>{
          if(dialog.open&&state.doc&&state.doc.id===docId)refreshManager(docId);
        },900);
      }
    }catch(error){
      status.textContent='读取规则失败：'+error.message;
    }
  }

  managerButton.onclick=async()=>{
    if(!state.doc){toast('请先打开一份文献');return;}
    const docId=state.doc.id;
    ensureManagerDialog().showModal();
    await refreshManager(docId);
  };

  // 翻页/换文献后重画遮罩：renderPage 先写 state.pageData 再替换图片 src，
  // 因此监听图片地址变化就能在"新页数据已就位"之后重画，不必改 app.js。
  const image=$('#page-image');
  if(image&&typeof MutationObserver==='function'){
    new MutationObserver(()=>{drawMaskLayer();}).observe(image,{attributes:true,attributeFilter:['src']});
  }
  // 初次进入阅读区（工具栏从隐藏变为可见）时补画一次，避免依赖时序。
  const toolbar=$('#toolbar');
  if(toolbar&&typeof MutationObserver==='function'){
    new MutationObserver(()=>{if(!toolbar.hidden)drawMaskLayer();}).observe(toolbar,{attributes:true,attributeFilter:['hidden']});
  }
})();
