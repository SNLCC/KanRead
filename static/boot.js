// Internal feature lifecycle. No user paths, remote modules or runtime plugin installation.
// 版本号由服务端在 index.html 里注入（按静态文件修改时间生成），避免改了 JS/CSS
// 却因为浏览器缓存继续跑旧代码。
(async()=>{
 const features=[
  {id:'markdown',file:'markdown.js',requires:[]},
  {id:'core',file:'app.js',requires:['markdown']},
  {id:'library',file:'library.js',requires:['core']},
  {id:'settings',file:'settings.js',requires:['core']},
  {id:'reading',file:'reading.js',requires:['core','library']},
  {id:'translation',file:'translation.js',requires:['reading']},
  {id:'connections',file:'connections.js',requires:['settings']},
  {id:'interaction',file:'interaction.js',requires:['reading','connections']},
  {id:'speech',file:'speech.js',requires:['interaction','settings']},
  {id:'experience',file:'experience.js',requires:['speech','reading']},
  {id:'catalog',file:'catalog.js',requires:['experience','settings']},
  {id:'usability',file:'usability.js',requires:['catalog']}
 ];
 const loaded=new Set();
 try{for(const feature of features){if(feature.requires.some(id=>!loaded.has(id)))throw new Error('功能依赖缺失：'+feature.id);await new Promise((resolve,reject)=>{const script=document.createElement('script');script.src='/static/'+feature.file+'?v='+(window.__ASSET_VERSION||'1');script.onload=resolve;script.onerror=()=>reject(new Error('功能加载失败：'+feature.id));document.head.append(script);});loaded.add(feature.id);}await init();}
 catch(error){const status=document.getElementById('toast');status.textContent=error.message+'，请刷新页面重试。';status.hidden=false;}
})();
