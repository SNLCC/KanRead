"""Versioned local page/section/visual index; source files are never rewritten."""
import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path
from . import cancellation
_write_lock=threading.RLock()


def connect():
    folder=Path(os.environ.get('READER_DATA_DIR',Path(__file__).resolve().parent.parent/'data'))
    folder.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(folder/'structure.sqlite3',timeout=30)
    c.execute('CREATE TABLE IF NOT EXISTS pages(path TEXT,page INTEGER,version TEXT,value TEXT,PRIMARY KEY(path,page))')
    c.execute('CREATE TABLE IF NOT EXISTS structures(document TEXT PRIMARY KEY,path TEXT,version TEXT,value TEXT)')
    return c


def path_key(path):return hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()


def page_get(path,version,page):
    with connect() as c:row=c.execute('SELECT value FROM pages WHERE path=? AND page=? AND version=?',(path_key(path),page,json.dumps(version))).fetchone()
    return json.loads(row[0]) if row else None


def page_put(path,version,page,value):
    with _write_lock,connect() as c:
        cancellation.check()
        c.execute('DELETE FROM pages WHERE path=? AND version<>?',(path_key(path),json.dumps(version)))
        c.execute('INSERT OR REPLACE INTO pages VALUES(?,?,?,?)',(path_key(path),page,json.dumps(version),json.dumps(value,ensure_ascii=False)))


def structure_put(document,path,version,tree,nodes):
    with _write_lock,connect() as c:
        cancellation.check()
        old=c.execute('SELECT value FROM structures WHERE document=? AND version=?',(document,json.dumps(version))).fetchone()
        previous=json.loads(old[0]) if old else {}
        retained=previous.get('semantic_nodes',[])
        # Keep captions from other already indexed pages during local reading.
        captions={json.dumps(n,sort_keys=True,ensure_ascii=False):n for n in previous.get('visual_nodes',[])+nodes}
        c.execute('INSERT OR REPLACE INTO structures VALUES(?,?,?,?)',
            (document,path_key(path),json.dumps(version),json.dumps({'sections':tree,'visual_nodes':list(captions.values()),'semantic_nodes':retained},ensure_ascii=False)))


def add_semantics(document,page,elements,version):
    with _write_lock,connect() as c:
        cancellation.check()
        row=c.execute('SELECT value FROM structures WHERE document=? AND version=?',(document,json.dumps(version))).fetchone()
        if not row:return
        value=json.loads(row[0]);nodes=[n for n in value.get('semantic_nodes',[]) if n['page']!=page]
        nodes.extend({'page':page,'kind':str(n.get('kind','visual')),'description':str(n.get('description',''))[:8000],'derived':True} for n in elements if isinstance(n,dict))
        value['semantic_nodes']=nodes
        c.execute('UPDATE structures SET value=? WHERE document=?',(json.dumps(value,ensure_ascii=False),document))


def indexed_chunks(documents):
    """Derived visual descriptions locate pages only; they never replace original evidence."""
    result=[];ids=list(documents)
    with connect() as c:
        for start in range(0,len(ids),400):
            group=ids[start:start+400]
            rows=c.execute('SELECT document,value,version FROM structures WHERE document IN ('+','.join('?' for _ in group)+')',group).fetchall()
            for doc,value,version in rows:
                value=json.loads(value)
                for i,node in enumerate(value.get('sections',[])+value.get('visual_nodes',[])+value.get('semantic_nodes',[])):
                    text=node.get('title') or node.get('caption') or node.get('description')
                    if text:result.append({'id':f'structure:{doc}:{i}','document_id':doc,'page':node['page'],'text':text,'bbox':'[]','bbox_space':'visual','derived':node.get('derived',False),'index_version':json.loads(version)})
    return result


def purge(document,path=None):
    with _write_lock,connect() as c:
        row=c.execute('SELECT path FROM structures WHERE document=?',(document,)).fetchone()
        key=row[0] if row else path_key(path) if path else None
        if key:c.execute('DELETE FROM pages WHERE path=?',(key,))
        c.execute('DELETE FROM structures WHERE document=?',(document,))
        c.commit();c.execute('VACUUM')
