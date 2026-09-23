const $ = s => document.querySelector(s);
// 缩放参数唯一定义处：百分比制，步长必须整除 100，否则 100% 永远回不去。
// experience.js 的滚轮/按钮、interaction.js 的快捷键、usability.js 的数字输入都读这组值。
const ZOOM_MIN_PERCENT=55;
const ZOOM_MAX_PERCENT=300;
const ZOOM_STEP_PERCENT=5;
const ZOOM_DEFAULT_PERCENT=100;
const zoomPercent=percent=>Math.min(ZOOM_MAX_PERCENT,Math.max(ZOOM_MIN_PERCENT,Math.round(percent)));
const zoomStep=(direction,percent)=>zoomPercent(percent+direction*ZOOM_STEP_PERCENT);
// 以工具栏实际显示的比例为准，避免连续操作时与 state 漂移。
const currentZoomPercent=()=>{const field=$('#zoom-label');if(field&&field.tagName==='INPUT'){const shown=Number(field.value);if(Number.isFinite(shown)&&shown>=ZOOM_MIN_PERCENT&&shown<=ZOOM_MAX_PERCENT)return Math.round(shown);}return Math.round(state.zoom*100);};
const state = {docs:[], doc:null, page:1, zoom:1, pageData:null, selection:'', selectionRects:[], annotations:[], crop:false, renderId:0, busy:false, answering:false, toolBusy:false, selectionOrigin:''};
const el = (tag, cls, text) => {const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
async function api(path, options={}) {const r=await fetch('/api'+path,options);if(!r.ok){let msg='请求失败（HTTP '+r.status+'）';try{const j=await r.json();msg=typeof j.detail==='string'?j.detail:JSON.stringify(j.detail);}catch{}throw new Error(msg);}return r.json();}
const post=(path,data,method='POST')=>api(path,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
// 提示条必须能盖住模态设置面板：浏览器把 <dialog>.showModal() 放进 top layer，
// 那里的元素永远画在普通 z-index 之上，所以只调 z-index 没用（用户报告的正是
// "保存提示在设置界面下层"）。popover 是同一层的另一种元素，用 showPopover()
// 把提示条也放进 top layer，它就显示在设置界面之上；不支持 popover 时退回到
// "把提示条挪进当前打开的那个对话框内部"。toastTop 三态：null 未探测、true 用
// popover、false 用退路（只探测一次，避免每次都踩同一个异常）。
let toastTimer,toastTop=null;
function toast(text){const node=$('#toast');if(!node)return;node.textContent=text;
 if(toastTop===null){toastTop=typeof node.showPopover==='function';if(toastTop)node.removeAttribute('hidden');}
 if(toastTop){try{if(!node.matches(':popover-open'))node.showPopover();}catch(error){toastTop=false;}}
 if(!toastTop){const host=document.querySelector('dialog[open]')||document.body;if(node.parentElement!==host)host.append(node);node.hidden=false;}
 clearTimeout(toastTimer);toastTimer=setTimeout(()=>{if(toastTop){try{node.hidePopover();}catch(error){}}else node.hidden=true;},6500);}
function requireDoc(){if(!state.doc){toast('请先导入或打开一份 PDF');return false;}return true;}
// Phase 8：界面上只有两个受控选项，`payload` 是**唯一**把它们折算成请求字段的地方。
// 旧口径（cross_book / whole_document / model_knowledge / web）全部由这两个选项推导出来，
// 因此不存在"两套开关各说各话"：
//   阅读范围 → reading_scope，并据此维护 `#whole-doc`（局部/全文）与 `#cross-book`（跨文献）
//   阅读深度 → reading_rigor
//   来源策略 → source_policy；"仅文献"时**本地也不再勾**web/模型知识，与后端同一条口径
//             （许可不是命令：用户没说可以用别的来源，就不带出去）。
function readingOptions(){
 const scope=$('#reading-scope')?.value||'auto',rigor=$('#reading-rigor')?.value||'auto',policy=$('#source-policy')?.value||'auto';
 // 隐藏复选框保留为"跨文献范围选择器"的挂点（library.js 监听 #cross-book 的 change）。
 if($('#whole-doc'))$('#whole-doc').checked=['document','corpus'].includes(scope);
 if($('#cross-book'))$('#cross-book').checked=scope==='corpus';
 const onlyDocument=policy==='document_only';
 // 第 94 轮：**"自动"不再等于"每轮都联网"**。
 // 自动的含义是"由链路按论断性质决定要不要查证"——真正需要外部依据的论断会走既有的自动查证，
 // 关键词仍要用户确认后才会发出请求（权限是许可不是命令）。
 // 以前这里把 web 直接置真，于是**每一轮**提问都会：规划搜索主题 → 抓网页正文 →
 // 再综合一次 → 再核对一次；这四步全部发生在正文之后，正是"首答很快、后面很慢"的主因。
 const web=false;
 updateReadingHint(scope,rigor,policy);
 return {reading_scope:scope,reading_rigor:rigor,source_policy:policy,
  web,model_knowledge:onlyDocument?false:scope!=='local',
  whole_document:$('#whole-doc')?.checked||false,cross_book:$('#cross-book')?.checked||false};
}
// 「智能阅读 / 智能来源」到底会做什么：把选项的含义**写在界面上**（第 89 轮用户反馈：
// "当前文献局部是什么意思并不清晰""四档深度有什么区别并不清楚"）。
// 文案只描述代码里真实发生的事；改行为时必须同步改这里，否则界面就会开始骗人。
const READING_HINTS={
 auto:'范围自动：先花一次很快的判断调用决定读哪里（多一次调用，但不必自己选）。',
 local:'本页与相邻页：以当前页为中心读上下各一页，并按所在章节补齐边界（单节文献可能整篇都在这一节里）；不联网、不用模型背景知识，通常最快。',
 document:'整篇文献：读完整篇提取文本（按容量分批，批与批并发）。',
 corpus:'跨文献：按下面选定的文献集合读；先读与问题术语最相关的几篇，预算用完可点「继续阅读」。'};
const RIGOR_HINTS={
 auto:'深度自动＝**按这一轮的形态判断**要查几遍：单遍读完整篇时缺口回读最多 1 轮，多批阅读 2 轮，核对发现 3 段以上缺证时 3 轮（实际采用几轮会写在阅读报告里）。',
 fast:'快速：不做"针对论断缺口的再回读"（省 1–2 次调用）；逐字核验、覆盖台账一项不减。',
 standard:'标准：需要时做一轮缺口回读；核对后仍缺证据时最多再读一轮（共 2 轮）。',
 exhaustive:'全面核查：核对后最多三轮回读——最慢，查得最细。它**不再强制读整篇**：读哪里由「范围」决定，深度只管多查几遍。'};
const POLICY_HINTS={
 auto:'来源自动：**默认不联网**。要不要查证由链路按论断性质判断（见下），'
  +'判断"需要外部依据"时才会提议关键词；发不发出去看你在设置里的联网权限档位——'
  +'**"每次先确认"会弹出关键词让你批准，"自动发送"则直接发出，关闭时什么都不发**。',
 document_only:'仅文献：这一轮不发出任何外部请求，也不用模型知识补充。'};
function updateReadingHint(scope,rigor,policy){
 const node=$('#reading-hint');if(!node)return;
 // 说明**放进弹窗**（第 90 轮按用户反馈）：常驻显示太占输入区，现在点「说明」才展开。
 node.textContent=[READING_HINTS[scope],RIGOR_HINTS[rigor],POLICY_HINTS[policy]].filter(Boolean).join(' ');
}
$('#reading-help')?.addEventListener('click',()=>{const dialog=$('#reading-help-dialog');if(dialog&&!dialog.open)dialog.showModal();});
function payload(query){return {...crossScope,...readingOptions(),query,document_id:state.doc.id,page:state.page,selection:state.selection,mode:'broad'};}
function syncReadingOptions(){readingOptions();if(typeof syncScopePicker==='function')syncScopePicker();}
for(const id of ['#reading-scope','#reading-rigor','#source-policy'])$(id)?.addEventListener('change',syncReadingOptions);
function drawLibrary(){ $('#count').textContent=state.docs.length;$('#documents').replaceChildren();for(const d of state.docs){const b=el('button','document-item'+(state.doc?.id===d.id?' active':''));b.title=d.name;b.dataset.docId=d.id;b.append(el('strong','', '▧  '+d.name),el('small','',`${d.pages} 页 · 读至 ${d.current_page} 页`));const p=el('div','progress'),i=el('i');i.style.width=(d.current_page/d.pages*100)+'%';p.append(i);b.append(p);b.onclick=()=>openDoc(d);$('#documents').append(b);}}
async function loadLibrary(){state.docs=await api('/documents');drawLibrary();}
async function openDoc(d){if(state.busy){toast('请等待当前操作完成');return;}document.body.classList.remove('managing');resetReaderInteraction();state.annotations=[];state.doc=d;state.page=d.current_page;setSelection('');$('#doc-title').textContent=d.name;$('#welcome').hidden=true;for(const s of ['#toolbar','#viewport','#reader-footer'])$(s).hidden=false;drawLibrary();const id=d.id;try{await renderPage();const history=await api(`/documents/${id}/messages`);if(state.doc.id!==id)return;$('#messages').replaceChildren();if(!history.length)$('#messages').append(el('div','chat-intro','◇\n已关联文献。选中一段原文，或者从概括这一页开始。'));for(const m of history)addMessage(m.role,m.content,m.sources,m.metadata);await Promise.all([loadNotes(),loadAnnotations()]);}catch(e){toast(e.message);}}
// 译文对照是内置翻译功能接到阅读区的挂点。app.js 只在"新页面已经画好"时通知一次，
// 不直接调用翻译界面里的函数：这样这个文件不必知道翻译功能存在，功能被删除时也只是没有回调。
// 两个回调都用 typeof 判断后再调，不假定翻译功能一定加载过。
let pageTranslation=null,pageTranslationLayout=null;
async function renderPage(highlight){state.pageData=null;$('#annotation-layer').replaceChildren();const id=++state.renderId,docId=state.doc.id,page=state.page;quoteHighlightToken++;$('#context span').textContent=`当前文献 · 第 ${page} 页`;if(typeof syncOutlineActive==='function')syncOutlineActive();if(typeof pageTranslation==='function')pageTranslation(docId,page);$('#page-number').value=page;$('#page-total').textContent=`/ ${state.doc.pages}`;$('#page-number').max=state.doc.pages;$('#prev').disabled=page<=1;$('#next').disabled=page>=state.doc.pages;$('#highlight').style.display='none';$('#text-layer').replaceChildren();$('#page-image').style.opacity='.35';try{const img=new Image();img.src=`/api/documents/${docId}/pages/${page}/image`;const [data]=await Promise.all([api(`/documents/${docId}/pages/${page}`),img.decode()]);if(id!==state.renderId)return;state.pageData=data;$('#page-image').src=img.src;$('#page-image').style.opacity='1';resizePage();$('#viewport').scrollTop=0;quotePreview().hidden=true;quoteLayer().hidden=true;if(highlight&&highlight.quote){hideQuoteHighlight();$('#highlight').style.display='none';localQuoteHighlight(highlight);refineQuoteHighlight(highlight);}else if(highlight)showHighlight(highlight);post(`/documents/${docId}/progress`,{page},'PUT').catch(e=>toast(e.message));const doc=state.docs.find(d=>d.id===docId);doc.current_page=page;drawLibrary();if(typeof pageTranslation==='function')pageTranslation(docId,page,data);}catch(e){if(id===state.renderId)toast(e.message);}}
function resizePage(){if(!state.pageData)return;const data=state.pageData,width=Math.max(200,$('#viewport').clientWidth-56)*state.zoom;$('#paper').style.width=width+'px';const layer=$('#text-layer'),scale=width/data.width;if(layer.dataset.pageKey!==String(state.renderId)){const fragment=document.createDocumentFragment(),glyphs=[];for(const span of(data.characters||data.spans)){const n=el('span','',span.text+(span.line_end?'\n':'')),[x,y,x1,y1]=span.bbox;Object.assign(n.style,{left:x+'px',top:y+'px',fontSize:(span.size||(y1-y))+'px',height:(y1-y)+'px'});fragment.append(n);glyphs.push({n,width:x1-x});}layer.replaceChildren(fragment);layer.style.width=data.width+'px';layer.style.height=data.height+'px';layer.style.transform='none';const widths=glyphs.map(g=>g.n.getBoundingClientRect().width);glyphs.forEach((g,i)=>{if(widths[i])g.n.style.transform=`scaleX(${g.width/widths[i]})`;});layer.dataset.pageKey=String(state.renderId);}layer.style.transformOrigin='0 0';layer.style.transform=`scale(${scale})`;const label=$('#zoom-label');if(label.tagName==='INPUT')label.value=Math.round(state.zoom*100);else label.textContent=Math.round(state.zoom*100)+'%';drawAnnotations();if(typeof pageTranslationLayout==='function')pageTranslationLayout();}
async function go(page,highlight){if(!requireDoc())return;page=Number(page);if(!Number.isInteger(page)||page<1||page>state.doc.pages){toast('请输入有效页码');$('#page-number').value=state.page;return;}resetReaderInteraction();state.page=page;setSelection('');await renderPage(highlight);}
// 引用定位到"内容"而不是整页：把核验过的摘引在版面上找出来。
//
// 关键点是**按行匹配**而不是整段字符串比较：双栏页面经过重排后，阅读文本的行序
// 与字形顺序并不一致（见 literature_core/structure.py 的 layout_text），
// 整段比较会失败，而"摘引覆盖的每一行"仍然能各自对应到版面上的那一行。
// 扫描页没有字形，则由后端用识别时保存的 OCR 行框做同一件事。
// 记录页码与实际不符时（后端会在全文里找回真正的页），这里把引用标签一起更正，
// 不让"标签写第 1 页、实际在第 5 页"继续误导用户。
function correctCitationLabel(button,page){
 if(!button||!button.dataset||!button.dataset.index)return;
 const label=button.querySelector('span');
 if(label)label.textContent=`[${button.dataset.index}] ${button.dataset.name||'文献'} · 第 ${page} 页`;
 button.title=`已更正为第 ${page} 页（原记录与正文引用不一致）`;
}
function normalizeQuote(text){return String(text||'').replace(/\s+/g,'');}
function quoteLayer(){let node=$('#quote-highlight');if(!node){node=el('div');node.id='quote-highlight';node.hidden=true;$('#paper').append(node);}return node;}
// 会话里的引用高亮是**单独一层**（#quote-highlight）：批注的常驻高亮在 #annotation-layer，
// 一般跳转高亮是 #highlight。所以"取消引用高亮"只清这一层，绝不触碰前两者——
// 即使引用框和批注高亮在同一处重合，取消后批注高亮依旧原样在页面上。
let quoteHighlightToken=0;   // 每次换/取消引用高亮都换令牌，让还在路上的定位请求作废
function hideQuoteHighlight(){const layer=quoteLayer();layer.replaceChildren();layer.hidden=true;}
function dismissQuoteHighlight(){quoteHighlightToken++;hideQuoteHighlight();}
function pageLines(){
 const data=state.pageData;if(!data)return [];
 const nodes=Array.from($('#text-layer').children),glyphs=data.characters||data.spans||[];
 const lines=[];let text='',rects=[];
 const push=()=>{if(text.trim()&&rects.length)lines.push({text,rects:rects.slice()});text='';rects=[];};
 glyphs.forEach((glyph,index)=>{
  const node=nodes[index];if(!node)return;
  text+=node.textContent;rects.push(node.getBoundingClientRect());
  // characters 带 line_end；用 spans 时每个元素本身就是一行。
  if(glyph.line_end||!data.characters)push();
 });
 push();
 return lines;
}
function quotePartLines(pageText,quote){
 const text=String(pageText||''),needle=normalizeQuote(quote);
 if(needle.length<4||!text)return [];
 const positions=[];
 for(let index=0;index<text.length;index++)if(!/\s/.test(text[index]))positions.push(index);
 const at=normalizeQuote(text).indexOf(needle);
 if(at<0)return [];
 const start=positions[at],end=positions[at+needle.length-1]+1;
 const parts=[];let offset=0;
 for(const line of text.split('\n')){
  const lineStart=offset,lineEnd=offset+line.length;offset=lineEnd+1;
  if(lineEnd<=start||lineStart>=end)continue;
  const piece=line.slice(Math.max(start,lineStart)-lineStart,Math.min(end,lineEnd)-lineStart);
  if(piece.trim())parts.push(piece);
 }
 return parts;
}
function matchQuoteParts(parts,lines){
 const matched=[];let cursor=0;
 for(const part of parts){
  const target=normalizeQuote(part);if(target.length<4)continue;   // 过短片段不定位，避免框错位置
  let best=null,rank=0,index=cursor;
  for(let step=0;step<lines.length;step++){
   const position=(cursor+step)%lines.length,text=normalizeQuote(lines[position].text);
   const score=text===target?2:text.includes(target)?1:0;
   if(score>rank){best=lines[position];rank=score;index=position;if(score===2)break;}
  }
  if(best)matched.push(best);cursor=index;
 }
 return matched;
}
function boxesOf(lines){
 const paper=$('#paper').getBoundingClientRect();if(!paper.width||!paper.height)return [];
 return lines.map(line=>{
  const x0=Math.min(...line.rects.map(r=>r.left)),y0=Math.min(...line.rects.map(r=>r.top));
  const x1=Math.max(...line.rects.map(r=>r.right)),y1=Math.max(...line.rects.map(r=>r.bottom));
  return {left:(x0-paper.left)/paper.width,top:(y0-paper.top)/paper.height,
          width:(x1-x0)/paper.width,height:(y1-y0)/paper.height};
 }).filter(box=>box.width>0&&box.height>0);
}
function drawQuoteBoxes(boxes){
 const layer=quoteLayer();layer.replaceChildren();
 // 引用框自己接收点击：点一下就取消这处引用高亮。图层其余部分仍是 pointer-events:none，
 // 所以选文、批注、框选识别与页面上的其它交互完全不受影响。
 for(const box of boxes){
  const mark=el('div','quote-box');
  Object.assign(mark.style,{left:100*box.left+'%',top:100*box.top+'%',width:100*box.width+'%',height:100*box.height+'%'});
  mark.title='点击取消这处引用高亮（批注高亮不会受影响）';
  // 框选识别这类工具占用页面时（state.crop / toolBusy）不拦截指针事件，工具照常工作。
  mark.onpointerdown=event=>{if(state.crop||state.toolBusy)return;event.stopPropagation();};
  mark.onpointerup=event=>{if(state.crop||state.toolBusy)return;event.stopPropagation();};
  mark.onclick=event=>{
   if(state.crop)return;
   // 不让这次点击继续传播：它只用来取消引用高亮，不该顺手打开批注弹窗或清掉当前选文。
   event.preventDefault();event.stopPropagation();
   dismissQuoteHighlight();
  };
  layer.append(mark);
 }
 layer.hidden=!boxes.length;
 if(boxes.length)$('#highlight').style.display='none';
 // 画完就把引用的起点滚进视口：只画框不滚动时，页面停在 renderPage 刚设的顶部，
 // 用户看到的仍然是"跳到了这一页的顶部"。
 if(boxes.length)scrollQuoteIntoView();
}
// 引用要落到"那段文字"而不是页面顶部：把第一处框（引用的起点）滚到视口中间。
// 页面刚渲染完时图片与字体可能还在收尾，下一帧若那处框仍不可见就再对一次。
function scrollQuoteIntoView(){
 const mark=quoteLayer().firstElementChild;
 if(!mark||typeof mark.scrollIntoView!=='function')return;
 const center=()=>mark.scrollIntoView({block:'center',inline:'center',behavior:'smooth'});
 center();
 requestAnimationFrame(()=>{
  const view=$('#viewport').getBoundingClientRect(),rect=mark.getBoundingClientRect();
  if(rect.bottom<view.top||rect.top>view.bottom)center();
 });
}
function localQuoteHighlight(highlight){
 if(!highlight.text){showQuotePreview(highlight.quote,highlight.page,highlight.name);return;}
 const parts=quotePartLines(highlight.text,highlight.quote);
 const boxes=boxesOf(matchQuoteParts(parts,pageLines()));
 if(boxes.length)drawQuoteBoxes(boxes);else showQuotePreview(highlight.quote,highlight.page,highlight.name);
}
// 后端再定位一次：扫描页没有字形，只能靠识别时保存的行框；双栏、校订文本也在这里兜底。
// **并以后端为准**：记录的页码不对时（这一页根本没有这段文字），后端会在全文里找回真正的页；
// 全文都找不到时也必须说清楚，而不是把用户留在错误的页面上——
// 用户看到的"所有参考都跳第一页、而不是具体文本所在处"就是后一种情况被静默吞掉的结果。
function locateDestination(result,page){
 const boxes=(result?.boxes||[]).map(item=>({left:item.bbox[0],top:item.bbox[1],
   width:item.bbox[2]-item.bbox[0],height:item.bbox[3]-item.bbox[1]}));
 // 后端没给页码（异常返回）时按请求页处理：page 与 moved 必须由**同一个**归一化结果算出来，
 // 否则会出现"页号是 4、却报告跳到了别处"这种自相矛盾的状态。
 const reported=boxes.length?Number(result?.page):NaN;
 const target=Number.isInteger(reported)&&reported>0?reported:page;
 return {
  boxes,
  // 有框：以后端给的页为准；没框：留在原地，但必须挂出后端给出的原因。
  page:boxes.length?target:page,
  moved:boxes.length>0&&target!==page,
  message:result?.message||'',
 };
}
async function refineQuoteHighlight(highlight){
 if(!state.doc)return;
 const docId=state.doc.id,page=state.page;
 // 令牌记下"这次定位属于哪一次引用高亮"：用户在请求返回前点了取消（或又点了别的引用），
 // 结果回来时就不再画——否则刚取消的高亮会自己冒出来。
 const token=quoteHighlightToken;
 try{
  // 用 api() 而不是 post()：这样它会挂上任务的中止信号（换引用/取消时立刻作废，不必等超时）。
  const result=await api(`/documents/${docId}/pages/${page}/locate`,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({quote:highlight.quote})});
  if(state.doc?.id!==docId||token!==quoteHighlightToken)return;
  const target=locateDestination(result,page);
  const boxes=target.boxes;
  if(!boxes.length){
   // 定位不到时把原因说出来（模型转述、已校订文本、全文都没有），不让用户以为是点错了。
   const preview=quotePreview();
   if(!preview.hidden&&target.message)preview.append(el('small','',target.message));
   if(target.message)toast(target.message);
   return;
  }
  if(target.moved){
   // 把标签也更正过来：否则界面一边跳到第 5 页、一边写着第 1 页，用户只会更困惑。
   if(highlight.source)highlight.source.page=target.page;
   correctCitationLabel(highlight.button,target.page);
   toast(target.message||`这段文字在第 ${target.page} 页，已跳过去`);
   await go(target.page,target);
   return;
  }
  drawQuoteBoxes(boxes);quotePreview().hidden=true;
 }catch{}
}
function quotePreview(){let node=$('#quote-preview');if(!node){node=el('div');node.id='quote-preview';node.hidden=true;node.setAttribute('role','status');document.body.append(node);}return node;}
function showQuotePreview(quote,page,name){
 if(!quote)return;
 const box=quotePreview();box.replaceChildren();
 const title=el('strong','',`引用原文${name?' · '+name:''} · 第 ${page} 页`);
 const close=el('button','','收起');close.onclick=()=>box.hidden=true;
 const text=el('q','',quote);
 box.append(title,text,close);box.hidden=false;
 const width=Math.min(360,window.innerWidth-32);
 box.style.left=Math.max(8,(window.innerWidth-width)/2)+'px';box.style.top='96px';box.style.width=width+'px';
}
function setHighlightBox(box){const node=$('#highlight');Object.assign(node.style,{display:'block',left:100*box.left+'%',top:100*box.top+'%',width:100*box.width+'%',height:100*box.height+'%'});node.scrollIntoView({block:'center',behavior:'smooth'});}
function showHighlight(source){const box=source.bbox;if(!box){$('#highlight').style.display='none';return;}let b=typeof box==='string'?JSON.parse(box):box;const d=state.pageData;if(d.rotation_matrix&&source.bbox_space!=='visual'){const [a,c,e,f,g,h]=d.rotation_matrix;const points=[[b[0],b[1]],[b[2],b[3]],[b[0],b[3]],[b[2],b[1]]].map(([x,y])=>[a*x+e*y+g,c*x+f*y+h]);b=[Math.min(...points.map(p=>p[0])),Math.min(...points.map(p=>p[1])),Math.max(...points.map(p=>p[0])),Math.max(...points.map(p=>p[1]))];}const n=$('#highlight');Object.assign(n.style,{display:'block',left:100*b[0]/d.width+'%',top:100*b[1]/d.height+'%',width:100*(b[2]-b[0])/d.width+'%',height:100*(b[3]-b[1])/d.height+'%'});n.scrollIntoView({block:'center',behavior:'smooth'});}
async function cite(source,quote,button){if(state.busy&&!state.answering)return;const doc=state.docs.find(d=>d.id===source.document_id);if(!doc)return toast('引用文献不在当前文献库（可能已移入回收站）');if(state.doc?.id!==doc.id)await openDoc(doc);
 // 优先用逐字核验过的摘引；没有就用"定位锚点"（正文用词与原文的逐字重合），仍能落到文字。
 // 这里给的页码只是**起点**：renderPage 之后 refineQuoteHighlight 会向后端要一次全文定位，
 // 落到真正包含这段文字的那一页；全文都没有时也会明说，不会停在错误的页上。
 const text=quote||(source.quotes||[])[0]||source.anchor||'';
 await go(source.page,text?{quote:text,page:source.page,name:source.name,text:source.text,bbox:source.bbox,bbox_space:source.bbox_space,source,button}:source);}
function addMessage(role,text,sources=[],metadata={}){return renderMessage(role,text,sources,metadata||{});}
// 选文：识别结果或手动选区都可以直接编辑调整（OCR 难免有错字，选区也可能需要删掉多余的行）。
// 编辑只影响这一轮提问用的选文，不改动页面文字或原 PDF。
function setSelection(text,rects=[],meta={}){
 state.selectionRects=rects;state.selection=text.slice(0,12000);state.selectionOrigin=meta.origin||'';
 $('#selection').hidden=!text;
 const paragraph=$('#selection p');paragraph.hidden=false;paragraph.textContent=state.selection;
 const editor=$('#selection-editor');editor.hidden=true;editor.value=state.selection;
 const edit=$('#edit-selection');edit.hidden=!text;edit.textContent='编辑选文';
 $('#selection-origin').textContent=text&&meta.origin==='ocr'?'（识别结果，可能有错字）':'';
}
function openSelectionEditor(){
 const editor=$('#selection-editor'),paragraph=$('#selection p'),button=$('#edit-selection');
 if($('#selection').hidden)return;
 editor.value=state.selection;editor.hidden=false;paragraph.hidden=true;button.textContent='完成';editor.focus();
}
$('#edit-selection').onclick=()=>{
 const editor=$('#selection-editor'),paragraph=$('#selection p'),button=$('#edit-selection');
 if(editor.hidden)openSelectionEditor();
 else{state.selection=editor.value.slice(0,12000).trim();paragraph.textContent=state.selection;paragraph.hidden=false;editor.hidden=true;button.textContent='编辑选文';}
};
$('#selection-editor').addEventListener('input',()=>{state.selection=$('#selection-editor').value.slice(0,12000);});
// 选文整理：PDF 的"版面折行"不是段落。
//
// 用户报告：选中文字后仍带着 PDF 的换行，显示出来就成了错误的分段；但同时要保住"选中的文字
// 里本来就有的分段"。页面上每个字形都带坐标、每个版面行的末尾也标了 line_end，所以这里按
// **几何 + 文本**两条信号判断每一处换行：
//   - 行间距明显更大（或下一行跳到页面更上方，说明换栏/换区域）→ 真分段，保留换行；
//   - 行尾是句末标点且间距略大、或下一行以项目符号/编号/章节标题开头 → 也按真分段；
//   - 其余情况是排版折行 → 合并：两侧都是中日韩字符就直接相接，否则补一个空格。
// **只动空白字符，绝不改动任何非空白字符**——引用定位与逐字核验都会先去掉空白（app/citation.py
// 的 normalize），因此合并折行不会让"逐字核验"失败。
const SELECTION_CJK=/[\u2e80-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]/;
const SELECTION_SENTENCE_END=/[.。！？!?；;：:…]["'”’）)】\]]?$/;
const SELECTION_BLOCK_START=/^\s*(?:[•·▪◦※*+\-–—]|\(?\d{1,2}[.)、]|[一二三四五六七八九十]{1,3}[、.]|第[一二三四五六七八九十百千\d]+[章节节])/;
function joinSelectionText(left,right){return (SELECTION_CJK.test(left.slice(-1))||SELECTION_CJK.test(right.charAt(0))?'':' ');}
function reflowSelection(pieces){
 // 先按 line_end 还原"版面行"，再用几何判断行与行之间是折行还是分段。
 const lines=[];
 for(const piece of pieces){
  if(!piece||!piece.text)continue;
  const current=lines[lines.length-1];
  if(current&&!current.closed){
   // 同一版面行里的字形片段：横向有明显空隙说明原文这里就是空格（两端对齐、分栏内的字距），
   // 空隙很小则是同一串字的切分，直接相接。中日韩字符之间不插空格。
   const gap=piece.left-current.right,pieceHeight=Math.max(1,piece.bottom-piece.top);
   current.text+=(gap>pieceHeight*.3?joinSelectionText(current.text,piece.text):'')+piece.text;
   current.bottom=Math.max(current.bottom,piece.bottom);current.right=Math.max(current.right,piece.right);
   current.closed=!!piece.lineEnd;
  }else{
   lines.push({text:piece.text,top:piece.top,bottom:piece.bottom,left:piece.left,right:piece.right,closed:!!piece.lineEnd});
  }
 }
 if(!lines.length)return '';
 const heights=lines.map(line=>Math.max(1,line.bottom-line.top)).sort((a,b)=>a-b);
 const lineHeight=heights[heights.length>>1];
 let out=lines[0].text;
 for(let i=1;i<lines.length;i++){
  const previous=lines[i-1],current=lines[i];
  const gap=current.top-previous.bottom;
  const columnJump=current.top<previous.top-lineHeight*.5;
  const paragraph=columnJump||gap>lineHeight*.55||(SELECTION_SENTENCE_END.test(previous.text)&&gap>lineHeight*.15)||SELECTION_BLOCK_START.test(current.text);
  out+=paragraph?'\n':joinSelectionText(previous.text,current.text);
  out+=current.text;
 }
 return out;
}
function captureSelection(){
 // 只在"框选识别/上传"这类本机工具占用阅读区时暂停取词；
 // 模型正在生成回答不算占用——用户可以一边等一边选文、加批注。
 if(state.crop||!state.pageData||state.toolBusy)return;
 const selection=window.getSelection(),layer=$('#text-layer');
 if(!selection?.rangeCount||!layer.contains(selection.anchorNode)||!layer.contains(selection.focusNode)||!selection.toString().trim())return;
 const box=$('#paper').getBoundingClientRect();
 const range=selection.getRangeAt(0);
 // 按字形片段取出精确选区：端点所在片段只取被选中的那一段，其余整段取用。
 // 顺带记下每个片段的位置与它是否是该版面行的末尾，供 reflowSelection 判断折行/分段。
 const pieces=[],rects=[];
 for(const span of Array.from(layer.children)){
  const node=span.firstChild;
  if(!node||!range.intersectsNode(span))continue;
  const raw=node.textContent,lineEnd=raw.endsWith('\n'),text=lineEnd?raw.slice(0,-1):raw;
  let from=0,to=text.length;
  if(range.startContainer===node)from=Math.max(0,Math.min(range.startOffset,text.length));
  else if(range.startContainer===span&&range.startOffset>0)from=text.length;
  if(range.endContainer===node)to=Math.max(0,Math.min(range.endOffset,text.length));
  else if(range.endContainer===span&&range.endOffset<1)to=0;
  if(from>=to)continue;
  const r=span.getBoundingClientRect();
  if(r.width<=0||r.height<=0)continue;
  pieces.push({text:text.slice(from,to),top:r.top,bottom:r.bottom,left:r.left,right:r.right,lineEnd:lineEnd&&to>=text.length});
  rects.push([Math.max(0,(r.left-box.left)/box.width),Math.max(0,(r.top-box.top)/box.height),Math.min(1,(r.right-box.left)/box.width),Math.min(1,(r.bottom-box.top)/box.height)]);
 }
 const text=reflowSelection(pieces).trim();
 setSelection(text,mergeSelectionRects(rects.filter(r=>r[2]>r[0]&&r[3]>r[1])));
}
function tab(name){document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));for(const t of ['chat','search','notes','annotations'])$('#'+t+'-tab').hidden=t!==name;}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>tab(b.dataset.tab));
$('#import').onclick=$('#start').onclick=()=>$('#file').click();
$('#file').onchange=async()=>{const file=$('#file').files[0];if(!file)return;if(file.size>100*1024*1024){toast('PDF 不能超过 100 MB');return;}if(state.busy){toast('请等待当前操作完成');return;}state.busy=true;$('#import').disabled=true;$('#start').disabled=true;toast('正在导入并建立全文索引…');const form=new FormData();form.append('file',file);try{const d=await api('/documents',{method:'POST',body:form});await loadLibrary();state.busy=false;await openDoc(state.docs.find(x=>x.id===d.id));toast(d.warning || (d.chunks?`已导入 ${d.pages} 页，建立 ${d.chunks} 个文本片段`:'已导入扫描文档，可框选区域识别文字'));}catch(e){toast(e.message);}finally{state.busy=false;$('#import').disabled=false;$('#start').disabled=false;$('#file').value='';}};
$('#prev').onclick=()=>go(state.page-1);$('#next').onclick=()=>go(state.page+1);$('#page-number').onchange=e=>go(e.target.value);
window.addEventListener('resize',scheduleReaderResize);
document.addEventListener('selectionchange',captureSelection);
document.addEventListener('pointerup',captureSelection);
$('#ask-selection').addEventListener('pointerdown',captureSelection);
$('#ask-selection').onclick=()=>{captureSelection();if(!state.selection){toast('请先选择原文，扫描件可使用框选识别');return;}tab('chat');if(!$('#question').value.trim())$('#question').value='请解释选中的这段原文，并结合上下文说明。';$('#question').scrollIntoView({block:'center',behavior:'smooth'});$('#question').focus();toast('已带入选中文字，可修改问题后发送');};$('#clear-selection').onclick=()=>{window.getSelection()?.removeAllRanges();setSelection('');};

