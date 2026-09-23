"""Page-addressable section tree and layout signals; no model or PDF dependency."""
import re
import unicodedata


def text_quality(text):
    visible=[c for c in text if not c.isspace()]
    bad=sum(c=='\ufffd' or unicodedata.category(c) in ('Co','Cc','Cs') for c in visible)
    return {'suspect':not visible or bad/max(1,len(visible))>.02,
            'bad_ratio':bad/max(1,len(visible)),'characters':len(visible)}


def section_tree(entries,total):
    """Hierarchy with inclusive boundary pages to retain same-page section endings."""
    rows=sorted((dict(e) for e in entries if 1<=e['page']<=total),key=lambda e:e['page'])
    result=[];stack=[]
    for i,row in enumerate(rows):
        level=max(1,row.get('level',1))
        while stack and result[stack[-1]]['level']>=level:stack.pop()
        end=next((e['page'] for e in rows[i+1:] if e.get('level',1)<=level),total)
        result.append({**row,'id':i,'level':level,'end_page':end,'parent':stack[-1] if stack else None})
        stack.append(i)
    return result


def expand_sections(pages,tree,total):
    # A directory-less local hit cannot establish the end of an argument.
    if not tree:return set(range(1,total+1))
    expanded=set()
    for page in pages:
        matches=[s for s in tree if s['page']<=page<=s['end_page']]
        if not matches:
            # 落在标题覆盖不到的地方（扉页、摘要、章节之间的整页图表）时，
            # 以前会直接读整篇——一次局部提问因此变成通读全书。这里改为取最近的
            # 标题节点作为上下文，并把范围钳制在"该页 + 相邻一节"以内。
            following=[s for s in tree if s['page']>=page]
            node=min(following,key=lambda s:s['page']) if following else tree[-1]
            if node['parent'] is not None:node=tree[node['parent']]
            expanded.update(range(max(1,min(page,node['page'])-1),min(total,max(node['end_page'],page)+1)+1))
            continue
        node=max(matches,key=lambda s:(s['level'],s['page']))
        # Read the containing parent so definitions/qualifications in sibling subsections survive.
        if node['parent'] is not None:node=tree[node['parent']]
        expanded.update(range(max(1,node['page']-1),min(total,node['end_page']+1)+1))
    return expanded


def heading(text):
    line=text.strip()
    if len(line)>120:return None
    if re.match(r'^(?:第[一二三四五六七八九十百\d]+[章节]|\d+(?:\.\d+){0,3}\s+\S|(?:chapter|section)\s+\d+\b)',line,re.I):
        prefix=re.match(r'\d+(?:\.\d+)*',line)
        return {'title':line,'level':prefix[0].count('.')+1 if prefix else 1}
    if re.fullmatch(r'abstract|introduction|methods?|results?|discussion|conclusions?|references|摘要|引言|方法|结果|讨论|结论|参考文献',line,re.I):
        return {'title':line,'level':1}
    return None


def layout_text(spans,width):
    """Conservative two-column ordering, segmented by full-width headings."""
    spans=[s for s in spans if s.get('text','').strip()]
    left=[s for s in spans if s['bbox'][2]<width*.55]
    right=[s for s in spans if s['bbox'][0]>width*.45]
    two=len(left)>=4 and len(right)>=4 and max(s['bbox'][2] for s in left)<min(s['bbox'][0] for s in right)
    if not two:return '\n'.join(s['text'] for s in spans),False
    wide=[s for s in spans if s not in left and s not in right]
    remaining=list(spans);ordered=[]
    for separator in sorted(wide,key=lambda s:s['bbox'][1])+[None]:
        y=separator['bbox'][1] if separator else float('inf')
        band=[s for s in remaining if s['bbox'][1]<y and s is not separator]
        ordered.extend(sorted(band,key=lambda s:(s['bbox'][0]>width*.45,s['bbox'][1],s['bbox'][0])))
        remaining=[s for s in remaining if s not in band]
        if separator:
            ordered.append(separator);remaining.remove(separator)
    return '\n'.join(s['text'] for s in ordered),True


