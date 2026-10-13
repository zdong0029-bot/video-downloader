/* Keep original bytes. The OS share sheet owns the final Photos operation. */
(() => {
 const q=s=>document.querySelector(s),LIMIT=128*1048576,PACK=16;
 const mimeByExt={jpg:'image/jpeg',jpeg:'image/jpeg',png:'image/png',webp:'image/webp',gif:'image/gif',bmp:'image/bmp',avif:'image/avif',heic:'image/heic',heif:'image/heif',mp4:'video/mp4',mov:'video/quicktime',m4v:'video/mp4',webm:'video/webm',mkv:'video/x-matroska',avi:'video/x-msvideo'};
 const mime=n=>mimeByExt[String(n).split('.').pop().toLowerCase()]||'';
 window.isAlbumMedia=n=>!!mime(n);
 const dialog=q('#previewDialog'),video=q('#previewVideo'),image=q('#previewImage'),share=q('#shareVideo'),help=q('#saveHelp'),next=q('#nextAlbumBatch'),download=q('#saveOriginal');
 let current=null;const previewURLs=[];
 function fileURL(item){return '/api/files/'+encodeURIComponent(item.id)+'/'+encodeURIComponent(item.name);}
 function live(s){return current===s&&!s.controller.signal.aborted;}
 function supported(){return window.isSecureContext&&typeof navigator.share==='function'&&typeof navigator.canShare==='function';}
 function canShare(files){try{return supported()&&navigator.canShare({files});}catch(e){return false;}}
 function stop(){window.MEDIA_PREPARING=false;for(const url of previewURLs)URL.revokeObjectURL(url);previewURLs.length=0;if(current){current.controller.abort();current.files=[];}current=null;video.pause();video.removeAttribute('src');video.load();image.removeAttribute('src');share.onclick=null;next.onclick=null;}
 window.closePreview=()=>{stop();if(dialog.open)dialog.close();};
 dialog.addEventListener('cancel',stop);dialog.addEventListener('close',()=>{if(!dialog.open)stop();});
 function preview(item,file=null,remote=false){
  video.pause();for(const old of previewURLs)URL.revokeObjectURL(old);previewURLs.length=0;
  const type=mime(item.name);let url=fileURL(item);const isImage=type.startsWith('image/');if(file){url=URL.createObjectURL(file);previewURLs.push(url);}
  video.hidden=isImage;image.hidden=!isImage;
  if(isImage){video.removeAttribute('src');if(file||remote||!MOBILE){image.src=file?url:url+'?inline=1';}else{image.removeAttribute('src');image.hidden=true;}}
  else{image.removeAttribute('src');video.preload='none';if(file||remote||!MOBILE)video.src=file?url:url+'?inline=1';else video.removeAttribute('src');}
  download.href=file?url:fileURL(item);download.download=item.name;download.onclick=null;
 }
 function fallback(s,message){
  if(!live(s))return;preview(s.items[s.offset],null,!window.BrowserEnvironment?.wechat);share.hidden=false;share.disabled=true;share.textContent='系统相册保存不可用';next.hidden=true;
  help.textContent=message+(s.items.length===1&&mime(s.items[0].name).startsWith('image/')?' 可长按上面的原图，选择保存图片。':' 可点“下载原文件”，再从手机文件应用保存到相册。');
 }
 async function readOriginal(item,s){
  const cached=await window.MediaOriginalCache?.get(item);if(cached){if(!live(s))throw new DOMException('Stopped','AbortError');if(s.bytes+cached.size>LIMIT)throw Error('当前批次超过手机准备容量，请减少勾选数量。');s.bytes+=cached.size;s.cachedCount++;return new File([cached],item.name,{type:mime(item.name)||cached.type});}
  if(item.size>LIMIT)throw Error('此原文件超过 128 MB，请使用“下载原文件”，下载后从文件应用存入相册。');
  const start=performance.now();
  const progress=(received,total,route='原文件传输')=>{
   if(!live(s)||performance.now()-s.lastPaint<250)return;s.lastPaint=performance.now();
   const elapsed=Math.max(.1,(performance.now()-start)/1000),rate=received/elapsed,remaining=rate>0&&total?Math.ceil((total-received)/rate):0;
   help.textContent=route+' · '+fmtSize(received)+(total?' / '+fmtSize(total)+' · '+Math.min(100,Math.floor(received/total*100))+'%':'')+' · '+fmtSize(rate)+'/秒'+(remaining>0?' · 约剩 '+(remaining>60?Math.ceil(remaining/60)+' 分钟':remaining+' 秒'):'')+'。接收完成后存相册，原画不变。';
  };
  const r=await window.fetchAlbumOriginal(item,s.controller.signal,progress);
  if(!r.ok)throw Error('原文件读取失败（HTTP '+r.status+'），请重试。');
  const type=(r.headers.get('content-type')||'').split(';')[0].trim();
  if(type==='text/html'||type==='application/json'){await r.body?.cancel();throw Error('服务器未返回媒体原文件，请重试。');}
  const expected=Number(r.headers.get('content-length'))||Number(item.size)||0;
  if(expected>LIMIT){await r.body?.cancel();throw Error('此原文件较大，请使用“下载原文件”，下载后再存入相册。');}
  // Reuse the verified assembled Blob without a second JS chunk-array copy.
  if(r.originalBlob instanceof Blob){
   const blob=r.originalBlob;if(!blob.size||blob.size!==expected||(item.size&&blob.size!==Number(item.size)))throw Error('原文件接收不完整');
   s.bytes+=blob.size;if(s.bytes>LIMIT)throw Error('当前批次超过手机准备容量');
   await r.body?.cancel();if(!live(s))throw new DOMException('Stopped','AbortError');
   const file=new File([blob],item.name,{type:mime(item.name)||type});help.textContent='原文件已完整接收，正在缓存到手机，再次保存可直接复用…';await window.MediaOriginalCache?.put(item,file);return file;
  }
  const chunks=[];let size=0;const reader=r.body?.getReader();
  if(reader){try{while(true){const data=await reader.read();if(data.done)break;size+=data.value.byteLength;s.bytes+=data.value.byteLength;if(size>LIMIT||s.bytes>LIMIT)throw Error('当前批次超过手机准备容量，请减少勾选数量或下载原文件。');chunks.push(data.value);progress(size,expected,r.headers.get('X-Transfer-Route')==='parallel2'?'两路原文件传输':'原文件传输');}}finally{try{await reader.cancel();}catch(e){}reader.releaseLock();}}
  else{const buffer=await r.arrayBuffer();size=buffer.byteLength;s.bytes+=size;if(size>LIMIT||s.bytes>LIMIT)throw Error('原文件较大，请先下载原文件。');chunks.push(buffer);}
  if(!size||(expected&&size!==expected)||(Number(item.size)&&size!==Number(item.size)))throw Error('原文件接收不完整，请重试；未交给系统保存。');
  if(!live(s))throw new DOMException('Stopped','AbortError');
  const file=new File(chunks,item.name,{type:mime(item.name)||type});if(live(s))help.textContent='原文件已完整接收，正在缓存到手机，再次保存可直接复用…';await window.MediaOriginalCache?.put(item,file);return file;
 }
 async function prepare(s){
  if(!live(s))return;window.MEDIA_PREPARING=true;s.files=[];s.bytes=0;s.cachedCount=0;s.lastPaint=0;share.hidden=false;share.disabled=true;share.textContent='正在准备…';next.hidden=true;
  const start=s.offset;let end=start,estimate=0;
  while(end<s.items.length&&end-start<s.pack){const size=Number(s.items[end].size)||0;if(end>start&&estimate+size>LIMIT)break;estimate+=size;end++;}
  s.batchCount=end-start;preview(s.items[start]);
  q('#previewTitle').textContent=s.items.length===1?(mime(s.items[0].name).startsWith('image/')?'图片 · 原画保存':'视频 · 原画保存'):'选中素材 · 原画保存';
  if(!MOBILE){share.hidden=true;help.textContent='预览和下载均为原文件，画质保持不变。';window.MEDIA_PREPARING=false;return;}
  if(window.BrowserEnvironment?.wechat&&(!supported()||!canShare([new File([new Uint8Array([0])],s.items[start].name,{type:mime(s.items[start].name)})]))){fallback(s,'微信内的系统相册分享受限制。请点击右上角“…”→ 在 Safari / 系统浏览器打开本站；可先复制本轮素材链接继续。');window.MEDIA_PREPARING=false;return;}
  if(!supported()){fallback(s,window.isSecureContext?'此浏览器不支持图片、视频的系统保存，请用系统浏览器打开。':'请从公网 HTTPS 网站打开，再保存到相册。');window.MEDIA_PREPARING=false;return;}
  help.textContent='正在准备原始图片 / 视频，请稍候…';
  try{
   for(let i=start;i<end;i++){if(!live(s))return;s.files.push(await readOriginal(s.items[i],s));}
   if(!live(s))return;
   if(!canShare(s.files)){
    if(s.files.length>1&&canShare([s.files[0]])){s.pack=1;end=start+1;s.files=s.files.slice(0,1);}
    else{fallback(s,'系统不支持保存这个原文件格式。为保持画质，不会自动压缩或转码。');return;}
   }
   s.end=end;preview(s.items[start],s.files[0]);share.disabled=false;share.textContent=s.files.length===1?'保存到相册':'保存这 '+s.files.length+' 个到相册';
   help.textContent='原文件已准备好并缓存到本机'+(s.items.length>1?'（'+(start+1)+'–'+end+' / '+s.items.length+'）':'')+'。点击按钮后，在系统菜单向下找到“存储图像 / 存储视频”。图片也可长按原图，选择存入照片；本页再次打开会复用缓存。';
   share.onclick=async()=>{
    if(!live(s)||s.sharing)return;s.sharing=true;share.disabled=true;
    try{
     // Invoke immediately from this fresh click; do not fetch before native share.
     await navigator.share({files:s.files});
     if(!live(s))return;help.textContent='已交给系统菜单，请确认你已选择保存，并到相册查看结果。网页无法确认是否已写入相册。';
     if(s.end<s.items.length){next.hidden=false;next.textContent='准备下一批（剩余 '+(s.items.length-s.end)+' 个）';next.onclick=()=>{s.offset=s.end;void prepare(s);};}
    }catch(e){
     if(!live(s))return;
     if(e.name==='AbortError')help.textContent='已关闭系统菜单，未确认保存；可以再次点击。';
     else if(e.name==='NotAllowedError')help.textContent='系统未允许保存。请再次点击，或在系统浏览器打开；也可下载原文件。';
     else help.textContent='系统保存未完成。可重试、减少勾选，或下载原文件后保存到相册。';
    }finally{if(live(s)){s.sharing=false;share.disabled=false;}}
   };
  }catch(e){if(e.name!=='AbortError'&&live(s))fallback(s,e.message);}finally{if(live(s))window.MEDIA_PREPARING=false;}
 }
 function open(items){
  stop();const list=items.filter(item=>mime(item.name));
  if(!list.length){hintEl.textContent='请先选择已经准备好的图片或视频，再存入相册。';return;}
  current={items:list,offset:0,end:0,files:[],pack:PACK,bytes:0,controller:new AbortController(),sharing:false};
  if(!dialog.open)dialog.showModal();void prepare(current);
 }
 window.saveAlbumItems=open;
 window.previewFile=(id,name)=>{
  const job=cache.find(j=>j.id===id),file=job?.files.find(f=>f.name===name);
  open([{id,name,size:file?.size||0}]);
 };
 window.saveSelectedAlbum=id=>{
  const job=viewJob(id);if(!job)return;open(selectedIn(job).map(f=>({id:originId(job,f),name:f.name,size:f.size})));
 };
 window.saveCatalogAlbum=id=>{
  const chosen=catalogSet(id),files=catalogFiles(id),items=[];
  for(const entry of chosen){const f=files.get(entry);if(f)items.push({id:f.job_id,name:f.name,size:f.size});}
  if(!items.length){hintEl.textContent='选中的素材还未准备好，请先点击“下载选中”，主机准备完成后再存相册。';return;}
  open(items);
 };
})();