let activeChat=null,switchingChat=false,resumeChatBody=null;
// 程序化发起一轮提问（"继续阅读"与 Phase 7 的"查证这个缺口"都走这里）。
// 关键是 `resumeChatBody`：它**必须**在表单提交时被真正取用，否则 gap_* 这类由后端定义的字段
// 会静默丢掉——用户点了"查证"却发出一轮普通提问，而界面上看不出任何异常。
function startProgrammaticChat(body){if(activeChat||state.busy)return false;if(state.doc?.id!==body.document_id){toast('请先返回原文献再继续。');return false;}resumeChatBody={...body,search_token:''};$('#question').value=body.query;updateSendButton();$('#chat-form').requestSubmit();return true;}
function resumeReading(body){startProgrammaticChat(body);}
async function stopAnswer(){if(!activeChat)return;const task=activeChat;task.stopped=true;task.controller.abort();$('#search-plan-dialog')?.close();updateSendButton();try{await api(`/chat/${task.id}/stop`,{method:'POST',signal:AbortSignal.timeout(5000)});}catch{toast('本机已停止等待；服务端取消未确认。');}}
$('#chat-form').onsubmit=async e=>{
 e.preventDefault();if(!requireDoc()||switchingChat)return;
 const q=$('#question').value.trim();if(activeChat&&!q){await stopAnswer();return;}if(!q||state.busy&&!activeChat)return;
 if(activeChat){switchingChat=true;const previous=activeChat;try{await stopAnswer();await previous.done;}finally{switchingChat=false;}}

 const task={id:crypto.randomUUID().replaceAll('-',''),controller:new AbortController(),stopped:false};task.done=new Promise(resolve=>task.resolve=resolve);activeChat=task; const body={...(resumeChatBody||payload(q)),request_id:task.id,voice_original:resumeChatBody?.voice_original||(typeof voiceOriginal==='string'?voiceOriginal:'')},rects=state.selectionRects;resumeChatBody=null;
 const sendRequest=(path,data)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data),signal:task.controller.signal});
 let questionMessage,pending,finishReadingProgress,failureMessage;state.busy=true;state.answering=true;$('#question').value='';updateSendButton();
 try{

  const quote={voice_original:body.voice_original,quote:body.selection,page:body.page,document_id:body.document_id,name:state.doc.name,mode:body.mode};
  window.getSelection()?.removeAllRanges();setSelection('');
  questionMessage=addMessage('user',q,[],quote);pending=addMessage('assistant','正在理解问题…');
  if(body.web){const plan=await sendRequest('/search-plan',body);if(plan.skip){body.search_token='';}else{body.search_token='';if(plan.review&&!await approveSearchPlan(plan)){body.web=false;}else body.search_token=plan.token||'';}}
  if(task.stopped)return;
  if(typeof trackReadingProgress==='function')finishReadingProgress=trackReadingProgress(task,pending);
  const result=await sendRequest('/chat',body);if(task.stopped)return;
  pending.remove();pending=null;addMessage('assistant',result.answer,result.sources,result.metadata);
  if(typeof voiceOriginal!=='undefined')voiceOriginal='';if(typeof autoSpeakAnswer==='function')autoSpeakAnswer(result.answer);
 }catch(err){failureMessage=err.message;if(!task.stopped)toast(err.message);}
 finally{
  finishReadingProgress?.();
  if(!questionMessage&&!task.stopped&&!$('#question').value.trim())$('#question').value=q;
  if(pending){pending.remove();const failed=addMessage('assistant',task.stopped?'已停止回答，已完成阅读批次保存在本机。':'回答未完成：'+(failureMessage||'可重新发送问题。'));
   const resume=el('button','secondary','继续阅读');resume.type='button';resume.onclick=()=>{if(activeChat||state.busy)return;failed.remove();questionMessage?.remove();resumeReading(body);};failed.append(resume);
  }
  if(activeChat===task){activeChat=null;state.busy=false;state.answering=false;updateSendButton();}
  task.resolve();
 }
};
document.querySelectorAll('[data-question]').forEach(b=>b.onclick=()=>{$('#question').value=b.dataset.question;$('#question').focus();updateSendButton();});
$('#search-form').onsubmit=async e=>{e.preventDefault();if(!requireDoc())return;const button=e.submitter;button.disabled=true;const id=state.doc.id;$('#search-results').textContent='正在检索…';try{const results=await post('/search',payload($('#search-query').value));if(state.doc.id!==id)return;$('#search-results').replaceChildren();if(!results.length)$('#search-results').append(el('p','hint','没有匹配原文。可尝试更具体的关键词，或对扫描页面进行框选识别。'));for(const r of results){const b=el('button','result');b.append(el('small','',`${r.name} · 第 ${r.page} 页`),el('span','',r.match||r.text.slice(0,450)));b.onclick=()=>cite(r,r.match||'');$('#search-results').append(b);}}catch(err){$('#search-results').textContent=err.message;}finally{button.disabled=false;}};
async function loadNotes(){if(!state.doc)return;const id=state.doc.id;const notes=await api(`/documents/${id}/notes`);if(id!==state.doc.id)return;$('#notes').replaceChildren();for(const n of notes){const d=el('div','note'),b=el('button','',`第 ${n.page} 页 ↗`);b.onclick=()=>go(n.page);d.append(b);if(n.quote)d.append(el('blockquote','',n.quote));d.append(el('p','',n.content));$('#notes').append(d);}if(!notes.length)$('#notes').append(el('p','hint','还没有笔记。记下第一个问题或发现。'));}
$('#note-form').onsubmit=async e=>{e.preventDefault();if(!requireDoc())return;const content=$('#note-content').value.trim();if(!content)return;const b=e.submitter;b.disabled=true;try{await post(`/documents/${state.doc.id}/notes`,{page:state.page,quote:state.selection,content});$('#note-content').value='';await loadNotes();toast('笔记已保存到本地');}catch(err){toast(err.message);}finally{b.disabled=false;}};
$('#export').onclick=()=>{if(requireDoc())window.location.href=`/api/documents/${state.doc.id}/export`;};
$('#crop').onclick=()=>{if(state.toolBusy||!state.pageData)return;window.getSelection()?.removeAllRanges();setSelection('');state.crop=!state.crop;$('#crop').classList.toggle('active',state.crop);$('#paper').classList.toggle('cropping',state.crop);if(state.crop)toast('在页面上拖动框选需要识别的区域');};
let startPoint=null;function point(e){const r=$('#paper').getBoundingClientRect();return {x:Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),y:Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))};}
$('#paper').onpointerdown=e=>{if(!state.crop||state.toolBusy||!state.pageData||e.button!==0)return;startPoint={...point(e),doc:state.doc.id,page:state.page};$('#paper').setPointerCapture(e.pointerId);e.preventDefault();};
$('#paper').onpointermove=e=>{if(!startPoint)return;const p=point(e);Object.assign($('#crop-box').style,{display:'block',left:Math.min(startPoint.x,p.x)*100+'%',top:Math.min(startPoint.y,p.y)*100+'%',width:Math.abs(startPoint.x-p.x)*100+'%',height:Math.abs(startPoint.y-p.y)*100+'%'});};
$('#paper').onpointercancel=()=>{startPoint=null;$('#crop-box').style.display='none';};
$('#paper').onpointerup=async e=>{if(!startPoint)return;const end=point(e),start=startPoint;startPoint=null;$('#crop-box').style.display='none';if(Math.abs(start.x-end.x)<.01||Math.abs(start.y-end.y)<.01)return;state.toolBusy=true;toast('正在识别所选区域…');try{const r=await post(`/documents/${start.doc}/ocr`,{page:start.page,x0:Math.min(start.x,end.x),y0:Math.min(start.y,end.y),x1:Math.max(start.x,end.x),y1:Math.max(start.y,end.y)});if(state.doc.id!==start.doc||state.page!==start.page)return;state.crop=false;$('#crop').classList.remove('active');$('#paper').classList.remove('cropping');setSelection(r.text_reflowed||r.text,[[Math.min(start.x,end.x),Math.min(start.y,end.y),Math.max(start.x,end.x),Math.max(start.y,end.y)]],{origin:'ocr'});tab('chat');openSelectionEditor();toast('文字已识别并整理成段；可直接在下方修改后发送');}catch(err){toast(err.message);}finally{state.toolBusy=false;}};
$('#library-home').onclick=$('#breadcrumb-home').onclick=()=>showWorkbench();
// 打开文献进入阅读态时，工作台的三类视图都不再是"当前页"，
// 侧边栏回到「全部文献」，避免回收站或 Zotero 一直保持高亮。
const openDocBeforeLibraryState=openDoc;
openDoc=async function(d){$('#library-home').classList.add('active');document.querySelectorAll('.library .nav[data-library-view]').forEach(b=>b.classList.remove('active'));await openDocBeforeLibraryState(d);};
// 前端要求的后端接口版本。后端 API_VERSION 比它小（或压根没有这个字段）就说明：
// 磁盘上的代码已经更新，但正在运行的后台进程还是旧的——本项目改的是后端，只刷新页面不够。
// 更麻烦的是：旧后台进程不会因为"重新双击图标"而消失（启动器只在没人应答时才拉起新进程），
// 所以这里把可行的做法一并写清楚。
const REQUIRED_API_VERSION = 46;
function staleBackendBanner(){
 let node=$('#stale-backend');
 if(!node){node=el('div');node.id='stale-backend';node.setAttribute('role','alert');document.body.append(node);}
 return node;
}
// 界面脚本本身是不是旧的：后端按静态文件修改时间给出当前版本号，页面载入时把当时的版本号
// 注入到 __ASSET_VERSION。两者不一致 = 浏览器还在跑缓存的旧 JS/CSS（改代码后只重启后台、
// 没有刷新页面时就会这样）。不说出来的话，用户只会看到"功能没生效"，然后在旧界面上反复尝试。
function staleFrontendBanner(){
 let node=$('#stale-frontend');
 if(!node){node=el('div');node.id='stale-frontend';node.setAttribute('role','alert');document.body.append(node);}
 return node;
}
function checkFrontendFreshness(serverVersion){
 const loaded=String(window.__ASSET_VERSION||'');
 if(!serverVersion||!loaded||serverVersion===loaded){$('#stale-frontend')?.remove();return;}
 const node=staleFrontendBanner();
 node.textContent='界面文件已经更新，但当前页面还在用载入时的旧脚本（'+loaded.slice(0,8)+' → '+String(serverVersion).slice(0,8)+'）。'
  +'请刷新页面（Ctrl+F5，或点这里刷新）后再试；只重启后台不会让已打开的页面换脚本。';
 node.style.cursor='pointer';
 node.onclick=()=>location.reload();
}
async function refreshModelStatus(){
  const s=await api('/status');
  $('#model-badge').textContent=s.ai?s.model:'未配置模型';
  $('#model-badge').title='点击配置会话与 RAG 模型';
  $('#privacy').textContent=s.ai?'已配置模型 · 相关原文将发送到所选服务':(s.embedding?'仅检索模式 · 使用已配置的语义检索服务':'本地 RAG · TF-IDF 向量 / 词项重排序 / OCR 已内置');
  checkFrontendFreshness(s.asset_version);
  const version=Number(s.api_version||0);
  if(version<REQUIRED_API_VERSION){
   staleBackendBanner().textContent='正在提供接口的后台仍是旧版本（接口 v'+version+'，当前程序需要 v'+REQUIRED_API_VERSION+'）。'
     +'旧后台进程不会因为重新双击图标而退出：请用新的启动器（勘读.exe）打开一次，它会先结束旧后台再启动新版本；'
     +'也可以在任务管理器里结束 pythonw.exe 后重新打开程序。只刷新页面不够。';
  }else{$('#stale-backend')?.remove();}
}
async function init(){
 // 独立译文窗口（多显示器用）：/translate-window 载入的是同一份页面与脚本，
 // 只多一个 __TRANSLATION_WINDOW 信号——这里据此直接打开指定的文献与页码，
 // 翻译功能看到同一个信号后会切到"只显示译文"的形态。
 const standalone=window.__TRANSLATION_WINDOW;
 try{
  await refreshModelStatus();
  if(standalone&&standalone.doc)document.body.classList.add('standalone-translation');
  await loadLibrary();
  if(standalone&&standalone.doc){
   const doc=state.docs.find(d=>d.id===standalone.doc);
   if(doc){await openDoc(doc);await go(Math.max(1,Number(standalone.page)||1));}
   else toast('这份文献已不在文献库中（可能已移入回收站）');
  }else await showWorkbench();
 }catch(e){toast(e.message);}
}

