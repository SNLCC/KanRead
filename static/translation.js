// 翻译功能的前端：阅读区右侧的译文栏、翻译服务设置、选文翻译与整篇后台翻译。
// 只使用本项目自己的接口与静态资源；译文只在本机保存与读取。
//
// 与阅读区的边界：本文件不改 app.js / reading.js 的既有逻辑，只在它们提供的挂点上接进来
// （pageTranslation 回调、右键菜单与选文操作条各加一项）。
(()=>{
'use strict';

// 译文的呈现方式：**在阅读区右侧打开一栏译文**（用户要求），译文按原段落排版，
// 与原文页并排各自滚动；中间的分栏条可拖动改宽度，宽度记在本机。
// 只有"打开/收起"两种状态——底部对照与整页替换那两套已按用户要求删除。
const RATIO_KEY='reader-translation-width';
// 译文栏**不记忆"上次开着"**（用户明确要求）：打开文献时默认不翻译、也不开译文栏，
// 否则每次打开文件都会自动翻整页，白花 token。要看得自己点工具栏的「⇄ 翻译整页」。
// used 只活在这一次页面会话里。
let config=null;
let used=false;
let onlyTranslation=false;
// 译文字号：**绝对 px，由用户定**（用户第五次反馈："字号不必一样大了……字号调整目前无效，
// 让其生效即可"）。上一版是按"原文正文字号 × 原文显示比例"算出来的百分比，基准只有 7px 左右，
// 每档 10% 才 0.7px，点了看不出变化——等于没生效。现在直接就是字号本身，一档 1px。
const FONT_KEY='reader-translation-font';
const FONT_MIN=10,FONT_MAX=32,FONT_DEFAULT=15,FONT_STEP=1;
let fontPx=Math.max(FONT_MIN,Math.min(FONT_MAX,Math.round(Number(localStorage.getItem(FONT_KEY))||FONT_DEFAULT)));
let ratio=Number(localStorage.getItem(RATIO_KEY))||0.45;
let current={docId:'',page:0,data:null,segments:[],loading:false,error:'',status:'',service:'',
             pageSize:null,pageHeight:842,contentWidth:620,
             // 页眉页脚段数（状态行要显示）；后端还给出栏数与正文字号，界面上不再用它们排版。
             furniture:0};

const panel=el('section','translation-panel');panel.id='translation-panel';panel.hidden=true;
const head=el('div','translation-head');
const title=el('div','translation-title');
const titleText=el('b','','译文');
const stateText=el('span','translation-state','');
title.append(titleText,stateText);
const actions=el('div','translation-actions');
const translateButton=el('button','secondary','翻译这一页');
translateButton.id='translation-page-action';
const retranslateButton=el('button','secondary','重新翻译');
retranslateButton.title='用「设置 · 翻译」里当前选的服务重译这一页，覆盖已有译文（含人工修改）';
const wholeButton=el('button','secondary','整篇后台翻译');
const copyButton=el('button','secondary','复制译文');
const detachButton=el('button','secondary','独立窗口');
detachButton.title='把译文放到另一个窗口（多显示器）；主窗口翻页时它跟着走';
// 页眉页脚/注释默认不翻译（用户反馈它们曾被当成正文翻掉）。这个按钮把这一页一起带上。
const furnitureButton=el('button','secondary','连同页眉页脚');
furnitureButton.title='把这一页的页眉、页脚与注释也一起翻译';
furnitureButton.hidden=true;
const closeButton=el('button','','收起');
closeButton.title='收起译文栏（译文仍保存在本机）';
// 「收起原文」只在工具栏上放一个（用户反馈：两处重复）。这里不再放同名按钮。
actions.append(translateButton,retranslateButton,wholeButton,copyButton,detachButton,furnitureButton,closeButton);
head.append(title,actions);

const statusNode=el('p','translation-status','');
// 状态行右侧是译文字号控件（用户要求"译文应当允许单独调整字号"）。
// 现在直接显示**字号本身**（px）：点一下能看见字变大变小，不再是一个"百分比"。
const fontGroup=el('div','translation-font');
const fontDown=el('button','secondary','A−'),fontValue=el('button','secondary',String(FONT_DEFAULT)),fontUp=el('button','secondary','A+');
fontDown.title='译文调小 1px';fontUp.title='译文调大 1px';
fontValue.title=`点一下回到默认字号（${FONT_DEFAULT}px）`;
fontGroup.append(el('span','translation-font-label','字号'),fontDown,fontValue,fontUp);
const statusBar=el('div','translation-statusbar');
statusBar.append(statusNode,fontGroup);
const segmentList=el('div','translation-segments');
const splitter=el('div','translation-splitter');
splitter.tabIndex=0;splitter.setAttribute('role','separator');splitter.setAttribute('aria-orientation','vertical');
splitter.setAttribute('aria-label','调整译文栏宽度');
// 抓手用 CSS 画一条细线（与批注窗口的抓手同一做法），不引入新的字形。
splitter.append(el('span',''));
panel.append(head,splitter,statusBar,segmentList);
// 译文栏是**页面右侧的一栏**：应该插在阅读区的横向容器 `.reader-body` 末尾
// （experience.js 里才把缩略图、目录面板与 #viewport 放进那个容器）。
// 但脚本加载顺序上 translation.js 在 experience.js 之前，所以首次挂载时它还不存在，
// 只能先挂到 .reader，等 .reader-body 出现后再搬进去——用 appendChild 搬家不会丢事件绑定。
let readerArea=document.querySelector('.reader-body');
const readerHost=readerArea||document.querySelector('.reader')||$('#viewport').parentElement;
readerHost.append(panel);
if(!readerArea&&typeof MutationObserver==='function'){
  const waitForReaderBody=new MutationObserver(()=>{
    const found=document.querySelector('.reader-body');
    if(!found)return;
    found.append(panel);          // appendChild 语义：从旧父节点移走
    readerArea=found;
    applyRatio();
    waitForReaderBody.disconnect();
  });
  waitForReaderBody.observe(document.querySelector('.reader')||document.body,{childList:true,subtree:true});
}

// 工具栏按钮（用户要求的位置：缩放 / 框选识别那一组之后）。
const toolbarButton=el('button','','⇄ 翻译整页');
toolbarButton.id='translate-page';
toolbarButton.title='在右侧打开这一页的译文（按原段落排版）；再点一次收起译文栏';
toolbarButton.onclick=()=>setView(used?'source':'both');
const originalButton=el('button','','收起原文');
originalButton.id='toggle-original';
// 一直显示、译文栏没开时只是不可用：上一版用 hidden 藏起来，用户直接说"没有收起原文了"。
originalButton.disabled=true;
originalButton.title='只看译文（收起左侧的原文页）；再点一次把原文放回来';
originalButton.onclick=()=>setOnlyTranslation(!onlyTranslation);
const toolbarGroup=$('#toolbar > div:last-child');
toolbarGroup.append(toolbarButton,originalButton);

function persist(){localStorage.setItem(RATIO_KEY,String(ratio));}
function markUsed(){used=true;}

function applyRatio(){
  ratio=Math.max(.2,Math.min(.6,ratio));
  // 宽度直接写成 flex-basis（面板是 .reader-body 的 flex 子项，见 style.css）。
  panel.style.flexBasis=(ratio*100).toFixed(2)+'%';
  splitter.setAttribute('aria-valuenow',String(Math.round(ratio*100)));
  splitter.setAttribute('aria-valuemin','20');splitter.setAttribute('aria-valuemax','60');
  // 译文整页的最小高度按栏宽算，所以把可用宽度记下来。
  // （applyRatio 在模块初始化时就会被 syncPanelVisibility 调到，那时 current 还没建，
  //  所以这里包一层——宽度在 renderPanel 之前还会再算一次。）
  try{
    current.contentWidth=Math.max(240,Math.round((readerArea||panel).clientWidth*(onlyTranslation?1:ratio))-76);
  }catch(error){/* 初始化早期：下一次 renderPanel 之前会再算 */}
}

// 译文字号（用户要求"译文应当允许单独调整字号"）：**直接就是 px**，一档 1px，
// 点了立刻看得见变化。只改译文栏的字号变量，不动原文页，也不重建段落节点（重建会丢滚动位置）。
function setFontPx(next){
  fontPx=Math.max(FONT_MIN,Math.min(FONT_MAX,Math.round(next)));
  localStorage.setItem(FONT_KEY,String(fontPx));
  applyFont();
}
function applyFont(){
  const page=segmentList.querySelector('.translation-page');
  if(page)applyPageVars(page);
  else updateFontLabel();
}
function updateFontLabel(){
  fontValue.textContent=String(fontPx);
  fontValue.classList.toggle('active',fontPx!==FONT_DEFAULT);
  fontValue.title=`当前译文字号 ${fontPx}px（默认 ${FONT_DEFAULT}px；点一下回到默认）`;
}
fontDown.onclick=()=>setFontPx(fontPx-FONT_STEP);
fontUp.onclick=()=>setFontPx(fontPx+FONT_STEP);
fontValue.onclick=()=>setFontPx(FONT_DEFAULT);

// 「翻译这一页」在请求期间换成「正在翻译……」，翻完恢复（用户要求的：点了要有状态变化）。
function setBusy(next){
  translateButton.disabled=!!next;
  translateButton.textContent=next?'正在翻译……':'翻译这一页';
  translateButton.classList.toggle('busy',!!next);
}

// 「只看译文」：把原文页整块收起来（用户要求"也要能收起原文"）。
// 按钮只在工具栏上有一个（曾经在译文栏头部也放了一个，用户指出重复）。
function setOnlyTranslation(next){
  onlyTranslation=!!next&&used;
  document.body.classList.toggle('translation-only',onlyTranslation);
  originalButton.textContent=onlyTranslation?'显示原文':'收起原文';
  originalButton.classList.toggle('active',onlyTranslation);
  originalButton.disabled=!used;
  if(typeof scheduleReaderResize==='function')scheduleReaderResize();
}

function syncPanelVisibility(){
  panel.hidden=!used;
  toolbarButton.classList.toggle('active',used);
  toolbarButton.textContent=used?'⇄ 收起译文':'⇄ 翻译整页';
  document.body.classList.toggle('translation-open',used);
  if(!used)setOnlyTranslation(false);else setOnlyTranslation(onlyTranslation);
  if(used){applyRatio();renderPanel();}
}

function setView(next){
  if(next==='source'){used=false;}else{markUsed();}
  persist();syncPanelVisibility();
  // 只**读取**已保存的译文，不自动发起翻译（用户要求：不要一打开就翻整页、白花 token）。
  if(used)load({silent:false});
  if(typeof scheduleReaderResize==='function')scheduleReaderResize();
}

let dragging=false;
splitter.onpointerdown=event=>{
  if(event.button!==0)return;event.preventDefault();dragging=true;
  splitter.setPointerCapture(event.pointerId);splitter.dataset.dragging='1';
  document.body.classList.add('resizing-panel');
};
splitter.onpointermove=event=>{
  if(!dragging)return;
  const box=(readerArea||panel.parentElement).getBoundingClientRect();
  if(box.width<80)return;
  // 面板在右侧：宽度 = 右边界到指针的距离。
  ratio=(box.right-event.clientX)/box.width;applyRatio();
};
function finishDrag(){
  if(!dragging)return;
  dragging=false;delete splitter.dataset.dragging;
  document.body.classList.remove('resizing-panel');persist();
  if(typeof scheduleReaderResize==='function')scheduleReaderResize();
}
splitter.onpointerup=splitter.onpointercancel=finishDrag;
splitter.onkeydown=event=>{
  if(!['ArrowLeft','ArrowRight'].includes(event.key))return;
  event.preventDefault();
  ratio+=(event.key==='ArrowLeft'?-0.03:0.03);applyRatio();persist();
  if(typeof scheduleReaderResize==='function')scheduleReaderResize();
};

// ---------------------------------------------------------------- 页面渲染

function setStatus(text,kind){stateText.textContent=text||'';stateText.className='translation-state'+(kind?' '+kind:'');}

function shorten(text,limit){
  const value=String(text||'');
  return value.length>limit?value.slice(0,limit)+'…':value;
}

// 段落状态的徽标：只在"真的发生了什么"时才加标签。
function segmentBadges(row){
  const marks=el('div','translation-segment-marks');
  const kind=row.state==='missing'?'未翻译'
    :row.state==='stale'?'原文已变，待重新翻译'
    :row.edited?'已人工修改'
    :'机器译文';
  marks.append(el('span','translation-badge badge-'+row.state,kind));
  if(row.service)marks.append(el('span','translation-badge badge-service',row.service));
  // 用户反映"段落有时候会从中间截断"：真因是翻译请求没给输出预算、被服务端默认上限截断，
  // 而服务端常常仍返回 finish_reason='stop'（否则我们会直接报错）。这里把"看起来没翻完"标出来，
  // 让用户知道该重译这一段、并去「模型服务」把这个平台的输出预留调大。
  if(row.truncated){
    const badge=el('span','translation-badge badge-truncated','可能没翻完');
    badge.title='这段译文比原文短很多、也没有句末标点：多半是服务端的输出上限截断了。'
      +'点这一段的「重新翻译这一段」重试；仍是这样就到「模型服务」把该平台的输出预留调大。';
    marks.append(badge);
  }
  return marks;
}

function segmentTools(item,paragraph,row){
  const tools=el('div','translation-segment-tools');
  const entries=[];
  if(row.text)entries.push(['复制译文',()=>navigator.clipboard.writeText(row.text)]);
  if(row.text&&row.id)entries.push(['修改译文',()=>openEditor(item,paragraph,row)]);
  if(row.text&&row.edited&&row.id)entries.push(['恢复机器译文',()=>restore(row)]);
  // 重译这一段要看得见状态：点了变「翻译中……」，回来再变回去（用户要求：
  // 否则不知道到底有没有在翻）。翻完会重画译文栏，这个按钮节点随之被替换掉。
  entries.push([row.text?'重新翻译这一段':'翻译这一段',()=>retranslate(row),'翻译中……']);
  for(const [label,action,busy] of entries){
    const button=el('button','secondary',label);
    button.onclick=()=>busy?withBusy(button,busy,action):action();
    tools.append(button);
  }
  return tools;
}

// 按钮上的"正在做"状态：文字换成 busy 文案并禁用，结束后（无论成败）恢复。
async function withBusy(button,busy,action){
  const original=button.textContent;
  button.disabled=true;
  button.textContent=busy;
  try{await action();}
  finally{button.disabled=false;button.textContent=original;}
}

// 译文正文的排版（定稿）：**单栏流动 + 用户自己定的字号**。
//
// 六轮下来试过并否掉的四种做法，都记在这里，免得以后再走一遍：
//   1) 一块一块的卡片 —— "不是一块一块的"；
//   2) 每段绝对定位回它在原页的格子里 —— 译文比原文长，只能把字号压到 7px，下推还会重叠；
//   3) 用原文的行高当字号 —— 行高比字号大，"字号和原文并不一致"；
//   4) 用原文正文字号 × 原文显示比例（号称与原文一致）+ 分栏 —— "字号不必一样大了，
//      因为即使双栏排版也不完全一样"，而且分栏会把长段落从中间劈开（"段落有时候会从中间截断"）。
// 现在的规则最简单：一栏连续排版、段落按阅读顺序流动、字号由用户设（A−/数值/A+）。
// "这一段对应原文的哪一块"由**悬停高亮**负责（用户要求保留的效果，见 highlightSource）。
// 页眉/页脚与注释**不参与正文流动**：它们单独排在整页的开头/结尾，默认不翻译（用户反馈它们
// 曾被当成正文翻掉），要翻可以单点那一段，或用头部的「连同页眉页脚」把这一页一起翻。
function renderSegments(segments){
  // 记住滚动位置：批注变化会触发重画（译文上要补/去批注标记），不能让读者被弹回页首。
  const keepScroll=segmentList.scrollTop;
  segmentList.replaceChildren();
  const page=el('div','translation-page');
  applyPageVars(page);
  const furniture=segments.filter(row=>row.kind==='furniture');
  const notes=segments.filter(row=>row.kind==='note');
  const body=segments.filter(row=>!row.kind||row.kind==='body');
  const pageNotes=pageNotesBar();
  if(pageNotes)page.append(pageNotes);
  if(furniture.length)page.append(furnitureBlock('页眉/页脚（按原文位置，默认不翻译）','page-furniture',furniture));
  const flow=el('div','translation-flow');
  // 超长段落会被切开成几段（避免单次请求过大），切出来的几段带同一个 `para`：
  // 这里把它们装进同一个容器里，界面上仍然是**一个自然段**，而不是"被分成两部分翻译"
  // （用户反馈："段落中间阶段分为两部分翻译的情况"）。
  let group=null,groupPara=null;
  for(const row of body){
    const para=row.para===undefined?row.index:row.para;
    if(!group||para!==groupPara){
      group=el('div','translation-paragraph');
      groupPara=para;
      flow.append(group);
    }
    group.append(segmentNode(row));
  }
  page.append(flow);
  if(notes.length)page.append(furnitureBlock('注释与页脚小字（按原文位置，默认不翻译）','page-notes',notes));
  segmentList.append(page);
  segmentList.scrollTop=keepScroll;
}

// 把版面参数写到整页上（字号、按原页比例的最小高度）。
// renderSegments 与 pageTranslationLayout 共用它：窗口尺寸/用户改字号时只更新变量、不重建节点。
function applyPageVars(page){
  const geometry=pageGeometry();
  page.style.setProperty('--page-font',geometry.font.toFixed(2)+'px');
  page.style.setProperty('--page-columns','1');
  page.style.minHeight=Math.round(geometry.pageHeight)+'px';
  updateFontLabel();
}

// 一段译文（含"对应原文"、状态徽标、单段工具）；正文与页眉页脚共用同一个节点结构。
function segmentNode(row){
  const item=el('article','translation-segment state-'+row.state+(row.edited?' edited':''));
  item.dataset.index=String(row.index);
  if(row.id)item.dataset.segmentId=row.id;
  const paragraph=el('p','translation-text',row.text||'（还没有译文）');
  const source=el('details','translation-source-text');
  source.append(el('summary','','对应原文'),el('p','',shorten(row.source,600)));
  const marks=segmentBadges(row);
  if(row.legacy)marks.append(el('span','translation-badge badge-unchanged','旧版术语替换，不是译文'));
  item.append(marks);
  // 这一段的批注（锚点在原文）：在译文上按**原文的样式**标出来，并且点得开（用户反馈第 2、3 条）。
  const notes=annotationsForRow(row);
  if(notes.length)markAnnotations(item,paragraph,notes);
  item.append(paragraph,source,segmentTools(item,paragraph,row));
  // 悬停时在原文页上高亮对应区域：两栏之间靠它对应起来（不滚动原文，避免打乱阅读位置）。
  item.onmouseenter=()=>highlightSource(row);
  item.onmouseleave=()=>highlightSource(null);
  return item;
}

// 译文段落上的批注标记：**沿用原文那一套样式**（用户第五次反馈："译文的批注高亮或下划线等
// 显示方式和原文不同，应当采取原文的方式"）。原文用的是 #annotation-layer 里的色块/下划线/
// 侧线/区域框（style.css 的 .annotation-mark.mark-*），这里用同一份色板和同样的四种样式，
// 只是画在译文文字自己身上（译文里没有"文字下面的图层"，所以用背景色而不是叠加半透明色块）。
const ANNOTATION_MARK_COLORS={yellow:'#ebc743',green:'#74ba6a',blue:'#66aadb',
                              pink:'#e58cac',purple:'#b083d2',orange:'#efaa57'};

function markAnnotations(item,paragraph,notes){
  const colorOf=annotation=>ANNOTATION_MARK_COLORS[annotation.color]||ANNOTATION_MARK_COLORS.yellow;
  const classOf=annotation=>({
    highlight:'has-mark-highlight',underline:'has-mark-underline',
    margin:'has-mark-margin',region:'has-mark-region',
  }[annotationKindOf(annotation)]||'has-mark-highlight');
  const visual=notes.filter(annotation=>(annotation.rects||[]).length);
  // 一部分批注记着"用户真正选中的那一小段译文"（translation_excerpt）：**只标那一小段**，
  // 而不是整段——用户明确要求"可以对其中部分内容进行批注"。没有这个字段的批注（在原文页上
  // 选的那种）由后端按句级对齐映射出译文里对应的一段，界面用 ``bilingualExcerpt`` 取回来；
  // 两边都拿不到才整段标。
  const text=paragraph.textContent||'';
  const spans=[];
  const whole=[];
  for(const annotation of visual){
    const excerpt=excerptFor(annotation,text);
    const spot=excerpt?findExcerpt(text,excerpt):null;
    if(!spot){whole.push(annotation);continue;}
    const range={start:spot.start,end:spot.end,annotation};
    // 与已经标出来的那片**重叠**时不再标：这种情况只是这一小段被两条批注覆盖，
    // 整段标出来反而会变成"把不该标的地方也标上了"（用户报过）。点击这一段仍能看到两条批注。
    if(spans.some(other=>range.start<other.end&&range.end>other.start))continue;
    spans.push(range);
  }
  if(spans.length){
    spans.sort((left,right)=>left.start-right.start);
    const nodes=[];
    let cursor=0;
    for(const range of spans){
      if(range.start>cursor)nodes.push(document.createTextNode(text.slice(cursor,range.start)));
      const mark=el('span','translation-mark '+classOf(range.annotation));
      mark.style.setProperty('--mark',colorOf(range.annotation));
      mark.style.setProperty('--mark-line',colorOf(range.annotation));
      mark.textContent=text.slice(range.start,range.end);
      mark.title=shorten(range.annotation.content||'仅标记，无文字批注',80);
      nodes.push(mark);
      cursor=range.end;
    }
    if(cursor<text.length)nodes.push(document.createTextNode(text.slice(cursor)));
    paragraph.replaceChildren(...nodes);
  }
  if(whole.length){
    // 整段标记：一段上可能同时有高亮和下划线（两条批注），两个变量互不覆盖。
    const pick=kind=>whole.find(annotation=>annotationKindOf(annotation)===kind);
    const highlight=pick('highlight'),underline=pick('underline');
    const margin=pick('margin'),region=pick('region');
    const box=highlight||margin||region;
    if(box)paragraph.style.setProperty('--mark',colorOf(box));
    if(underline)paragraph.style.setProperty('--mark-line',colorOf(underline));
    if(highlight)paragraph.classList.add('has-mark-highlight');
    if(underline)paragraph.classList.add('has-mark-underline');
    if(margin)paragraph.classList.add('has-mark-margin');
    if(region)paragraph.classList.add('has-mark-region');
  }
  if(!visual.length)return;                       // 只有整页批注：页首另有入口
  item.classList.add('has-annotations');
  item.title=notes.map(annotation=>
    `${annotationStylesOf(annotation)}：${shorten(annotation.content||'仅标记，无文字批注',80)}`).join('\n');
  item.onclick=event=>{
    if(event.target.closest('button'))return;     // 改译文/重新翻译这些按钮照常工作
    const selection=window.getSelection();
    if(selection&&selection.toString().trim())return;   // 正在选文，不要弹窗打断
    openAnnotations(notes);
  };
}

function annotationKindOf(annotation){
  if(typeof annotationKind==='function')return annotationKind(annotation);
  return annotation.style||((annotation.rects||[]).length?'highlight':'page');
}
function annotationStylesOf(annotation){
  const kind=annotationKindOf(annotation);
  if(typeof annotationStyles==='object'&&annotationStyles)return annotationStyles[kind]||kind;
  return kind;
}
function openAnnotations(notes){
  if(typeof showAnnotationPopover==='function')showAnnotationPopover(notes);
  else tab('annotations');
}

// 整页批注（没有版面框、标不到文字上）：在整页顶部给一个入口，与原文页上的「▣ N」同一作用。
function pageNotesBar(){
  const notes=(state.annotations||[]).filter(annotation=>
    Number(annotation.page)===Number(current.page)&&!(annotation.rects||[]).length);
  if(!notes.length)return null;
  const bar=el('div','translation-page-notes');
  const button=el('button','secondary',`本页有 ${notes.length} 条整页批注`);
  button.type='button';
  button.title='整页批注没有具体的文字位置，点这里查看';
  button.onclick=()=>openAnnotations(notes);
  bar.append(button);
  return bar;
}

// 找出锚点落在这一段上的批注：按"摘引与原文的重叠"判断，规则与后端一致（忽略空白差异）。
// 用 state.annotations（打开文献时就取到了）而不是双语文案缓存，这样标记不会因为缓存没到而缺席。
function annotationsForRow(row){
  const source=normalizeForMatch(row&&row.source);
  if(!source||!state.doc)return [];
  return (state.annotations||[]).filter(annotation=>{
    if(Number(annotation.page)!==Number(current.page))return false;
    const quote=normalizeForMatch(annotation.quote);
    return quote && (source.includes(quote)||quote.includes(source));
  });
}

function normalizeForMatch(text){return String(text||'').replace(/\s+/g,'');}

// 这段译文里有没有这条批注记着的片段：返回它在**原文（段落文字）里的区间**。
// 直接 indexOf 找不到时再"去掉空白"找一遍：映射出来的片段可能与译文里的空白略有差异
// （归一化过的空格），找不到就退回整段标——用户看到的就是"译文整段都被标上了"。
function findExcerpt(text,excerpt){
  const needle=String(excerpt||'').trim();
  if(!needle||!text)return null;
  const direct=text.indexOf(needle);
  if(direct>=0)return {start:direct,end:direct+needle.length};
  const compact=needle.replace(/\s+/g,'');
  if(!compact)return null;
  let stripped='';
  const map=[];
  for(let index=0;index<text.length;index++){
    if(!/\s/.test(text[index])){stripped+=text[index];map.push(index);}
  }
  const at=stripped.indexOf(compact);
  if(at<0)return null;
  return {start:map[at],end:map[at+compact.length-1]+1};
}

// 这条批注在译文里对应哪一小段文字：
//   1. 从译文栏加的批注自己带着（translation_excerpt，就是用户选的那几个字，最准）；
//   2. 在**原文页**上选的批注没有这个字段，用后端按句级对齐映射出来的译文片段
//      （annotations_with_translation 返回的 excerpt）——否则会出现"原文标的是所选文字、
//      译文却整段都标上了"（用户第十二次反馈）。
function excerptFor(annotation,text){
  const own=(annotation.translation_excerpt||'').trim();
  if(own&&findExcerpt(text,own))return own;
  const rows=(state.translationAnnotations||[]).find(item=>item.id===annotation.id);
  for(const item of (rows&&rows.translation)||[]){
    const piece=(item.excerpt||'').trim();
    if(piece&&findExcerpt(text,piece))return piece;
    const narrowed=(item.text||'').trim();
    if(item.narrowed&&narrowed&&findExcerpt(text,narrowed))return narrowed;
  }
  return own;
}

// 页眉/页脚与注释：归拢在整页的开头/结尾，显示原文，并说明为什么没翻（可单独翻）。
function furnitureBlock(label,className,rows){
  const block=el('section',className);
  block.append(el('p','page-furniture-label',label));
  for(const row of rows){
    const item=el('div','page-furniture-item');
    item.dataset.index=String(row.index);
    const text=el('p','page-furniture-text',row.text||row.source);
    item.append(text);
    const notes=annotationsForRow(row);
    if(notes.length)markAnnotations(item,text,notes);
    if(!row.text){
      const button=el('button','secondary','翻译这一段');
      button.onclick=()=>withBusy(button,'翻译中……',()=>retranslate(row));
      item.append(button);
    }
    item.onmouseenter=()=>highlightSource(row);
    item.onmouseleave=()=>highlightSource(null);
    block.append(item);
  }
  return block;
}

// 版面参数：栏间距、字号（用户自己定）、整页高度。**只有一栏**：
// 分栏会把长段落从中间劈开（用户第五次反馈"译文的段落有时候会从中间截断"），而且译文栏本来
// 就只有三四百像素宽，两栏每栏只剩十几个字。所以不再提供分栏，也不跟着原文的栏数走。
function pageGeometry(){
  const size=current.pageSize||{};
  const width=Math.max(240,current.contentWidth||620);
  const pageHeight=size.width&&size.height?width*size.height/size.width:width*842/595;
  return {columns:1,gap:0,font:fontPx,pageHeight};
}

function openEditor(item,paragraph,row){
  const editor=el('textarea','translation-editor');editor.value=row.text;editor.rows=4;
  editor.setAttribute('aria-label','修改译文');
  const bar=el('div','translation-segment-tools');
  const save=el('button','primary','保存修改'),cancel=el('button','secondary','取消');
  save.onclick=async()=>{
    const value=editor.value.trim();
    if(!value){toast('译文不能为空；如需回到机器译文请点「恢复机器译文」');return;}
    save.disabled=true;
    try{await post(`/documents/${current.docId}/translation`,{id:row.id,text:value},'PATCH');toast('译文已保存到本机');await load({silent:true});}
    catch(e){toast(e.message);save.disabled=false;}
  };
  cancel.onclick=()=>editor.replaceWith(paragraph);
  bar.append(save,cancel);
  paragraph.replaceWith(editor);
  item.append(bar);editor.focus();
}

async function restore(row){
  try{
    const result=await post(`/documents/${current.docId}/translation/${row.id}/restore`,{});
    toast(result.message||'已恢复机器译文');
    await load({silent:true});
  }catch(e){toast(e.message);}
}

async function retranslate(row){
  try{
    const result=await post(`/documents/${current.docId}/translation/pages/${current.page}/segments/${row.index}`,{});
    toast(result.error||'这一段已用当前服务重新翻译');
    await load({silent:true});
  }catch(e){toast(e.message);}
}

// 悬停某段译文时，在原文页上把对应的版面区域高亮出来。
// 只画框、不滚动原文：阅读位置是用户自己定的，鼠标扫过译文就把原文滚走会很烦人。
// 定位框由后端用既有引用定位算出（双栏页面的重排与 OCR 行框都成立）；算不出来就只是没有高亮。
let highlightLayer=null;
let highlightTimer=0;
function highlightSource(row){
  clearTimeout(highlightTimer);
  if(!row||!row.bbox){
    if(highlightLayer){highlightLayer.hidden=true;}
    return;
  }
  const paper=$('#paper');
  if(!paper)return;
  if(!highlightLayer){
    highlightLayer=el('div','translation-source-layer');highlightLayer.id='translation-source-layer';
    paper.append(highlightLayer);
  }
  // 框同样夹到 [0,1]：PDF 文字可能画到页宽之外，不夹就会画到纸外面去。
  const box=clampRects([row.bbox])[0];
  if(!box)return;
  const [x0,y0,x1,y1]=box;
  highlightLayer.replaceChildren();
  const mark=el('div','translation-locate active');
  Object.assign(mark.style,{left:(x0*100)+'%',top:(y0*100)+'%',width:((x1-x0)*100)+'%',height:((y1-y0)*100)+'%'});
  mark.title='这一段译文对应的原文位置';
  highlightLayer.append(mark);
  highlightLayer.hidden=false;
}

function renderPanel(){
  if(panel.hidden)return;
  // 每次重画前按当前栏宽刷新一次可用宽度（译文字号按它换算）。
  try{
    current.contentWidth=Math.max(240,Math.round(panel.clientWidth)-76);
  }catch(error){/* 面板还没量到宽度时用上一次的值 */}
  if(!current.docId){setStatus('打开一份文献后即可在这里看译文。');segmentList.replaceChildren();return;}
  const segments=current.segments||[];
  // 页眉页脚/注释单独计数：默认不翻它们，所以"正文翻了多少"才是用户关心的进度。
  const body=segments.filter(row=>!row.kind||row.kind==='body');
  const furniture=segments.filter(row=>row.kind&&row.kind!=='body');
  const untranslated=furniture.filter(row=>!row.text).length;
  furnitureButton.hidden=!untranslated;
  furnitureButton.textContent=`连同页眉页脚（${untranslated}）`;
  if(current.error)setStatus(current.error,'error');
  else if(current.loading)setStatus('正在翻译…');
  else if(!segments.length)setStatus('这一页没有可翻译的文字（扫描页请先识别）。');
  else{
    const translated=body.filter(row=>row.text&&row.state!=='stale').length;
    const legacy=segments.filter(row=>row.legacy).length;
    // 没翻过时把"下一步点哪里"说清楚：现在不会自动翻译了，得用户自己点。
    const pending=translated<body.length;
    setStatus(`第 ${current.page} 页 · `
      +(pending?`还没有这一页的译文：点「翻译这一页」只翻这一页（服务只收到正文段落）`
               :`正文已保存 ${translated}/${body.length} 段`)
      +(translated&&pending?` · 已保存 ${translated}/${body.length} 段`:'')
      +(untranslated?` · 另有 ${untranslated} 段页眉/页脚/注释按原文保留（默认不翻译）`:'')
      +(current.status==='partial'?'（部分未完成）':'')
      +(legacy?` · 其中 ${legacy} 段是旧版的术语替换、不是译文，请点「重新翻译（覆盖）」`:'')); 
  }
  renderSegments(segments);
  highlightSource(null);
}

async function load(options={}){
  const docId=state.doc?.id||'';
  if(!docId){current={...current,docId:'',page:0,data:null,segments:[],loading:false,error:'',status:''};renderPanel();return;}
  const page=state.page;
  const key=`${docId}:${page}`;
  // 同一页已经读过译文时，"静默刷新"就真的只刷新，不顺手发起翻译请求。
  // 这一页还没读过（或刚翻到这一页）时，按下方的按需翻译规则走。
  if(options.silent&&current.loadedKey===key&&!options.bypass){options={...options,refresh:true};}
  current={...current,docId,page,data:state.pageData,loading:true,error:'',status:'',segments:[]};
  renderPanel();
  try{
    const result=await api(`/documents/${docId}/translation/pages/${page}`);
    if(state.doc?.id!==docId||state.page!==page)return;   // 用户已经翻到别处：这次结果作废
    current={...current,segments:result.segments||[],status:result.status,service:result.service||'',
             pageSize:result.page_size||null,pageHeight:(result.page_size||{}).height||842,
             furniture:result.furniture||0,
             loading:false,error:'',loadedKey:key};
  }catch(e){
    if(state.doc?.id!==docId||state.page!==page)return;
    current={...current,loading:false,error:e.message,segments:[]};
  }
  renderPanel();
  // **不自动翻译**（用户要求）：打开文献、翻页、点开译文栏都只读取本机已保存的译文，
  // 要翻必须自己点「翻译这一页」/「重新翻译」/「连同页眉页脚」。上一版这里会自动发起翻译，
  // 于是"每次一打开文件就翻整页"，白花 token。缺失的段落会在状态行里提示怎么翻。
}

// 正文段落（不含页眉页脚/注释）。
function bodyRows(segments){
  return (segments||[]).filter(row=>!row.kind||row.kind==='body');
}

async function translatePage(options={}){
  if(!current.docId)return;
  const docId=current.docId,page=current.page;
  current.loading=true;current.error='';setStatus('正在翻译…');setBusy(true);renderPanel();
  try{
    const result=await post(`/documents/${docId}/translation/pages/${page}`,
                            {force:!!options.force,include_furniture:!!options.includeFurniture});
    if(state.doc?.id!==docId||state.page!==page)return;
    current={...current,segments:result.segments||[],status:result.status,service:result.service||'',error:result.error||'',
             loading:false,furniture:result.furniture||0};
    if(result.error)toast(result.error);
    else if(!options.auto)toast(options.force?'已用当前服务重新翻译这一页':'这一页的译文已保存到本机');
    if(!result.error)refreshBilingualAnnotations();   // 译文变了，批注栏里的双语要跟着更新
  }catch(e){
    if(state.doc?.id!==docId||state.page!==page)return;
    current={...current,loading:false,error:e.message};
    if(!options.auto)toast(e.message);
  }finally{setBusy(false);}
  renderPanel();
}

// ---------------------------------------------------------------- 面板上的按钮

translateButton.onclick=()=>translatePage({force:false});
retranslateButton.onclick=()=>{
  const has=(current.segments||[]).some(row=>row.text);
  if(has&&!confirm('用当前翻译服务重新翻译这一页？原有译文会被覆盖（含人工修改）。'))return;
  translatePage({force:true});
};
wholeButton.onclick=async()=>{
  if(!state.doc){toast('请先打开一份文献');return;}
  try{
    const result=await post(`/documents/${state.doc.id}/translate`,{});
    toast(result.message||'已开始后台翻译');
    pollTask();
  }catch(e){toast(e.message);}
};
copyButton.onclick=async()=>{
  const text=(current.segments||[]).map(row=>row.text).filter(Boolean).join('\n\n');
  if(!text){toast('这一页还没有译文');return;}
  try{await navigator.clipboard.writeText(text);toast('本页译文已复制（只复制译文，不含原文）');}
  catch(e){toast('复制失败：'+e.message);}
};
// 独立译文窗口：用同一个后端与同一份脚本，只是不带原文栏。
// 主窗口翻页时通过 BroadcastChannel 通知它；不支持该接口时它也自己能翻。
detachButton.onclick=()=>{
  if(!state.doc){toast('请先打开一份文献');return;}
  const url=`/translate-window?doc=${encodeURIComponent(state.doc.id)}&page=${state.page}`;
  const opened=window.open(url,'reader-translation','width=900,height=1000,noopener=false');
  if(!opened){toast('浏览器拦住了新窗口：请允许本应用打开弹出窗口后重试');return;}
  try{translationChannel?.postMessage({doc:state.doc.id,page:state.page,view:'translation'});}catch(e){}
};
closeButton.onclick=()=>setView('source');
// 页眉页脚/注释：默认不翻（用户反馈它们混进了正文），点了才把这一页一起带上。
furnitureButton.onclick=()=>translatePage({force:false,includeFurniture:true});

// 主窗口与独立译文窗口之间的同步：只同步"看哪一页"，不同步 DOM。
// 用 BroadcastChannel（同源、同浏览器）；不支持时独立窗口自己仍然可以翻页。
const translationChannel=(()=>{
  try{return typeof BroadcastChannel==='function'?new BroadcastChannel('reader-translation'):null;}
  catch(e){return null;}
})();
function broadcastPage(){
  if(window.__TRANSLATION_WINDOW)return;
  if(!state.doc)return;
  try{translationChannel?.postMessage({doc:state.doc.id,page:state.page});}catch(e){}
}
if(translationChannel){
  // 两个方向都接收"看哪一页"；独立窗口不广播（见 broadcastPage 的第一行），
  // 否则两个窗口会互相推着翻页、翻页请求翻倍。
  translationChannel.onmessage=event=>{
    const data=event.data||{};
    if(!data.doc||data.doc!==state.doc?.id||!data.page||data.page===state.page)return;
    go(data.page);
  };
}

// 对照面板打开期间轮询后台整篇翻译的进度：进度写在本机，刷新页面也不会丢。
let pollTimer=null;
async function pollDocument(docId,onProgress){
  try{
    const result=await api(`/documents/${docId}/translation/tasks`);
    const job=result.job||{};
    if(job.running){
      onProgress(result,job);
      pollTimer=setTimeout(()=>pollDocument(docId,onProgress),1500);
      return;
    }
    if(job.error){onProgress(result,{error:job.error});return;}
    onProgress(result,null);
  }catch(e){/* 轮询失败不打扰阅读；下一次翻页会重新读取 */}
}
function pollTask(){
  clearTimeout(pollTimer);
  if(!state.doc)return;
  const docId=state.doc.id;
  pollDocument(docId,(result,job)=>{
    if(job&&job.error){setStatus(job.error,'error');return;}
    if(job){
      setStatus(`整篇翻译中：第 ${job.page||'—'} 页 / 共 ${job.pages||'—'} 页（已完成 ${result.translated_pages} 页）`);
      if(job.page===current.page&&!current.loading)load({silent:true});
      return;
    }
    if(result.translated_pages>0)setStatus(`整篇已完成 ${result.translated_pages}/${result.pages} 页`);
  });
}
// 工作台里发起的后台翻译：用户不打开文献也能看到结束提示。
function pollTaskFor(docId){
  pollDocument(docId,(result,job)=>{
    if(job&&job.error){toast('翻译未完成：'+job.error);refreshWorkbenchTranslations();return;}
    if(job)return;
    toast(`后台翻译已结束：已翻译 ${result.translated_pages}/${result.pages} 页`);
    refreshWorkbenchTranslations();
    if(state.doc?.id===docId)load({silent:true});
  });
}

// ---------------------------------------------------------------- 工作台上的翻译状态
//
// 用户要求：工作台点「翻译整篇」之后状态要变，翻完了要变成"以新的服务重新翻译整篇"。
// 状态一次问清（/api/translation/overview），不是每张卡片发一个请求。

const OVERVIEW_LABELS={
  none:{label:'翻译整篇（后台）',title:'在后台把整篇文献翻译好，可以继续做别的事'},
  partial:{label:'继续翻译（还有未完成的页）',title:'接着把还没翻译的页翻完'},
  done:{label:'以新的服务重新翻译整篇',title:'用「设置 · 翻译」里当前选的服务重译整篇（覆盖原有译文）'},
  running:{label:'翻译中…',title:'正在后台翻译，进度会显示在这里'},
  error:{label:'重新翻译（上次失败）',title:'上次翻译中途失败，点击重试'},
};

async function refreshWorkbenchTranslations(){
  const cards=[...document.querySelectorAll('#document-grid .document-card')];
  if(!cards.length)return;
  const ids=cards.map(card=>card.dataset.docId).filter(Boolean);
  try{
    const result=await api(`/translation/overview?ids=${ids.join(',')}`);
    for(const card of cards){
      const entry=result.documents?.[card.dataset.docId];
      const holder=card.querySelector('.document-translation');
      if(!entry){holder?.remove();continue;}
      const node=holder||el('p','hint document-translation');
      if(!holder)card.append(node);
      const label=OVERVIEW_LABELS[entry.status]||OVERVIEW_LABELS.none;
      const detail=entry.status==='none'?''
        :entry.status==='running'?`（第 ${entry.page||'—'} / ${entry.pages} 页）`
        :`（${entry.translated_pages}/${entry.pages} 页）`;
      node.textContent='翻译：'+label.label.replace('（后台）','')+detail;
      node.title=entry.error?('上次翻译未完成：'+entry.error):label.title;
      node.dataset.status=entry.status;
      // 按钮文案跟着状态走（用户要求：翻完之后要变成"以新的服务重新翻译整篇"）。
      // 卡片是先建后补状态的，所以这里必须**改写已经画出来的按钮**，不能只在建按钮时取名。
      const button=card.querySelector('[data-translation-action]');
      if(button){
        button.textContent=label.label;
        button.title=label.title;
        button.dataset.translationAction=entry.status;
        button.hidden=entry.status==='running';
      }
    }
  }catch(e){/* 状态读不到就不显示这一行，不打断工作台 */}
  const running=cards.some(card=>card.querySelector('.document-translation')?.dataset.status==='running');
  if(running)setTimeout(refreshWorkbenchTranslations,2000);
}

// drawWorkbench 每次重建卡片后由 library.js 调用（它只调用这个全局函数）。
window.translationDecorations=refreshWorkbenchTranslations;

// 卡片按钮：按当前状态给不同的文案与动作。library.js 把它的 action(label,fn) 交给这里，
// 返回的按钮由本函数改写文案（"翻译整篇（后台）" → "以新的服务重新翻译整篇"）。
function translationAction(action,document_){
  let status='none';
  try{
    const card=document.querySelector(`#document-grid .document-card[data-doc-id="${document_.id}"]`);
    status=card?.querySelector('.document-translation')?.dataset.status||'none';
  }catch(error){
    // 卡片还没进 DOM、或状态行还没画出来：按钮按"没翻译过"给，绝不让工作台整体画不出来
    // （用户上报过工作台空白的现象，根因就是这里抛错被 drawWorkbench 的 try 吞掉）。
    status='none';
  }
  const label=(OVERVIEW_LABELS[status]||OVERVIEW_LABELS.none).label;
  const button=action(label,()=>translateDocument(document_.id,status==='done'));
  if(button){
    button.dataset.translationAction=status;
    button.title=(OVERVIEW_LABELS[status]||OVERVIEW_LABELS.none).title;
  }
  return button;
}
window.translationAction=translationAction;

// ---------------------------------------------------------------- 与阅读区、批注、检索的衔接

function translationPanel(){
  return {
    open(){setView('both');},
    close(){setView('source');},
    get open_(){return used;},
    setView,
    refresh:()=>load({silent:true}),
  };
}

// 只显示译文时，批注引用的文字是译文；这里把它反查回原文，批注锚点仍然是原文。
async function resolveQuote(text){
  if(!state.doc||!text)return text;
  try{
    const result=await api(`/documents/${state.doc.id}/translation/lookup?page=${state.page}&quote=${encodeURIComponent(text.slice(0,2000))}`);
    const match=(result.matches||[])[0];
    if(match){toast('批注锚点已按对应原文记录（你选中的是译文）');return match.source;}
  }catch(e){/* 查不到就按选中的文字记录，不做猜测 */}
  return text;
}

// ---------------------------------------------------------------- 直接对译文加批注
//
// 用户要求：**译文也能批注，但批注真正生效在原文**，而且批注栏里原文与译文同时显示。
// 做法（不新增批注类型，也不改批注表结构）：
//   1. 在译文栏里选中的文字 → 用它去反查它属于哪几段（GET /translation/lookup）；
//   2. 把 state.selection / state.selectionRects 换成**那几段原文**与它们在版面上的框，
//      于是既有的选文操作条、「添加批注」弹窗、保存路径全都按原文走（锚点 = 原文）；
//   3. 批注栏的双语显示由后端的 annotations_with_translation 负责（按摘引匹配译文段），
//      所以不需要为"从译文进入的批注"另写一套显示逻辑。
// 选中文字在译文里对不上任何一段时，就按选中的原文照记，不做猜测。
function segmentRowOf(node){
  const holder=node&&node.nodeType===1?node:node?.parentElement;
  if(!holder||!holder.closest)return null;
  const item=holder.closest('.translation-segment,.page-furniture-item');
  if(!item)return null;
  const index=Number(item.dataset.index);
  return (current.segments||[]).find(row=>row.index===index)||null;
}

// 版面框 → 批注用的选区框。**必须夹到 [0,1]**：PDF 里文字有时会画到页宽之外
// （实测某段 bbox 的 x1 = 1.096），而后端会拒绝越界的框（400「批注位置无效」），
// 于是"从译文加批注"整条路都保存不了。夹完还要丢掉夹成空框的那些。
function clampRects(rects){
  return (rects||[]).map(box=>{
    const [x0,y0,x1,y1]=[0,1,2,3].map(index=>{
      const value=Number(box&&box[index]);
      return Number.isFinite(value)?Math.max(0,Math.min(1,value)):0;
    });
    return [x0,y0,x1,y1];
  }).filter(box=>box[2]>box[0]&&box[3]>box[1]);
}

async function anchorTranslationSelection(text){
  const trimmed=(text||'').trim();
  if(!trimmed)return null;
  let quote=trimmed,rects=[],matched=false,exact=false;
  const row=segmentRowOf(window.getSelection()?.anchorNode);
  try{
    if(state.doc){
      // **句级对齐**：把选中的译文映射回原文的那几句，并拿到那几句在原页上的框。
      // （用户反馈"批注位置不准确、内容依旧是一整段"：以前只会把整段原文当摘引。）
      const result=await post(`/documents/${state.doc.id}/translation/anchor`,
                              {page:state.page,excerpt:trimmed,segment:row?row.index:null});
      if(result&&result.quote){
        quote=result.quote;
        rects=result.bbox?[result.bbox]:[];
        matched=!!result.matched;
        exact=!!result.exact;
        if(result.fallback)toast('原文那几句没能定位，批注按整段原文的范围记录');
        else if(matched&&!exact)toast('这段译文与原文的句子数不一致，已按句子位置对齐；如位置不对请改用原文选文批注');
      }
    }
  }catch(e){/* 对不上就按选中的文字记录（见 resolveQuote 的同一原则） */}
  // **一定要有框**：没有框的批注会被存成"整页批注"（用户报过"浮条的高亮/下划线直接就是整页批注"）。
  // 拿不到句子级框时退到这一段原文的框（段本身有框的话）。
  if(!rects.length&&row&&row.bbox)rects=[row.bbox];
  rects=clampRects(rects);
  // 记下"用户在译文里真正选中的那一小段"：保存批注时会带上它（锚点仍是原文），
  // 译文上的标记才能只标这一小段，而不是整段（用户要求"可以对其中部分内容进行批注"）。
  pendingTranslationExcerpt=trimmed;
  setSelection(quote,rects,{origin:'translation'});
  return {quote,rects,matched,exact};
}

// 在译文栏里选完文字，把操作条挪到鼠标旁（与 #paper 上的选文同一套按钮）。
function showSelectionTools(event){
  const bar=document.querySelector('#selection-tools');
  if(!bar||typeof placeSelectionTools!=='function')return;
  bar.hidden=false;
  placeSelectionTools({clientX:event.clientX,clientY:event.clientY});
}

// 选文操作条上的「高亮 / 下划线 / 段落批注」原本直接调 openAnnotation()：那条路是给
// **原文页**选文用的（草稿里的框来自页面上逐字的选区），从译文进来时它拿不到译文对应的原文
// 那几句，于是被后端当成没有位置的批注存成"整页批注"（用户报过："便捷菜单中的高亮和下划线
// 直接就是整页批注了"）。这里在**捕获阶段**把这三个按钮接管过来，走与右键菜单同一条路：
// 先按句子对齐锚定到原文，再带上选中的译文片段打开弹窗。
function interceptSelectionTools(){
  const styles={'高亮':'highlight','下划线':'underline','段落批注':'margin'};
  const attach=()=>{
    const bar=document.querySelector('#selection-tools');
    if(!bar||!bar.addEventListener||bar.dataset.translationIntercept==='1')return false;
    bar.dataset.translationIntercept='1';       // 只挂一次（浮条可能被重建）
    bar.addEventListener('click',event=>{
      if(state.selectionOrigin!=='translation')return;    // 原文页上的选文照旧
      const label=(event.target&&event.target.textContent||'').trim();
      const style=styles[label];
      if(!style)return;
      event.preventDefault();
      event.stopImmediatePropagation();                   // 不要再走 experience.js 那条路
      bar.hidden=true;
      openAnnotation();
      // **必须在 openAnnotation() 之后**设样式：experience.js 包装了 openAnnotation，它在里面
      // 按草稿推断出的 kind 把样式选择框重设一遍（有框就是 highlight），先设会被它覆盖掉——
      // 实测踩过：点「段落批注」存下来却是"高亮"。
      if(typeof annotationStyle!=='undefined')annotationStyle.value=style;
    },true);
    return true;
  };
  if(!attach()){
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',attach,{once:true});
    else attach();
    let tries=0;
    const timer=setInterval(()=>{if(attach()||++tries>20)clearInterval(timer);},250);
  }
}

function selectedTranslationText(){
  const selection=window.getSelection();
  const text=(selection?selection.toString():'').trim();
  if(!text)return '';
  const node=selection.anchorNode;
  const holder=node&&node.nodeType===1?node:node?.parentElement;
  return holder&&holder.closest&&holder.closest('.translation-segments')?text:'';
}

async function annotationFromTranslation(text){
  const anchored=await anchorTranslationSelection(text);
  if(anchored&&anchored.matched)toast('批注锚点已按对应原文记录（你选中的是译文）');
  openAnnotation();
}

// 「在译文里真正选中的那一小段」——保存批注时要一起存下来，译文上的标记才能只标这一小段。
// 在锚定选文的那一刻记下（anchorTranslationSelection 是译文选文的唯一入口）。
let pendingTranslationExcerpt='';

// 「段落批注」按语义标**整段**：用户明确要求"当我选择的是段落批注时候就两边都应当是整段标识"。
// 所以样式决定粒度——高亮/下划线/区域框只标所选的那一小段，段落批注两边都标整段。
// 这里找出摘引所在的那一段（译文栏里的"段"就是原文的一个自然段），用它整段的原文与框。
function paragraphAnchor(quote){
  const target=normalizeForMatch(quote);
  if(!target||!(current.segments||[]).length)return null;
  return (current.segments||[]).find(row=>{
    const text=normalizeForMatch(row.source);
    return text&&(text.includes(target)||target.includes(text));
  })||null;
}

// 从译文栏进来的批注要多带一个字段（translation_excerpt），而弹窗的保存路径在 experience.js 里
// 写死了要发的字段，`post` / `api` 又是 const 绑定（没法从外面包一层）。所以在**捕获阶段**接管
// 这一次提交：自己发带 excerpt 的请求，然后做与原保存路径一样的收尾。
// 接管的两种情况：①选文来自译文栏（要带 excerpt）；②用户选了「段落批注」（两边都要标整段）。
function interceptAnnotationSubmit(){
  const form=$('#annotation-form');
  if(!form||!form.addEventListener)return;
  form.addEventListener('submit',async event=>{
    const styleValue=typeof annotationStyle!=='undefined'?annotationStyle.value:'highlight';
    const draft=annotationDraft;
    const wantsParagraph=styleValue==='margin'&&!!draft&&Number(draft.page)===Number(current.page);
    if(!pendingTranslationExcerpt&&!wantsParagraph)return;
    const excerpt=pendingTranslationExcerpt;
    pendingTranslationExcerpt='';
    event.preventDefault();
    event.stopImmediatePropagation();
    if(!draft)return;
    // 段落批注：摘引换成**整段**原文、框换成整段的框、不带"选中的那几个字"→ 两边都标整段。
    const row=wantsParagraph?paragraphAnchor(draft.quote):null;
    const body={
      page:draft.page,
      quote:row?row.source:draft.quote,
      rects:row?clampRects([row.bbox]):draft.rects,
      content:$('#annotation-content').value,color:$('#annotation-color').value,
      style:draft.rects.length?styleValue:'page',
      translation_excerpt:row?'':excerpt,
    };
    const submitter=event.submitter;
    if(submitter)submitter.disabled=true;
    try{
      await post(`/documents/${draft.doc}/annotations${draft.id?'/'+draft.id:''}`,body,draft.id?'PATCH':'POST');
      $('#annotation-dialog').close();
      window.getSelection()?.removeAllRanges();
      setSelection('');
      await loadAnnotations();
      tab('annotations');
      toast(row?'批注已保存：段落批注标的是整段原文（译文上也是整段）'
               :'批注已保存：锚点在原文，标记落在你选中的那段译文上');
    }catch(error){
      toast(error.message);
    }finally{
      if(submitter)submitter.disabled=false;
    }
  },true);
  // 关窗（取消、Esc、保存后）时清掉，免得上一次选中的译文片段被带到下一条批注上。
  const dialog=$('#annotation-dialog');
  if(dialog&&dialog.addEventListener)dialog.addEventListener('close',()=>{pendingTranslationExcerpt='';});
}

// 译文栏里的右键菜单：选文时给出"按原文批注/提问"，没选文时给出这一页的操作。
panel.addEventListener('contextmenu',event=>{
  if(!used||!current.docId)return;
  if(!event.target.closest('.translation-segments'))return;
  event.preventDefault();
  const text=selectedTranslationText();
  const actions=[];
  if(text){
    actions.push(['复制选中的译文',()=>navigator.clipboard.writeText(text)]);
    actions.push(['添加批注（锚定原文）',()=>annotationFromTranslation(text)]);
    actions.push(['对这段原文提问',async()=>{
      await anchorTranslationSelection(text);
      $('#ask-selection').click();
    }]);
    actions.push(['翻译这段原文',async()=>{
      await anchorTranslationSelection(text);
      const row=bodyRows(current.segments).find(item=>item.source&&state.selection.includes(item.source))
        ||segmentRowOf(window.getSelection()?.anchorNode);
      if(row)retranslate(row);else toast('没有找到对应的原文段落');
    }]);
  }else{
    actions.push(['翻译这一页',()=>translatePage({force:false})]);
    actions.push(['重新翻译这一页（覆盖）',()=>translatePage({force:true})]);
    actions.push(['复制本页译文',()=>copyButton.click()]);
  }
  showMenu(event,actions);
});

// 译文栏里松开鼠标（选完译文）：把选文换成**对应的原文**，再显示选文操作条。
// 这样操作条上的「段落批注」「高亮」「提问」全都作用在原文上（用户要求"真正生效在原文"）。
panel.addEventListener('pointerup',event=>{
  if(!used||event.button!==0)return;
  if(!event.target.closest('.translation-segments'))return;
  const text=selectedTranslationText();
  if(!text)return;
  anchorTranslationSelection(text).then(()=>showSelectionTools(event));
});

// 选中文字的"翻译"：只把选中的这一段发给配置好的翻译服务，不落库（选文不是文献内容）。
async function translateSelection(){
  const text=(state.selection||'').trim();
  if(!text){toast('请先选中要翻译的文字');return;}
  if(!config)await loadConfig();
  selectionBox.hidden=false;
  selectionBox.querySelector('.translation-selection-source').textContent=shorten(text,500);
  selectionBox.querySelector('.translation-selection-target').textContent='正在翻译…';
  selectionBox.querySelector('.translation-selection-meta').textContent='';
  try{
    const result=await post('/translation/quote',{text,document_id:state.doc?.id||'',page:state.page||0});
    selectionBox.querySelector('.translation-selection-target').textContent=result.text||'（服务没有返回译文）';
    selectionBox.querySelector('.translation-selection-meta').textContent=`服务：${result.service||'未配置'}`;
  }catch(e){
    selectionBox.querySelector('.translation-selection-target').textContent=e.message;
  }
}

const selectionBox=el('div','translation-selection');selectionBox.id='translation-selection';selectionBox.hidden=true;
{
  const boxHead=el('div','translation-selection-head');
  boxHead.append(el('b','','选文翻译'));
  const boxActions=el('div','');
  const toPanel=el('button','secondary','在译文栏里看整页');
  toPanel.onclick=()=>{translationPanel().open();translatePage({});};
  const close=el('button','','×');close.title='关闭';close.onclick=()=>{selectionBox.hidden=true;};
  boxActions.append(toPanel,close);boxHead.append(boxActions);
  selectionBox.append(boxHead,el('small','','原文'),el('p','translation-selection-source'),
                       el('small','','译文'),el('p','translation-selection-target'),el('p','translation-selection-meta hint'));
  document.body.append(selectionBox);
}

// ---------------------------------------------------------------- 翻译服务设置
//
// 只有一件事要配：用哪个翻译服务、翻成什么语言。
// 服务来源两种——「模型服务」里已连接的平台（含本机 Ollama / LM Studio，都先在那里配好），
// 或直接填一个兼容接口。没有"翻译方式 / 内置术语表"这类选项：术语替换不是翻译。

const settingsUi={section:null,form:null,read:null,feedback:null};
{
  const section=el('section','model-profile');section.id='translation-settings-panel';section.hidden=true;
  const heading=el('div','profile-heading');
  const copy=el('div');
  copy.append(el('h3','','翻译'),
              el('p','','选一个翻译服务与目标语言。译文只保存在本机数据库，随文献一起删除；服务只会收到"当前这一段原文"。'));
  heading.append(copy);section.append(heading);
  const form=el('form');form.id='translation-settings-form';section.append(form);
  $('.settings-panels').append(section);
  settingsUi.section=section;settingsUi.form=form;
  const nav=el('button');nav.type='button';nav.dataset.settingTab='translation';
  nav.append(el('span','','译 翻译'),el('small','','翻译服务与目标语言'));
  const groups=document.querySelectorAll('.settings-nav .settings-group');
  if(groups.length>1)groups[1].prepend(nav);else $('.settings-nav').append(nav);
  nav.onclick=()=>switchModelTab('translation');
}

function fillSettings(){
  const form=settingsUi.form;form.replaceChildren();
  const target=el('select');
  for(const item of config.targets||[])target.append(new Option(item.name,item.id));
  target.value=config.target_language||'zh';
  form.append(fieldLabel('目标语言',target));
  form.append(el('p','hint','译文只保存在本机数据库，随文献一起删除。翻译服务只会收到"当前这一段原文"，不会收到整页、整篇、页码或本机路径。'));

  const connections=(typeof catalogConnections!=='undefined'?catalogConnections:[]);
  const sourceKind=el('select');
  sourceKind.append(new Option('「模型服务」里已连接的平台','connection'),
                   new Option('直接填写兼容接口的地址','direct'));
  sourceKind.value=config.connection_id?'connection':'direct';
  form.append(fieldLabel('翻译服务来源',sourceKind));

  const connectionSelect=el('select');
  connectionSelect.append(new Option('—— 请选择已连接的平台 ——',''));
  for(const connection of connections)connectionSelect.append(new Option(connection.name,connection.id));
  connectionSelect.value=config.connection_id||'';
  if(!connections.length)connectionSelect.append(new Option('（还没有连接的平台：请到「模型服务」添加）',''));

  const modelSelect=el('select');
  // 模型一律从所选平台的模型列表里选，**不提供任何内置/默认模型名**：
  // Ollama、LM Studio 也应当先在「模型服务」里连好，这里直接选它的模型即可。
  function fillModels(){
    modelSelect.replaceChildren();
    const connection=connections.find(c=>c.id===connectionSelect.value);
    if(!connection){modelSelect.append(new Option('请先选择平台',''));modelSelect.disabled=true;return;}
    const models=connection.models||[];
    const capable=models.filter(m=>(m.capabilities||[]).includes('chat'));
    const list=capable.length?capable:models;
    modelSelect.disabled=!list.length;
    if(!list.length){modelSelect.append(new Option('这个连接里还没有模型：请到「模型服务」获取或手动标注',''));return;}
    for(const model of list)modelSelect.append(new Option(model.id+(capable.length?'':'（未标注能力）'),model.id));
    if(config.model&&list.some(m=>m.id===config.model))modelSelect.value=config.model;
  }

  const urlInput=el('input');urlInput.value=config.base_url||'';urlInput.placeholder='http://localhost:11434/v1';
  const keyInput=el('input');keyInput.type='password';keyInput.placeholder=config.has_key?'已保存密钥（留空保留）':'API Key（本机服务可留空）';
  const directModelInput=el('input');directModelInput.value=config.base_url?config.model||'':'';
  directModelInput.placeholder='例如 qwen2.5:7b';

  const connectionRow=fieldLabel('使用哪个已连接的平台',connectionSelect);
  const modelRow=fieldLabel('模型（从该平台的模型列表里选）',modelSelect);
  const urlRow=fieldLabel('服务地址（根地址）',urlInput);
  const keyRow=fieldLabel('API Key',keyInput);
  const directModelRow=fieldLabel('模型名称',directModelInput);
  const note=el('p','hint','');
  const fields=[connectionRow,modelRow,urlRow,keyRow,directModelRow];
  form.append(...fields,note);

  function syncRows(){
    const kind=sourceKind.value;
    connectionRow.hidden=kind!=='connection';
    modelRow.hidden=kind!=='connection';
    urlRow.hidden=kind!=='direct';
    keyRow.hidden=kind!=='direct';
    directModelRow.hidden=kind!=='direct';
    if(kind==='connection'){
      fillModels();
      const connection=connections.find(c=>c.id===connectionSelect.value);
      note.textContent=connection?'地址与密钥取自「模型服务」里的这个连接，这里不再重复保存一份。':'请选择平台；还没有的话先到「模型服务」添加（本机 Ollama / LM Studio 也在那里添加）。';
    }else{
      note.textContent='填兼容接口的根地址即可（程序按 /chat/completions 调用）。本机模型服务可填 http://localhost:11434/v1（Ollama）或 http://localhost:1234/v1（LM Studio），此时密钥可留空。';
    }
  }
  connectionSelect.onchange=()=>{config.connection_id=connectionSelect.value;syncRows();};
  sourceKind.onchange=syncRows;

  const probeButton=el('button','secondary','试译一句');probeButton.type='button';
  const probeStatus=el('p','hint','试译只发送这一句固定示例，不发送任何文献内容。');
  probeButton.onclick=async()=>{
    probeButton.disabled=true;probeStatus.textContent='正在试译…';
    try{
      const result=await post('/translation-settings/probe',{text:'Deep learning improves the baseline on the test set.',settings:settingsUi.read()});
      probeStatus.textContent=(result.text||'')+'　—　'+result.message;
    }catch(e){probeStatus.textContent='试译失败：'+e.message;}
    finally{probeButton.disabled=false;}
  };
  const probeRow=el('div','provider-actions');probeRow.append(probeButton);
  form.append(probeRow,probeStatus);
  syncRows();

  settingsUi.read=()=>{
    const kind=sourceKind.value;
    return {
      target_language:target.value,
      connection_id:kind==='connection'?connectionSelect.value:'',
      model:kind==='connection'?modelSelect.value:directModelInput.value.trim(),
      base_url:kind==='direct'?urlInput.value.trim():'',
      provider:'custom',
      api_key:kind==='direct'?keyInput.value:'',clear_key:false,
    };
  };
  settingsUi.feedback=probeStatus;
}

async function loadConfig(){
  config=await api('/translation-settings');
  if(settingsUi.section&&!settingsUi.section.hidden)fillSettings();
}

async function saveSettings(){
  if(!settingsUi.read)return;
  const payload=settingsUi.read();
  const saved=await post('/translation-settings',payload,'PUT');
  config={...config,...saved};
  toast('翻译服务设置已保存');
}

// 设置页把「翻译」并进自己那一栏：切栏目时显示本面板，页脚唯一按钮改指本表单。
function patchSettingsSwitcher(){
  if(typeof switchModelTab!=='function')return;
  const base=switchModelTab;
  window.switchModelTab=function(kind){
    base(kind);
    const mine=kind==='translation';
    settingsUi.section.hidden=!mine;
    if(mine){
      fillSettings();
      const feedback=$('#settings-feedback');
      if(feedback){feedback.textContent='本页保存翻译方式、目标语言、外部服务与本机术语表。设置仅保存到此设备。';feedback.classList.remove('error');}
    }
    const button=$('#settings-save');
    if(button){
      if(mine){button.hidden=false;button.removeAttribute('form');button.textContent='保存翻译设置';}
      else button.setAttribute('form',(typeof saveForms!=='undefined'&&saveForms[kind])||'model-settings-form');
    }
    document.querySelectorAll('[data-setting-tab]').forEach(b=>{
      const active=b.dataset.settingTab===kind;b.classList.toggle('active',active);b.setAttribute('aria-current',active?'true':'false');
    });
  };
  const button=$('#settings-save');
  if(button)button.addEventListener('click',async event=>{
    const tab=document.querySelector('[data-setting-tab="translation"]');
    if(!tab||!tab.classList.contains('active'))return;
    event.preventDefault();event.stopImmediatePropagation();
    button.disabled=true;
    try{await saveSettings();}catch(e){toast(e.message);}finally{button.disabled=false;}
  },true);
}

// ---------------------------------------------------------------- 菜单与批注页

function addMenuEntries(){
  const original=showMenu;
  window.showMenu=function(event,items){
    const extra=[...items];
    if(state.doc&&state.selection&&!extra.some(([label])=>label==='翻译选中文字')){
      extra.splice(Math.min(1,extra.length),0,['翻译选中文字',()=>translateSelection()]);
    }
    if(state.doc&&!extra.some(([label])=>label==='原文/译文对照')){
      extra.push(['原文/译文对照',()=>{translationPanel().open();}]);
    }
    return original(event,extra);
  };
  // 选文浮条也要有「翻译」。这里有个加载顺序问题：浮条由 experience.js 建出来，
  // 而它在 boot 清单里排在 translation.js **之后**——所以初始化时 #selection-tools
  // 还不存在，直接找会静默跳过（用户反馈"悬浮菜单缺少翻译选项"正是这个原因）。
  // 现在等 DOM 就绪后再挂，并在头部插入（与右键菜单里"翻译选中文字"的位置一致）。
  const attach=()=>{
    const tools=document.querySelector('#selection-tools');
    if(!tools||tools.querySelector('[data-translation-action="selection"]'))return false;
    const button=el('button','','翻译');
    button.dataset.translationAction='selection';
    button.onpointerdown=event=>event.preventDefault();
    button.onclick=()=>{translateSelection();tools.hidden=true;};
    tools.insertBefore(button,tools.firstElementChild||null);
    return true;
  };
  if(!attach()){
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',attach,{once:true});
    else attach();
    // 兜底：万一 DOMContentLoaded 早已过去、浮条还没建好，就再看一小会儿。
    let tries=0;
    const timer=setInterval(()=>{if(attach()||++tries>20)clearInterval(timer);},250);
  }
}

// 批注页同时显示原文与译文（用户要求：即使阅读区只显示译文，批注页也要两边都在）。
//
// 两条硬约束（都是实测踩出来的）：
//   1. 双语内容必须**挂在每个 .note 里面**——批注列表在打开文献、新增/删除/撤销批注时
//      会整体重建（replaceChildren），游离在外的浮层会被抹掉；
//   2. 它必须**跟着列表一起重画**。做法是把翻译数据先取到 state 上，再补画一遍，
//      而不是改 drawAnnotationList 的全局绑定：本项目所有脚本共享全局作用域，
//      但模块内部对同名函数的引用走的是词法绑定，改 window 上的名字在自己的模块里不生效。
function annotationBox(annotation,data){
  const box=el('div','annotation-bilingual');
  box.append(el('small','','原文（批注所引）'),el('p','translation-source-text',annotation.quote||'（整页批注，未引用具体文字）'));
  // 在译文栏里选中的那一小段：批注栏里也如实显示"你批注的是哪一句译文"。
  if(annotation.translation_excerpt){
    box.append(el('small','','你批注的译文片段'),
               el('p','translation-text',annotation.translation_excerpt));
  }
  if(data&&data.translation&&data.translation.length){
    box.append(el('small','','译文'));
    for(const item of data.translation)box.append(el('p','translation-text',item.text));
    // 摘引只是整段里的一句时，默认只显示对应那一句，整段译文折叠在后面
    // （用户反馈过"即使只标识了所选的几个文本，内容依旧是一整段"）。
    const wide=data.translation.filter(item=>item.full_text);
    if(wide.length){
      const more=el('details','translation-source-text');
      more.append(el('summary','','整段译文'));
      for(const item of wide)more.append(el('p','translation-source-text',item.full_text));
      box.append(more);
    }
  }else{
    box.append(el('small','','译文'),el('p','hint','这一页还没有译文：用「原文/译文对照」翻译本页后，这里会同时显示原文与译文。'));
  }
  return box;
}

function drawBilingualAnnotations(){
  const list=$('#annotations');
  if(!list||!state.doc)return;
  const rows=state.translationAnnotations;
  const ready=!!rows&&state.translationAnnotationsDoc===state.doc.id;
  note({stage:'draw',ready,rows:rows?rows.length:0,notes:list.querySelectorAll('.note').length});
  if(!ready){refreshAnnotationTranslations();return;}  const byId=new Map(rows.map(row=>[row.id,row]));
  // 列表按 state.annotations 的顺序生成 .note 节点，因此按下标配对即可；
  // 不按文字内容反查，避免两条批注引用同一段文字时张冠李戴。
  for(const [index,node] of [...list.querySelectorAll('.note')].entries()){
    const annotation=state.annotations[index];
    if(!annotation)continue;
    node.querySelector('.annotation-bilingual')?.remove();
    node.append(annotationBox(annotation,byId.get(annotation.id)));
  }
  note({stage:'drawn',boxes:list.querySelectorAll('.annotation-bilingual').length});
}

function patchAnnotationPanel(){
  if(typeof askAboutAnnotation==='function'){
    const base=askAboutAnnotation;
    window.askAboutAnnotation=async function(annotation){
      const text=(annotation?.quote||'').trim();
      if(text&&state.doc){
        const resolved=await resolveQuote(text);
        if(resolved!==text)return base({...annotation,quote:resolved});
      }
      return base(annotation);
    };
  }
  // loadAnnotations 是"批注数据变了"的唯一入口（打开文献、保存、删除、撤销都走它），
  // 因此在这里接一次：批注重画完成后补上双语内容。
  if(typeof loadAnnotations==='function'){
    const base=loadAnnotations;
    window.loadAnnotations=async function(...args){
      const result=await base(...args);
      await refreshBilingualAnnotations();
      return result;
    };
  }
  // 用户切到「批注」栏时也要显示（此时可能还没打开过对照面板）：先补数据再画一次。
  if(typeof tab==='function'){
    const base=tab;
    window.tab=function(name){
      const result=base(name);
      if(name==='annotations')(async()=>{await refreshAnnotationTranslations();drawBilingualAnnotations();})();
      return result;
    };
  }
  // 从译文栏进来的批注：弹窗里那张"摘引"卡片上要说清**锚点已经换成原文**，
  // 否则用户会以为自己批注的是译文（实际生效在原文，批注栏里也会原文+译文一起显示）。
  if(typeof openAnnotation==='function'){
    const base=openAnnotation;
    window.openAnnotation=function(existing){
      base(existing);
      document.querySelectorAll('.translation-anchor-hint').forEach(node=>node.remove());
      // 从原文页选的批注不带走"上次在译文里选中的片段"。
      if(state.selectionOrigin!=='translation')pendingTranslationExcerpt='';
      if(existing||state.selectionOrigin!=='translation')return;
      const quote=$('#annotation-quote');
      if(!quote)return;
      quote.after(el('p','hint translation-anchor-hint',
        '锚点已按对应的原文记录：你选中的是译文，批注仍落在原文上；批注栏里原文与译文会同时显示。'));
    };
  }
}

// 供真实浏览器探针（scripts/ui_probe）诊断用：只保留最近若干条，避免长时间阅读时无限增长。
const annotationLog=[];
state.translationLog=annotationLog;
function note(entry){annotationLog.push(entry);if(annotationLog.length>60)annotationLog.splice(0,annotationLog.length-60);}

let annotationFetch=null;
async function refreshAnnotationTranslations(){
  const docId=state.doc?.id;
  if(!docId)return;
  if(annotationFetch)return annotationFetch;
  if(state.translationAnnotations&&state.translationAnnotationsDoc===docId)return;
  note({stage:'fetch',docId:docId.slice(0,6),notes:document.querySelectorAll('#annotations .note').length});
  const promise=(async()=>{
    try{
      const rows=await api(`/documents/${docId}/translation/annotations`);
      if(state.doc?.id!==docId)return;
      state.translationAnnotations=rows;
      state.translationAnnotationsDoc=docId;
      note({stage:'fetched',rows:rows.length,translated:rows.filter(r=>(r.translation||[]).length).length});
    }catch(e){
      // 译文接口不可用时保持原有批注列表，但要在控制台留下线索，避免"静默不生效"。
      note({stage:'error',message:e.message});
      if(window.console&&console.warn)console.warn('批注双语显示失败：',e.message);
    }
  })();
  annotationFetch=promise;
  try{await promise;}finally{if(annotationFetch===promise)annotationFetch=null;}
}

// 批注或译文变了 → 双语缓存必须作废后重取。
// 这是实测出来的缺陷：新增一条批注时，`loadAnnotations()` 之后的刷新会因为**缓存命中而直接返回**，
// 于是新批注拿不到译文，批注栏里显示"这一页还没有译文"（要刷新页面才对）——正是"对译文的批注"。
function invalidateAnnotationTranslations(){
  state.translationAnnotations=null;state.translationAnnotationsDoc='';
}
async function refreshBilingualAnnotations(){
  invalidateAnnotationTranslations();
  await refreshAnnotationTranslations();
  drawBilingualAnnotations();
  // 译文上的批注标记也要跟着变（新增/删除/改颜色）。renderSegments 会保留滚动位置。
  if(used&&current.docId)renderPanel();
}

// ---------------------------------------------------------------- 初始化

function init(){
  // 独立译文窗口（多显示器用）：同一份脚本、同一个后端，但只显示译文。
  // 它同样会注册右键菜单与设置栏目（那些界面在这个窗口里是隐藏的），
  // 因此不需要为独立窗口另写一套界面代码。
  const standalone=!!window.__TRANSLATION_WINDOW;
  if(standalone){document.body.classList.add('standalone-translation');used=true;}
  // 独立窗口：只保留译文栏（原文页在那边没有意义，用 CSS 隐藏 #viewport 即可）。
  syncPanelVisibility();
  addMenuEntries();
  patchAnnotationPanel();
  interceptAnnotationSubmit();
  interceptSelectionTools();
  patchSettingsSwitcher();
  window.translationPanel=translationPanel;
  window.translateSelection=translateSelection;
  // 供真实浏览器探针（scripts/ui_probe）调用：探针只能访问全局名字，
  // 而这些函数都在这个 IIFE 里。只暴露诊断入口，不暴露内部状态。
  window.translationDiagnostics={refreshAnnotationTranslations,drawBilingualAnnotations,resolveQuote,load,current:()=>current};
  window.translateDocument=async(docId,force)=>{
    if(!docId){toast('请先选择一份文献');return;}
    try{
      // 文献工作台里也能直接对整篇文献发起后台翻译（需求第 2 条），不必先打开它。
      // force=true（"以新的服务重新翻译整篇"）才会覆盖已有译文；否则按段复用、只补缺的。
      const result=await post(`/documents/${docId}/translate`,{force:!!force});
      toast(result.message||'已开始后台翻译');
      refreshWorkbenchTranslations();
      pollTaskFor(docId);
    }catch(e){toast(e.message);}
  };
  pageTranslation=(docId,page)=>{
    // 由 app.js 在翻页（含换文献）时调用。第 1 次调用只记下"现在该显示哪一页"，
    // 第 2 次（页面数据到位后）才决定要不要请求译文：避免翻页瞬间就抢网络。
    const sameContext=current.docId===docId&&current.page===page;
    if(!sameContext&&state.translationAnnotationsDoc&&state.translationAnnotationsDoc!==docId){
      // 换文献：清掉上一份文献的批注译文缓存，否则会把别人的译文贴到新文献的批注上。
      state.translationAnnotations=null;state.translationAnnotationsDoc='';
    }
    current={...current,docId,page,data:state.pageData};
    if(sameContext){load({silent:true});broadcastPage();}
  };
  // 原文缩放/窗口尺寸变化时**只更新版面参数**，不重建段落节点：
  // 重建会把译文栏的滚动位置与展开的"对应原文"一起清掉，缩放一下就跳回顶部。
  pageTranslationLayout=()=>{
    const page=segmentList.querySelector('.translation-page');
    if(!page)return;
    applyPageVars(page);
  };
  applyFont();
  // 打开文献时**不自动打开译文栏、也不自动翻译**（用户要求：默认就是不翻译的状态）。
  // 这两次 load 只读取本机已保存的译文，供用户点开译文栏时立刻看到（不发翻译请求）。
  loadConfig().then(()=>load({silent:true})).catch(()=>{});
  if(state.doc)load({silent:true});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!selectionBox.hidden)selectionBox.hidden=true;});
}
init();
})();
