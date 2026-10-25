// 从 Zotero 迁移批注/高亮。
//
// 为什么单独成文件：这是"一次性导入"的入口，不属于阅读、批注渲染或 Zotero 列表任何一方；
// 它只做三件事——判断当前文献是否来自 Zotero、预览会导入什么、确认后调用后端导入并刷新批注。
// 迁移是只读 Zotero 的：本文件不提供任何"写回 Zotero"的入口。
(function(){
  let button=null, dialog=null, busy=false;

  function currentDoc(){ return (typeof state!=='undefined' && state.doc) ? state.doc : null; }
  function isZoteroDoc(doc){ return !!doc && String(doc.storage_kind||'').startsWith('zotero'); }

  function mountButton(){
    const tab=$('#annotations-tab');
    if(!tab||$('#zotero-annotations-import'))return;
    const anchor=$('#new-annotation');
    button=el('button','secondary','⇩ 从 Zotero 导入批注');
    button.type='button';
    button.id='zotero-annotations-import';
    button.title='把 Zotero 里的高亮、下划线与便签迁移成本应用批注（只读 Zotero，不改它）';
    button.onclick=preview;
    if(anchor&&anchor.parentElement)anchor.after(button);else tab.prepend(button);
  }

  function ensureDialog(){
    if(dialog)return dialog;
    dialog=el('dialog','zotero-migrate');
    dialog.id='zotero-migrate-dialog';
    dialog.setAttribute('aria-label','从 Zotero 导入批注');
    dialog.addEventListener('close',()=>{dialog.hidden=true;});
    document.body.append(dialog);
    return dialog;
  }

  function summary(report){
    const box=el('div','zotero-migrate-summary');
    const line=(label,value)=>box.append(el('p','zotero-migrate-line',`${label}：${value}`));
    line('Zotero 附件',report.attachment_key||'（未知）');
    line('可迁移',report.mode==='import'?`已导入 ${report.imported} 条`:`将导入 ${report.would_import} 条`);
    if(report.located)line('按原文定位成功',`${report.located} 条`);
    if(report.page_level)line('按页级导入（位置需核对）',`${report.page_level} 条`);
    if(report.skipped_existing)line('已存在、未重复导入',`${report.skipped_existing} 条`);
    const skipped=(report.skipped_type||0)+(report.skipped_page||0)+(report.skipped_empty||0);
    if(skipped)line('跳过（类型/位置/空）',`${skipped} 条`);
    const p=el('p','hint','只读 Zotero：迁移不修改、不删除 Zotero 里的任何内容；你导入后会得到本应用自己的副本。'
      +'原文位置由本应用重新定位，定位不到的会如实标成"页级批注"，不会假装精确。');
    box.append(p);
    return box;
  }

  function itemList(report){
    const list=el('div','zotero-migrate-items');
    for(const item of (report.items||[])){
      const row=el('div','zotero-migrate-item');
      const head=el('strong','',`第 ${item.page||'?'} 页 · ${item.type||'标注'}`);
      row.append(head,el('span','hint',item.located?'已定位':'未精确到原文'));
      if(item.quote)row.append(el('blockquote','',item.quote));
      if(item.comment)row.append(el('p','',item.comment));
      if(item.reason)row.append(el('p','hint',item.reason));
      list.append(row);
    }
    return list;
  }

  function showReport(report,canImport){
    const box=ensureDialog();
    box.replaceChildren(el('h2','','从 Zotero 导入批注'));
    box.append(summary(report));
    for(const warning of report.warnings||[])box.append(el('p','zotero-migrate-warning','⚠ '+warning));
    if(report.items&&report.items.length)box.append(itemList(report));
    const actions=el('div','dialog-actions');
    const close=el('button','secondary','关闭');close.type='button';close.onclick=()=>box.close();
    actions.append(close);
    if(canImport){
      const confirm=el('button','primary',`导入 ${report.would_import} 条批注`);confirm.type='button';
      confirm.onclick=()=>runImport(confirm);
      actions.append(confirm);
    }
    box.append(actions);
    if(!box.open)box.showModal();
  }

  async function request(path,button){
    if(busy)return null;
    busy=true;if(button)button.disabled=true;
    try{
      return await post(path,{});
    }catch(error){
      toast(error.message);
      return null;
    }finally{busy=false;if(button)button.disabled=false;}
  }

  async function preview(){
    const doc=currentDoc();
    if(!doc)return toast('先打开一篇文献');
    if(!isZoteroDoc(doc))return toast('这篇文献不是从 Zotero 打开的：请先在「Zotero 文献」页打开附件，再迁移批注');
    const report=await request(`/documents/${doc.id}/zotero-annotations/preview`,button);
    if(!report)return;
    showReport(report,(report.would_import||0)>0);
  }

  async function runImport(confirmButton){
    const doc=currentDoc();
    if(!doc)return;
    const report=await request(`/documents/${doc.id}/zotero-annotations/import`,confirmButton);
    if(!report)return;
    showReport(report,false);
    if(report.imported){
      if(typeof loadAnnotations==='function')await loadAnnotations();
      toast(`已从 Zotero 导入 ${report.imported} 条批注`);
    }else{
      toast(report.message||'没有可导入的批注');
    }
  }

  mountButton();
  window.__kanreadZoteroMigrate={summary,itemList,isZoteroDoc};
})();