// Re-measure only the transparent selection overlay after the bundled UI font loads.
document.fonts.ready.then(()=>resizePage());

function resetReaderInteraction(){startPoint=null;state.crop=false;window.getSelection()?.removeAllRanges();setSelection('');$('#crop-box').style.display='none';$('#crop').classList.remove('active');$('#paper').classList.remove('cropping');}
function mergeSelectionRects(rects){
 const merged=[];
 for(const r of rects){const last=merged[merged.length-1];if(last&&Math.abs(last[1]-r[1])<.003&&Math.abs(last[3]-r[3])<.003&&r[0]<=last[2]+.012&&r[0]>=last[0]-.003){last[2]=Math.max(last[2],r[2]);last[1]=Math.min(last[1],r[1]);last[3]=Math.max(last[3],r[3]);}else merged.push([...r]);}
 return merged;
}

let selectionPointer=null;
$('#viewport').addEventListener('pointerdown',e=>{selectionPointer=e.button===0&&!state.crop&&!state.busy?{x:e.clientX,y:e.clientY}:null;});
$('#viewport').addEventListener('pointerup',e=>{const start=selectionPointer;selectionPointer=null;if(start&&!state.crop&&!state.busy&&Math.hypot(e.clientX-start.x,e.clientY-start.y)<4&&(!e.target.closest('#text-layer span')||!window.getSelection()?.toString().trim())){window.getSelection()?.removeAllRanges();setSelection('');}});

