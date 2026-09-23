"""在**真实 app 页面**里检查翻译功能（Edge headless + CDP）。

要证明的事情（都是只有真实布局与真实事件才能回答的）：

1. 打开文献后只有原文：译文栏默认不显示，既有的单页阅读没有被动过；
2. 点工具栏「⇄ 翻译整页」后，译文栏开在**阅读区右侧**（与原文页并排，不是底部一块）；
3. 译文按原段落排版：一段原文对应一段译文，字号 15px；
4. 拖动译文栏左侧的分栏条能改宽度（真实鼠标事件），宽度写进本机存储；
5. 「收起」真的关掉译文栏、原文页恢复整宽；
6. 每段可改、可只重译这一段；覆盖后"人工修改"标记消失；
7. 选文翻译（右键菜单入口）能用；
8. 批注页同时显示原文与译文；
9. 整篇后台翻译完成后，工作台状态与按钮文案跟着变；检索能命中译文；
10. 「在新窗口打开」开出独立译文窗口（只显示译文），主窗口翻页时它会跟着走；
11. 设置里只有"翻译服务来源 + 目标语言"：模型是从所选平台模型列表下拉选择，
    选已连接的平台后不再要求填地址与密钥；没有"翻译方式/内置术语表"这类选项；
12. 工作台的标签筛选是下拉选择，检索提示写明"名称、标签或作者"；
13. 译文是单栏、可调字号的整页排版：段落不再是逐段绝对定位、两两包围盒无重叠；
    字号**由用户控件决定**（A− / 数值 / A+，一档 1px，正文实际渲染字号跟着变），
    原文缩放不再影响译文字号；**没有分栏**（分栏会把长段落从中间劈开）；
14. 「翻译这一页」点下去变成「正在翻译……」并禁用，结束后恢复；
15. **打开文献不自动翻译、也不自动开译文栏**（看网络请求，不看界面）；
16. **直接对译文加批注**：选中的译文会反查成对应的原文（`state.selectionOrigin==='translation'`、
    原文页上出现对应区域的高亮），批注弹窗里的摘引是原文并注明锚点已换，保存后批注栏里
    原文与译文同时显示；
17. 批注在**译文上的标记与原文同一套显示方式**（高亮/下划线/侧线/区域框 + 同一色板，
    点一下能看到这条批注），收起原文（只看译文）时也在；
18. **长段落整段显示、不被截断**（用户报过"段落有时候会从中间截断"：真因是长段落被切成几段后，
    尾巴那一段的起点落在页面底部、被当成脚注而不翻译，现在注释必须同时满足"短行/小字号/脚注特征"）；
19. 头部按钮的真实命中测试必须命中按钮自己（曾经被 `overflow-x` 裁掉而点不动）。

译文内容相关的断言只要求"显示的是服务返回的内容"（探针用
`scripts/ui_probe/fake_translate.py` 的假服务，返回带「探针译文」前缀的文本），
**不假设任何一家真实平台的译文质量**——本项目没有可用密钥去实测真实平台。

用法：python scripts/ui_probe/drive_translation.py [页面地址]
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from drive import CDP, PORT, wait_for_cdp, ws_connect  # noqa: E402

URL = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8799/'
DOC_NAME = 'probe-translation.pdf'
MARK = '探针译文'


def main():
    pages = [t for t in wait_for_cdp(PORT) if t.get('type') == 'page']
    if not pages:
        raise RuntimeError('没有可用的页面目标')
    cdp = CDP(ws_connect(pages[0]['webSocketDebuggerUrl']))
    for domain in ('Page.enable', 'Runtime.enable', 'Log.enable', 'Network.enable'):
        cdp.call(domain)
    # 视口固定成宽屏：窄视口会命中 style.css 里的 `@media(max-width:850px)`（译文栏改成上下排列、
    # 分栏条变成横向），量出来的几何量就不代表桌面布局了。实测踩过：窗口尺寸参数不一定生效，
    # 所以这里用 CDP 明确指定，让每次运行的布局条件一致。
    cdp.call('Emulation.setDeviceMetricsOverride', width=1400, height=900,
             deviceScaleFactor=1, mobile=False)

    # 从干净状态开始：浏览器配置目录复用，上一次的显示状态会留在 localStorage 里。
    # 必须先打开一次、在同源页面里清掉这两个键、再重新加载。
    cdp.call('Page.navigate', url=URL + '?warmup=' + str(int(time.time())))
    for _ in range(60):
        if cdp.evaluate('document.readyState') == 'complete':
            break
        time.sleep(0.2)
    cdp.evaluate("(()=>{for(const key of ['reader-translation-used','reader-translation-width'])"
                 "localStorage.removeItem(key);})()")
    cdp.call('Page.navigate', url=URL + '?probe=' + str(int(time.time())))
    for _ in range(120):
        if cdp.evaluate('document.readyState') == 'complete' and cdp.evaluate(
                "typeof translationPanel==='function'&&typeof translateDocument==='function'"):
            break
        time.sleep(0.25)
    problems = []
    print('页面：', URL, '| 资产版本：', cdp.evaluate('window.__ASSET_VERSION'))

    def mouse(kind, x, y, buttons=0):
        cdp.call('Input.dispatchMouseEvent', type=kind, x=int(x), y=int(y), button='left',
                 buttons=buttons, clickCount=1 if kind == 'mousePressed' else 0, pointerType='mouse')

    def click(selector):
        position = cdp.evaluate("""(()=>{const node=document.querySelector(%s);if(!node)return null;
          const box=node.getBoundingClientRect();
          return {x:Math.round(box.left+box.width/2),y:Math.round(box.top+box.height/2)};})()""" % json.dumps(selector))
        if not position:
            return False
        mouse('mousePressed', position['x'], position['y'], buttons=1)
        mouse('mouseReleased', position['x'], position['y'])
        return True

    opened = cdp.evaluate("""(async()=>{
      await loadLibrary();
      const doc=state.docs.find(d=>d.name==='%s')||state.docs[0];
      if(doc&&state.doc?.id!==doc.id)await openDoc(doc);
      // 文献会恢复"上次读到第几页"，而探针的检查都假定从第 1 页开始（那一页有文字层与版面框）。
      // 之前被别的排查脚本留在第 3 页，结果整组检查都跑在扫描页上（没有框 → 没有高亮、没有批注位置）。
      if(state.page!==1)await go(1);
      for(let i=0;i<60;i++){
        await new Promise(r=>setTimeout(r,250));
        if(state.page===1&&state.pageData)break;
      }
      await new Promise(r=>setTimeout(r,600));
      return {doc:state.doc?state.doc.name:null,page:state.page,hasPageData:!!state.pageData};
    })()""" % DOC_NAME)
    print('打开文献：', opened)
    if not opened or opened.get('doc') != DOC_NAME:
        problems.append('没能打开合成文献 %s' % DOC_NAME)
    if opened and opened.get('page') != 1:
        problems.append('探针没有从第 1 页开始（后面的版面检查都依赖它有文字层）：%s' % opened)
    # 探针每次运行都会往这份合成文献里加批注，而它们是**持久**的：不清理的话，
    # 上一次留下的"只标一小段"标记会让这一次的选文步骤找不到纯文本节点（踩过）。
    cleaned = cdp.evaluate("""(async()=>{
      const rows=await api('/documents/'+state.doc.id+'/annotations');
      const mine=rows.filter(row=>/^(探针|调试)/.test(row.content||''));
      for(const row of mine)await api('/documents/'+state.doc.id+'/annotations/'+row.id,{method:'DELETE'});
      await loadAnnotations();
      return {removed:mine.length,left:rows.length-mine.length};
    })()""")
    print('清理上一轮探针批注：', cleaned)
    # 从"打开文献"这一刻开始记网络事件：下面要断言没有自动翻译请求。
    cdp.events.clear()
    cdp.evaluate('1')          # 排空 socket，把已到达的事件收进 events

    # 顺手量一次布局：grid 是否真的生效（读数写进日志，出问题时不必再猜）。
    layout = cdp.evaluate("""(()=>{const reader=document.querySelector('.reader');
      return {display:getComputedStyle(reader).display,columns:getComputedStyle(reader).gridTemplateColumns};})()""")
    print('阅读区布局：', layout)

    # ---------------------------------------------------------------- 1 默认只有原文
    baseline = cdp.evaluate("""(()=>{const p=document.querySelector('#translation-panel');
      return {exists:!!p,hidden:p?p.hidden:null,display:p?getComputedStyle(p).display:null,
              inReader:p?!!p.closest('.reader'):null,
              viewportWidth:Math.round(document.querySelector('#viewport').getBoundingClientRect().width),
              toolbarButton:!!document.querySelector('#translate-page')};})()""")
    print('默认状态：', baseline)
    if not baseline['exists'] or baseline['hidden'] is not True or baseline['display'] != 'none':
        problems.append('没点过翻译之前译文栏就占位置了：既有的单页阅读被改动')
    if not baseline['inReader']:
        problems.append('译文栏不在阅读区里（用户要求开在文献阅读区的右侧）')
    if not baseline['toolbarButton']:
        problems.append('工具栏上没有「翻译整页」按钮')

    # ------------------------------------------- 1b 打开文献不自动翻译、也不自动开译文栏
    # 用户要求：默认就是"不翻译"的状态，否则每次一打开文件就翻整页、白花 token。
    # 这里直接看**网络请求**（比看界面更硬）：打开文献后不得出现翻译请求。
    time.sleep(1.5)
    cdp.evaluate('1')          # 再排空一次，把这段等待里的事件都收进来
    auto_posts = [event['params']['request']['url'] for event in cdp.events
                  if event.get('method') == 'Network.requestWillBeSent'
                  and event['params']['request']['method'] == 'POST'
                  and '/translation/pages/' in event['params']['request']['url']]
    quiet = cdp.evaluate("""(async()=>{
      const doc=state.docs.find(d=>d.name==='%s')||state.docs[0];
      const page=await api('/documents/'+doc.id+'/translation/pages/'+state.page);
      return {panelHidden:document.querySelector('#translation-panel').hidden,
              translatedSegments:page.segments.filter(row=>row.text).length,
              statusText:(document.querySelector('.translation-status')||{}).textContent||''};
    })()""" % DOC_NAME)
    print('打开文献后的默认状态：', json.dumps(quiet, ensure_ascii=False), '自动翻译请求：', auto_posts)
    if not quiet['panelHidden']:
        problems.append('打开文献后译文栏自己开了（用户要求默认不打开）：%s' % quiet)
    if auto_posts:
        problems.append('打开文献时自己发起了翻译请求（会白花 token）：%s' % auto_posts[:2])

    # ------------------------------- 1c 没打开译文栏时翻页，也不得偷偷翻译（用户担心偷跑 token）
    cdp.events.clear()
    cdp.evaluate("""(async()=>{
      for(const page of [2,3,2,1]){await go(page);await new Promise(r=>setTimeout(r,900));}
      await new Promise(r=>setTimeout(r,1500));
    })()""")
    cdp.evaluate('1')          # 排空 socket，把这段等待里的事件都收进来
    leaked = [{'method': event['params']['request']['method'], 'url': event['params']['request']['url']}
              for event in cdp.events
              if event.get('method') == 'Network.requestWillBeSent'
              and event['params']['request']['method'] in ('POST', 'PUT', 'PATCH', 'DELETE')
              and '/translation' in event['params']['request']['url']]
    page_state = cdp.evaluate("""(async()=>({page:state.page,
      panelHidden:document.querySelector('#translation-panel').hidden}))()""")
    print('翻页时的翻译请求：', json.dumps(leaked, ensure_ascii=False), page_state)
    if leaked:
        problems.append('没打开译文栏、只是翻页就发起了翻译请求（用户担心的偷跑 token）：%s' % leaked[:3])
    if not page_state['panelHidden']:
        problems.append('翻页过程中译文栏被打开了：%s' % page_state)

    # ---------------------------------------------------------------- 2/3 右侧译文栏与排版
    sidebar = cdp.evaluate("""(async()=>{
      const before=await api('/documents/'+state.doc.id+'/translation/pages/'+state.page);
      document.querySelector('#translate-page').click();
      await new Promise(r=>setTimeout(r,900));
      const afterOpen={panelHidden:document.querySelector('#translation-panel').hidden,
                       segments:document.querySelectorAll('.translation-segment').length,
                       status:(document.querySelector('.translation-status')||{}).textContent||''};
      // 打开译文栏只读取已保存的译文：这一步**不能**自己发起翻译（否则就是"一打开就翻"）。
      const button=document.querySelector('#translation-page-action');
      button.click();
      for(let i=0;i<80;i++){
        await new Promise(r=>setTimeout(r,250));
        const rows=[...document.querySelectorAll('.translation-segment')];
        if(rows.length&&rows.every(r=>r.querySelector('.translation-text').textContent.indexOf('（还没有译文）')<0))break;
      }
      const panel=document.querySelector('#translation-panel');
      const viewport=document.querySelector('#viewport').getBoundingClientRect();
      const box=panel.getBoundingClientRect();
      const reader=document.querySelector('.reader');
      const rows=[...document.querySelectorAll('.translation-segment')];
      const text=rows.length?rows[0].querySelector('.translation-text'):null;
      return {hidden:panel.hidden,bodyClass:document.body.className,
              beforeSaved:before.segments.filter(row=>row.text).length,
              afterOpen:afterOpen,
              readerDisplay:getComputedStyle(reader).display,
              panelColumn:getComputedStyle(panel).gridColumnStart,
              viewportColumn:getComputedStyle(document.querySelector('#viewport')).gridColumnStart,
              panel:[Math.round(box.left),Math.round(box.top),Math.round(box.width),Math.round(box.height)],
              viewport:[Math.round(viewport.left),Math.round(viewport.top),Math.round(viewport.width),Math.round(viewport.height),Math.round(viewport.right)],
              sideBySide:Math.round(box.left)>=Math.round(viewport.right)-2,
              sameRow:Math.abs(Math.round(box.top)-Math.round(viewport.top))<40,
              sameHeight:Math.abs(Math.round(box.height)-Math.round(viewport.height))<60,
              segments:rows.length,
              texts:rows.map(r=>r.querySelector('.translation-text').textContent).slice(0,3),
              fontSize:text?getComputedStyle(text).fontSize:null,
              paperVisible:getComputedStyle(document.querySelector('#paper')).display!=='none',
              toolbarLabel:document.querySelector('#translate-page').textContent.trim()};
    })()""")
    print('右侧译文栏：', json.dumps(sidebar, ensure_ascii=False))
    if sidebar['hidden'] or not sidebar['segments']:
        problems.append('点工具栏按钮没有在右侧打开译文栏')
    if sidebar['afterOpen']['segments'] and not sidebar['beforeSaved']:
        problems.append('刚打开译文栏就有译文了（说明打开时自己发起了翻译）：%s' % sidebar['afterOpen'])
    if not sidebar['beforeSaved'] and '翻译这一页' not in sidebar['afterOpen']['status']:
        problems.append('还没翻译时状态行没有告诉用户点哪里：%s' % sidebar['afterOpen']['status'])
    if not sidebar['sideBySide'] or not sidebar['sameRow']:
        problems.append('译文栏没有显示在原文页右侧同一行（用户要求右侧一栏，不是底部一块）')
    if not sidebar['paperVisible']:
        problems.append('打开译文栏后原文页被藏起来了（两栏应当同时可见）')
    if MARK not in ' '.join(sidebar['texts']):
        problems.append('译文栏显示的不是翻译服务返回的内容（没拿到「%s」）：%s' % (MARK, sidebar['texts'][:1]))
    # 字号不再写死 15px：它 = 原文正文字号 × 原文显示比例（见 3d 的精确核对）。
    try:
        sidebar_font = float(str(sidebar['fontSize']).replace('px', ''))
    except ValueError:
        sidebar_font = 0
    if not 4 <= sidebar_font <= 48:
        problems.append('译文正文的字号超出合理范围：%s' % sidebar['fontSize'])

    # ---------------------------------------------------------------- 3b 一整页连续排版
    flowing = cdp.evaluate("""(()=>{
      const page=document.querySelector('.translation-page');
      const rows=[...document.querySelectorAll('.translation-segment')];
      const style=rows.length?getComputedStyle(rows[0]):null;
      return {pages:document.querySelectorAll('.translation-page').length,rows:rows.length,
              rowBackground:style?style.backgroundColor:null,
              rowBorder:style?style.borderTopWidth:null,
              pageBackground:page?getComputedStyle(page).backgroundColor:null,
              toolsOpacity:rows.length?getComputedStyle(rows[0].querySelector('.translation-segment-tools')).opacity:null};
    })()""")
    print('译文整页排版：', json.dumps(flowing, ensure_ascii=False))
    if not flowing['pages'] or not flowing['rows']:
        problems.append('译文没有装进整页容器（.translation-page）')
    if flowing['rowBackground'] != 'rgba(0, 0, 0, 0)':
        problems.append('译文段落仍有自己的底色（看起来还是一块一块的）：%s' % flowing['rowBackground'])
    if flowing['rowBorder'] not in ('0px', None):
        problems.append('译文段落仍有边框（看起来还是一块一块的）：%s' % flowing['rowBorder'])
    if flowing['toolsOpacity'] != '0':
        problems.append('段落的校对按钮默认应当是隐藏的（移上去才出现），否则打断整页阅读：%s' % flowing['toolsOpacity'])

    # ---------------------------------------------------------------- 3c 收起原文（只看译文）
    duplicates = cdp.evaluate("""(()=>{
      const inPanel=[...document.querySelectorAll('#translation-panel button')].filter(b=>b.textContent.includes('收起原文'));
      const inToolbar=[...document.querySelectorAll('#toolbar button')].filter(b=>b.textContent.includes('收起原文'));
      const all=[...document.querySelectorAll('button')].filter(b=>b.textContent.trim()==='收起原文');
      return {inPanel:inPanel.length,inToolbar:inToolbar.length,all:all.length};
    })()""")
    print('「收起原文」按钮：', duplicates)
    if duplicates['all'] > 1:
        problems.append('「收起原文」出现了不止一个（用户反馈重复）：%s' % duplicates)

    hide = cdp.evaluate("""(async()=>{
      const before={panelWidth:Math.round(document.querySelector('#translation-panel').getBoundingClientRect().width),
                    viewportDisplay:getComputedStyle(document.querySelector('#viewport')).display};
      const button=document.querySelector('#toggle-original');
      if(button)button.click();
      await new Promise(r=>setTimeout(r,500));
      const after={bodyClass:document.body.className,
                   viewportDisplay:getComputedStyle(document.querySelector('#viewport')).display,
                   panelWidth:Math.round(document.querySelector('#translation-panel').getBoundingClientRect().width),
                   panelHidden:document.querySelector('#translation-panel').hidden,
                   toolbarLabel:button?button.textContent:null,
                   rows:document.querySelectorAll('.translation-segment').length};
      if(button)button.click();
      await new Promise(r=>setTimeout(r,500));
      return {before,after,
              restored:{bodyClass:document.body.className,
                        viewportDisplay:getComputedStyle(document.querySelector('#viewport')).display,
                        toolbarLabel:button?button.textContent:null}};
    })()""")
    print('收起原文：', json.dumps(hide, ensure_ascii=False))
    if hide['after']['viewportDisplay'] != 'none':
        problems.append('「收起原文」没有把原文页收起来')
    if hide['after']['panelHidden'] or not hide['after']['rows']:
        problems.append('「收起原文」把译文也一起收掉了（应当只收原文）')
    if hide['after']['panelWidth'] <= hide['before']['panelWidth']:
        problems.append('收起原文后译文栏没有占满阅读区')
    if hide['restored']['viewportDisplay'] == 'none':
        problems.append('再次点击没有把原文放回来')
    if hide['after']['toolbarLabel'] != '显示原文' or hide['restored']['toolbarLabel'] != '收起原文':
        problems.append('工具栏按钮没有跟着状态改文案：%s' % hide['after']['toolbarLabel'])

    # ------------------------------------------- 3d 按原文分栏排版（PDF 的字号与缩放原理）
    # 定稿做法：栏数照抄原文、字号按 PDF 正文行高换算、段落按顺序在栏内流动。
    # 这里同时验证"没有回到逐段绝对定位"——那正是上一版挤在一起的原因。
    layout = cdp.evaluate("""(async()=>{
      const page=document.querySelector('.translation-page');
      const flow=document.querySelector('.translation-flow');
      const rows=[...document.querySelectorAll('.translation-flow .translation-segment')];
      const first=rows[0];
      const style=first?getComputedStyle(first):null;
      const pageBox=page?page.getBoundingClientRect():null;
      const boxes=[];
      for(const row of rows){const box=row.getBoundingClientRect();
        boxes.push([Math.round(box.left),Math.round(box.top),Math.round(box.right),Math.round(box.bottom)]);}
      // 顺带核对接口真的把版面参数给了界面（页眉页脚识别与栏数就靠它），
      // 以及"译文字号由用户定"——原文页的字号量出来只作参考，不再用来推算译文。
      const apiPage=await api('/documents/'+state.doc.id+'/translation/pages/'+state.page);
      const paper=document.querySelector('#paper').getBoundingClientRect();
      const glyphs=[...document.querySelectorAll('#text-layer > span')]
        .map(span=>span.getBoundingClientRect().height).filter(height=>height>1);
      glyphs.sort((a,b)=>a-b);
      return {hasPage:!!page,hasFlow:!!flow,
              fontVar:page?page.style.getPropertyValue('--page-font'):null,
              columnVar:page?page.style.getPropertyValue('--page-columns'):null,
              columnCount:flow?getComputedStyle(flow).columnCount:null,
              position:style?style.position:null,
              inlineLeft:first?first.style.left:'',inlineTop:first?first.style.top:'',
              fontSize:style?style.fontSize:null,
              paperWidth:Math.round(paper.width),
              originalGlyphHeight:glyphs.length?Math.round(glyphs[Math.floor(glyphs.length/2)]*10)/10:null,
              pageMinHeight:page?page.style.minHeight:null,
              pageWidth:pageBox?Math.round(pageBox.width):null,
              apiColumns:apiPage.columns,apiLineHeight:apiPage.body_line_height,
              apiFontSize:apiPage.body_font_size,
              apiPageSize:apiPage.page_size,apiKinds:[...new Set(apiPage.segments.map(r=>r.kind))],
              apiFurniture:apiPage.furniture,
              rows:rows.length,boxes:boxes};
    })()""")
    print('译文整页排版：', json.dumps({k: v for k, v in layout.items() if k != 'boxes'}, ensure_ascii=False))
    if not layout['hasPage'] or not layout['hasFlow']:
        problems.append('译文没有整页容器或分栏容器：%s' % layout)
    if layout['position'] == 'absolute' or layout['inlineLeft'] or layout['inlineTop']:
        problems.append('译文段落又回到"逐段绝对定位"（那正是被压小、挤在一起的原因）：%s'
                        % {'position': layout['position'], 'left': layout['inlineLeft'], 'top': layout['inlineTop']})
    if not layout['fontVar'] or not layout['columnVar']:
        problems.append('整页没有写入版面参数（--page-font / --page-columns）：%s' % layout)
    if layout['columnCount'] not in ('auto', '1'):
        problems.append('译文栏又变成分栏了（分栏会把长段落从中间劈开）：%s' % layout['columnCount'])
    if not layout['pageMinHeight']:
        problems.append('整页没有按原页宽高比给出最小高度：%s' % layout)
    if not (layout['apiPageSize'] or {}).get('height'):
        problems.append('接口没有给出原页尺寸：%s' % layout['apiPageSize'])
    if not layout['apiKinds'] or any(kind not in ('body', 'furniture', 'note') for kind in layout['apiKinds']):
        problems.append('段落的版面类型（正文/页眉页脚/注释）取值不合法：%s' % layout['apiKinds'])
    # 字号现在**就是用户设的 px**，不再跟着原文的字号/缩放走（用户第五次反馈）。
    try:
        font_px = float(str(layout['fontSize']).replace('px', ''))
    except ValueError:
        font_px = 0
    if not 10 <= font_px <= 32:
        problems.append('译文字号不在控件允许的范围（10~32px）：%s' % layout['fontSize'])
    if abs(font_px - float(layout['fontVar'].replace('px', ''))) > 0.1:
        problems.append('--page-font 与实际算出来的字号不一致：%s' % layout)
    # 分栏流动里的段落不允许互相重叠：两栏左右分开，同栏内上下相接。
    for index, one in enumerate(layout['boxes']):
        clash = next((other for other in layout['boxes'][index + 1:]
                      if min(one[2], other[2]) - max(one[0], other[0]) > 2
                      and min(one[3], other[3]) - max(one[1], other[1]) > 2), None)
        if clash:
            problems.append('译文段落互相重叠：%s / %s' % (one, clash))
            break

    # ------------------------------------------- 3e 「翻译这一页」在翻译期间要变成「正在翻译……」
    busy = cdp.evaluate("""(async()=>{
      const button=document.querySelector('#translation-page-action');
      if(!button)return {error:'译文栏里没有「翻译这一页」按钮'};
      for(let i=0;i<80&&button.textContent.trim()!=='翻译这一页';i++)await new Promise(r=>setTimeout(r,250));
      const before=button.textContent.trim();
      button.click();
      const during=button.textContent.trim();
      const disabledDuring=button.disabled;
      for(let i=0;i<80&&button.textContent.trim()==='正在翻译……';i++)await new Promise(r=>setTimeout(r,250));
      return {before:before,during:during,disabledDuring:disabledDuring,after:button.textContent.trim()};
    })()""")
    print('翻译按钮状态：', json.dumps(busy, ensure_ascii=False))
    if busy.get('error'):
        problems.append(busy['error'])
    else:
        if busy['during'] != '正在翻译……':
            problems.append('点「翻译这一页」后按钮没有变成「正在翻译……」：%s' % busy)
        if not busy['disabledDuring']:
            problems.append('翻译期间按钮没有禁用（会重复发起请求）：%s' % busy)
        if busy['after'] != '翻译这一页':
            problems.append('翻译结束后按钮没有恢复成「翻译这一页」：%s' % busy)

    # ------------------------------------------- 3f 原文缩放**不再**改变译文字号
    # 用户第五次反馈："字号不必一样大了，因为即使双栏排版也不完全一样" —— 字号现在由用户定。
    zoom = cdp.evaluate("""(async()=>{
      const font=()=>parseFloat(getComputedStyle(document.querySelector('.translation-page'))
                               .getPropertyValue('--page-font'));
      const paperWidth=()=>Math.round(document.querySelector('#paper').getBoundingClientRect().width);
      const base={font:font(),paper:paperWidth(),zoom:state.zoom};
      state.zoom=3;resizePage();
      await new Promise(r=>setTimeout(r,300));
      const zoomed={font:font(),paper:paperWidth(),zoom:state.zoom};
      state.zoom=1;resizePage();
      await new Promise(r=>setTimeout(r,300));
      const restored={font:font(),paper:paperWidth(),zoom:state.zoom};
      return {base,zoomed,restored,scroll:document.querySelector('.translation-segments').scrollTop};
    })()""")
    print('原文缩放与译文字号：', json.dumps(zoom, ensure_ascii=False))
    if zoom['zoomed']['paper'] <= zoom['base']['paper']:
        problems.append('把原文放大到 300% 没有改变原文页宽度（缩放没生效）：%s' % zoom)
    if abs(zoom['zoomed']['font'] - zoom['base']['font']) > 0.01:
        problems.append('原文缩放改变了译文字号（字号应由用户控件说了算）：%s' % zoom)
    if abs(zoom['restored']['font'] - zoom['base']['font']) > 0.01:
        problems.append('原文缩回 100% 后译文字号变了：%s' % zoom)

    # ------------------------------------------- 3g 译文可以单独调字号（用户要求）
    fonts = cdp.evaluate("""(async()=>{
      const page=()=>document.querySelector('.translation-page');
      const font=()=>parseFloat(getComputedStyle(page()).getPropertyValue('--page-font'));
      const text=()=>parseFloat(getComputedStyle(document.querySelector('.translation-text')).fontSize);
      const label=()=>document.querySelector('.translation-font button.active')
        ||[...document.querySelectorAll('.translation-font button')].find(b=>/^[0-9]+$/.test(b.textContent.trim()));
      const normal=font(),shownText=text(),stored=localStorage.getItem('reader-translation-font');
      const up=[...document.querySelectorAll('.translation-font button')].find(b=>b.textContent==='A+');
      const down=[...document.querySelectorAll('.translation-font button')].find(b=>b.textContent==='A\u2212');
      const reset=[...document.querySelectorAll('.translation-font button')].find(b=>/^[0-9]+$/.test(b.textContent.trim()));
      up.click();await new Promise(r=>setTimeout(r,150));
      const bigger={font:font(),text:text(),label:label().textContent.trim(),stored:localStorage.getItem('reader-translation-font')};
      if(down){down.click();down.click();}
      await new Promise(r=>setTimeout(r,150));
      const smaller={font:font(),text:text(),label:label().textContent.trim()};
      reset.click();await new Promise(r=>setTimeout(r,150));
      return {normal,shownText,bigger,smaller,back:{font:font(),text:text(),label:label().textContent.trim()},
              storedAtStart:stored,
              hasDown:!!down,hasReset:!!reset,
              paperHidden:getComputedStyle(document.querySelector('#viewport')).display};
    })()""")
    print('译文单独调字号：', json.dumps(fonts, ensure_ascii=False))
    if not fonts['hasDown'] or not fonts['hasReset']:
        problems.append('译文栏里没有字号控件（A− / 数值 / A+）：%s' % fonts)
    if fonts['bigger']['font'] <= fonts['normal']:
        problems.append('点 A+ 没有把译文调大：%s' % fonts)
    if fonts['bigger']['text'] <= fonts['shownText']:
        problems.append('调了字号但译文正文实际渲染的字号没变（用户说"字号调整目前无效"）：%s' % fonts)
    if fonts['smaller']['font'] >= fonts['normal']:
        problems.append('点 A− 没有把译文调小：%s' % fonts)
    if abs(fonts['back']['font'] - fonts['normal']) > 0.1:
        problems.append('点数值没有回到默认字号：%s' % fonts)
    if not fonts['bigger']['label'].isdigit() or int(fonts['bigger']['label']) != int(fonts['bigger']['font']):
        problems.append('控件显示的不是当前字号：%s' % fonts['bigger'])
    if fonts['storedAtStart'] and int(float(fonts['storedAtStart'])) != int(fonts['normal']):
        problems.append('本机存的字号与当前字号不一致：%s' % fonts)
    if fonts['bigger']['stored'] != fonts['bigger']['label']:
        problems.append('字号没有记进本机存储：%s' % fonts['bigger'])
    # 调字号只动译文，不动原文页的字号与几何。
    if fonts['paperHidden'] == 'none':
        problems.append('调译文字号把原文页藏起来了：%s' % fonts)

    # ---------------------------------------------------------------- 3c 悬停译文 → 原文页高亮
    hover = cdp.evaluate("""(async()=>{
      const row=document.querySelector('.translation-segment');
      if(!row)return {error:'没有译文段落'};
      const box=row.getBoundingClientRect();
      return {x:Math.round(box.left+box.width/2),y:Math.round(box.top+Math.min(20,box.height/2)),
              located:row.dataset.located||null,hasBox:!!row.dataset.bbox,index:row.dataset.index};
    })()""")
    print('准备悬停段落：', hover)
    if hover.get('error'):
        problems.append(hover['error'])
    else:
        mouse('mouseMoved', hover['x'], hover['y'])
        time.sleep(0.4)
        marked = cdp.evaluate("""(()=>{
          const layer=document.querySelector('#translation-source-layer');
          const marks=[...document.querySelectorAll('.translation-locate')];
          const rect=marks.length?marks[0].getBoundingClientRect():null;
          return {layer:!!layer,layerHidden:layer?layer.hidden:null,
                  layerParent:layer&&layer.parentElement?layer.parentElement.id:null,
                  marks:marks.length,
                  markRect:rect?[Math.round(rect.left),Math.round(rect.top),Math.round(rect.width),Math.round(rect.height)]:null,
                  markDisplay:marks.length?getComputedStyle(marks[0]).display:null,
                  markVisible:marks.length?marks[0].checkVisibility?marks[0].checkVisibility():null:null};
        })()""")
        print('悬停后的高亮：', json.dumps(marked, ensure_ascii=False))
        if not marked['layer'] or marked['layerHidden']:
            problems.append('悬停译文时没有在原文页上出现高亮层：%s' % marked)
        elif not marked['marks'] or not marked['markRect'] or marked['markRect'][2] < 4:
            problems.append('高亮层里没有可见的定位框（旧版有这个效果，不能丢）：%s' % marked)
        # 移开后应当收起来
        mouse('mouseMoved', 8, 8)
        time.sleep(0.3)
        cleared = cdp.evaluate("""(()=>{const layer=document.querySelector('#translation-source-layer');
          return {exists:!!layer,hidden:layer?layer.hidden:null};})()""")
        print('移开后的高亮：', cleared)
        if cleared['exists'] and cleared['hidden'] is False:
            problems.append('鼠标移开后高亮没有收起：%s' % cleared)

    # ------------------------------------------- 3h 直接对译文加批注（锚点必须是原文）
    # 用户要求：译文也能批注，但批注真正生效在原文，且批注栏里原文与译文同时显示。
    anchor = cdp.evaluate("""(async()=>{
      // 段落里可能已经有"只标一小段"的标记（那样第一个子节点是 span），所以找**文本节点**来选。
      const usable=node=>{const list=[];for(const child of node.childNodes){
        if(child.nodeType===3&&child.length>=20)list.push(child);}return list;};
      const article=[...document.querySelectorAll('.translation-segment')]
        .find(item=>usable(item.querySelector('.translation-text')||{childNodes:[]}).length);
      if(!article)return {error:'这一段没有译文文字（先翻译这一页）'};
      const paragraph=article.querySelector('.translation-text');
      const node=usable(paragraph)[0];
      const range=document.createRange();
      range.setStart(node,0);range.setEnd(node,Math.min(40,node.length));
      const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
      const box=paragraph.getBoundingClientRect();
      // 真实指针事件：translation.js 在译文栏的 pointerup 里把选文换成"对应的原文"。
      paragraph.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,button:0,
        clientX:Math.round(box.left+20),clientY:Math.round(box.top+8)}));
      for(let i=0;i<40;i++){
        await new Promise(r=>setTimeout(r,150));
        if(state.selectionOrigin==='translation')break;
      }
      const bar=document.querySelector('#selection-tools');
      const mark=bar?[...bar.querySelectorAll('button')].find(b=>b.textContent==='段落批注'):null;
      const markBox=mark?mark.getBoundingClientRect():null;
      return {selected:selection?selection.toString():'',anchored:state.selection,
              rects:state.selectionRects.length,origin:state.selectionOrigin,
              barVisible:bar?!bar.hidden:null,
              originalMarks:document.querySelectorAll('#paper .selected-overlay div').length,
              annotateButton:mark?{x:Math.round(markBox.left+markBox.width/2),
                                    y:Math.round(markBox.top+markBox.height/2)}:null,
              translatedText:(article.querySelector('.translation-text')||{}).textContent||''};
    })()""")
    print('从译文选文：', json.dumps(anchor, ensure_ascii=False))
    if anchor.get('error'):
        problems.append(anchor['error'])
    else:
        if anchor['origin'] != 'translation':
            problems.append('在译文栏里选文没有走"锚定原文"这条路：%s'
                            % {k: anchor[k] for k in ('origin', 'rects')})
        if not anchor['anchored'] or anchor['anchored'] == anchor['selected']:
            problems.append('批注锚点还是译文本身，没有换成对应的原文：%s'
                            % {'selected': anchor['selected'][:40], 'anchored': (anchor['anchored'] or '')[:40]})
        if not anchor['rects']:
            problems.append('锚定原文后没有给出原文的版面框（批注高亮会落空）：%s' % anchor)
        if not anchor['originalMarks']:
            problems.append('译文选文没有在原文页上标出对应区域：%s' % anchor)
        if not anchor['barVisible']:
            problems.append('在译文栏里选文没有出现选文操作条：%s' % anchor)
        if not anchor['annotateButton']:
            problems.append('选文操作条上没有「段落批注」按钮：%s' % anchor)
        else:
            # 真实鼠标点「段落批注」→ 打开批注弹窗
            mouse('mousePressed', anchor['annotateButton']['x'], anchor['annotateButton']['y'], buttons=1)
            mouse('mouseReleased', anchor['annotateButton']['x'], anchor['annotateButton']['y'])
            time.sleep(0.5)
            dialog = cdp.evaluate("""(()=>{const box=document.querySelector('#annotation-dialog');
              return {open:box?box.open:null,quote:(document.querySelector('#annotation-quote')||{}).textContent||'',
                      hint:!!document.querySelector('.translation-anchor-hint'),
                      draft:annotationDraft?{page:annotationDraft.page,rects:(annotationDraft.rects||[]).length,
                                             quote:(annotationDraft.quote||'').slice(0,40)}:null};})()""")
            print('批注弹窗：', json.dumps(dialog, ensure_ascii=False))
            if not dialog['open']:
                problems.append('点「段落批注」没有打开批注弹窗：%s' % dialog)
            elif dialog['quote'].strip() != (anchor['anchored'] or '').strip()[:len(dialog['quote'].strip())]:
                problems.append('批注弹窗里的摘引不是锚定的原文：%s' % dialog)
            if not dialog['hint']:
                problems.append('从译文进来的批注没有说明"锚点已换成原文"：%s' % dialog)
            if dialog['draft'] and not dialog['draft']['rects']:
                problems.append('批注草稿没有带上原文的版面框：%s' % dialog)
            # 保存这条批注，再看批注栏里是不是原文+译文同时显示
            saved = cdp.evaluate("""(async()=>{
              document.querySelector('#annotation-content').value='探针：对译文的批注';
              const form=document.querySelector('#annotation-form');
              form.requestSubmit(form.querySelector('button.primary'));
              await new Promise(r=>setTimeout(r,1500));
              // 保存失败时弹窗会留着（模态弹窗会挡住后面所有真实鼠标操作），所以无论如何先收起来。
              const box=document.querySelector('#annotation-dialog');
              const stillOpen=box?box.open:false;
              if(stillOpen)box.close();
              tab('annotations');
              await new Promise(r=>setTimeout(r,1500));
              const notes=[...document.querySelectorAll('#annotations .note')];
              const mine=notes.find(note=>note.textContent.indexOf('探针：对译文的批注')>=0);
              const bilingual=mine?mine.querySelector('.annotation-bilingual'):null;
              const result={notes:notes.length,found:!!mine,stillOpen:stillOpen,
                      quote:mine?(mine.querySelector('blockquote')||{}).textContent||'':null,
                      bilingual:!!bilingual,text:bilingual?bilingual.textContent.slice(0,600):null};
              tab('chat');       // 后面的检查都在阅读区，切回来
              return result;
            })()""")
            print('从译文加的批注：', json.dumps(saved, ensure_ascii=False))
            if saved['stillOpen']:
                problems.append('批注没有保存成功（弹窗还开着）：%s' % saved)
            if not saved['found']:
                problems.append('从译文加的批注没有出现在批注栏里：%s' % saved)
            elif not saved['bilingual']:
                problems.append('这条批注在批注栏里没有同时显示原文与译文：%s' % saved)
            elif MARK not in (saved['text'] or ''):
                problems.append('批注栏里的译文不是服务返回的内容：%s' % saved['text'])

            # 用户第七次反馈："译文批注只能成段批注，无法对其中部分内容进行批注"。
            # 现在：选中译文里的**一小段**加批注，只有那一小段被标出来。
            partial = cdp.evaluate("""(async()=>{
              // 先强制重译第 1 页：保证有完整的、未被人为改写的译文（前几组改过第一段）。
              await api('/documents/'+state.doc.id+'/translation/pages/1',{method:'POST',
                headers:{'Content-Type':'application/json'},body:JSON.stringify({force:true})});
              translationPanel().refresh();
              await new Promise(r=>setTimeout(r,1200));
              const usable=()=>[...document.querySelectorAll('.translation-segment')].find(item=>{
                const node=item.querySelector('.translation-text');
                return node&&[...node.childNodes].some(child=>child.nodeType===3&&child.length>=40);
              });
              for(let i=0;i<20&&!usable();i++)await new Promise(r=>setTimeout(r,200));
              const article=usable();
              if(!article)return {error:'找不到"含足够长文本节点"的译文段落'};
              const paragraph=article.querySelector('.translation-text');
              const node=[...paragraph.childNodes].find(child=>child.nodeType===3&&child.length>=40);
              const from=0,to=Math.min(20,node.length);
              const excerpt=node.textContent.slice(from,to);
              const range=document.createRange();
              range.setStart(node,from);range.setEnd(node,to);
              const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
              const box=paragraph.getBoundingClientRect();
              paragraph.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,button:0,
                clientX:Math.round(box.left+30),clientY:Math.round(box.top+10)}));
              for(let i=0;i<40;i++){await new Promise(r=>setTimeout(r,150));
                if(state.selectionOrigin==='translation')break;}
              const anchored={excerpt:excerpt,quote:(state.selection||'').slice(0,40),
                              rects:state.selectionRects.length};
              openAnnotation();
              await new Promise(r=>setTimeout(r,200));
              document.querySelector('#annotation-content').value='探针：只批注一小段';
              const form=document.querySelector('#annotation-form');
              form.requestSubmit(form.querySelector('button.primary'));
              await new Promise(r=>setTimeout(r,1800));
              const dialog=document.querySelector('#annotation-dialog');
              const stillOpen=dialog?dialog.open:false;
              if(stillOpen)dialog.close();
              tab('chat');
              await new Promise(r=>setTimeout(r,300));
              // 界面上：被标出来的应该**只有那一小段**，而不是整段
              const marks=[...document.querySelectorAll('.translation-segment .translation-mark')];
              const marked=marks.map(node=>node.textContent);
              const wholeMarked=!!document.querySelector('.translation-segment .translation-text.has-mark-highlight');
              const stored=await api('/documents/'+state.doc.id+'/annotations');
              const saved=stored.filter(row=>row.content==='探针：只批注一小段').pop();
              return {anchored,stillOpen,marked:marked.slice(0,3),markCount:marks.length,
                      wholeMarked:wholeMarked,
                      excerptStored:saved?saved.translation_excerpt:null,
                      anchorStored:saved?saved.quote:null,
                      anchorRects:saved?(saved.rects||[]).length:0,
                      paragraphLength:paragraph.textContent.length,
                      sourceLength:saved?((current_segment_source()||'').length):0};
              function current_segment_source(){
                const row=translationDiagnostics.current().segments.find(item=>String(item.index)===article.dataset.index);
                return row?row.source:'';
              }
            })()""")
            print('只批注一小段：', json.dumps(partial, ensure_ascii=False))
            if partial.get('error'):
                problems.append(partial['error'])
            elif partial['stillOpen']:
                problems.append('这一小段批注没有保存成功：%s' % partial)
            elif partial['excerptStored'] != partial['anchored']['excerpt'].strip():
                problems.append('选中的那一小段译文没有随批注存下来（partial 批注无从标记）：%s' % partial)
            elif partial['wholeMarked'] or not partial['marked']:
                problems.append('译文上不是"只标选中的那一小段"（又变成整段了）：%s' % partial)
            elif not any(item.strip() == partial['anchored']['excerpt'].strip()
                         for item in partial['marked']):
                problems.append('标出来的文字与选中的不一致：%s' % partial)
            # 用户反馈"内容依旧是一整段、位置划到不属于的位置"：摘引必须是**句子级**的那几句，
            # 框也必须是那几句的框，而不是整段。
            elif not partial['anchorStored'] or len(partial['anchorStored']) >= partial['sourceLength']:
                problems.append('批注摘引还是整段原文（应当是映射后的那几句）：%s' % partial)
            elif not partial['anchorRects']:
                problems.append('批注没有带原文那几句的框：%s' % partial)

            # 用户第十二、十三次反馈：
            #   ①浮条上的「高亮/下划线」曾被存成整页批注；
            #   ②「段落批注」按语义就该**两边都标整段**（高亮/下划线才只标所选内容）。
            # 这里用**真实鼠标拖选**走完三个按钮，逐个核对"样式 + 标记粒度"。
            for label, wanted, whole in (('高亮', 'highlight', False), ('下划线', 'underline', False),
                                         ('段落批注', 'margin', True)):
                drag = cdp.evaluate("""(async()=>{
                  const paragraph=[...document.querySelectorAll('.translation-segment .translation-text')]
                    .find(node=>[...node.childNodes].some(child=>child.nodeType===3&&child.length>=40));
                  if(!paragraph)return {error:'找不到可选的译文段落'};
                  const box=paragraph.getBoundingClientRect();
                  return {x:Math.round(box.left+30),y:Math.round(box.top+12)};
                })()""")
                if drag.get('error'):
                    problems.append(drag['error'])
                    break
                mouse('mousePressed', drag['x'], drag['y'], buttons=1)
                for step in (30, 60, 90, 120):
                    mouse('mouseMoved', drag['x'] + step, drag['y'], buttons=1)
                    time.sleep(0.05)
                mouse('mouseReleased', drag['x'] + 120, drag['y'])
                time.sleep(1.2)                     # 等 /translation/anchor 回来
                spot = cdp.evaluate("""(()=>{
                  const tools=document.querySelector('#selection-tools');
                  if(!tools||tools.hidden)return null;
                  const button=[...tools.querySelectorAll('button')].find(b=>b.textContent===%s);
                  if(!button)return null;
                  const rect=button.getBoundingClientRect();
                  return {x:Math.round(rect.left+rect.width/2),y:Math.round(rect.top+rect.height/2),
                          origin:state.selectionOrigin,rects:(state.selectionRects||[]).length};
                })()""" % json.dumps(label))
                print('浮条「%s」入口：' % label, json.dumps(spot, ensure_ascii=False))
                if not spot:
                    problems.append('译文选文后浮条上没有「%s」可用（或浮条没出现）' % label)
                    break
                if spot['origin'] != 'translation' or not spot['rects']:
                    problems.append('译文选文没有锚定到原文（点「%s」就会变成整页批注）：%s' % (label, spot))
                    break
                mouse('mousePressed', spot['x'], spot['y'], buttons=1)
                mouse('mouseReleased', spot['x'], spot['y'])
                time.sleep(0.5)
                opened = cdp.evaluate("""(()=>({open:document.querySelector('#annotation-dialog').open,
                  hint:!!document.querySelector('.translation-anchor-hint')}))()""")
                if not opened['open']:
                    problems.append('点浮条「%s」没有打开批注弹窗：%s' % (label, opened))
                    break
                saved_bar = cdp.evaluate("""(async()=>{
                  document.querySelector('#annotation-content').value='探针：浮条%s';
                  const form=document.querySelector('#annotation-form');
                  form.requestSubmit(form.querySelector('button.primary'));
                  await new Promise(r=>setTimeout(r,1600));
                  const dialog=document.querySelector('#annotation-dialog');
                  const stillOpen=dialog?dialog.open:false;
                  if(stillOpen)dialog.close();
                  await new Promise(r=>setTimeout(r,600));
                  const rows=await api('/documents/'+state.doc.id+'/annotations');
                  const mine=rows.filter(row=>row.content==='探针：浮条%s').pop();
                  const marked=[...document.querySelectorAll('.translation-segment .translation-mark')]
                    .map(node=>node.textContent);
                  const paragraph=document.querySelector('.translation-segment .translation-text');
                  return {stillOpen:stillOpen,found:!!mine,style:mine?mine.style:null,
                          rects:mine?(mine.rects||[]).length:0,
                          excerpt:mine?mine.translation_excerpt:null,
                          quote:mine?(mine.quote||''):null,
                          paragraphLength:paragraph?paragraph.textContent.length:0,
                          inlineMarks:marked.length,
                          wholeMarked:!!document.querySelector('.translation-text.has-mark-%s')};
                })()""" % (label, label, wanted))
                print('浮条「%s」保存结果：' % label, json.dumps(saved_bar, ensure_ascii=False))
                if saved_bar['stillOpen'] or not saved_bar['found']:
                    problems.append('浮条「%s」的批注没有保存成功：%s' % (label, saved_bar))
                    break
                if saved_bar['style'] != wanted:
                    problems.append('浮条「%s」存下来的样式是 %s（应为 %s）——先设样式会被 '
                                    'experience.js 的包装覆盖掉' % (label, saved_bar['style'], wanted))
                if not saved_bar['rects'] or saved_bar['style'] == 'page':
                    problems.append('浮条「%s」被存成了整页批注（用户报过这个）：%s' % (label, saved_bar))
                if whole:
                    # 段落批注：两边都标整段 → 摘引是整段原文、不带"选中的那几个字"、译文整段标
                    if saved_bar['excerpt']:
                        problems.append('「段落批注」还带着"选中的那几个字"，应当标整段：%s' % saved_bar)
                    if not saved_bar['wholeMarked']:
                        problems.append('「段落批注」没有把译文整段标上（用户要求两边都是整段）：%s' % saved_bar)
                    if saved_bar['paragraphLength'] and len(saved_bar['quote'] or '') < 20:
                        problems.append('「段落批注」的摘引不像整段原文：%s' % saved_bar)
                else:
                    if not saved_bar['excerpt']:
                        problems.append('浮条「%s」没有记住选中的那一小段译文：%s' % (label, saved_bar))
                    if saved_bar['wholeMarked']:
                        problems.append('浮条「%s」把译文整段都标上了（应当只标所选内容）：%s'
                                        % (label, saved_bar))
            marks = cdp.evaluate("""(async()=>{
              const marked=()=>[...document.querySelectorAll('.translation-segment.has-annotations')];
              for(let i=0;i<20&&!marked().length;i++)await new Promise(r=>setTimeout(r,200));
              const item=marked()[0];
              if(!item)return {marked:0};
              const paragraph=item.querySelector('.translation-text');
              const inline=[...paragraph.querySelectorAll('.translation-mark')];
              const sample=inline[0]||paragraph;
              const box=sample.getBoundingClientRect();
              const style=getComputedStyle(sample);
              // 只看译文时（原文页收起来）标记也必须还在：那时用户只能看译文。
              document.querySelector('#toggle-original').click();
              await new Promise(r=>setTimeout(r,300));
              const onlyTranslation={marked:marked().length,
                                     visible:sample.checkVisibility?sample.checkVisibility():null};
              document.querySelector('#toggle-original').click();
              await new Promise(r=>setTimeout(r,300));
              return {marked:marked().length,
                      inlineMarks:inline.length,
                      classes:paragraph.className+' | '+sample.className,
                      background:style.backgroundColor,
                      decoration:style.textDecorationLine+' '+style.textDecorationColor,
                      borderLeft:style.borderLeftWidth+' '+style.borderLeftColor,
                      mark:sample.style.getPropertyValue('--mark')||paragraph.style.getPropertyValue('--mark'),
                      title:item.title.slice(0,40),
                      chips:item.querySelectorAll('.translation-annotation-chip').length,
                      x:Math.round(box.left+Math.max(8,box.width/2)),
                      y:Math.round(box.top+8),
                      onlyTranslation};
            })()""")
            print('译文上的批注标记：', json.dumps(marks, ensure_ascii=False))
            if not marks.get('marked'):
                problems.append('这条批注没有在译文上留下标记（用户要求译文上也有标识）：%s' % marks)
            elif marks['chips']:
                problems.append('译文上又用回了自造的小标签（用户要求采用原文的显示方式）：%s' % marks)
            elif not marks['mark'] or (marks['background'] in ('rgba(0, 0, 0, 0)', 'transparent')
                                       and not marks['decoration'].startswith('underline')
                                       and marks['borderLeft'].startswith('0px')):
                problems.append('译文上的批注没有任何高亮/下划线/侧线样式：%s' % marks)
            elif not marks['onlyTranslation']['marked'] or marks['onlyTranslation']['visible'] is False:
                problems.append('收起原文（只看译文）时批注标记不见了：%s' % marks)
            else:
                mouse('mousePressed', marks['x'], marks['y'], buttons=1)
                mouse('mouseReleased', marks['x'], marks['y'])
                time.sleep(0.5)
                popover = cdp.evaluate("""(()=>{const box=document.querySelector('.annotation-popover');
                  return {hidden:box?box.hidden:null,
                          text:box?box.textContent.slice(0,160):null};})()""")
                print('点译文上的批注：', json.dumps(popover, ensure_ascii=False))
                if popover['hidden'] is not False:
                    problems.append('点译文上的批注标记没有打开批注（用户要求"可以点击查看"）：%s' % popover)
                elif '探针' not in (popover['text'] or ''):
                    problems.append('打开的批注内容不对：%s' % popover['text'])
                cdp.evaluate("(()=>{const box=document.querySelector('.annotation-popover');if(box)box.hidden=true;})()")

    # ------------------------------------------- 3i 在原文页上选的批注：译文也只标所选内容
    # 用户第十二次反馈：原文页上选文字做段落批注时，原文标的是所选文字、译文却整段都标上了。
    page_note = cdp.evaluate("""(async()=>{
      const page=await api('/documents/'+state.doc.id+'/translation/pages/'+state.page);
      const row=page.segments.find(item=>item.text&&item.source.split(/(?<=[.])\\s+/).length>1)
             || page.segments.find(item=>item.text);
      if(!row)return {error:'这一页没有可用的译文段落'};
      const sentence=row.source.split(/(?<=[.])\\s+/)[0];
      await api('/documents/'+state.doc.id+'/annotations',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({page:state.page,quote:sentence,content:'探针：原文页选的',
          rects:[[0.1,0.2,0.9,0.3]],color:'blue',style:'highlight'})});
      await loadAnnotations();
      await new Promise(r=>setTimeout(r,1200));
      const marks=[...document.querySelectorAll('.translation-segment .translation-mark')];
      const paragraph=[...document.querySelectorAll('.translation-segment .translation-text')]
        .find(node=>node.querySelector('.translation-mark'));
      return {sentence:sentence.slice(0,50),markCount:marks.length,
              marked:marks.map(node=>node.textContent.slice(0,40)),
              paragraphLength:paragraph?paragraph.textContent.length:0,
              wholeMarked:!!document.querySelector('.translation-text.has-mark-highlight')};
    })()""")
    print('原文页上选的批注在译文里的标记：', json.dumps(page_note, ensure_ascii=False))
    if page_note.get('error'):
        problems.append(page_note['error'])
    else:
        if not page_note['marked']:
            problems.append('原文页上选的批注没有在译文上标出对应内容：%s' % page_note)
        elif page_note['wholeMarked']:
            problems.append('原文页上选的批注把译文整段都标上了（用户报过这个）：%s' % page_note)
        elif page_note['paragraphLength'] and \
                len(page_note['marked'][0]) >= page_note['paragraphLength'] - 2:
            problems.append('标出来的还是整段：%s' % page_note)
    # ------------------------------------------- 3j 「重新翻译这一段」要有"翻译中……"
    busy_row = cdp.evaluate("""(async()=>{
      const rows=[...document.querySelectorAll('.translation-segment')];
      const item=rows.find(node=>[...node.querySelectorAll('button')]
        .some(button=>button.textContent==='重新翻译这一段'));
      if(!item)return {error:'没有可重译的段落'};
      const button=[...item.querySelectorAll('button')].find(b=>b.textContent==='重新翻译这一段');
      button.click();
      const during=button.textContent;
      const disabledDuring=button.disabled;
      let after=null;
      for(let i=0;i<60;i++){
        await new Promise(r=>setTimeout(r,250));
        const again=[...document.querySelectorAll('.translation-segment button')]
          .find(b=>b.textContent==='重新翻译这一段'||b.textContent==='翻译中……');
        if(again&&again.textContent==='重新翻译这一段'){after=again.textContent;break;}
      }
      return {during:during,disabledDuring:disabledDuring,after:after};
    })()""")
    print('「重新翻译这一段」的状态：', json.dumps(busy_row, ensure_ascii=False))
    if busy_row.get('error'):
        problems.append(busy_row['error'])
    else:
        if busy_row['during'] != '翻译中……':
            problems.append('点「重新翻译这一段」没有变成「翻译中……」：%s' % busy_row)
        if not busy_row['disabledDuring']:
            problems.append('翻译中按钮没有禁用：%s' % busy_row)
        if busy_row['after'] != '重新翻译这一段':
            problems.append('翻完没有恢复成「重新翻译这一段」：%s' % busy_row)

    # ---------------------------------------------------------------- 4 分栏拖动
    geom = cdp.evaluate("""(()=>{const s=document.querySelector('.translation-splitter').getBoundingClientRect();
      const panel=document.querySelector('#translation-panel').getBoundingClientRect();
      return {splitter:[Math.round(s.left),Math.round(s.top),Math.round(s.width),Math.round(s.height)],
              panelWidth:Math.round(panel.width)};})()""")
    print('拖动前：', geom)
    x = geom['splitter'][0] + geom['splitter'][2] / 2
    y = geom['splitter'][1] + max(20, geom['splitter'][3] / 2)
    mouse('mousePressed', x, y, buttons=1)
    for step in (-40, -80, -120):
        mouse('mouseMoved', x + step, y, buttons=1)
        time.sleep(0.06)
    mouse('mouseReleased', x - 120, y)
    time.sleep(0.3)
    dragged = cdp.evaluate("""(()=>({panelWidth:Math.round(document.querySelector('#translation-panel').getBoundingClientRect().width),
      viewportWidth:Math.round(document.querySelector('#viewport').getBoundingClientRect().width),
      stored:localStorage.getItem('reader-translation-width')}))()""")
    print('拖动分栏条后：', dragged)
    if dragged['panelWidth'] <= geom['panelWidth'] + 20:
        problems.append('向左拖动分栏条没有把译文栏拉宽')
    if not dragged['stored']:
        problems.append('译文栏宽度没有写进本机存储（下次打开会回到默认）')

    # ---------------------------------------------------------------- 4b 长段落整段显示、不被截断
    # 用户第六次反馈："译文的段落有时候会从中间截断"。这里往第 3 页写一段很长的原文，
    # 强制重译，然后核对**界面上显示出来的字数**与原文一致、且整段是连续的一栏。
    long_text = ' '.join(['Sentence %d explains the experimental setup and the evaluation protocol.' % index
                          for index in range(1, 41)])
    truncation = cdp.evaluate("""(async()=>{
      const doc=state.doc;
      await api('/documents/'+doc.id+'/pages/3/text',{method:'PUT',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({text:%s})});
      await api('/documents/'+doc.id+'/translation/pages/3',{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({force:true})});
      await go(3);await new Promise(r=>setTimeout(r,900));
      translationPanel().open();await new Promise(r=>setTimeout(r,400));
      translationPanel().refresh();await new Promise(r=>setTimeout(r,900));
      const page=await api('/documents/'+doc.id+'/translation/pages/3');
      const kinds=page.segments.map(row=>row.kind);
      const missing=page.segments.filter(row=>!row.text).length;
      const rows=[...document.querySelectorAll('.translation-flow .translation-segment')];
      const rendered=rows.map(row=>row.querySelector('.translation-text').textContent);
      const source=page.segments.map(row=>row.source);
      const clipped=rows.filter((row,index)=>{
        const text=row.querySelector('.translation-text');
        return text.scrollHeight>text.clientHeight+2&&getComputedStyle(text).overflow!=='visible';
      }).length;
      const list=document.querySelector('.translation-segments');
      return {kinds:kinds,missingSegments:missing,rows:rows.length,
              sourceChars:source.join('').replace(/\\s+/g,'').length,
              renderedChars:rendered.join('').replace(/\\s+/g,'').length,
              prefix:rendered[0]?rendered[0].slice(0,6):null,
              lastSource:source.length?source[source.length-1].slice(-40):null,
              lastRendered:rendered.length?rendered[rendered.length-1].slice(-40):null,
              clipped:clipped,scrollable:list.scrollHeight>list.clientHeight,
              // 用户反馈"段落中间阶段分为两部分翻译"：切开的几段必须装在同一个自然段容器里
              paragraphs:document.querySelectorAll('.translation-paragraph').length,
              groups:[...document.querySelectorAll('.translation-paragraph')]
                .map(node=>node.querySelectorAll('.translation-segment').length),
              paragraphsFromApi:[...new Set(page.segments.map(row=>row.para))].length,
              columns:getComputedStyle(document.querySelector('.translation-flow')).columnCount};
    })()""" % json.dumps(long_text))
    print('长段落：', json.dumps(truncation, ensure_ascii=False))
    if truncation['missingSegments']:
        problems.append('长段落有没翻的段（界面上会看起来"从中间截断"）：%s' % truncation)
    if truncation['rows'] != len(truncation['kinds']):
        problems.append('长段落没有全部渲染出来：%s' % truncation)
    if truncation['renderedChars'] < truncation['sourceChars']:
        problems.append('界面上显示的字数少于原文（被截断）：%s' % truncation)
    if truncation['lastSource'] not in (truncation['lastRendered'] or ''):
        problems.append('段落结尾没有显示出来（被截断）：%s' % truncation)
    if truncation['clipped']:
        problems.append('有段落被容器裁掉：%s' % truncation)
    if truncation['columns'] not in ('auto', '1'):
        problems.append('译文栏又变成分栏了（会把段落从中间劈开）：%s' % truncation)
    if any(kind != 'body' for kind in truncation['kinds']):
        problems.append('长段落的某一段被判成页眉/脚注（那段就不会翻译）：%s' % truncation['kinds'])
    # 一个原始自然段被切成几段时，界面上必须是**一个** .translation-paragraph
    if truncation['rows'] > 1 and truncation['paragraphs'] != 1:
        problems.append('切开的段落没有合成一个自然段（用户反馈"分为两部分翻译"）：%s' % truncation)
    # 回第 1 页：后面的检查（差异拖动、收起、改译文、选文、批注）都依赖有文字层的那一页。
    back = cdp.evaluate("""(async()=>{
      await go(1);
      for(let i=0;i<60;i++){
        await new Promise(r=>setTimeout(r,250));
        if(state.page===1&&translationDiagnostics.current().page===1)break;
      }
      return {page:state.page,pane:translationDiagnostics.current().page,
              rows:document.querySelectorAll('.translation-segment').length};
    })()""")
    print('长段落检查后回到第 1 页：', back)
    if back['page'] != 1 or back['pane'] != 1:
        problems.append('长段落检查后没有回到第 1 页，后面的检查会跑错页：%s' % back)

    # ---------------------------------------------------------------- 4c 整页翻译是并发的（速度）
    # 用户反馈"整页翻译的速度非常慢"。这里量墙钟时间：单段的耗时 vs 整页的耗时。
    # 串行的话整页 ≈ 段数 × 单段；并发（4 段同时）则明显更小。
    # 想看得清楚就把假服务设成每次回答要等一会儿：PROBE_DELAY_MS=400。
    timing = cdp.evaluate("""(async()=>{
      const doc=state.doc,page=await api('/documents/'+doc.id+'/translation/pages/3');
      const segments=page.segments.length;
      const clock=()=>performance.now();
      let started=clock();
      await api('/documents/'+doc.id+'/translation/pages/3/segments/0',{method:'POST',
        headers:{'Content-Type':'application/json'},body:'{}'});
      const single=clock()-started;
      started=clock();
      await api('/documents/'+doc.id+'/translation/pages/3',{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({force:true})});
      const whole=clock()-started;
      return {segments:segments,singleMs:Math.round(single),wholeMs:Math.round(whole),
              sequentialMs:Math.round(single*segments)};
    })()""")
    print('整页翻译耗时：', json.dumps(timing, ensure_ascii=False))
    if timing['segments'] > 1 and timing['wholeMs'] > timing['sequentialMs'] * 0.75:
        problems.append('整页翻译看起来还是串行的（耗时接近"段数 × 单段"）：%s' % timing)
    cdp.evaluate('1')

    # ---------------------------------------------------------------- 5 收起
    collected = cdp.evaluate("""(async()=>{
      translationPanel().close();
      await new Promise(r=>setTimeout(r,300));
      const panel=document.querySelector('#translation-panel');
      return {hidden:panel.hidden,bodyClass:document.body.className,
              viewportWidth:Math.round(document.querySelector('#viewport').getBoundingClientRect().width),
              toolbarLabel:document.querySelector('#translate-page').textContent.trim()};
    })()""")
    print('收起译文栏：', collected)
    if collected['hidden'] is not True or 'translation-open' in collected['bodyClass']:
        problems.append('「收起」没有关掉译文栏')
    if collected['viewportWidth'] < baseline['viewportWidth'] - 5:
        problems.append('收起后原文页没有恢复原来的宽度')
    cdp.evaluate("translationPanel().open();")
    time.sleep(0.6)

    # ---------------------------------------------------------------- 6 改译文 / 只重译这一段
    edited = cdp.evaluate("""(async()=>{
      const rows=[...document.querySelectorAll('.translation-segment')];
      const row=rows[0];
      [...row.querySelectorAll('button')].find(b=>b.textContent==='修改译文').click();
      await new Promise(r=>setTimeout(r,150));
      row.querySelector('.translation-editor').value='探针改写的译文';
      [...row.querySelectorAll('button')].find(b=>b.textContent==='保存修改').click();
      for(let i=0;i<40;i++){
        await new Promise(r=>setTimeout(r,200));
        const text=document.querySelector('.translation-segment .translation-text');
        if(text&&text.textContent==='探针改写的译文')break;
      }
      const first=document.querySelector('.translation-segment');
      const after={text:first.querySelector('.translation-text').textContent,
                   badges:[...first.querySelectorAll('.translation-badge')].map(b=>b.textContent)};
      [...first.querySelectorAll('button')].find(b=>b.textContent==='重新翻译这一段').click();
      for(let i=0;i<60;i++){
        await new Promise(r=>setTimeout(r,250));
        if(document.querySelector('.translation-segment .translation-text').textContent!=='探针改写的译文')break;
      }
      const again=document.querySelector('.translation-segment');
      return {after,retranslated:{text:again.querySelector('.translation-text').textContent,
                                  badges:[...again.querySelectorAll('.translation-badge')].map(b=>b.textContent)}};
    })()""")
    print('改译文再定点重译：', json.dumps(edited, ensure_ascii=False))
    if edited['after']['text'] != '探针改写的译文':
        problems.append('人工修改后的译文没有显示出来')
    if not any('人工修改' in badge for badge in edited['after']['badges']):
        problems.append('人工修改过的段落没有标注来源')
    if edited['retranslated']['text'] == '探针改写的译文' or MARK not in edited['retranslated']['text']:
        problems.append('「重新翻译这一段」没有用服务译文覆盖人工修改')
    if any('人工修改' in badge for badge in edited['retranslated']['badges']):
        problems.append('覆盖后仍声称是人工修改内容')

    # ---------------------------------------------------------------- 7 选文翻译（右键菜单）
    selection = cdp.evaluate("""(async()=>{
      setSelection('Deep learning improves the validation of the model.');
      await translateSelection();
      for(let i=0;i<40;i++){
        await new Promise(r=>setTimeout(r,200));
        const box=document.querySelector('#translation-selection');
        if(box&&!box.hidden&&box.querySelector('.translation-selection-target').textContent!=='正在翻译…')break;
      }
      const box=document.querySelector('#translation-selection');
      return {hidden:box.hidden,source:box.querySelector('.translation-selection-source').textContent,
              target:box.querySelector('.translation-selection-target').textContent,
              meta:box.querySelector('.translation-selection-meta').textContent};
    })()""")
    print('选文翻译：', json.dumps(selection, ensure_ascii=False))
    if selection['hidden'] or MARK not in selection['target']:
        problems.append('选文翻译没有返回服务译文：' + selection['target'][:160])

    menu = cdp.evaluate("""(()=>{
      const items=[];
      const original=showMenu;
      window.showMenu=(event,actions)=>{items.push(...actions.map(a=>a[0]));};
      setSelection('Deep learning');
      document.querySelector('#paper').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:300,clientY:300}));
      window.showMenu=original;
      return items;
    })()""")
    print('阅读区右键菜单项：', menu)
    for expected in ('翻译选中文字', '原文/译文对照'):
        if expected not in (menu or []):
            problems.append('右键菜单里缺少「%s」' % expected)

    # 选中文字的浮条（由 experience.js 建、translation.js 后挂）：第一项应当是「翻译」。
    floating = cdp.evaluate("""(async()=>{
      setSelection('Deep learning improves the validation of the model.');
      const paper=document.querySelector('#paper');
      const box=paper.getBoundingClientRect();
      const x=Math.round(box.left+40), y=Math.round(box.top+40);
      // 真实指针事件：experience.js 在 #paper 的 pointerup 里才显示浮条。
      for(const type of ['pointerdown','pointerup']){
        paper.dispatchEvent(new PointerEvent(type,{bubbles:true,clientX:x,clientY:y,button:0,buttons:type==='pointerdown'?1:0}));
      }
      await new Promise(r=>setTimeout(r,150));
      const tools=document.querySelector('#selection-tools');
      return {exists:!!tools,hidden:tools?tools.hidden:null,
              labels:tools?[...tools.querySelectorAll('button')].map(b=>b.textContent):null,
              firstAction:tools&&tools.firstElementChild?tools.firstElementChild.dataset.translationAction:null};
    })()""")
    print('选文浮条：', json.dumps(floating, ensure_ascii=False))
    if not floating['exists']:
        problems.append('页面上没有选文浮条（#selection-tools）')
    elif '翻译' not in (floating['labels'] or []):
        problems.append('选文浮条里缺少「翻译」：%s' % floating['labels'])
    elif floating['firstAction'] != 'selection':
        problems.append('「翻译」不在浮条的第一项：%s' % floating['labels'])

    # 译文栏内滚动时原文页不应跟着动（两栏各自滚动）。
    # 注意：译文整页的高度按**原页比例**缩放（PDF 的缩放原理），所以短页可能一屏装得下、
    # 这时"没滚动"是正确行为。因此这里断言"容器可滚动 + 内容超出时必须真的滚 + 不带动原文"。
    scroll = cdp.evaluate("""(async()=>{
      const list=document.querySelector('.translation-segments');
      const viewport=document.querySelector('#viewport');
      viewport.scrollTop=0;
      const max=list.scrollHeight-list.clientHeight;
      list.scrollTop=200;
      await new Promise(r=>setTimeout(r,200));
      return {list:list.scrollTop,viewport:viewport.scrollTop,overflowY:getComputedStyle(list).overflowY,
              max:max,scrollable:max>4};
    })()""")
    print('译文栏内滚动：', json.dumps(scroll, ensure_ascii=False))
    if scroll['overflowY'] not in ('auto', 'scroll'):
        problems.append('译文栏不是可滚动容器（长译文会被裁掉）：%s' % scroll)
    if scroll['max'] > 4 and abs(scroll['list'] - min(200, scroll['max'])) > 20:
        problems.append('译文栏内容超过一屏却没有跟着滚动：%s' % scroll)
    if scroll['viewport'] != 0:
        problems.append('在译文栏里滚动把原文页也滚走了（两栏应各自滚动）：%s' % scroll)

    # ---------------------------------------------------------------- 8 批注页双语（在第 2 页做）
    cdp.evaluate("""(async()=>{
      await go(2);await new Promise(r=>setTimeout(r,1200));
      const button=document.querySelector('#translation-page-action');
      if(button)button.click();
      for(let i=0;i<80;i++){
        await new Promise(r=>setTimeout(r,250));
        const rows=[...document.querySelectorAll('.translation-segment')];
        const text=rows.length?(rows.find(r=>r.dataset.index==='0')||rows[0]).querySelector('.translation-text').textContent:'';
        if(text&&text.indexOf('（还没有译文）')<0)break;
      }
    })()""")
    bilingual = cdp.evaluate("""(async()=>{
      const quote=(document.querySelector('.translation-segment details p')||{}).textContent||'';
      await post('/documents/'+state.doc.id+'/annotations',{page:2,quote:quote.slice(0,40),content:'探针批注'});
      await loadAnnotations();
      tab('annotations');
      await new Promise(r=>setTimeout(r,1500));
      // 批注列表按页码排序，所以不假定顺序：找出与这条批注同页、且带译文的那一块。
      const notes=[...document.querySelectorAll('#annotations .note')];
      const match=notes.map(note=>({note,box:note.querySelector('.annotation-bilingual')}))
        .filter(item=>item.box&&item.box.textContent.indexOf('这一页还没有译文')<0);
      const box=match.length?match[0].box:null;
      return {notes:notes.length,bilingual:!!box,visible:box?getComputedStyle(box).display:null,
              text:box?box.textContent.slice(0,600):null,bodyClass:document.body.className};
    })()""")
    print('批注页双语：', json.dumps(bilingual, ensure_ascii=False))
    if not bilingual['bilingual']:
        problems.append('批注页没有同时显示原文与译文')
    elif MARK not in (bilingual['text'] or ''):
        problems.append('批注页的译文不是服务返回的内容：' + (bilingual['text'] or '')[:140])

    # ---------------------------------------------------------------- 9 整篇翻译 + 检索命中译文
    task = cdp.evaluate("""(async()=>{
      const started=await post('/documents/'+state.doc.id+'/translate',{});
      let state_=null;
      for(let i=0;i<80;i++){
        await new Promise(r=>setTimeout(r,400));
        state_=await api('/documents/'+state.doc.id+'/translation/tasks');
        if(!state_.job.running)break;
      }
      const search=await post('/search',{query:'%s',document_id:state.doc.id,page:2});
      return {started:started.message,translated_pages:state_.translated_pages,pages:state_.pages,
              error:state_.job.error,
              searchHit:search.filter(r=>r.translation).map(r=>({page:r.page,text:r.text.slice(0,40)})).slice(0,3),
              searchTotal:search.length};
    })()""" % MARK)
    print('整篇翻译与检索译文：', json.dumps(task, ensure_ascii=False))
    if task['error']:
        problems.append('整篇后台翻译报错：' + str(task['error']))
    if task['translated_pages'] != task['pages']:
        problems.append('整篇翻译没有完成全部页：%s/%s' % (task['translated_pages'], task['pages']))
    if not task['searchHit']:
        problems.append('检索没有命中译文')

    # ---------------------------------------------------------------- 10 独立译文窗口
    position = cdp.evaluate("""(()=>{
      const rect=n=>{if(!n)return null;const b=n.getBoundingClientRect();
        return [Math.round(b.left),Math.round(b.right),Math.round(b.width)];};
      const button=[...document.querySelectorAll('#translation-panel button')].find(b=>b.textContent.includes('独立窗口'));
      const box=button?button.getBoundingClientRect():null;
      const x=box?Math.round(box.left+box.width/2):0,y=box?Math.round(box.top+box.height/2):0;
      const node=document.elementFromPoint(x,y);
      return {x,y,rect:button?rect(button):null,
              panel:rect(document.querySelector('#translation-panel')),
              actions:rect(document.querySelector('.translation-actions')),
              splitter:rect(document.querySelector('.panel-splitter')),
              hit:(node||{}).className||null,
              hitIsButton:!!(node&&node.closest&&node.closest('button')===button)};})()""")
    print('「独立窗口」按钮：', position)
    if not position['hitIsButton']:
        problems.append('「独立窗口」按钮在真实命中测试里点不到（被裁掉或被别的条压住）：%s' % position)
    if position:
        mouse('mousePressed', position['x'], position['y'], buttons=1)
        mouse('mouseReleased', position['x'], position['y'])
    else:
        problems.append('译文栏里没有「独立窗口」按钮')
    target = None
    for _ in range(40):
        found = [t for t in wait_for_cdp(PORT) if t.get('type') == 'page' and 'translate-window' in t.get('url', '')]
        if found:
            target = found[0]
            break
        time.sleep(0.3)
    print('独立译文窗口：', target['url'] if target else None)
    if not target:
        problems.append('点「独立窗口」没有开出独立译文窗口')
    else:
        second = CDP(ws_connect(target['webSocketDebuggerUrl']))
        for domain in ('Page.enable', 'Runtime.enable', 'Log.enable'):
            second.call(domain)
        for _ in range(80):
            if second.evaluate('document.readyState') == 'complete' and second.evaluate(
                    "typeof translationPanel==='function'&&state.doc"):
                break
            time.sleep(0.25)
        detach = second.evaluate("""(async()=>{
          for(let i=0;i<60;i++){
            if(document.querySelectorAll('.translation-segment').length)break;
            await new Promise(r=>setTimeout(r,250));
          }
          return {page:state.page,paperDisplay:getComputedStyle(document.querySelector('#viewport')).display,
                  segments:document.querySelectorAll('.translation-segment').length,
                  first:(document.querySelector('.translation-text')||{}).textContent||''};})()""")
        print('独立窗口内容：', json.dumps(detach, ensure_ascii=False))
        if not detach['segments']:
            problems.append('独立译文窗口里没有译文段落')
        if detach['paperDisplay'] != 'none':
            problems.append('独立译文窗口里还占着原文页的位置（它应当只显示译文）')
        # 主窗口翻页 → 独立窗口跟着翻（BroadcastChannel）
        cdp.evaluate("(async()=>{await go(3);})()")
        time.sleep(2.0)
        synced = second.evaluate("JSON.stringify({page:state.page,channel:typeof BroadcastChannel})")
        print('翻页同步：', synced)
        if '"page":1' in synced:
            problems.append('主窗口翻页后独立译文窗口没有跟着翻页：%s' % synced)
        errors = [e for e in second.events if e.get('method') == 'Runtime.exceptionThrown']
        if errors:
            print('独立窗口异常：', json.dumps(errors, ensure_ascii=False)[:600])
            problems.append('独立译文窗口里有运行期异常 %d 处' % len(errors))
        second.call('Runtime.evaluate', expression='window.close()')
    cdp.evaluate("(async()=>{await go(1);})()")
    time.sleep(0.6)

    # ---------------------------------------------------------------- 11 翻译设置
    settings = cdp.evaluate("""(async()=>{
      await openModelSettings();
      document.querySelector('[data-setting-tab="translation"]').click();
      await new Promise(r=>setTimeout(r,700));
      const panel=document.querySelector('#translation-settings-panel');
      const selects=[...panel.querySelectorAll('select')];
      const source=selects.find(s=>[...s.options].some(o=>o.textContent.includes('已连接的平台')));
      const connection=selects.find(s=>[...s.options].some(o=>o.textContent.includes('请选择已连接的平台')));
      const rowFor=node=>{let current=node;while(current&&current!==panel){
        if(current.classList&&current.classList.contains('model-field'))return current;current=current.parentElement;}return null;};
      const isVisible=node=>{if(!node)return null;const row=rowFor(node);return row?!row.hidden&&getComputedStyle(row).display!=='none':null;};
      const modelOf=()=>{const row=rowFor(connection);const sibling=row?row.nextElementSibling:null;return sibling?sibling.querySelector('select'):null;};
      const before={modelOptions:null,address:isVisible(panel.querySelector('input[placeholder*="11434"]')),
                    key:isVisible(panel.querySelector('input[type="password"]'))};
      let filled=null,rowsAfter=null;
      if(connection&&connection.options.length>1){
        connection.value=connection.options[1].value;
        connection.dispatchEvent(new Event('change'));
        await new Promise(r=>setTimeout(r,250));
        const model=modelOf();
        filled={options:model?[...model.options].map(o=>o.textContent):null,value:model?model.value:null,disabled:model?model.disabled:null};
        rowsAfter={address:isVisible(panel.querySelector('input[placeholder*="11434"]')),
                   key:isVisible(panel.querySelector('input[type="password"]'))};
      }
      let directRows=null;
      if(source){
        source.value='direct';source.dispatchEvent(new Event('change'));
        await new Promise(r=>setTimeout(r,200));
        directRows={address:isVisible(panel.querySelector('input[placeholder*="11434"]')),
                    key:isVisible(panel.querySelector('input[type="password"]'))};
      }
      document.querySelector('#settings-close').click();
      return {tabs:[...document.querySelectorAll('[data-setting-tab]')].map(b=>b.dataset.settingTab),
              sources:source?[...source.options].map(o=>o.textContent):null,
              hasEngineChoice:selects.some(s=>[...s.options].some(o=>o.textContent.includes('内置术语'))),
              hasGlossary:!!panel.querySelector('.translation-glossary'),
              probeButton:[...panel.querySelectorAll('button')].some(b=>b.textContent==='试译一句'),
              before,filled,rowsAfter,directRows};
    })()""")
    print('翻译设置：', json.dumps(settings, ensure_ascii=False))
    if 'translation' not in (settings['tabs'] or []):
        problems.append('设置里没有「翻译」栏目')
    if not settings['probeButton']:
        problems.append('翻译设置里没有「试译一句」')
    if settings['hasEngineChoice'] or settings['hasGlossary']:
        problems.append('设置里仍有「翻译方式 / 内置术语表」这类选项（用户要求删掉）')
    if settings['sources'] != ['「模型服务」里已连接的平台', '直接填写兼容接口的地址']:
        problems.append('翻译服务来源不是预期的两项：%s' % settings['sources'])
    if settings['filled'] is None:
        problems.append('没有可选的已连接平台，无法验证"模型用下拉选择"')
    else:
        if not settings['filled']['options'] or settings['filled']['options'][0].startswith('请先选择平台'):
            problems.append('选了已连接的平台后模型下拉是空的：%s' % settings['filled'])
        if settings['rowsAfter']['address'] is not False or settings['rowsAfter']['key'] is not False:
            problems.append('选了已连接的平台后仍要求填写服务地址或 API Key（用户明确要求不要重复填）')
    if settings['before']['address'] is not False or settings['before']['key'] is not False:
        problems.append('来源是"已连接的平台"时，打开设置就显示着地址/密钥栏：%s' % settings['before'])
    if not settings['directRows'] or settings['directRows']['address'] is not True or settings['directRows']['key'] is not True:
        problems.append('切到"直接填写地址"后地址与密钥两栏没有出现：%s' % settings['directRows'])

    # ---------------------------------------------------------------- 12 工作台状态 / 标签下拉 / 搜索提示
    workbench = cdp.evaluate("""(async()=>{
      await showWorkbench('library');
      await new Promise(r=>setTimeout(r,900));
      if(typeof translationDecorations==='function')translationDecorations();
      await new Promise(r=>setTimeout(r,1200));
      const cards=[...document.querySelectorAll('.document-card')];
      const card=cards.find(c=>c.querySelector('.document-translation')?.dataset.status==='done')||cards[0];
      const line=card?card.querySelector('.document-translation'):null;
      const button=card?card.querySelector('[data-translation-action]'):null;
      const tagFilter=document.querySelector('.workbench-controls select[aria-label*="标签"]');
      return {cards:cards.length,statusCode:line?line.dataset.status:null,status:line?line.textContent:null,
              button:button?button.textContent:null,title:card?card.querySelector('h2')?.textContent:null,
              tagFilterTag:tagFilter?tagFilter.tagName:null,
              tagOptions:tagFilter?[...tagFilter.options].map(o=>o.textContent).slice(0,5):null,
              queryPlaceholder:document.querySelector('#library-query')?.placeholder};
    })()""")
    print('工作台：', json.dumps(workbench, ensure_ascii=False))
    if not workbench['cards']:
        problems.append('工作台没有渲染出文献卡片')
    if workbench['statusCode'] != 'done':
        problems.append('整篇翻译完成后工作台没有显示"已完成"：%s' % workbench['status'])
    if not (workbench['button'] or '').startswith('以新的服务重新翻译'):
        problems.append('整篇翻译完成后按钮文案没有变成"以新的服务重新翻译整篇"：%s' % workbench['button'])
    if workbench['tagFilterTag'] != 'SELECT':
        problems.append('标签筛选不是下拉选择：%s' % workbench['tagFilterTag'])
    if any(option.startswith('@') for option in (workbench['tagOptions'] or [])):
        problems.append('标签下拉里出现了作者选项（用户要求下拉只放标签）：%s' % workbench['tagOptions'])
    if workbench['queryPlaceholder'] != '搜索文献名称、标签或作者':
        problems.append('检索框提示不是"名称、标签或作者"：%s' % workbench['queryPlaceholder'])

    console = [e for e in cdp.events if e.get('method') == 'Runtime.exceptionThrown']
    if console:
        print('页面异常：', json.dumps(console, ensure_ascii=False)[:1500])
        problems.append('页面运行期抛出异常 %d 处' % len(console))

    print('\n=== 结论 ===')
    if problems:
        for item in problems:
            print('× ' + item)
        return 1
    print('以上各项均通过（真实浏览器 + 真实鼠标事件 + 假翻译服务）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
