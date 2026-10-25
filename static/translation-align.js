// 原文 ↔ 译文双向定位与滚动同步（段级）。
//
// 边界（改这里之前先读）：
// - 这是**段级**对齐，不是句级：一段译文对应它在原文页上的一到多个版面框；
//   句级映射只存在于批注锚定（后端 map_sentences）。界面文案不得把它说成"逐句"。
// - 不重做翻译/对齐存储：段落与框来自后端既有的
//   `GET /api/documents/{id}/translation/pages/{page}`（每段都带 boxes/bbox/located）。
// - 不 monkey-patch translation.js 的全局函数，也不改它的翻译逻辑；只读它**显式导出**的
//   只读诊断入口 `window.translationDiagnostics.current()`，拿当前页的段落行数据。
// - 滚动同步默认关闭、不持久化；只做"译文栏 → 原文视口"单向，反向不驱动，天然不会回环。
// - 拿不到精确框时如实降级：先按段原文调既有引用定位接口再试一次；仍拿不到就只跳页并
//   toast 说明"只定位到页"，绝不编一个位置出来。
//
// 纯函数单独放在下面一段里：tests/translation_align.test.cjs 用 node:vm 抽取这一段做断言，
// 因此这一段不得引用 DOM 或全局状态。

// ===== 纯函数（tests/translation_align.test.cjs 抽取此段） =====
function alignDefaultEnabled(){
  // 同步滚动默认关闭，且不写 localStorage：刷新页面/重开窗口永远是关。
  return false;
}

// 段落所属页：后端行里目前不带 page（译文栏按页渲染），节点上的 data-page 是权威值；
// 两者都没有时退回"译文栏当前页"。不猜、不跨页。
function alignPageOf(row,fallbackPage){
  const page=Number(row&&row.page);
  if(Number.isFinite(page)&&page>0)return page;
  const fallback=Number(fallbackPage);
  return Number.isFinite(fallback)&&fallback>0?fallback:0;
}

// 归一化框夹到 [0,1]；退化的框（宽或高为 0）返回 null——画出来也看不见，还会干扰命中判断。
function alignClampBox(box){
  if(!box||typeof box.length!=='number'||box.length<4)return null;
  const values=[0,1,2,3].map(index=>{
    const value=Number(box[index]);
    return Number.isFinite(value)?Math.max(0,Math.min(1,value)):NaN;
  });
  if(values.some(Number.isNaN))return null;
  let [x0,y0,x1,y1]=values;
  if(x1<x0)[x0,x1]=[x1,x0];
  if(y1<y0)[y0,y1]=[y1,y0];
  return x1>x0&&y1>y0?[x0,y0,x1,y1]:null;
}

// 一段译文的全部原文框：优先后端的逐行 boxes（更精确），没有才用合并框 bbox。
function alignBoxesOf(row){
  if(!row)return [];
  const raw=Array.isArray(row.boxes)&&row.boxes.length?row.boxes:(row.bbox?[row.bbox]:[]);
  return raw.map(alignClampBox).filter(Boolean);
}

// 多个框的外接矩形：用于"整段在哪一块"的提示与滚动锚点。
function alignBoundingBox(boxes){
  const valid=(boxes||[]).map(alignClampBox).filter(Boolean);
  if(!valid.length)return null;
  return [Math.min(...valid.map(box=>box[0])),Math.min(...valid.map(box=>box[1])),
          Math.max(...valid.map(box=>box[2])),Math.max(...valid.map(box=>box[3]))];
}

function alignBoxAtPoint(box,x,y){
  const valid=alignClampBox(box);
  return !!valid&&x>=valid[0]&&x<=valid[2]&&y>=valid[1]&&y<=valid[3];
}

// 反向定位（点原文 → 找译段）：点在哪个框里就是哪一段。
// 重叠时优先正文段（页眉/页脚/注释的框常常包住正文边缘），再取面积更小的那一段。
function alignSegmentAtPoint(rows,x,y){
  let best=null;
  for(const row of rows||[]){
    if(!row)continue;
    for(const box of alignBoxesOf(row)){
      if(!alignBoxAtPoint(box,x,y))continue;
      const area=(box[2]-box[0])*(box[3]-box[1]);
      const body=!row.kind||row.kind==='body';
      if(!best
        ||(body&&!best.body)
        ||(body===best.body&&area<best.area)){
        best={row,area,body};
      }
    }
  }
  return best?best.row:null;
}