let readerResizeFrame=0;
function scheduleReaderResize(){cancelAnimationFrame(readerResizeFrame);readerResizeFrame=requestAnimationFrame(()=>{readerResizeFrame=0;resizePage();});}
let readerObservedWidth=0;
new ResizeObserver(entries=>{const width=entries[0].contentRect.width;if(Math.abs(width-readerObservedWidth)>.5){readerObservedWidth=width;scheduleReaderResize();}}).observe($('#viewport'));

function updateSendButton(){const stop=!!activeChat&&!$('#question').value.trim();const b=$('#send');b.textContent=stop?'■':'↑';b.title=stop?'停止回答':activeChat?'停止上一轮并发送新问题':'发送';b.setAttribute('aria-label',b.title);const changed=b.classList.contains('stop')!==stop;b.classList.toggle('stop',stop);if(changed&&b.animate&&!window.matchMedia('(prefers-reduced-motion: reduce)').matches)b.animate([{opacity:.35,transform:'scale(.75)'},{opacity:1,transform:'scale(1)'}],{duration:160});b.disabled=false;}
$('#question').addEventListener('input',updateSendButton);

// 后台随窗口关闭而退出：**长连接是主信号**——窗口一关连接立刻断开，后台马上知道；
// 心跳与 beacon 作兜底（浏览器会把后台/最小化页面的定时器限流到约每分钟一次甚至冻结，
// 只靠定时器会在窗口还开着的时候误判成"没人了"）。以前关掉窗口后这个本地服务会一直留着，
// 既没有托盘图标也不会退出，用户看不见也管不着。
// 想让后台常驻（例如自己在浏览器里长期使用），启动前设 READER_KEEP_SERVER=1。
let sessionStream=null;
function sessionConnect(){try{if(window.EventSource&&!sessionStream)sessionStream=new EventSource('/api/session/stream');}catch{}}
function sessionPing(){post('/session/ping',{}).catch(()=>{});}
function sessionClosed(){
 clearInterval(sessionTimer);
 try{sessionStream?.close();}catch{}
 try{navigator.sendBeacon('/session/close',new Blob(['close'],{type:'text/plain'}));}catch{}
}
sessionConnect();sessionPing();
const sessionTimer=setInterval(sessionPing,20000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)sessionPing();});
window.addEventListener('pagehide',sessionClosed);
window.addEventListener('beforeunload',sessionClosed);
