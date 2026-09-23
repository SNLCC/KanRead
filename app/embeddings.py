"""Optional OpenAI-compatible embeddings, with persistent per-provider cache."""
import hashlib
import json
import math
import os
import sqlite3

import httpx
from . import settings, model_services


def enabled():
    return settings.runtime('embedding')['enabled']


def embed(texts):
    profile=settings.runtime('embedding')
    with httpx.Client(timeout=120,trust_env=model_services.use_proxy(profile)) as client:
        r=client.post(profile['base_url']+'/embeddings',
                      headers={'Authorization':'Bearer '+(profile['api_key'] or 'local')},
                      json={'model':profile['model'],'input':texts})
        r.raise_for_status()
    rows=sorted(r.json()['data'],key=lambda x:x['index'])
    if len(rows)!=len(texts):
        raise ValueError('Embedding count does not match input')
    vectors=[row['embedding'] for row in rows]
    for vector in vectors:
        if not vector or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in vector):
            raise ValueError('Invalid embedding')
    return vectors


@settings.frozen
def vectors_for(chunks, data_dir):
    profile=settings.runtime('embedding')
    provider=profile['base_url']+'|'+profile['model']
    keys=[hashlib.sha256((provider+'|'+c['text']).encode()).hexdigest() for c in chunks]
    with sqlite3.connect(data_dir/'vectors.sqlite3',timeout=30) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS vectors(key TEXT PRIMARY KEY, value TEXT)')
        cached=dict(conn.execute('SELECT key,value FROM vectors'))
        missing=list(dict.fromkeys(k for k in keys if k not in cached))
        by_key=dict(zip(keys,chunks))
        for start in range(0,len(missing),32):
            batch=missing[start:start+32]
            results=embed([by_key[k]['text'] for k in batch])
            for key,vector in zip(batch,results):
                value=json.dumps(vector)
                conn.execute('INSERT OR REPLACE INTO vectors VALUES(?,?)',(key,value))
                cached[key]=value
        return [json.loads(cached[k]) for k in keys]


@settings.frozen
def semantic_rank(query,chunks,data_dir,document_id,page,mode='close',limit=8):
    if not chunks:
        return []
    vectors=vectors_for(chunks,data_dir)
    q=embed([query])[0]
    qnorm=math.sqrt(sum(x*x for x in q))
    results=[]
    for chunk,v in zip(chunks,vectors):
        if len(q)!=len(v):
            raise ValueError('Embedding dimension mismatch; use a distinct model name')
        norm=math.sqrt(sum(x*x for x in v))*qnorm
        score=sum(a*b for a,b in zip(q,v))/norm if norm else 0
        if score<=0:
            continue
        same=chunk['document_id']==document_id
        bonus=(.08 if mode=='close' else .02)/(1+abs(chunk['page']-page)) if same else 0
        results.append({**chunk,'score':round(score+bonus,4)})
    return sorted(results,key=lambda r:r['score'],reverse=True)[:limit]
