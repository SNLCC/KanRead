// 导出带批注的 PDF 副本。
//
// 为什么单独成文件：这是一个**只读导出**入口——它不改批注、不改原 PDF、不碰检索，
// 只让本机后台生成一份新副本并交给浏览器下载。和批注的增删改（reading.js /
// experience.js）没有关系，混在一起只会让那两块更难读。
//
// 写不进去的批注不会被假装成功：服务端把它们计入 X-KanRead-Unplaced，
// 这里按响应头如实提示（例如旋转页上的批注）。
(function(){
  let button=null,busy=false;

  function currentDoc(){ return (typeof state!=='undefined'&&state.doc)?state.doc:null; }

  function mountButton(){
    const tab=$('#annotations-tab');
    if(!tab||$('#export-annotated-pdf'))return;
    // 放在「＋ 添加批注」旁边；若 Zotero 迁移入口已经排在那儿，就排在它后面，
    // 保证两个"导入/导出"动作挨着，而不会反复插到最前面。
    const anchor=$('#zotero-annotations-import')||$('#new-annotation');
    button=el('button','secondary','⇩ 导出带批注的 PDF 副本');
    button.type='button';
    button.id='export-annotated-pdf';
    button.title='生成一份新的 PDF 副本，把高亮、下划线与便签写进去；原文件不改动';
    button.onclick=run;
    if(anchor&&anchor.parentElement)anchor.after(button);else tab.prepend(button);
  }

  // 文件名来自服务端的 RFC 5987 头；解析失败就用文献名自己拼一个，保证下载名有意义。
  function fileNameFromDisposition(value,fallbackName){
    const header=String(value||'');
    const encoded=/filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header);
    if(encoded){
      try{return decodeURIComponent(encoded[1].trim());}catch(error){/* 编码坏掉时退回下面的兜底 */}
    }
    const plain=/filename\s*=\s*"([^"]+)"/i.exec(header);
    if(plain)return plain[1];
    const base=String(fallbackName||'文献').replace(/\.pdf$/i,'');
    return base+'-批注副本.pdf';
  }

  function reasonLabel(key){
    return {rotated_page:'页面旋转',page_out_of_range:'页码超出范围',invalid_page:'页码无效',
      invalid_rect:'位置无效',empty_annotation:'没有文字内容',write_failed:'写入出错'}[key]||key;
  }

  function summary(placed,unplaced,reasons){
    if(!Number.isFinite(placed))return '已导出 PDF 副本（原文件未改动）';
    if(placed===0&&unplaced===0)return '这份文献还没有批注；已导出未改动的副本';
    const parts=[placed>0?`已写入 ${placed} 条`:'没有批注被写入'];
    if(unplaced>0){
      const detail=Object.entries(reasons||{}).map(([key,count])=>reasonLabel(key)+' '+count+' 条').join('、');
      parts.push(`${unplaced} 条未写入（${detail||'页面旋转或位置无效'}）`);
    }
    return `带批注的副本：${parts.join('；')}。原文件未改动。`;
  }

  function download(blob,filename){
    const url=URL.createObjectURL(blob);
    const link=document.createElement('a');
    link.href=url;link.download=filename;link.rel='noopener';
    document.body.append(link);link.click();link.remove();
    // 立刻 revoke 有浏览器来不及取数据的风险；留一段延迟再释放（不会泄漏，页面关闭也会清）。
    setTimeout(()=>URL.revokeObjectURL(url),15000);
  }

  async function run(){
    if(busy)return;
    const doc=currentDoc();
    if(!doc)return toast('请先打开一篇文献，再导出带批注的副本');
    busy=true;if(button)button.disabled=true;
    try{
      const headers={};
      if(window.__KANREAD_TOKEN)headers['X-KanRead-Token']=window.__KANREAD_TOKEN;
      let response;
      try{
        response=await fetch(`/api/documents/${doc.id}/annotated.pdf`,{headers});
      }catch(error){
        throw new Error('连不上本机后台：请确认应用没有被关闭，然后重试');
      }
      if(!response.ok){
        let message='导出失败（HTTP '+response.status+'）';
        try{const data=await response.json();if(typeof data.detail==='string')message=data.detail;}catch(error){/* 非 JSON 错误体就用状态码 */}
        throw new Error(message);
      }
      const placed=Number(response.headers.get('X-KanRead-Placed'));
      const unplaced=Number(response.headers.get('X-KanRead-Unplaced'));
      let reasons={};
      try{reasons=JSON.parse(response.headers.get('X-KanRead-Unplaced-Reasons')||'{}')||{};}catch(error){reasons={};}
      const blob=await response.blob();
      download(blob,fileNameFromDisposition(response.headers.get('Content-Disposition'),doc.name));
      toast(summary(placed,unplaced,reasons));
    }catch(error){
      toast('导出失败：'+error.message);
    }finally{
      busy=false;if(button)button.disabled=false;
    }
  }

  mountButton();
  // 供测试/其他脚本复用：文件名解析与提示文案都是纯函数，不依赖 DOM。
  window.__kanReadAnnotatedExport={fileNameFromDisposition,summary,reasonLabel};
})();
