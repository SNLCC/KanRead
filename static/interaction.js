const shortcutDefaults={previous:'PageUp',next:'PageDown',zoomIn:'Ctrl+=',zoomOut:'Ctrl+-',fit:'Ctrl+0',ask:'Ctrl+Enter',crop:'Ctrl+Shift+O',send:'Enter',newline:'Shift+Enter',newlineAlt:'Ctrl+Enter',stop:'Escape',sidebar:'Ctrl+Shift+L',voice:'F8',read:'Ctrl+Shift+R'};
const shortcutLabels={previous:'上一页',next:'下一页',zoomIn:'放大文献',zoomOut:'缩小文献',fit:'适应宽度',ask:'对选文提问（阅读区）',crop:'框选识别',send:'发送问题（输入框）',newline:'换行（输入框）',newlineAlt:'另一换行键（输入框）',stop:'停止回答 / 录音 / 朗读',sidebar:'显示或隐藏文献侧栏',voice:'按住说话，松开识别',read:'朗读最近回答'};
const composeKeys=['send','newline','newlineAlt'];
let readingPreferences={wheel:'zoom',keys:{...shortcutDefaults}};
try{const saved=JSON.parse(localStorage.getItem('reader-shortcuts')||'null');if(saved)readingPreferences={wheel:['zoom','page'].includes(saved.wheel)?saved.wheel:'zoom',keys:{...shortcutDefaults,...saved.keys}};}catch{}
function keyCombination(e){let key=e.key;if(!key)key='Mouse'+(e.button+1);if(key===' ')key='Space';if(key.length===1)key=key.toUpperCase();return [e.ctrlKey?'Ctrl':null,e.altKey?'Alt':null,e.shiftKey?'Shift':null,e.metaKey?'Meta':null,key].filter(Boolean).join('+');}
function toggleLibrary(){document.body.classList.toggle('sidebar-hidden');localStorage.setItem('reader-sidebar-hidden',document.body.classList.contains('sidebar-hidden'));$('#toggle-library').setAttribute('aria-expanded',!document.body.classList.contains('sidebar-hidden'));updateLibraryToggle();scheduleReaderResize();}
function updateLibraryToggle(){const hidden=document.body.classList.contains('sidebar-hidden');const button=$('#toggle-library');button.textContent=hidden?'▥':'▤';button.title=hidden?'展开文献栏':'收起文献栏';button.setAttribute('aria-label',button.title);button.setAttribute('aria-expanded',String(!hidden));}
const shortcutActions={previous:()=>go(state.page-1),next:()=>go(state.page+1),zoomIn:()=>$('#zoom-in').click(),zoomOut:()=>$('#zoom-out').click(),fit:()=>{state.zoom=1;resizePage();},ask:()=>$('#ask-selection').click(),crop:()=>$('#crop').click(),stop:()=>{stopAnswer();if(typeof cancelVoice==='function')cancelVoice();if(typeof stopSpeaking==='function')stopSpeaking();},sidebar:toggleLibrary,read:()=>readLastAnswer()};
let heldVoiceKey=null;
function dispatchShortcut(e){
 if(e.defaultPrevented||e.isComposing||e.keyCode===229||document.querySelector('dialog[open]'))return;
 const combo=keyCombination(e),input=e.target.closest('input,textarea,select,[contenteditable=true]'),isQuestion=e.target===$('#question');
 if(isQuestion){const action=composeKeys.find(k=>readingPreferences.keys[k]===combo);if(action){e.preventDefault();e.stopPropagation();if(action==='send'){if(!e.repeat&&(!state.busy||activeChat)&&e.target.value.trim())$('#chat-form').requestSubmit($('#send'));}else if(!e.repeat){const q=$('#question');q.setRangeText('\n',q.selectionStart,q.selectionEnd,'end');q.dispatchEvent(new Event('input',{bubbles:true}));}return;}}
 const action=Object.keys(shortcutLabels).filter(k=>!composeKeys.includes(k)).find(k=>readingPreferences.keys[k]===combo);
 if(!action||input&&!['stop','voice'].includes(action))return;
 if(!['stop','sidebar'].includes(action)&&(!state.doc||document.body.classList.contains('managing')))return;
 if(e.target.closest('.assistant-panel')&&!['stop','voice','read','sidebar'].includes(action))return;
 e.preventDefault();if(e.repeat)return;
 if(action==='voice'){heldVoiceKey=e.code||('Mouse'+(e.button+1));startVoice();return;}
 if(['stop','sidebar','previous','next','zoomIn','zoomOut','fit'].includes(action)||!state.busy)shortcutActions[action]();
}
document.addEventListener('keydown',dispatchShortcut);
document.addEventListener('mousedown',e=>{if(e.button!==0)dispatchShortcut(e);});
function releaseVoice(e){if(heldVoiceKey&&(e.code||('Mouse'+(e.button+1)))===heldVoiceKey){e.preventDefault();heldVoiceKey=null;finishVoice();}}
document.addEventListener('keyup',releaseVoice);document.addEventListener('mouseup',releaseVoice);
document.addEventListener('contextmenu',e=>{if(!document.querySelector('dialog[open]')&&Object.values(readingPreferences.keys).includes(keyCombination({...e,key:undefined,button:2,ctrlKey:e.ctrlKey,altKey:e.altKey,shiftKey:e.shiftKey,metaKey:e.metaKey}))){e.preventDefault();e.stopImmediatePropagation();}},true);
document.addEventListener('auxclick',e=>{if(Object.values(readingPreferences.keys).includes(keyCombination(e)))e.preventDefault();});
window.addEventListener('blur',()=>{heldVoiceKey=null;if(typeof cancelVoice==='function')cancelVoice();});
let lastWheelPage=0;
// 滚轮翻页由本模块独占；滚轮缩放由 experience.js 的捕获阶段处理器独占。
// 两者不可同时监听同一次事件：捕获阶段会 stopImmediatePropagation，
// 若这里也处理缩放，翻页分支将永远不可达（历史上即为此状态）。
$('#viewport').addEventListener('wheel',e=>{
 if(!e.ctrlKey)return;
 if(readingPreferences.wheel!=='page')return;
 e.preventDefault();if(!state.pageData||!e.deltaY)return;
 if(Date.now()-lastWheelPage<250)return;lastWheelPage=Date.now();go(state.page+(e.deltaY>0?1:-1));
},{passive:false});
function buildShortcutSettings(){
 const panel=$('#shortcut-settings-panel');panel.replaceChildren(el('h3','','快捷键与阅读'));
 panel.append(el('p','hint','发送和换行仅作用于提问输入框；阅读快捷键不会抢占输入。可绑定中键或侧键，默认不使用额外鼠标键。按住说话仅在语音输入开关开启后生效。'));
 const form=el('form');form.id='shortcut-settings-form';panel.append(form);const wheel=connectionField(form,'文献区 Ctrl + 鼠标滚轮',readingPreferences.wheel,'text',[['zoom','缩放文献（默认）'],['page','上下翻页']]);
 const keys={};for(const [action,label] of Object.entries(shortcutLabels)){
 const input=connectionField(form,label,readingPreferences.keys[action]);input.readOnly=true;input.placeholder='点击后按键，或按中键 / 侧键';input.setAttribute('aria-label',label+'快捷键');keys[action]=input;
 input.onkeydown=e=>{if(e.key==='Tab')return;e.preventDefault();e.stopPropagation();if(['Control','Alt','Shift','Meta'].includes(e.key))return;if(e.key==='Backspace'||e.key==='Delete'){input.value='';return;}input.value=keyCombination(e);};
 input.onmousedown=e=>{if(e.button===0)return;e.preventDefault();e.stopPropagation();input.value=keyCombination(e);};input.oncontextmenu=e=>e.preventDefault();input.onauxclick=e=>{e.preventDefault();e.stopPropagation();};
 }
 form.append(el('p','hint','Backspace 清除绑定。恢复默认后点击保存。系统保留键或鼠标驱动优先处理的按钮可能无法覆盖。阅读区与输入框允许复用组合键；停止、录音、侧栏等全局操作不能冲突。'));
 const reset=el('button','secondary','恢复默认');reset.type='button';form.append(reset);
 reset.onclick=()=>{wheel.value='zoom';for(const action of Object.keys(keys))keys[action].value=shortcutDefaults[action];};
 form.onsubmit=e=>{e.preventDefault();const entries=Object.entries(keys).filter(([,v])=>v.value);for(let i=0;i<entries.length;i++)for(let j=i+1;j<entries.length;j++){const [a,x]=entries[i],[b,y]=entries[j];if(x.value!==y.value)continue;const global=['stop','sidebar','voice','read'];if(global.includes(a)||global.includes(b)||composeKeys.includes(a)===composeKeys.includes(b))return toast('快捷键冲突：'+shortcutLabels[a]+' / '+shortcutLabels[b]);}
 readingPreferences={wheel:wheel.value,keys:Object.fromEntries(Object.entries(keys).map(([k,v])=>[k,v.value]))};localStorage.setItem('reader-shortcuts',JSON.stringify(readingPreferences));updateShortcutHint();toast('快捷键与阅读设置已保存');};
}
function updateShortcutHint(){const hint=$('#reader-footer>span');if(hint)hint.textContent='选中文字后点击「提问」'+(readingPreferences.keys.ask?'，或按 '+readingPreferences.keys.ask:'');$('#question').placeholder=`${readingPreferences.keys.send||'点击发送按钮'} 发送；${readingPreferences.keys.newline||'换行键未绑定'} 换行`;}
const libraryToggle=el('button','secondary','≡ 文献侧栏');libraryToggle.id='toggle-library';libraryToggle.onclick=toggleLibrary;libraryToggle.setAttribute('aria-expanded','true');$('header').prepend(libraryToggle);
if(localStorage.getItem('reader-sidebar-hidden')==='true')toggleLibrary();
updateShortcutHint();