// 文本兜底：点到的字形所属的**行**文字（spans 的 bbox 是页面像素坐标）。
// 段级对齐失败时用它按"原文里这一行属于哪段"反查，比单字匹配可靠得多。
function alignSpanTextAtPoint(spans,width,height,x,y){
  if(!spans||!width||!height)return '';
  let best=null;
  for(const span of spans){
    const box=alignClampBox([span.bbox?.[0]/width,span.bbox?.[1]/height,
                             span.bbox?.[2]/width,span.bbox?.[3]/height]);
    if(!box||!alignBoxAtPoint(box,x,y))continue;
    const area=(box[2]-box[0])*(box[3]-box[1]);
    if(!best||area<best.area)best={area,text:span.text||''};
  }
  return best?best.text:'';
}

function alignNormalizeText(text){
  return String(text||'').replace(/\s+/g,'');
}

// 原文一行文字 → 它所在的译段。只接受足够长的行，避免"的""the"这种到处都有的匹配。
function alignRowForText(rows,lineText,minimumLength=4){
  const needle=alignNormalizeText(lineText);
  if(needle.length<minimumLength)return null;
  let fallback=null;
  for(const row of rows||[]){
    if(!row)continue;
    const source=alignNormalizeText(row.source);
    if(!source||!source.includes(needle))continue;
    if(!row.kind||row.kind==='body')return row;
    if(!fallback)fallback=row;
  }
  return fallback;
}

// 译文面板滚动到哪一段：第一段"还没完全滚出顶部"的段落（阈值默认落在顶部 15% 处）。
// items 需按 top 升序，形如 [{top,height}]。
function alignAnchorIndex(items,scrollTop,clientHeight,threshold=0.15){
  if(!items||!items.length)return -1;
  const line=Math.max(0,Number(scrollTop)||0)+Math.max(0,Number(clientHeight)||0)*threshold;
  for(let index=0;index<items.length;index+=1){
    const item=items[index];
    const top=Number(item.top)||0;
    const height=Number(item.height)||0;
    if(top+height>=line)return index;
  }
  return -1;
}

// 一个原文框 → 原文视口应滚到哪里：把框顶部放在视口约 25% 处（下方留出阅读上下文）。
function alignScrollTopForBox(box,paperHeight,viewportClientHeight,viewportScrollHeight,anchor=0.25){
  const valid=alignClampBox(box);
  const paper=Number(paperHeight)||0;
  const client=Number(viewportClientHeight)||0;
  if(!valid||paper<=0||client<=0)return null;
  const target=valid[1]*paper-client*anchor;
  const max=Math.max(0,(Number(viewportScrollHeight)||0)-client);
  return Math.min(max,Math.max(0,target));
}

// 没有可用框时的比例回退：译文滚到哪，原文视口按同一比例滚到哪。
function alignRatioScrollTop(panelScrollTop,panelScrollHeight,panelClientHeight,
                             viewportScrollHeight,viewportClientHeight){
  const panelMax=(Number(panelScrollHeight)||0)-(Number(panelClientHeight)||0);
  const viewMax=(Number(viewportScrollHeight)||0)-(Number(viewportClientHeight)||0);
  if(panelMax<=0||viewMax<=0)return null;
  const ratio=Math.min(1,Math.max(0,(Number(panelScrollTop)||0)/panelMax));
  return Math.min(viewMax,Math.max(0,ratio*viewMax));
}

