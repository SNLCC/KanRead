// 发送前预算确认对话框。
//
// 为什么单独成文件：它只在"这一轮预计读得比较多"时出现，属于一次性的成本告知，
// 不属于阅读流程本身。全局只暴露 confirmBudget(budget) → Promise<boolean>，
// 由 app.js 的提问链路调用；取消必须能被上层识别为"用户主动取消"，不能当成失败。
(function(){
  let dialog=null;
  function build(){
    if(dialog)return dialog;
    dialog=el('dialog','budget-confirm');
    dialog.id='budget-confirm-dialog';
    dialog.setAttribute('aria-label','确认本轮读取量');
    dialog.addEventListener('close',()=>{dialog.hidden=true;});
    document.body.append(dialog);
    return dialog;
  }
  function formatNumber(value){return Number(value||0).toLocaleString('zh-CN');}
  function paint(budget,resolve){
    const box=build();
    box.replaceChildren(el('h2','','这一轮要读得比较多'));
    const facts=el('div','budget-confirm-facts');
    const line=(label,value)=>{const p=el('p');p.append(el('strong','',label),el('span','',value));facts.append(p);};
    line('预计读取',`${formatNumber(budget.pages)} 页 · ${formatNumber(budget.tokens)} token（估算）`);
    line('涉及文献',`${formatNumber(budget.documents)} 篇`);
    line('触发阈值',`超过 ${formatNumber(budget.thresholds?.pages)} 页或 ${formatNumber(budget.thresholds?.tokens)} token`);
    box.append(facts);
    box.append(el('p','hint',budget.method||'按本机已索引文本估算。'));
    box.append(el('p','hint','这是估算，不会改变读取范围；确认后才会开始读取并调用模型。取消不会开始阅读，也不会留下阅读进度。'));
    const actions=el('div','dialog-actions');
    const cancel=el('button','secondary','取消');
    cancel.type='button';
    const go=el('button','primary','继续读取');
    go.type='button';
    const finish=(value)=>{box.close();resolve(value);};
    cancel.onclick=()=>finish(false);
    go.onclick=()=>finish(true);
    box.addEventListener('cancel',event=>{event.preventDefault();finish(false);});
    actions.append(cancel,go);
    box.append(actions);
    box.hidden=false;
    box.showModal();
  }
  window.confirmBudget=function(budget){
    return new Promise(resolve=>{
      try{paint(budget||{},resolve);}
      catch(error){resolve(false);}
    });
  };
  // 供前端契约测试检查纯文案/结构。
  window.__kanreadBudgetConfirm={formatNumber};
})();
