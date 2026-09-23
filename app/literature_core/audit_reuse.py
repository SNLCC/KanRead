"""Conservative local audit reuse and output-aware grouping; no remote I/O."""
import hashlib
import json
import re
from .reading import cost

VERSION=3


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def keys(instruction,claims,context,allowed,originals):
    # Include all permitted originals, not just the selected excerpts: edits,
    # expanded coverage and citation renumbering must invalidate old verdicts.
    fields=('id','document_id','document_version','page','text','source_type','offset_start','offset_end')
    evidence=digest([context,[(i,{k:originals[i-1].get(k) for k in fields})
        for i in sorted(allowed) if 1<=i<=len(originals or [])]])
    headings=[row['text'] for row in claims if row['text'].lstrip().startswith('#')]
    result={}
    for row in claims:
        # A referential sentence may depend on other paragraphs. Reuse it only
        # when that surrounding group is also unchanged.
        referential=re.search(r'这|该|其|上述|前述|因此|所以|\b(this|that|it|they|these|those|therefore)\b',row['text'],re.I)
        surroundings=[r['text'] for r in claims] if referential else headings
        result[row['claim']]='audit:'+digest([VERSION,instruction,evidence,row['text'],surroundings])
    return result


def load(store,cache_keys):
    found={}
    if store is None:return found
    for number,key in cache_keys.items():
        try:row=store.load(key)
        except Exception:continue  # Cache availability must not break an answer.
        if isinstance(row,dict) and row.get('verdict') in ('supported','inference','heading'):
            found[number]={**row,'claim':number}
    return found


def save(store,cache_keys,claims):
    if store is None:return
    for row in claims:
        if row.get('verdict') not in ('supported','inference','heading'):continue
        try:store.save(cache_keys[row['claim']],row)
        except Exception:pass  # Cancellation is still checked by the caller.


def groups(lines,output_budget=2048,tokens_per_claim=100,input_budget=8000):
    # Keep output batches modest for latency, but do not impose a fixed count.
    # Learn from actual completion usage with a margin; long claims cost more.
    room=max(96,min(1600,int(output_budget*.85)-128))
    result=[];group=[];used=0;input_used=0;citations=set()
    for line in lines:
        weight=max(100,float(tokens_per_claim)*1.2,80+cost(line)//10)
        ids=set(re.findall(r'\[(\d+)\]',line))
        if group and (used+weight>room or input_used+cost(line)>max(512,input_budget//3)
                      or len(citations|ids)>6):
            result.append(group);group=[];used=0;input_used=0;citations=set()
        group.append(line);used+=weight;input_used+=cost(line);citations|=ids
    if group:result.append(group)
    return result