// 同步滚动的目标：优先"顶部可见段落对应的原文位置"（稳定、跟阅读位置有关）；
// 该段及其后续都没框时（未定位/扫描页）退回比例；原文不可见（只看译文）时不动。
function alignScrollTarget(options){
  const options_=options||{};
  const viewportClientHeight=Number(options_.viewportClientHeight)||0;
  if(viewportClientHeight<=0)return {mode:'none',scrollTop:null,index:-1};
  const items=options_.items||[];
  const anchorIndex=alignAnchorIndex(items,options_.panelScrollTop,options_.panelClientHeight);
  const paperHeight=Number(options_.paperHeight)||0;
  const viewportScrollHeight=Number(options_.viewportScrollHeight)||0;
  const order=[];
  for(let index=Math.max(0,anchorIndex);index<items.length;index+=1)order.push(index);
  for(let index=Math.max(0,anchorIndex)-1;index>=0;index-=1)order.push(index);
  for(const index of order){
    const box=items[index]&&items[index].box;
    const scrollTop=alignScrollTopForBox(box,paperHeight,viewportClientHeight,viewportScrollHeight);
    if(scrollTop!==null)return {mode:'segment',scrollTop,index};
  }
  const fallback=alignRatioScrollTop(options_.panelScrollTop,options_.panelScrollHeight,
    options_.panelClientHeight,viewportScrollHeight,viewportClientHeight);
  return fallback===null?{mode:'none',scrollTop:null,index:-1}:{mode:'ratio',scrollTop:fallback,index:-1};
}

// 暂停判断：用户手动翻页/选文/滚原文之后 N 秒内，同步不接管原文视口。
function alignPaused(pausedUntil,now){
  const until=Number(pausedUntil);
  const current=Number(now);
  return Number.isFinite(until)&&Number.isFinite(current)&&until>current;
}

function alignShouldThrottle(lastRun,now,minimumInterval){
  const last=Number(lastRun);
  const current=Number(now);
  if(!Number.isFinite(last)||!Number.isFinite(current))return false;
  return current-last<Number(minimumInterval||0);
}

function alignKindLabel(row){
  const kind=row&&row.kind;
  if(kind==='note')return '注释/页脚小字';
  if(kind==='furniture')return '页眉或页脚';
  return '正文段落';
}

function alignToggleText(enabled){
  return enabled?'同步滚动：开':'同步滚动：关';
}
// ===== 纯函数结束 =====