updateLibraryToggle();

const composerGrip=el('div','composer-grip');composerGrip.tabIndex=0;composerGrip.setAttribute('role','separator');composerGrip.setAttribute('aria-label','调整输入框高度');composerGrip.setAttribute('aria-orientation','horizontal');$('#question').before(composerGrip);
function setComposerHeight(height){height=Math.max(52,Math.min(window.innerHeight*.4,height));$('#question').style.height=height+'px';composerGrip.setAttribute('aria-valuemin','52');composerGrip.setAttribute('aria-valuemax',Math.round(window.innerHeight*.4));composerGrip.setAttribute('aria-valuenow',Math.round(height));localStorage.setItem('reader-composer-height',height);}
const storedHeight=Number(localStorage.getItem('reader-composer-height'));setComposerHeight(storedHeight||70);
let composerDrag=null;
composerGrip.onpointerdown=e=>{if(e.button!==0)return;e.preventDefault();composerDrag={y:e.clientY,height:$('#question').getBoundingClientRect().height};composerGrip.setPointerCapture(e.pointerId);};
composerGrip.onpointermove=e=>{if(composerDrag)setComposerHeight(composerDrag.height+composerDrag.y-e.clientY);};
composerGrip.onpointerup=composerGrip.onpointercancel=()=>{composerDrag=null;};
composerGrip.onkeydown=e=>{if(['ArrowUp','ArrowDown','Home'].includes(e.key)){e.preventDefault();setComposerHeight(e.key==='Home'?70:$('#question').clientHeight+(e.key==='ArrowUp'?16:-16));}};
