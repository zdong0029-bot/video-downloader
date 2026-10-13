let catalogCache=[],catalogSnapshot='',catalogEtag='',catalogRefreshing=false,catalogInitialized=false,catalogRefreshAgain=false;
const catalogVisible=new Map(),catalogChosen=new Map(),catalogBusy=new Set(),catalogAutoSave=new Set();
function rememberCatalogSelection(){}
function visibleCatalog(c){
  return (VIEW==='history'||CATEGORY==='profile'&&workspaceCatalogs.has(c.id))
    && matchStatus(c.status)
    && (!searchValue()||[c.title,c.url,...c.entries.map(e=>e.title)].join(' ').toLowerCase().includes(searchValue()));
}
function catalogSet(id){if(!catalogChosen.has(id))catalogChosen.set(id,new Set());return catalogChosen.get(id);}
function catalogToggle(id,item,on){
  const set=catalogSet(id);on?set.add(item):set.delete(item);renderCatalogs();
}
function catalogAll(id,on){
  const c=catalogCache.find(c=>c.id===id);if(!c)return;
  const set=catalogSet(id);set.clear();if(on){c.entries.forEach(e=>set.add(e.id));expandedCards.add('catalog-'+id);}
  renderCatalogs();
}
function catalogFold(id){
  const k='catalog-'+id;expandedCards.has(k)?expandedCards.delete(k):expandedCards.add(k);renderCatalogs();
}
function durationLabel(s){s=Math.round(Number(s)||0);return s?Math.floor(s/60)+':'+String(s%60).padStart(2,'0'):'';}
function platformLabel(url){try{return new URL(url).hostname.replace(/^www\./,'');}catch(e){return '主页';}}
function catalogJobs(id){return cache.filter(j=>j.catalog_id===id);}
function catalogFiles(id){
  const files=new Map();
  for(const j of catalogJobs(id))for(const f of j.files||[])if(f.entry_id)files.set(f.entry_id,{...f,job_id:j.id});
  return files;
}
function renderCatalogs(){
  const shown=catalogCache.filter(visibleCatalog);
  document.querySelector('#catalogs').innerHTML=shown.map(c=>{
    const set=catalogSet(c.id),open=expandedCards.has('catalog-'+c.id),pending=['loading','queued'].includes(c.status);
    const working=catalogBusy.has(c.id),all=c.entries.length&&set.size===c.entries.length,files=catalogFiles(c.id),jobs=catalogJobs(c.id);
    const actions=open?c.entries.slice(0,catalogVisible.get(c.id)||(MOBILE?20:60)).map(e=>{
      const f=files.get(e.id),j=jobs.find(j=>j.current_entry===e.id||j.failed_entries?.[e.id]||j.selected_entries?.includes(e.id));
      const running=j&&['running','queued'].includes(j.status),err=j?.failed_entries?.[e.id];
      const progress=j?.current_entry===e.id?Math.round(j.progress*100):null;
      const state=f?'<span class="status-done">✓ 主机已就绪</span>':err?'<span class="status-error">失败</span>':progress!==null?'<div class="row-progress"><i style="width:'+progress+'%"></i></div>'+progress+'%':running?'等待后台准备':'待选择';
      const arg=f?esc(JSON.stringify(f.name)):'';
      const detail=f?'原文件<br><span class="fsize">'+fmtSize(f.size)+'</span>':'<span class="fsize">下载时解析最高画质</span>';
      const buttons=f?'<button class="ghost" onclick="'+(MOBILE&&isAlbumMedia(f.name)?'previewFile':'saveFile')+'(\''+f.job_id+'\','+arg+')">'+(MOBILE&&isAlbumMedia(f.name)?'存相册':'保存')+'</button>'+(!MOBILE?LOCAL?'<button class="ghost" onclick="revealFile(\''+f.job_id+'\','+arg+')">定位</button>':'<button class="ghost" data-folder-file="'+esc(f.name)+'" onclick="folderLocatorHelp()">定位</button>':''):'<a target="_blank" rel="noopener noreferrer" href="'+esc(e.url)+'">查看</a>';
      return '<div class="file catalog-file '+(set.has(e.id)?'sel':'')+'" data-entry="'+esc(e.id)+'"><label><input aria-label="选择视频" type="checkbox" '+(set.has(e.id)?'checked ':'')+(working?'disabled ':'')+' onchange="catalogToggle(\''+c.id+'\','+esc(JSON.stringify(e.id))+',this.checked)"></label><div class="catalog-name">'+(e.thumbnail?'<img class="thumb" alt="" loading="lazy" referrerpolicy="no-referrer" src="'+esc(e.thumbnail)+'" onerror="this.style.visibility=\'hidden\'">':'<span class="thumb thumb-empty" aria-label="暂无缩略图"></span>')+'<div class="fname">'+esc(e.title)+'<br><span class="fsize">'+durationLabel(e.duration)+'</span></div></div><div class="file-detail">'+detail+'</div><div class="file-state" title="'+esc(err||'')+'">'+state+'</div><div class="file-actions">'+buttons+'</div></div>';
    }).join(''):'';
    const active=jobs.filter(j=>['queued','running'].includes(j.status));
    const progress=jobs.length?'<div class="note">本主页已准备 '+files.size+' 个文件'+(active.length?' · '+active.length+' 组后台处理中':'')+'</div>':'';
    return '<section class="job catalog" data-catalog="'+c.id+'"><div class="job-top"><div><div class="job-title"><span class="catalog-badge">'+esc(platformLabel(c.url))+'</span>'+esc(c.title)+' · '+c.entries.length+' 个素材</div><div class="job-url">'+esc(c.url)+'</div></div><button class="ghost" aria-expanded="'+open+'" onclick="catalogFold(\''+c.id+'\')">'+(open?'收起':'展开')+'</button></div><p class="msg">'+esc(c.message)+'</p>'+
      (c.entries.length?'<div class="toolbar"><label><input data-catalog-all="'+c.id+'" type="checkbox" '+(all?'checked ':'')+(working?'disabled ':'')+'onchange="catalogAll(\''+c.id+'\',this.checked)">全选此主页</label><span class="count">已选 '+set.size+' / '+c.entries.length+'</span><span class="spacer"></span><button class="small" '+(!set.size||working?'disabled':'')+' onclick="catalogDownload(\''+c.id+'\')">'+(working?'提交中…':MOBILE?'准备 / 保存选中':'下载选中')+'</button>'+(MOBILE&&files.size?(window.shortcutButtons?.('shortcutCatalog(\''+c.id+'\')',!!set.size)||'')+'<button class="ghost" '+(!set.size?'disabled':'')+' onclick="saveCatalogAlbum(\''+c.id+'\')">保存选中到相册</button>':'')+'</div>':'')+progress+
      (jobs.map(j=>'<div class="note">'+esc(j.message)+(['running','queued'].includes(j.status)?' <button class="ghost" onclick="cancelJob(\''+j.id+'\')">取消后台下载</button>':'')+'</div><div class="hint" data-transfer="'+j.id+'" '+(downloadTransfers.has(j.id)?'':'hidden')+'></div>').join(''))+
      (!pending&&c.limited?'<p class="note">部分列表，平台尚未确认读取结束。<button class="ghost" onclick="catalogMore(\''+c.id+'\')">继续读取全部</button></p>':!pending&&c.complete?'<p class="note">全部公开作品 · '+c.entries.length+' 个</p>':'')+
      (open?'<div class="file-heading"><span></span><span>文件</span><span>画质 / 大小</span><span>状态 / 进度</span><span>操作</span></div><div class="files">'+actions+'</div>':c.entries.length?'<p class="fold-note">已收起素材列表，勾选与进度保留。</p>':'')+
      (open&&c.entries.length>(catalogVisible.get(c.id)||(MOBILE?20:60))?'<div class="load-sentinel" data-more-catalog="'+c.id+'">已显示 '+(catalogVisible.get(c.id)||(MOBILE?20:60))+' / '+c.entries.length+' · 向下滚动继续加载 <button class="ghost" onclick="catalogShowMore(\''+c.id+'\')">继续加载</button></div>':'')+
      (VIEW==='history'&&c.completed_at?'<p class="note">记录保留至 '+esc(new Date((c.completed_at+7*86400)*1000).toLocaleString())+'</p>':'')+
      '<div class="acts"><button class="ghost" onclick="catalogRemove(\''+c.id+'\')">移出列表</button></div></section>';
  }).join('');
  for(const c of shown){const box=document.querySelector('[data-catalog-all="'+c.id+'"]');if(box)box.indeterminate=catalogSet(c.id).size>0&&catalogSet(c.id).size<c.entries.length;}
  downloadTransfers.forEach((_,id)=>updateTransfer(id));
  scheduleListLoading();
  for(const id of [...catalogAutoSave]){
    const job=cache.find(j=>j.id===id);
    if(job&&!['queued','running'].includes(job.status)){
      catalogAutoSave.delete(id);
      for(const f of job.files||[])selected.add(key(id,f.name));
      if(job.files.length&&!MOBILE)void downloadSelected(id);else if(job.files.length)hintEl.textContent='原文件已准备好，请点击“保存选中到相册”，然后在系统菜单确认。';
    }
  }
}
async function refreshCatalogs(force=false){
  if(catalogRefreshing){if(force)catalogRefreshAgain=true;return;}
  if(!force&&catalogInitialized&&!catalogCache.some(c=>['loading','queued'].includes(c.status)))return;
  catalogRefreshing=true;
  try{
    const r=await fetch('/api/catalogs',{headers:!force&&catalogEtag?{'If-None-Match':catalogEtag}:{}});if(r.status===304)return;if(!r.ok)return;
    catalogEtag=r.headers.get('ETag')||'';
    const data=await r.json(),snap=JSON.stringify(data);
    for(const c of data){const known=new Set(c.entries.map(e=>e.id));for(const id of catalogSet(c.id))if(!known.has(id))catalogSet(c.id).delete(id);}
    catalogCache=data;catalogInitialized=true;
    if(force||snap!==catalogSnapshot){catalogSnapshot=snap;renderCatalogs();repaint();}
  }catch(e){}finally{catalogRefreshing=false;if(catalogRefreshAgain){catalogRefreshAgain=false;void refreshCatalogs(true);}}
}
async function parseCatalog(url=null,limit=0){
  url=url||urlEl.value.trim();if(!url)return;if(window.rejectWorkspaceURL?.(url))return;
  const button=document.querySelector('#scanProfile');button.disabled=true;
  try{
    const r=await fetch('/api/catalogs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,limit})});
    const d=await r.json();if(!r.ok)throw new Error(d.detail||'主页识别失败');
    // Accept legacy single responses during a rolling deployment.
    const result=d.catalogs?d:{catalogs:[d]};
    if(!result.catalogs.length)throw Error('识别失败：'+(d.rejected||[]).map(x=>x.reason).join('；'));
    acceptSubmission(result);
    const rejected=d.rejected||[];
    hintEl.textContent='已提交 '+result.catalogs.length+' 个主页，逐组识别，完成后勾选下载。'+(rejected.length?' '+rejected.map(x=>x.reason).join('；'):'');hintEl.style.color=rejected.length?'var(--warn)':'';
    updateCount();await refreshCatalogs(true);
  }catch(e){hintEl.textContent=e.message;hintEl.style.color='var(--err)';}
  finally{button.disabled=false;}
}
async function catalogMore(id){
  try{
    const r=await fetch('/api/catalogs/'+id+'/continue',{method:'POST'}),d=await r.json();
    if(!r.ok)throw Error(d.detail||'继续读取失败');
    await refreshCatalogs(true);
  }catch(e){hintEl.textContent=e.message;hintEl.style.color='var(--err)';}
}
async function catalogRemove(id){
  if(!await ask({title:'移出这个主页？',body:'只移除素材预览记录，已下载文件和正在运行的下载任务保留。',ok:'移出列表'}))return;
  const r=await fetch('/api/catalogs/'+id,{method:'DELETE'});
  if(r.ok){catalogChosen.delete(id);catalogVisible.delete(id);workspaceCatalogs.delete(id);await refreshCatalogs(true);repaint();}
}
async function catalogDownload(id){
  if(catalogBusy.has(id))return;
  const ids=[...catalogSet(id)];if(!ids.length)return;
  const existing=catalogFiles(id),inProgress=new Set(catalogJobs(id).filter(j=>['running','queued'].includes(j.status)).flatMap(j=>j.selected_entries||[])),needed=ids.filter(i=>!existing.has(i)&&!inProgress.has(i));
  catalogBusy.add(id);renderCatalogs();
  try{
    for(const j of catalogJobs(id)){
      const files=j.files.filter(f=>ids.includes(f.entry_id));
      if(files.length){files.forEach(f=>selected.add(key(j.id,f.name)));if(!MOBILE)void downloadSelected(j.id);}
    }
    if(needed.length){
      const r=await fetch('/api/catalogs/'+id+'/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:needed})});
      const d=await r.json();if(!r.ok)throw new Error(d.detail||'提交失败');
      acceptSubmission(d,'profile');const job=d.created[0];catalogAutoSave.add(job.id);
      hintEl.textContent=MOBILE?'已加入 '+needed.length+' 个素材，主机准备后点击“保存选中到相册”。':'已加入 '+needed.length+' 个视频；主机准备完成后将在当前页面发起浏览器保存。刷新后请到下载记录继续保存。';hintEl.style.color='';
      await refresh(true);
    }else if(MOBILE)saveCatalogAlbum(id);
  }catch(e){hintEl.textContent=e.message;hintEl.style.color='var(--err)';}
  finally{catalogBusy.delete(id);renderCatalogs();}
}
function catalogShowMore(id){catalogVisible.set(id,(catalogVisible.get(id)||60)+60);renderCatalogs();}
document.querySelector('#scanProfile').onclick=()=>parseCatalog();
setInterval(()=>{if(!document.querySelector('#main').hidden)void refreshCatalogs();},2000);