def visual_nodes(text,page):
    nodes=[]
    for line in text.splitlines():
        if re.match(r'^\s*(?:fig(?:ure)?\.?|table|图|表)\s*[\d一二三四五六七八九十]',line,re.I):
            nodes.append({'kind':'figure' if re.match(r'^\s*(?:fig|图)',line,re.I) else 'table','page':page,'caption':line[:500]})
        elif re.search(r'[=∑∫√≈≤≥]',line):nodes.append({'kind':'equation','page':page,'caption':line[:500]})
    return nodes


# 真句末标点：只有它才说明"这一行是句子的结尾"。
#
# 这里**不再**把收尾引号/括号（”』）)】）与顿号式的分号、冒号算作句末标点。上一版把它们也算进去，
# 于是"行尾恰好是一个引号"就被当成句末 → 把一句话从中间断成两段：用户截图里那段译文正是这样
# 被劈开的（原文行尾是 …排斥"底线伦理"，下一行是"的倾向；又要防止…"，断段后两半各自翻译，
# 读起来就是"段落中间截断了"）。
_SENTENCE_END='。！？…!?.'
# 收尾符号：跟在句末标点后面，剥掉它们之后再判断行尾是不是真句末。
_TRAILING_CLOSERS='”’"\'）)】」』》>]'
# 行首像标题、列表项、图表题注时不合并——它们本来就不是被排版折断的句子。
_LIST_OR_HEADING=re.compile(r'^\s*(?:[•·▪◦*\-–—]|\(?\d{1,3}[.)、]|[一二三四五六七八九十]{1,3}[、.)]|第[一二三四五六七八九十百\d]{1,4}[章节条]|(?:fig(?:ure)?|table|图|表|式)\s*[\d一二三四五六七八九十])',re.I)


def ends_sentence_line(line):
    """这一行是否以**真句末标点**结束（收尾引号/括号剥掉后再看）。

    ``…排斥"底线伦理"`` 这种行尾不算句末：引号只是收尾，句子还在下一行继续。
    """
    trimmed=str(line or '').rstrip().rstrip(_TRAILING_CLOSERS).rstrip()
    return trimmed.endswith(tuple(_SENTENCE_END))


def _needs_space(left,right):
    """断行拼接处要不要补空格：只有两侧都是 ASCII 词字符时才补（中英混排不补）。"""
    return bool(left and right and left[-1].isascii() and left[-1].isalnum()
                and right[0].isascii() and right[0].isalnum())


def reflow_lines(text):
    """把扫描/排版造成的硬换行合并成自然段。

    只用于**展示与选文**（框选识别结果、批注引文）：扫描件逐行断开会让选中的原文
    看起来"很僵硬"，复制出去也不成句。页面阅读文本保持逐行结构不变——引用定位
    依赖"行"来对应版面上的行框，重排会破坏它。

    保守规则：空行断段；上一行以**真句末标点**结尾则断段（行尾的引号/括号不算——
    它们只是收尾，句子往往还在下一行）；本行像标题/列表/图表题注则断段；
    英文断词（行尾连字符 + 下一行小写字母）直接接上；其余按中英混排规则拼接。
    """
    paragraphs=[];buffer=''
    for raw in str(text or '').replace('\r\n','\n').replace('\r','\n').split('\n'):
        line=raw.strip()
        if not line:
            if buffer:paragraphs.append(buffer);buffer=''
            continue
        if not buffer:
            buffer=line;continue
        if ends_sentence_line(buffer) or _LIST_OR_HEADING.match(line):
            paragraphs.append(buffer);buffer=line;continue
        if heading(buffer):
            # 上一行本身就是小节标题（"3.1 Results"）：后面是正文，必须另起一段。
            # 标题不以句末标点结尾，不这样处理时正文会被并进标题里，
            # 翻译与选文都会拿到一个"标题+正文"的混合段落。
            paragraphs.append(buffer);buffer=line;continue
        if buffer.endswith('-') and line[:1].isascii() and line[:1].isalpha():
            buffer=buffer+line
        elif _needs_space(buffer,line):
            buffer=buffer+' '+line
        else:
            buffer=buffer+line
    if buffer:paragraphs.append(buffer)
    return '\n\n'.join(paragraphs)
