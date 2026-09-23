"""Local, expiring checkpoints. No credentials or web queries are stored here."""
import hashlib
import json
import sqlite3
import time
import threading
from . import cancellation
_write_lock=threading.RLock()


class ReadingStore:
    def __init__(self,folder,identity,documents):
        self.path=folder/'reading.sqlite3'
        self.key=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.documents=json.dumps(sorted(documents))
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS checkpoints(task TEXT, stage TEXT, value TEXT, documents TEXT, updated REAL, PRIMARY KEY(task,stage))')
            c.execute('CREATE TABLE IF NOT EXISTS reading_tasks(task TEXT PRIMARY KEY, payload TEXT, documents TEXT, updated REAL)')
            c.execute('DELETE FROM checkpoints WHERE updated<?',(time.time()-7*86400,))
            c.execute('DELETE FROM reading_tasks WHERE updated<?',(time.time()-7*86400,))
            c.execute('DELETE FROM checkpoints WHERE task NOT IN (SELECT task FROM checkpoints GROUP BY task ORDER BY MAX(updated) DESC LIMIT 128)')

    def connect(self):return sqlite3.connect(self.path,timeout=30)

    def load(self,stage):
        with self.connect() as c:
            row=c.execute('SELECT value FROM checkpoints WHERE task=? AND stage=?',(self.key,stage)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self,stage,value):
        with _write_lock:
            cancellation.check()
            with self.connect() as c:
                c.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?,?)',(self.key,stage,json.dumps(value,ensure_ascii=False),self.documents,time.time()))

    def remember(self,payload):
        with _write_lock,self.connect() as c:
            cancellation.check()
            c.execute('INSERT OR REPLACE INTO reading_tasks VALUES(?,?,?,?)',(self.key,json.dumps(payload,ensure_ascii=False),self.documents,time.time()))

    def finish(self):
        with self.connect() as c:c.execute('DELETE FROM reading_tasks WHERE task=?',(self.key,))


def pending(folder,document_id):
    path=folder/'reading.sqlite3'
    if not path.exists():return []
    with sqlite3.connect(path,timeout=30) as c:
        rows=c.execute('SELECT task,payload,updated FROM reading_tasks WHERE updated>? ORDER BY updated DESC',(time.time()-7*86400,)).fetchall()
    return [{'id':key,'payload':json.loads(payload),'updated':updated} for key,payload,updated in rows if json.loads(payload).get('document_id')==document_id][:10]


def purge(folder,document_id):
    path=folder/'reading.sqlite3'
    if not path.exists():return
    with _write_lock,sqlite3.connect(path,timeout=30) as c:
        keys=[key for key,docs in c.execute('SELECT DISTINCT task,documents FROM checkpoints') if document_id in json.loads(docs)]
        c.executemany('DELETE FROM checkpoints WHERE task=?',[(key,) for key in keys])
        tasks=[key for key,docs in c.execute('SELECT task,documents FROM reading_tasks') if document_id in json.loads(docs)]
        c.executemany('DELETE FROM reading_tasks WHERE task=?',[(key,) for key in tasks])
        c.commit()
        c.execute('VACUUM')
