"""Locally supplied tokenizer JSON only. Never loads code or downloads model assets."""
import functools
import hashlib
import os
from pathlib import Path
from fastapi import HTTPException
from . import settings
from .literature_core.reading import token_counter


def folder():return Path(os.environ.get('READER_DATA_DIR',Path(__file__).resolve().parent.parent/'data'))/'tokenizers'


@functools.lru_cache(maxsize=8)
def tokenizer(key):
    from tokenizers import Tokenizer
    result=Tokenizer.from_file(str(folder()/(key+'.json')))
    result.no_truncation();result.no_padding()
    return result


def import_tokenizer(raw):
    from tokenizers import Tokenizer
    if len(raw)>32*1024*1024:raise HTTPException(413,'分词器文件不能超过 32 MB')
    try:
        parsed=Tokenizer.from_str(raw.decode('utf-8'))
        parsed.encode('分词器验证 Tokenizer test',add_special_tokens=False)
    except Exception:raise HTTPException(400,'不是有效的 tokenizer.json；不支持执行 Python 分词器代码') from None
    key=hashlib.sha256(raw).hexdigest();folder().mkdir(parents=True,exist_ok=True)
    (folder()/(key+'.json')).write_bytes(raw)
    tokenizer.cache_clear()
    return {'tokenizer_id':key,'message':'已导入本机，请保存设置，并确认它与当前模型匹配。'}


def configured(fn):
    @functools.wraps(fn)
    def wrapped(*args,**kwargs):
        key=settings.runtime('chat').get('tokenizer_id','')
        counter=None
        if key:
            try:
                tok=tokenizer(key)
                counter=lambda text:len(tok.encode(text,add_special_tokens=False).ids)
            except Exception:raise HTTPException(400,'已配置的本机 tokenizer 不可用，请重新导入或切回保守估算') from None
        token=token_counter.set(counter)
        try:return fn(*args,**kwargs)
        finally:token_counter.reset(token)
    return wrapped
