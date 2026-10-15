// Video results as cards: cover, copyable title, and separately selectable video / cover / audio.
const collapsedCards=new Set();
const MEDIA_TITLES=[];
function partRole(name){
  if(/\.cover\.(jpe?g|png|webp)$/i.test(name))return 'cover';
  if(/\.audio\.(m4a|mp3|opus|aac|wav)$/i.test(name))return 'audio';
  if(/\.(mp4|webm|mkv|mov|m4v|avi)$/i.test(name))return 'video';
  return 'other';
}
function partStem(name){const m=name.match(/^(.*)\.(cover|audio)\.[^.]+$/i);return m?m[1]:name.replace(/\.[^.]+$/,'');}
function mediaCards(job){
  const cards=new Map(),rest=[];
  for(const f of job.files||[]){
    const role=partRole(f.name);
    if(role==='other'){rest.push(f);continue;}
    const id=originId(job,f)+'\u0000'+partStem(f.name);
    if(!cards.has(id))cards.set(id,{id,jobId:originId(job,f),stem:partStem(f.name),parts:{},source_url:f.source_url});
    cards.get(id).parts[role]=f;
  }
  const out=[];
  for(const c of cards.values())c.parts.video?out.push(c):rest.push(...Object.values(c.parts));
  return {cards:out,rest};
}
function cardTitle(job,c,count){
  if(job.is_group){const m=(job.members||[]).find(m=>m.id===c.jobId);if(m&&m.title&&m.title!==m.url)return m.title;}
  else if(count===1&&job.kind!=='tool'&&job.title&&job.title!==job.url&&!/^历史下载/.test(job.title))return job.title;
  return c.stem.replace(/\s*\[[^\]]+\]$/,'').replace(/^[^-]{1,40} - /,'');
}
function copyText(text){
  if(navigator.clipboard&&window.isSecureContext)return navigator.clipboard.writeText(text);
  const t=document.createElement('textarea');t.value=text;t.style.position='fixed';t.style.opacity='0';
  document.body.appendChild(t);t.select();try{document.execCommand('copy');}finally{t.remove();}
  return Promise.resolve();
}
function copyTitle(i,btn){
  copyText(MEDIA_TITLES[i]||'').then(()=>{if(btn){const old=btn.textContent;btn.textContent='已复制 ✓';setTimeout(()=>btn.textContent=old,1200);}});
}
function copyAllTitles(jobId,btn){
  const job=viewJob(jobId);if(!job)return;
  const {cards}=mediaCards(job);
  copyText(cards.map(c=>cardTitle(job,c,cards.length)).join('\n')).then(()=>{if(btn){const old=btn.textContent;btn.textContent='已复制 '+cards.length+' 条 ✓';setTimeout(()=>btn.textContent=old,1400);}});
}
function roleFiles(job,role){return (job.files||[]).filter(f=>partRole(f.name)===role&&mediaCards(job).cards.some(c=>c.parts[role]===f));}
function toggleRole(jobId,role,on){
  if(jobBusy(jobId))return;
  const job=viewJob(jobId);if(!job)return;
  if(on)collapsedCards.delete(jobId);
  for(const c of mediaCards(job).cards){const f=c.parts[role];if(!f)continue;const k=key(c.jobId,f.name);on?selected.add(k):selected.delete(k);}
  repaint();
}
function roleBar(job){
  const {cards}=mediaCards(job);
  if(!cards.length)return '';
  const box=(role,label,icon)=>{
    const have=cards.filter(c=>c.parts[role]);
    if(!have.length)return '';
    const on=have.filter(c=>selected.has(key(c.jobId,c.parts[role].name))).length;
    return '<label class="role-all'+(on===have.length?' on':'')+'"><input type="checkbox" '+(jobBusy(job.id)?'disabled ':'')+(on===have.length?'checked ':'')+'onchange="toggleRole(\''+job.id+'\',\''+role+'\',this.checked)"> '+icon+' 全选'+label+' <span class="fsize">'+on+'/'+have.length+'</span></label>';
  };
  return '<div class="role-bar">'+box('video','视频','🎬')+box('cover','封面','🖼')+box('audio','音频','🎵')
    +'<button class="ghost small-btn" onclick="copyAllTitles(\''+job.id+'\',this)">复制全部标题</button></div>';
}
function mediaCardHTML(job,c,count){
  const t=cardTitle(job,c,count);MEDIA_TITLES.push(t);const ti=MEDIA_TITLES.length-1;
  const v=c.parts.video,cov=c.parts.cover,busyAttr=jobBusy(job.id)?'disabled ':'';
  const enc=n=>encodeURIComponent(n),arg=n=>esc(JSON.stringify(n));
  const img=cov?'/api/files/'+c.jobId+'/'+enc(cov.name)+'?inline=1':'/api/thumb/'+c.jobId+'/'+enc(v.name);
  const part=(role,label,icon)=>{
    const f=c.parts[role];if(!f)return '<div class="mpart none">'+icon+' 无'+label+'</div>';
    const on=selected.has(key(c.jobId,f.name));
    const save=MOBILE&&role!=='audio'?'<button class="ghost tiny" onclick="previewFile(\''+c.jobId+'\','+arg(f.name)+')">存相册</button>'
      :'<button class="ghost tiny" '+busyAttr+'onclick="saveFile(\''+c.jobId+'\','+arg(f.name)+')">保存</button>';
    return '<div class="mpart'+(on?' on':'')+'"><label><input type="checkbox" '+busyAttr+(on?'checked ':'')+'onchange="toggleOne(\''+c.jobId+'\','+arg(f.name)+',this.checked)"> '+icon+' '+label+' <span class="fsize">'+fmtSize(f.size)+'</span></label>'+save+'</div>';
  };
  const src=c.source_url?' title="来源：'+esc(c.source_url)+'"':'';
  return '<div class="mcard">'
    +'<button class="mcover" title="预览视频" onclick="previewFile(\''+c.jobId+'\','+arg(v.name)+')"><img alt="封面" loading="lazy" src="'+img+'" onerror="this.style.visibility=\'hidden\'"><span class="mplay">▶</span></button>'
    +'<div class="mtitle-row"'+src+'><span class="mtitle" title="点击选中，可直接复制">'+esc(t)+'</span><button class="ghost tiny" onclick="copyTitle('+ti+',this)">复制</button></div>'
    +part('video','视频','🎬')+part('cover','封面','🖼')+part('audio','音频','🎵')
    +(!MOBILE&&LOCAL?'<button class="ghost tiny mlocate" title="定位视频" onclick="revealFile(\''+c.jobId+'\','+arg(v.name)+')">📁</button>':'')
    +'</div>';
}

