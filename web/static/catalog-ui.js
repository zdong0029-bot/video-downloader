let catalogCache=[],catalogSnapshot='',catalogEtag='',catalogRefreshing=false,catalogInitialized=false,catalogRefreshAgain=false;
const catalogVisible=new Map();
const catalogChosen=new Map(),catalogBusy=new Set();
const catalogSelectionKey='video-downloader-catalog-selections-v1';
try{
  const saved=JSON.parse(localStorage.getItem(catalogSelectionKey)||'{}');
  for(const [id,values] of Object.entries(saved))if(Array.isArray(values))catalogChosen.set(id,new Set(values.filter(v=>typeof v==='string')));
}catch(e){}
function rememberCatalogSelection(){try{localStorage.setItem(catalogSelectionKey,JSON.stringify(Object.fromEntries([...catalogChosen].map(([id,values])=>[id,[...values]]))));}catch(e){}}
function catalogSet(id){if(!catalogChosen.has(id))catalogChosen.set(id,new Set());return catalogChosen.get(id);}
function catalogToggle(id,item,on){
  const set=catalogSet(id);on?set.add(item):set.delete(item);rememberCatalogSelection();renderCatalogs();
}
function catalogAll(id,on){
  const c=catalogCache.find(c=>c.id===id);if(!c)return;
  const set=catalogSet(id);set.clear();if(on){c.entries.forEach(e=>set.add(e.id));expandedCards.add('catalog-'+id);rememberExpanded();}
  rememberCatalogSelection();
  renderCatalogs();
}
function catalogFold(id){
  const k='catalog-'+id;expandedCards.has(k)?expandedCards.delete(k):expandedCards.add(k);rememberExpanded();renderCatalogs();
}
function durationLabel(s){s=Math.round(Number(s)||0);return s?Math.floor(s/60)+':'+String(s%60).padStart(2,'0'):'';}
function renderCatalogs(){
  document.querySelector('#catalogs').innerHTML=catalogCache.map(c=>{
    const set=catalogSet(c.id),open=expandedCards.has('catalog-'+c.id),pending=c.status==='loading';
    const working=catalogBusy.has(c.id),all=c.entries.length&&set.size===c.entries.length;
    return '<section class="job catalog" data-catalog="'+c.id+'"><div class="job-top"><div><div class="job-title">'+esc(c.title)+'</div><div class="job-url">'+esc(c.url)+'</div></div><span class="tag '+(c.status==='error'?'err':'')+'">'+(pending?'识别中':c.status==='error'?'识别失败':'待选择')+'</span></div><p class="msg">'+esc(c.message)+'</p>'+
      (c.entries.length?'<div class="toolbar"><label><input type="checkbox" '+(all?'checked ':'')+(working?'disabled ':'')+'onchange="catalogAll(\''+c.id+'\',this.checked)">全选视频</label><span class="count">已选 '+set.size+' / '+c.entries.length+'</span><span class="spacer"></span><button class="ghost" aria-expanded="'+open+'" onclick="catalogFold(\''+c.id+'\')">'+(open?'收起':'展开视频')+'</button><button class="small" '+(!set.size||working?'disabled':'')+' onclick="catalogDownload(\''+c.id+'\')">'+(working?'提交中…':'下载选中')+'</button></div>':'')+
      (!pending&&c.limited?'<p class="note">部分列表：平台尚未确认分页结束。<button class="ghost" onclick="catalogMore(\''+c.id+'\')">继续读取全部</button></p>':!pending&&c.complete?'<p class="note">全部公开作品 · '+c.entries.length+' 个'+(c.pages?' · '+c.pages+' 页':'')+'</p>':'')+
      (open?'<div class="files">'+c.entries.slice(0,catalogVisible.get(c.id)||60).map(e=>'<div class="file '+(set.has(e.id)?'sel':'')+'"><label><input type="checkbox" '+(set.has(e.id)?'checked ':'')+(working?'disabled ':'')+' onchange="catalogToggle(\''+c.id+'\',\''+e.id+'\',this.checked)"></label>'+(e.thumbnail?'<img class="thumb" alt="" loading="lazy" referrerpolicy="no-referrer" src="'+esc(e.thumbnail)+'" onerror="this.style.visibility=\'hidden\'">':'<span class="thumb" style="display:grid;place-items:center">▶</span>')+'<div class="fname">'+esc(e.title)+'<br><span class="fsize">'+durationLabel(e.duration)+'</span></div><a target="_blank" rel="noopener noreferrer" href="'+esc(e.url)+'">查看</a></div>').join('')+'</div>':c.entries.length?'<p class="note">列表已收起，勾选状态保留。点击全选会自动展开。</p>':'')+
      (open&&c.entries.length>(catalogVisible.get(c.id)||60)?'<button class="ghost" onclick="catalogShowMore(\''+c.id+'\')">显示更多视频（已列出 '+(catalogVisible.get(c.id)||60)+' / '+c.entries.length+'）</button>':'')+
      '<div class="acts"><button class="ghost" onclick="catalogRemove(\''+c.id+'\')">移出列表</button></div></section>';
  }).join('');
}
async function refreshCatalogs(force=false){
  if(catalogRefreshing){if(force)catalogRefreshAgain=true;return;}
  if(!force&&catalogInitialized&&!catalogCache.some(c=>c.status==='loading'))return;
  catalogRefreshing=true;
  try{
    const r=await fetch('/api/catalogs',{headers:!force&&catalogEtag?{'If-None-Match':catalogEtag}:{}});if(r.status===304)return;if(!r.ok)return;
    catalogEtag=r.headers.get('ETag')||'';
    const data=await r.json(),snap=JSON.stringify(data);
    for(const c of data){const known=new Set(c.entries.map(e=>e.id));for(const id of catalogSet(c.id))if(!known.has(id))catalogSet(c.id).delete(id);}
    rememberCatalogSelection();
    catalogCache=data;catalogInitialized=true;
    if(force||snap!==catalogSnapshot){catalogSnapshot=snap;renderCatalogs();}
  }catch(e){}finally{catalogRefreshing=false;if(catalogRefreshAgain){catalogRefreshAgain=false;void refreshCatalogs(true);}}
}
async function parseCatalog(url=null,limit=0){
  url=url||urlEl.value.trim();if(!url)return;
  const button=document.querySelector('#scanProfile');button.disabled=true;
  try{
    const r=await fetch('/api/catalogs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,limit})});
    const d=await r.json();if(!r.ok)throw new Error(d.detail||'主页识别失败');
    hintEl.textContent='正在读取主页视频列表，识别完成后勾选下载。';hintEl.style.color='';
    updateCount();
    await refreshCatalogs(true);
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
  const r=await fetch('/api/catalogs/'+id,{method:'DELETE'});
  if(r.ok){catalogChosen.delete(id);catalogVisible.delete(id);rememberCatalogSelection();await refreshCatalogs(true);}
}
async function catalogDownload(id){
  if(catalogBusy.has(id))return;
  const ids=[...catalogSet(id)];if(!ids.length)return;
  catalogBusy.add(id);renderCatalogs();
  try{
    const r=await fetch('/api/catalogs/'+id+'/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids})});
    const d=await r.json();if(!r.ok)throw new Error(d.detail||'提交失败');
    const job=d.created[0];expandedCards.add(job.id);rememberExpanded();
    hintEl.textContent='已加入 '+ids.length+' 个视频，显示在独立的主页下载框中。';hintEl.style.color='';
    await refresh(true);
  }catch(e){hintEl.textContent=e.message;hintEl.style.color='var(--err)';}
  finally{catalogBusy.delete(id);renderCatalogs();}
}
function catalogShowMore(id){catalogVisible.set(id,(catalogVisible.get(id)||60)+100);renderCatalogs();}
document.querySelector('#scanProfile').onclick=()=>parseCatalog();
setInterval(()=>{if(!document.querySelector('#main').hidden)void refreshCatalogs();},2000);
