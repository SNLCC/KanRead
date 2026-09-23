"""在**真实 app 页面**里检查两个批注窗口能不能拖动/缩放（Edge headless + CDP）。

要检查的是**两个不同的界面**，用户报的两次"拖不动"分别指向它们：

1. 阅读区批注弹窗（`.annotation-popover`）：点页面上已有的批注高亮后出现的小窗；
2. 「添加批注 / 编辑批注」表单窗口（`#annotation-dialog`）：点「添加批注」后出现的表单，
   标题是「添加批注 · 第 N 页」——第二次用户带截图报的就是它。

与 drive.py 的区别：那个用最小 DOM 载入真实样式与真实代码片段；这个直接打开真实应用页面，
因此在真实布局（工具栏、阅读区、会话面板、模态顶层）里做命中测试与真实鼠标事件——
用户报告"无法拖动"的现象只可能在这种环境里复现。

用法：python scripts/ui_probe/drive_real.py [页面地址]
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from drive import CDP, PORT, wait_for_cdp, ws_connect  # noqa: E402

URL = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8799/'

RECT = "r=>[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)]"


def main():
    pages = [t for t in wait_for_cdp(PORT) if t.get('type') == 'page']
    if not pages:
        raise RuntimeError('没有可用的页面目标')
    cdp = CDP(ws_connect(pages[0]['webSocketDebuggerUrl']))
    cdp.call('Page.enable')
    cdp.call('Runtime.enable')
    cdp.call('Log.enable')
    cdp.call('Page.navigate', url=URL)
    for _ in range(100):
        if cdp.evaluate('document.readyState') == 'complete' and cdp.evaluate('typeof showAnnotationPopover'):
            break
        time.sleep(0.2)
    print('页面：', URL, '| 标题：', cdp.evaluate('document.title'))

    errors = [e for e in cdp.events if e.get('method') in ('Runtime.exceptionThrown', 'Log.entryAdded')]
    if errors:
        print('加载期错误/日志：', json.dumps(errors, ensure_ascii=False)[:1200])

    print('依赖是否就绪：', cdp.evaluate(
        "JSON.stringify({popover:typeof showAnnotationPopover,placeDialog:typeof placeAnnotationDialog,"
        "el:typeof el,$:typeof $,version:window.__ASSET_VERSION})"))

    # 打开一份文献：只有在阅读界面里，弹窗才有真实布局（工作台界面下 .workspace 是 display:none）。
    opened = cdp.evaluate("""(async()=>{
      await loadLibrary();
      if(state.docs.length&&!state.doc)await openDoc(state.docs[0]);
      return {docs:state.docs.length,doc:state.doc?state.doc.name:null,workspace:getComputedStyle(document.querySelector('.workspace')).display};
    })()""")
    print('打开文献：', opened)

    def mouse(kind, x, y, buttons=0):
        cdp.call('Input.dispatchMouseEvent', type=kind, x=int(x), y=int(y), button='left',
                 buttons=buttons, clickCount=1 if kind == 'mousePressed' else 0, pointerType='mouse')

    def drag(start, steps, hold=0.05):
        mouse('mousePressed', start[0], start[1], buttons=1)
        for dx, dy in steps:
            mouse('mouseMoved', start[0] + dx, start[1] + dy, buttons=1)
            time.sleep(hold)
        mouse('mouseReleased', start[0] + steps[-1][0], start[1] + steps[-1][1])
        time.sleep(0.25)

    # 每次从干净状态开始：浏览器配置目录是复用的，localStorage 会跨次保留，否则这次读到的
    # 是上一次记下的位置，看不出"拖动是否真的生效"。
    cdp.evaluate("(()=>{localStorage.removeItem('reader-annotation-popover');"
                 "localStorage.removeItem('reader-annotation-dialog');})()")

    # ---------------------------------------------------------------- 阅读区批注弹窗
    print('\n--- 阅读区批注弹窗（.annotation-popover）---')
    cdp.evaluate("""(()=>{showAnnotationPopover([{id:'probe',page:1,style:'highlight',color:'yellow',
      rects:[[0.1,0.1,0.5,0.2]],quote:'探针原文',content:'探针批注'}]);})()""")
    report = cdp.evaluate("""(()=>{
      const R=r=>[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)];
      const box=annotationPopover.getBoundingClientRect();
      const parts={};
      for(const [name,node] of [['head',annotationPopover.querySelector('.annotation-popover-head')],
                                ['grip',annotationPopover.querySelector('.annotation-popover-grip')],
                                ['se',annotationPopover.querySelector('.annotation-resize[data-dir="se"]')],
                                ['e',annotationPopover.querySelector('.annotation-resize[data-dir="e"]')],
                                ['s',annotationPopover.querySelector('.annotation-resize[data-dir="s"]')],
                                ['reset',annotationPopover.querySelector('.annotation-popover-reset')]]){
        if(!node){parts[name]={missing:true};continue;}
        const r=node.getBoundingClientRect();
        const x=Math.round(r.left+r.width/2), y=Math.round(r.top+r.height/2);
        const hit=document.elementFromPoint(x,y);
        parts[name]={rect:R(r),point:[x,y],hit:hit?(hit.className||hit.tagName):'null',
          hitInside:!!(hit&&annotationPopover.contains(hit))};
      }
      return {hidden:annotationPopover.hidden,connected:annotationPopover.isConnected,
        style:annotationPopover.getAttribute('style'),
        computed:{display:getComputedStyle(annotationPopover).display,position:getComputedStyle(annotationPopover).position,
          overflow:getComputedStyle(annotationPopover).overflow,zIndex:getComputedStyle(annotationPopover).zIndex},
        box:R(box),offsetParent:(annotationPopover.offsetParent||{}).className,parts};
    })()""")
    print('结构与命中测试：', json.dumps(report, ensure_ascii=False, indent=1))

    head = report['parts']['head']['point']
    before = cdp.evaluate(f"(()=>{{const r=annotationPopover.getBoundingClientRect();return JSON.stringify(({RECT})(r));}})()")
    drag(head, [(-30, 10), (-60, 20), (-90, 30)])
    after = cdp.evaluate(f"(()=>{{const r=annotationPopover.getBoundingClientRect();return JSON.stringify(({RECT})(r));}})()")
    print('真实鼠标拖动标题栏：', before, '→', after)

    corner = cdp.evaluate("(()=>{const r=annotationPopover.querySelector('.annotation-resize[data-dir=\\'se\\']').getBoundingClientRect();return [Math.round(r.left+r.width/2),Math.round(r.top+r.height/2)];})()")
    size_before = cdp.evaluate("Math.round(annotationPopover.getBoundingClientRect().width)+'x'+Math.round(annotationPopover.getBoundingClientRect().height)")
    drag(corner, [(30, 30), (60, 60)])
    size_after = cdp.evaluate("Math.round(annotationPopover.getBoundingClientRect().width)+'x'+Math.round(annotationPopover.getBoundingClientRect().height)")
    print(f'真实鼠标拖右下角：{size_before} → {size_after}')
    print('本机存储：', cdp.evaluate("localStorage.getItem('reader-annotation-popover')"))

    # ------------------------------------------------- 「添加批注」表单窗口（用户截图里的那个）
    print('\n--- 「添加批注」表单窗口（#annotation-dialog）---')
    # 必须**带着选中文字**打开：整页批注那种情形下颜色/样式两个下拉框本来就是隐藏的，
    # 拿它去量"下拉框有多宽"会永远量到 0（上一版探针就是这么误报的）。
    cdp.evaluate("(()=>{annotationPopover.hidden=true;setSelection('探针选中的一句话，用来量两个下拉框。',[[0.1,0.1,0.5,0.2]]);openAnnotation();})()")
    time.sleep(0.35)
    report = cdp.evaluate("""(()=>{
      const R=r=>[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)];
      const dlg=document.getElementById('annotation-dialog');
      const box=dlg.getBoundingClientRect();
      const pick={head:'.annotation-dialog-head',grip:'.annotation-dialog-grip',reset:'#annotation-dialog-reset',
        n:'.annotation-resize[data-dir="n"]',s:'.annotation-resize[data-dir="s"]',
        e:'.annotation-resize[data-dir="e"]',w:'.annotation-resize[data-dir="w"]',se:'.annotation-resize[data-dir="se"]',
        form:'#annotation-form',content:'#annotation-content',save:'.dialog-actions .primary',
        cancel:'#annotation-cancel',title:'#annotation-title'};
      const parts={};
      for(const [name,sel] of Object.entries(pick)){
        const node=dlg.querySelector(sel);
        if(!node){parts[name]={missing:true};continue;}
        const r=node.getBoundingClientRect();
        const x=Math.round(r.left+r.width/2), y=Math.round(r.top+r.height/2);
        const hit=document.elementFromPoint(x,y);
        parts[name]={rect:R(r),point:[x,y],text:(node.textContent||'').trim().slice(0,20),
          hit:hit?(hit.className||hit.tagName):'null',hitInside:!!(hit&&dlg.contains(hit))};
      }
      return {open:dlg.open,modal:dlg.matches(':modal'),style:dlg.getAttribute('style'),box:R(box),
        viewport:[innerWidth,innerHeight],offsetParent:dlg.offsetParent?dlg.offsetParent.className:null,
        computed:{display:getComputedStyle(dlg).display,position:getComputedStyle(dlg).position,
          overflow:getComputedStyle(dlg).overflow,margin:getComputedStyle(dlg).margin,
          maxWidth:getComputedStyle(dlg).maxWidth,maxHeight:getComputedStyle(dlg).maxHeight},
        parts};
    })()""")
    print('结构与命中测试：', json.dumps(report, ensure_ascii=False, indent=1))
    print('颜色 / 标记样式两个下拉框（用户要求：样式框别那么长，且跟在颜色框之后）：',
          cdp.evaluate("""(()=>{
      const c=document.getElementById('annotation-color');
      const s=document.getElementById('annotation-style');
      const dlg=document.getElementById('annotation-dialog').getBoundingClientRect();
      if(!c||!s)return JSON.stringify({missing:!c?'color':''});
      const rc=c.getBoundingClientRect(), rs=s.getBoundingClientRect();
      return JSON.stringify({
        color:[Math.round(rc.left),Math.round(rc.top),Math.round(rc.width)],
        style:[Math.round(rs.left),Math.round(rs.top),Math.round(rs.width)],
        dialogWidth:Math.round(dlg.width),
        sameRow:Math.abs(rc.top-rs.top)<4,
        styleAfterColor:rs.left>=rc.right-2,
        styleTooWide:rs.width>dlg.width*0.6,
        colorLabelHidden:!!document.querySelector('.annotation-fields label').hidden,
        children:[...document.getElementById('annotation-form').children].map(n=>
          (n.id||n.className||n.tagName)+':'+Math.round(n.getBoundingClientRect().width))});
    })()"""))

    head = report['parts']['head']['point']
    before = cdp.evaluate(f"(()=>{{const r=document.getElementById('annotation-dialog').getBoundingClientRect();return JSON.stringify(({RECT})(r));}})()")
    drag(head, [(-40, 20), (-80, 40), (-120, 60)])
    after = cdp.evaluate(f"(()=>{{const r=document.getElementById('annotation-dialog').getBoundingClientRect();return JSON.stringify(({RECT})(r));}})()")
    print('真实鼠标拖动标题栏：', before, '→', after)
    print('  偏移量读数（offsetLeft/offsetTop 必须和 rect 对得上，否则"下次打开还在原处"会跳）：',
          cdp.evaluate("(()=>{const d=document.getElementById('annotation-dialog');const r=d.getBoundingClientRect();"
                       "return JSON.stringify({rect:[Math.round(r.left),Math.round(r.top)],offset:[d.offsetLeft,d.offsetTop],"
                       "offsetParent:d.offsetParent?d.offsetParent.tagName+'.'+d.offsetParent.className:null});})()"))

    # 从多行输入框里拖：窗口不能跟着走（否则没法选字/放光标）。坐标必须**当场重新读**，
    # 否则按下的是窗口移动前的位置（上一版探针就踩了这个坑，结论不可信）。
    def live_point(selector):
        return cdp.evaluate(f"(()=>{{const r=document.querySelector({selector!r}).getBoundingClientRect();"
                            f"return [Math.round(r.left+r.width/2),Math.round(r.top+r.height/2)];}})()")

    def hit_at(point):
        return cdp.evaluate(f"(()=>{{const n=document.elementFromPoint({point[0]},{point[1]});"
                            f"return n?(n.id||n.className||n.tagName):'null';}})()")

    inside = live_point('#annotation-content')
    print('  输入框当前中心：', inside, '命中：', hit_at(inside))
    before = cdp.evaluate(f"(()=>{{const r=document.getElementById('annotation-dialog').getBoundingClientRect();return JSON.stringify(({RECT})(r));}})()")
    drag(inside, [(20, 20), (40, 40)])
    after = cdp.evaluate(f"(()=>{{const r=document.getElementById('annotation-dialog').getBoundingClientRect();return JSON.stringify(({RECT})(r));}})()")
    print('真实鼠标在批注输入框里拖（窗口应无变化）：', before, '→', after)
    print('  输入框里是否留下了选中的文字（说明手势没被窗口抢走）：',
          cdp.evaluate("(()=>{const t=document.getElementById('annotation-content');"
                       "return JSON.stringify({focused:document.activeElement===t,selectionStart:t.selectionStart,selectionEnd:t.selectionEnd});})()"))

    corner = cdp.evaluate("(()=>{const r=document.querySelector('#annotation-dialog .annotation-resize[data-dir=\\'se\\']').getBoundingClientRect();return [Math.round(r.left+r.width/2),Math.round(r.top+r.height/2)];})()")
    print('  右下角手柄中心：', corner, '命中：', hit_at(corner))
    before = cdp.evaluate(f"(()=>{{const r=document.getElementById('annotation-dialog').getBoundingClientRect();return JSON.stringify(({RECT})(r));}})()")
    drag(corner, [(40, 30)])
    after = cdp.evaluate(f"(()=>{{const r=document.getElementById('annotation-dialog').getBoundingClientRect();return JSON.stringify(({RECT})(r));}})()")
    print('真实鼠标拖右下角：', before, '→', after)
    print('  输入框是否跟着长高：', cdp.evaluate(
        "(()=>{const t=document.getElementById('annotation-content').getBoundingClientRect();"
        "return Math.round(t.width)+'x'+Math.round(t.height);})()"))
    print('本机存储：', cdp.evaluate("localStorage.getItem('reader-annotation-dialog')"))
    print('打开第二次是否回到记住的位置：', cdp.evaluate(f"""(()=>{{
      const dlg=document.getElementById('annotation-dialog');
      const was=({RECT})(dlg.getBoundingClientRect());
      dlg.close();openAnnotation();
      const r=dlg.getBoundingClientRect();
      return JSON.stringify({{was,now:({RECT})(r),style:dlg.getAttribute('style'),
        delta:[Math.round(r.left-was[0]),Math.round(r.top-was[1])]}});
    }})()"""))
    cdp.evaluate("document.getElementById('annotation-dialog').close()")

    # ------------------------------------------------ 联网搜索设置面板（只有自托管才显示服务地址）
    print('\n--- 联网搜索设置（设置 → 联网搜索）---')
    cdp.evaluate("(()=>{document.getElementById('settings-dialog').showModal();})()")
    time.sleep(0.2)
    print('面板构建：', cdp.evaluate("(async()=>{await buildConnectionSettings();return 'ok';})()"))
    time.sleep(0.2)
    print('按平台切换时"服务根地址"那一行的真实显示状态：', cdp.evaluate("""(()=>{
      const form=document.getElementById('web-settings-form');
      if(!form)return '没有找到 #web-settings-form';
      const rows=[...form.querySelectorAll('label.model-field')];
      const rowOf=t=>rows.find(r=>r.textContent.includes(t));
      const baseRow=rowOf('SearXNG 服务根地址');
      if(!baseRow)return '没有找到服务地址那一行';
      const provider=[...form.querySelectorAll('select')][0];
      const keyRow=rows.find(r=>r.textContent.includes('API Key'));
      const out={fields:rows.length};
      for(const value of ['brave','tavily','searxng','brave']){
        provider.value=value;provider.onchange();
        const rect=baseRow.getBoundingClientRect();
        out[value]={hidden:baseRow.hidden,display:getComputedStyle(baseRow).display,
          size:[Math.round(rect.width),Math.round(rect.height)],
          keyLabel:(keyRow.querySelector('span')||{}).textContent};
      }
      return JSON.stringify(out,null,1);})()"""))
    # 密钥说明必须说清"这份密钥属于哪个平台"（用户报告：无论选哪个都显示已配置，
    # 分不清之前配的是哪个）。这里给探针实例存一个假密钥，再逐个平台切换看文案。
    print('存一个探针密钥后切换平台，看密钥那一栏怎么写：', cdp.evaluate("""(async()=>{
      await api('/search-settings',{method:'PUT',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({provider:'brave',api_key:'probe-key-not-a-real-key',permission:'review'})});
      await buildConnectionSettings();
      const form=document.getElementById('web-settings-form');
      const rows=[...form.querySelectorAll('label.model-field')];
      const caption=rows.find(r=>r.textContent.startsWith('API Key')).querySelector('span');
      const select=[...form.querySelectorAll('select')][0];
      const out={};
      for(const value of ['brave','tavily','searxng']){
        select.value=value;select.onchange();out[value]=caption.textContent;
      }
      await api('/search-settings',{method:'PUT',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({provider:'brave',clear_key:true})});
      return JSON.stringify(out,null,1);})()"""))
    cdp.evaluate("(()=>{document.getElementById('settings-dialog').close();})()")

    late = [e for e in cdp.events if e.get('method') in ('Runtime.exceptionThrown',)]
    if late:
        print('\n运行期异常：', json.dumps(late, ensure_ascii=False)[:1500])
    else:
        print('\n没有运行期异常。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
