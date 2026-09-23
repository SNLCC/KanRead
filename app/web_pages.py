"""Opt-in public web text. Pinned public IPs, no cookies, auth or private-network access."""
import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import urlsplit,urljoin,urlunsplit
import httpx
from . import cancellation


class Text(HTMLParser):
    def __init__(self):super().__init__();self.parts=[];self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript','svg'):self.skip+=1
        if tag in ('p','div','h1','h2','h3','tr','li','br'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript','svg') and self.skip:self.skip-=1
    def handle_data(self,data):
        if not self.skip:self.parts.append(data)


def destination(url):
    parsed=urlsplit(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:raise ValueError('Not public URL')
    port=parsed.port or (443 if parsed.scheme=='https' else 80)
    if port not in (80,443):raise ValueError('Unsupported public web port')
    addresses=list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(parsed.hostname,port,type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):raise ValueError('Private or special network')
    address=addresses[0]
    netloc=('['+address+']' if ':' in address else address)+':'+str(port)
    return urlunsplit((parsed.scheme,netloc,parsed.path or '/',parsed.query,'')),parsed.hostname


def fetch(url):
    for _ in range(4):
        cancellation.check();target,hostname=destination(url)
        with httpx.Client(timeout=15,trust_env=False,follow_redirects=False) as client:
            cancellation.attach(client)
            with client.stream('GET',target,headers={'Host':hostname,'User-Agent':'LiteratureReader/1.0','Accept':'text/html,text/plain'},extensions={'sni_hostname':hostname}) as response:
                if response.is_redirect:
                    url=urljoin(url,response.headers['location']);continue
                response.raise_for_status()
                kind=response.headers.get('content-type','').lower()
                if not any(k in kind for k in ('text/html','text/plain','application/xhtml')):raise ValueError('Non-text page')
                chunks=[];size=0;limited=False
                for chunk in response.iter_bytes():
                    cancellation.check();size+=len(chunk)
                    if size>2*1024*1024:limited=True;break
                    chunks.append(chunk)
                raw=b''.join(chunks).decode(response.encoding or 'utf-8',errors='replace')
        if 'html' in kind:
            parser=Text();parser.feed(raw);raw=''.join(parser.parts)
        text='\n'.join(line.strip() for line in raw.splitlines() if line.strip())
        if not text:raise ValueError('No public text')
        return {'page_text':text[:30000],'page_url':url,'page_truncated':limited or len(text)>30000,'page_status':'已获取公开网页正文'}
    raise ValueError('Too many redirects')


def augment(results,enabled,limit=None):
    """按需抓取公开网页正文。

    `limit`（第 93 轮）：**只抓前 N 条**。抓取本身是有代价的外发行为，而调用方只会在材料里
    用最相关的那几条——实测一轮里抓了十几条、只用了六条，等于白发出十几次请求。
    没被抓的条目保留搜索摘要，`page_status` 会说明"仅使用搜索摘要"。
    """
    if not enabled:return results
    fetched=0
    for row in results:
        if 'page_status' in row:continue
        if limit is not None and fetched>=limit:
            row['page_status']='未获取正文（本轮只抓取最相关的若干条），仅使用搜索摘要'
            continue
        try:
            row.update(fetch(row['url']));fetched+=1
        except Exception:
            cancellation.check();row['page_status']='正文未能获取，仅使用搜索摘要'
    return results