(()=>{
'use strict';

const PANEL_ID='translation-panel';
const SYNC_MIN_INTERVAL=140;      // 滚动节流：再快没有意义，只会抖
const PAUSE_AFTER_MANUAL=2500;    // 手动翻页/选文/滚原文后，同步暂停 2.5 秒
const FLASH_MS=1600;
// 独立译文窗口（多显示器）里没有原文页：双向定位与同步滚动都在主窗口才成立。
const STANDALONE=!!window.__TRANSLATION_WINDOW;

let syncOn=alignDefaultEnabled();
let pausedUntil=0;
let frame=0;
let lastRun=-Infinity;
let applying=false;
let pinned=null;                  // {page, boxes}：点击定位后的持续高亮
let pinTimer=0;
let flashTimer=0;
let checkbox=null;
let live=null;

// ---- 读取当前页的段落行（只读诊断入口，不碰 translation.js 的内部状态） ----

function panel(){return document.getElementById(PANEL_ID);}
function paper(){return document.getElementById('paper');}
function viewport(){return document.getElementById('viewport');}

function translationData(){
  try{
    const diagnostics=window.translationDiagnostics;
    if(!diagnostics||typeof diagnostics.current!=='function')return null;
    return diagnostics.current();
  }catch(error){return null;}
}

function segments(){const data=translationData();return data&&Array.isArray(data.segments)?data.segments:[];}
function panelPage(){const data=translationData();return Number(data&&data.page)||Number(state.page)||0;}
function rowByIndex(index){
  const wanted=Number(index);
  return segments().find(row=>Number(row&&row.index)===wanted)||null;
}
function segmentsContainer(){const host=panel();return host?host.querySelector('.translation-segments'):null;}

function rowOfNode(node){
  const element=node&&node.nodeType===1?node:(node&&node.parentElement)||null;
  if(!element||typeof element.closest!=='function')return null;
  const item=element.closest('.translation-segment,.page-furniture-item');
  if(!item)return null;
  const row=rowByIndex(item.dataset.index);
  const page=Number(item.dataset.page)||0;
  return row?{item,row,page}:null;
}

// ---- live 区域与提示 ----

function announce(text){
  if(!live){
    live=el('div','translation-align-live');
    live.id='translation-align-live';
    live.setAttribute('role','status');
    live.setAttribute('aria-live','polite');
    document.body.append(live);
  }
  live.textContent=String(text||'');
}

// ---- 原文页上的两层高亮：hover（临时描边）与 pin（点击后持续） ----

function alignLayer(kind){
  const id=kind==='pin'?'translation-align-pin':'translation-align-hover';
  let node=document.getElementById(id);
  if(node)return node;
  const host=paper();
  if(!host)return null;
  node=el('div','translation-align-layer '+kind);
  node.id=id;
  host.append(node);
  return node;
}

function clearLayer(kind){
  const node=document.getElementById(kind==='pin'?'translation-align-pin':'translation-align-hover');
  if(node){node.replaceChildren();node.hidden=true;}
}

function paintBoxes(kind,boxes){
  const layer=alignLayer(kind);
  if(!layer)return false;
  layer.replaceChildren();
  const valid=(boxes||[]).map(alignClampBox).filter(Boolean);
  for(const box of valid){
    const mark=el('div','translation-align-box');
    Object.assign(mark.style,{left:box[0]*100+'%',top:box[1]*100+'%',
      width:(box[2]-box[0])*100+'%',height:(box[3]-box[1])*100+'%'});
    layer.append(mark);
  }
  layer.hidden=!valid.length;
  return valid.length>0;
}

function clearAllHighlights(){
  clearLayer('hover');clearLayer('pin');pinned=null;
}

function hoverRow(row){
  if(!row)return;
  // 只有段落与当前原文页一致时才能画框：不为了悬停去翻页（阅读位置由用户控制）。
  const loaded=panelPage();
  if(loaded&&Number(loaded)!==Number(state.page))return;
  const boxes=alignBoxesOf(row);
  if(!boxes.length){clearLayer('hover');return;}
  paintBoxes('hover',boxes);
}

function pinRow(page,boxes){
  pinned={page,boxes};
  paintBoxes('pin',boxes);
  clearLayer('hover');
  clearTimeout(pinTimer);
  // 持续高亮不该永久占着版面：一段时间后自动收起（用户可随时再点）。
  pinTimer=setTimeout(()=>{pinned=null;clearLayer('pin');},12000);
}

// ---- 译文 → 原文：点击一段译文，跳页 + 高亮对应原文 ----

function isInteractive(target){
  return !!(target&&target.closest&&target.closest(
    'button,a,summary,input,textarea,select,option,label,.translation-editor'));
}

function selectionBusy(){
  const selection=window.getSelection();
  return !!(selection&&!selection.isCollapsed&&String(selection.toString()).trim());
}

async function locateBySourceText(docId,page,source){
  // 后端逐段定位失败时的降级：用既有引用定位接口按"这一段原文"再找一次（只读，不新增路由）。
  const quote=String(source||'').slice(0,2000).trim();
  if(!quote)return {page:0,boxes:[]};
  try{
    const result=await post(`/documents/${docId}/pages/${page}/locate`,{quote});
    const boxes=Array.isArray(result&&result.boxes)
      ?result.boxes.map(row=>alignClampBox(row&&row.bbox)).filter(Boolean):[];
    return {page:Number(result&&result.page)||page,boxes};
  }catch(error){return {page:0,boxes:[]};}
}

function revealBoxesInViewport(boxes){
  const view=viewport();
  if(!view||!view.clientHeight)return;
  const target=alignScrollTopForBox(alignBoundingBox(boxes),paper()?.getBoundingClientRect().height||0,
    view.clientHeight,view.scrollHeight);
  if(target===null)return;
  applying=true;
  try{view.scrollTop=target;}finally{setTimeout(()=>{applying=false;},30);}
}

function isAnnotated(item){
  return !!(item&&item.classList&&item.classList.contains('has-annotations'));
}

async function locateTranslationRow(info,options){
  const settings=options||{};
  const row=info.row;
  const doc=state.doc;
  if(!doc)return;
  const docId=doc.id;
  let page=info.page||panelPage();
  if(!page){toast('这一段没有可定位的原文页');return;}
  if(page!==state.page){
    if(settings.jump===false)return;   // 带批注的段落：保持"点批注只弹批注"，不替用户翻页
    try{await go(page);}catch(error){toast('跳转到第 '+page+' 页失败：'+error.message);return;}
    if(!state.doc||state.doc.id!==docId||state.page!==page)return;
  }
  let boxes=alignBoxesOf(row);
  if(!boxes.length){
    const fallback=await locateBySourceText(docId,page,row.source);
    if(fallback.page&&fallback.page!==page&&settings.jump!==false){
      // 记录的页码偏了：既有定位接口在别的页找到了这段原文，跟着去真实页。
      try{await go(fallback.page);}catch(error){boxes=[];}
      if(state.doc&&state.doc.id===docId){page=fallback.page;boxes=fallback.boxes;}
    }else{
      boxes=fallback.page===page?fallback.boxes:[];
    }
  }
  if(!boxes.length){
    // 如实降级：只到页，不编位置。
    pinned=null;clearLayer('pin');
    toast(`只定位到第 ${page} 页：这一段没有可用的原文位置框（可能是扫描页，或段落与原文错位）`);
    announce(`只定位到第 ${page} 页，没有可用的原文位置`);
    return;
  }
  pinRow(page,boxes);
  // 带批注的段落点击本来就会弹批注：这里只补定位框，不滚动原文，避免打断阅读。
  if(settings.reveal!==false)revealBoxesInViewport(boxes);
  announce(`已定位到原文第 ${page} 页的${alignKindLabel(row)}（共 ${boxes.length} 处位置）`);
}

async function clickTranslationSegment(event){
  if(STANDALONE)return;    // 独立译文窗口没有原文页；保留它原有的"点批注弹批注"行为
  if(selectionBusy())return;
  const info=rowOfNode(event.target);
  if(!info||isInteractive(event.target))return;
  const annotated=isAnnotated(info.item);
  await locateTranslationRow(info,{reveal:!annotated,jump:!annotated});
}

// ---- 原文 → 译文：点原文页上的文字，译文栏滚到对应段并短暂高亮 ----

function paperPoint(event){
  const rect=paper()?.getBoundingClientRect();
  if(!rect||!rect.width||!rect.height)return null;
  return {x:(event.clientX-rect.left)/rect.width,y:(event.clientY-rect.top)/rect.height};
}

function flashSegment(index){
  const list=segmentsContainer();
  if(!list)return null;
  const node=list.querySelector('.translation-segment[data-index="'+String(index)+'"],'+
                               '.page-furniture-item[data-index="'+String(index)+'"]');
  if(!node)return null;
  const listRect=list.getBoundingClientRect(),nodeRect=node.getBoundingClientRect();
  list.scrollTop+=(nodeRect.top-listRect.top)-list.clientHeight*0.35;
  node.classList.remove('translation-align-flash');
  void node.offsetWidth;   // 重启动画
  node.classList.add('translation-align-flash');
  clearTimeout(flashTimer);
  flashTimer=setTimeout(()=>node.classList.remove('translation-align-flash'),FLASH_MS);
  return node;
}

function clickOriginalText(event){
  if(STANDALONE)return;                         // 独立译文窗口没有原文页
  const host=panel();
  if(!host||host.hidden)return;                 // 译文栏没开：不改变阅读区的点击语义
  if(state.crop||state.toolBusy||state.busy)return;
  if(selectionBusy())return;                    // 用户正在选文/校订
  const element=event.target instanceof Element?event.target:event.target?.parentElement||null;
  if(element&&element.closest('.annotation-mark'))return;   // 点批注走批注自己的交互
  // 段落行属于译文栏当前页；若用户刚翻页、新页数据还没到，宁可不动也不按旧段落猜位置。
  const loadedPage=panelPage();
  if(loadedPage&&Number(loadedPage)!==Number(state.page))return;
  const point=paperPoint(event);
  if(!point)return;
  const rows=segments();
  let row=alignSegmentAtPoint(rows,point.x,point.y);
  let method=row?'box':'';
  if(!row){
    // 段级框对不上时的文本兜底：用光标所在行的原文文字反查它属于哪一段。
    const data=state.pageData||{};
    const line=alignSpanTextAtPoint(data.spans,data.width,data.height,point.x,point.y);
    row=alignLineRow(line,rows);
    method=row?'text':'';
  }
  if(!row)return;                                // 点不到段：安静走过，不打扰阅读
  if(!flashSegment(row.index))return;
  announce(`译文栏已定位到${alignKindLabel(row)}（${method==='box'?'按版面框':'按原文行匹配'}）`);
}

function alignLineRow(line,rows){
  // 纯函数里只给出"按行文字找段"；DOM 侧保留最小包装，便于在测试里只测纯函数。
  return alignRowForText(rows,line,4);
}

// ---- 滚动同步（译文栏 → 原文视口，单向，天然无回环） ----

function viewportMetrics(){
  const view=viewport(),host=paper();
  if(!view||!host)return null;
  if(view.clientHeight<=0)return null;          // 原文被收起（只看译文）时不接管
  return {paperHeight:host.getBoundingClientRect().height,
          viewportScrollHeight:view.scrollHeight,viewportClientHeight:view.clientHeight,
          viewport:view};
}

function syncItems(){
  const list=segmentsContainer();
  if(!list)return [];
  const listRect=list.getBoundingClientRect(),items=[];
  for(const node of list.querySelectorAll('.translation-segment')){
    const info=rowOfNode(node);
    if(!info)continue;
    const row=info.row;
    if(row.kind&&row.kind!=='body')continue;    // 页眉/页脚不参与正文滚动同步
    const rect=node.getBoundingClientRect();
    items.push({index:Number(row.index),top:rect.top-listRect.top+list.scrollTop,
                height:rect.height,box:alignBoundingBox(alignBoxesOf(row))});
  }
  items.sort((left,right)=>left.top-right.top);
  return items;
}

function requestSync(){
  if(!syncOn||frame)return;
  if(alignPaused(pausedUntil,performance.now()))return;
  frame=requestAnimationFrame(()=>{
    frame=0;
    const now=performance.now();
    if(!syncOn||applying||alignPaused(pausedUntil,now))return;
    if(alignShouldThrottle(lastRun,now,SYNC_MIN_INTERVAL))return;
    lastRun=now;
    const list=segmentsContainer(),metrics=viewportMetrics();
    if(!list||!metrics)return;
    const target=alignScrollTarget({
      items:syncItems(),panelScrollTop:list.scrollTop,panelScrollHeight:list.scrollHeight,
      panelClientHeight:list.clientHeight,paperHeight:metrics.paperHeight,
      viewportScrollHeight:metrics.viewportScrollHeight,viewportClientHeight:metrics.viewportClientHeight});
    if(target.scrollTop===null)return;
    applying=true;
    metrics.viewport.scrollTop=target.scrollTop;
    requestAnimationFrame(()=>{applying=false;});
  });
}

function pause(){
  pausedUntil=performance.now()+PAUSE_AFTER_MANUAL;
}

// ---- 换页 / 面板开关时的清理与"用户手动翻页不被拽回" ----

function renderedNewPage(){
  // 点击定位可能"先跳页、再画框"：这里把待补画的框先取出来，清完再画回去，
  // 否则换页触发的清理会把刚定位好的高亮一起吞掉。
  const pending=pinned&&pinned.page===state.page?pinned:null;
  clearAllHighlights();
  pause();
  if(pending){pinned=pending;paintBoxes('pin',pending.boxes);}
}

function onPanelHidden(){
  if(!panel()?.hidden)return;
  clearAllHighlights();
}

// ---- 开关（默认关；不写 localStorage，刷新即关） ----

function setSync(enabled,options){
  syncOn=!!enabled;
  if(checkbox)checkbox.checked=syncOn;
  const text=alignToggleText(syncOn);
  if(options&&options.silent)return text;
  announce(syncOn
    ?'已开启同步滚动：滚动译文栏时原文页会跟到对应段落位置；手动翻页、选文或滚动原文后暂停 2.5 秒。'
    :'已关闭同步滚动：译文栏滚动不再带动原文页。');
  toast(text+(syncOn?'：滚动译文栏时原文页跟随（手动翻页/选文暂停 2.5 秒）':'：译文栏与原文各自滚动'));
  return text;
}

function ensureToggle(host){
  const bar=host.querySelector('.translation-statusbar');
  if(!bar){checkbox=null;return;}
  const label=el('label','translation-sync');
  label.id='translation-sync-row';
  label.title='开启后，滚动译文栏会让原文页跟到对应段落位置；默认关闭，不记忆。手动翻页、选文或滚动原文后暂停 2.5 秒。';
  checkbox=document.createElement('input');
  checkbox.type='checkbox';
  checkbox.id='translation-sync-scroll';
  checkbox.checked=syncOn;
  checkbox.addEventListener('change',()=>setSync(checkbox.checked));
  label.append(checkbox,el('span','','同步滚动'));
  if(STANDALONE){
    // 没有原文页可同步：把开关显示为不可用并说明原因，而不是给一个勾了也没用的控件。
    checkbox.disabled=true;
    label.title='独立译文窗口没有原文页：同步滚动与双向定位请在主窗口使用';
  }
  const fontGroup=bar.querySelector('.translation-font');
  if(fontGroup)bar.insertBefore(label,fontGroup);else bar.append(label);
}

// ---- 初始化 ----

function bindPanel(host){
  ensureToggle(host);
  const list=host.querySelector('.translation-segments');
  if(list){
    list.addEventListener('click',event=>{clickTranslationSegment(event).catch(error=>toast(error.message));});
    list.addEventListener('mouseover',event=>{const info=rowOfNode(event.target);if(info)hoverRow(info.row);});
    list.addEventListener('mouseout',event=>{
      const info=rowOfNode(event.target);
      if(!info)return;
      const related=event.relatedTarget;
      if(related&&info.item.contains(related))return;
      clearLayer('hover');
    });
    list.addEventListener('scroll',requestSync,{passive:true});
  }
}

let attempts=0;
function init(){
  const host=panel();
  if(!host){
    attempts+=1;
    if(attempts<40)setTimeout(init,200);
    return;
  }
  bindPanel(host);
  // 原文页文字层：只在译文栏打开时响应点击（不改变关闭时的阅读交互）。
  const textLayer=document.getElementById('text-layer');
  if(textLayer)textLayer.addEventListener('click',clickOriginalText);
  // 用户手动操作原文 → 暂停同步，避免"我翻页它把我拽回去"。
  const view=viewport();
  if(view){
    for(const type of ['pointerdown','wheel','touchstart','keydown']){
      view.addEventListener(type,pause,{passive:true});
    }
  }
  // 用户选文（原文或译文）→ 暂停同步。
  document.addEventListener('selectionchange',()=>{
    if(selectionBusy()){clearLayer('hover');pause();}
  });
  // 换页：清理高亮 + 暂停同步（手动翻页与程序跳页都适用；同步随后会自然恢复）。
  const image=document.getElementById('page-image');
  if(image&&typeof MutationObserver==='function'){
    new MutationObserver(renderedNewPage).observe(image,{attributes:true,attributeFilter:['src']});
  }
  // 收起译文栏时清掉原文页上的临时框。
  if(typeof MutationObserver==='function'){
    new MutationObserver(onPanelHidden).observe(host,{attributes:true,attributeFilter:['hidden']});
    // 译文重画（批注变化、重新翻译、换页）会替换段落节点：旧的悬停描边必须跟着消失，
    // 否则鼠标早已离开、页面上却残留一个框。
    const list=host.querySelector('.translation-segments');
    if(list)new MutationObserver(()=>clearLayer('hover')).observe(list,{childList:true});
  }
  // 供真实浏览器探针/文档示例使用的最小只读入口（不暴露内部结构）。
  window.translationAlignment={
    enabled:()=>syncOn,
    setEnabled:value=>setSync(!!value),
    pause:()=>pause(),
    rowIndexAtPaperPoint:(x,y)=>{
      const row=alignSegmentAtPoint(segments(),Number(x),Number(y));
      return row?Number(row.index):null;
    },
    clear:()=>clearAllHighlights(),
  };
}

init();
})();