// ---- Batch tools on the selected files ----
const TOOL_INFO={
  trim:{title:'批量剪辑',body:'每个选中的视频去掉相同的片头 / 片尾，画质按近无损重新编码，分辨率和帧率不变。'},
  convert:{title:'批量格式转换',body:'MP4 / MOV / MKV 能直接换封装的不重新编码（无损）；WebM、GIF 需要重新编码。'},
  md5:{title:'批量修改 MD5',body:'只改文件元数据，画面和声音完全不变，每个文件得到新的 MD5。'},
  audio:{title:'视频转音频',body:'提取每个视频的音轨。M4A 能直接拷贝原音轨（无损），MP3 用最高质量 VBR。'}
};
function selectedItems(){
  return [...selected].map(k=>{const [job_id,name]=k.split('\u0000');return {job_id,name};});
}
function updateToolBar(){
  const bar=document.querySelector('#toolBar');if(!bar)return;
  const items=selectedItems(),vids=items.filter(i=>partRole(i.name)==='video').length;
  bar.hidden=VIEW==='history'&&!items.length||CATEGORY==='cloud'&&!items.length;
  const n=document.querySelector('#toolCount');if(n)n.textContent=items.length?'已选 '+items.length+' 个文件（视频 '+vids+' 个）':'先在下方勾选文件';
  bar.querySelectorAll('button[data-tool]').forEach(b=>{b.disabled=!(b.dataset.tool==='md5'?items.length:vids);});
}
let toolOp='';
function openTool(op){
  toolOp=op;const info=TOOL_INFO[op];
  document.querySelector('#toolTitle').textContent=info.title;
  document.querySelector('#toolBody').textContent=info.body;
  document.querySelectorAll('#toolDialog [data-for]').forEach(el=>el.hidden=el.dataset.for!==op);
  document.querySelector('#toolHint').textContent='';
  document.querySelector('#toolDialog').showModal();
}
async function submitTool(){
  const params={},hint=document.querySelector('#toolHint');
  if(toolOp==='trim'){params.head=document.querySelector('#toolHead').value||0;params.tail=document.querySelector('#toolTail').value||0;}
  if(toolOp==='convert')params.format=document.querySelector('#toolFormat').value;
  if(toolOp==='audio')params.format=document.querySelector('#toolAudio').value;
  let items=selectedItems();
  if(toolOp!=='md5')items=items.filter(i=>partRole(i.name)==='video');
  if(!items.length){hint.textContent='没有选中可处理的视频';return;}
  const btn=document.querySelector('#toolGo');btn.disabled=true;
  try{
    const r=await fetch('/api/tools',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({op:toolOp,items,params})});
    const data=await r.json().catch(()=>({}));
    if(!r.ok){hint.textContent=data.detail||'提交失败';return;}
    acceptSubmission(data,'video');
    document.querySelector('#toolDialog').close();
    hintEl.textContent=TOOL_INFO[toolOp].title+'已开始，处理好的文件会出现在列表最上面的新任务里。';hintEl.style.color='';
    refresh(true);
  }finally{btn.disabled=false;}
}
