// Small DOM-only renderer: raw HTML stays text; no images or remote embeds.
function renderMarkdown(value){
 const root=document.createElement('div');root.className='markdown';
 function inline(parent,text,depth=0){
  if(depth>8){parent.append(document.createTextNode(text));return;}
  const pattern=/(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g;
  let pos=0;for(const m of text.matchAll(pattern)){parent.append(document.createTextNode(text.slice(pos,m.index)));const t=m[0];let n;
   if(t[0]==='`'){n=document.createElement('code');n.textContent=t.slice(1,-1);}
   else if(t[0]==='['){n=document.createElement('a');const split=t.indexOf('](');n.textContent=t.slice(1,split);n.href=t.slice(split+2,-1);n.target='_blank';n.rel='noopener noreferrer';}
   else{const strong=t.startsWith('**')||t.startsWith('__');n=document.createElement(strong?'strong':'em');inline(n,t.slice(strong?2:1,strong?-2:-1),depth+1);}parent.append(n);pos=m.index+t.length;
  }parent.append(document.createTextNode(text.slice(pos)));
 }
 const lines=String(value||'').replace(/\r\n?/g,'\n').split('\n');let list=null;
 for(let i=0;i<lines.length;i++){
  const line=lines[i];if(line.startsWith('```')){list=null;const pre=document.createElement('pre'),code=document.createElement('code'),body=[];while(++i<lines.length&&!lines[i].startsWith('```'))body.push(lines[i]);code.textContent=body.join('\n');pre.append(code);root.append(pre);continue;}
  if(!line.trim()){list=null;continue;}
  if(line.includes('|')&&i+1<lines.length&&/^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[i+1])){
   list=null;const table=document.createElement('table'),head=document.createElement('thead'),body=document.createElement('tbody');
   const cells=text=>text.trim().replace(/^\||\|$/g,'').split('|').map(t=>t.trim());
   const row=(text,tag)=>{const tr=document.createElement('tr');for(const value of cells(text)){const td=document.createElement(tag);inline(td,value);tr.append(td);}return tr;};
   head.append(row(line,'th'));i++;while(i+1<lines.length&&lines[i+1].includes('|')&&lines[i+1].trim())body.append(row(lines[++i],'td'));table.append(head,body);const wrap=document.createElement('div');wrap.className='markdown-table';wrap.append(table);root.append(wrap);continue;
  }
  const heading=line.match(/^(#{1,6})\s+(.+)$/),item=line.match(/^\s*(?:([-+*])|\d+[.)])\s+(.+)$/);
  if(item){const tag=item[1]?'ul':'ol';if(!list||list.tagName.toLowerCase()!==tag){list=document.createElement(tag);root.append(list);}const li=document.createElement('li');inline(li,item[2]);list.append(li);continue;}
  list=null;const node=document.createElement(heading?'h'+Math.min(6,heading[1].length+2):line.startsWith('> ')?'blockquote':'p');inline(node,heading?heading[2]:line.replace(/^> /,''));root.append(node);
 }return root;
}
