"""Local, traceable original-text packets for claim support checks.

Packets check cited support, not exhaustive whole-book coverage. All excerpts
remain verbatim and are bound to the original source numbers.
"""
import json
import re
from .reading import cost
from app.retrieval import rank


def passages(text, width=600, overlap=150):
    start=0
    while start<len(text):
        end=min(len(text),start+width)
        if end<len(text):
            boundary=max(text.rfind('\n',start+width//2,end),text.rfind('。',start+width//2,end))
            if boundary>=0:end=boundary+1
        yield start,end,text[start:end]
        if end==len(text):break
        start=max(start+1,end-overlap)


def select(answer, originals, allowed, budget):
    """Citations first, lexical alternatives and adjacent spans next; no I/O."""
    chunks=[]
    cited={int(n) for n in re.findall(r'\[(\d+)\]',answer)} & set(allowed)
    for source in sorted(allowed):
        if not 1<=source<=len(originals):continue
        row=originals[source-1]
        for start,end,text in passages(row.get('text','')):
            chunks.append({'id':len(chunks),'document_id':row.get('document_id',''),
                'page':row.get('page',0),'text':text,'source':source,'start':start,'end':end,
                'source_type':row.get('source_type','document')})
    ranked=rank(answer,chunks,None,0,limit=len(chunks))
    scores={row['id']:row['score'] for row in ranked}
    ordered=sorted(chunks,key=lambda row:(row['source'] not in cited,-scores.get(row['id'],0),row['id']))
    # Give each cited source a first opportunity before a long first source
    # consumes the packet. Adjacent spans retain nearby conditions/qualifiers.
    seeds=[];seen_sources=set()
    for row in ordered:
        if row['source'] in cited and row['source'] not in seen_sources:
            seeds.append(row);seen_sources.add(row['source'])
    # Different claims citing one page can need different parts of that page.
    anchors=[]
    for line in answer.splitlines():
        ids={int(n) for n in re.findall(r'\[(\d+)\]',line)} & set(allowed)
        if not ids:continue
        hits=rank(line,[row for row in chunks if row['source'] in ids],None,0,limit=1)
        anchors.extend(hits)
    candidates=[];seen=set()
    def add(row):
        if row['id'] not in seen:
            candidates.append(row);seen.add(row['id'])
    # All cited sources get a turn before any source's neighboring passages.
    for row in seeds+anchors:add(row)
    for row in seeds+anchors+ranked[:max(2,len(cited))]+ordered:
        add(row)
        for pos in (row['id']-1,row['id']+1):
            if 0<=pos<len(chunks) and chunks[pos]['source']==row['source']:add(chunks[pos])
    bank=[]
    for row in candidates:
        item={'source':row['source'],'quote':row['text'],'evidence_key':f'E{len(bank)+1}',
              'offset_start':row['start'],'offset_end':row['end'],'source_type':row['source_type']}
        candidate=json.dumps({'verified_quotes':bank+[item]},ensure_ascii=False)
        if cost(candidate)<=budget:bank.append(item)
    context=json.dumps({'verified_quotes':bank},ensure_ascii=False)
    return context,{item['evidence_key']:item for item in bank}, {
        'mode':'按论断选取原文片段，非全书穷尽核查',
        'available_passages':len(chunks),'selected_passages':len(bank),
        'sources':sorted({item['source'] for item in bank})}
