/* One URL, adaptive layout, and explicit embedded-browser capabilities. */
(() => {
 const embedded=/MicroMessenger/i.test(navigator.userAgent),ios=/iPhone|iPad|iPod/i.test(navigator.userAgent);
 window.BrowserEnvironment={mobile:MOBILE,wechat:embedded,ios};
 document.documentElement.dataset.device=MOBILE?'mobile':'desktop';
 document.documentElement.dataset.browser=embedded?'wechat':'system';
 const selfURL=text=>{try{const u=new URL(text);return [location.hostname,new URL(PUBLIC_SITE).hostname,'127.0.0.1','localhost'].includes(u.hostname)&&['','/index.html','/cloud-accounts.html'].includes(u.pathname.replace(/\/$/,''));}catch(e){return false;}};
 window.rejectWorkspaceURL=text=>{
  const links=text.match(/https?:\/\/[^\s]+/g)||[];
  if(!links.some(selfURL))return false;
  hintEl.textContent='这是下载工作台的网址，直接打开即可。请在下载框粘贴视频、主页或网盘分享链接。';hintEl.style.color='var(--err)';return true;
 };
 async function copy(text,message){
  try{await navigator.clipboard.writeText(text);}catch(e){const t=document.createElement('textarea');t.value=text;t.style.position='fixed';t.style.opacity='0';document.body.append(t);t.select();const ok=document.execCommand('copy');t.remove();if(!ok){prompt('请复制后到系统浏览器打开：',text);return;}}
  hintEl.textContent=message;hintEl.style.color='';
 }
 window.copySourceLinks=()=>{
  const urls=[...new Set([...(urlEl.value.match(/https?:\/\/[^\s]+/g)||[]),...cache.filter(j=>workspaceJobs.has(j.id)).map(j=>j.url),...catalogCache.filter(c=>workspaceCatalogs.has(c.id)).map(c=>c.url)])].filter(u=>!selfURL(u));
  if(!urls.length){hintEl.textContent='请先粘贴视频或网盘链接。';return;}return copy(urls.join('\n'),'已复制素材链接。在 Safari / 系统浏览器打开本站后，粘贴即可继续；两个浏览器的记录和缓存分别保存。');
 };
 window.showBrowserGuide=()=>{
  const panel=document.querySelector('#embeddedBrowserHelp');if(panel){panel.hidden=false;panel.scrollIntoView({behavior:'smooth',block:'nearest'});}
  hintEl.textContent=ios?'点击微信右上角“…”→ 在 Safari 打开，再保存到相册。':'点击微信右上角“…”→ 在浏览器打开，再下载或存相册。';
 };
 if(embedded){
  const panel=document.createElement('section');panel.id='embeddedBrowserHelp';panel.className='browser-help';panel.setAttribute('role','note');
  const title=document.createElement('strong');title.textContent='当前在微信内打开';
  const note=document.createElement('p');note.textContent='可粘贴链接、解析并准备文件。微信内的文件下载和系统相册分享能力受限制，保存请点击右上角“…”→ '+(ios?'在 Safari 打开':'在浏览器打开')+'。电脑和手机都使用同一个网址。';
  const actions=document.createElement('div');actions.className='browser-help-actions';
  const site=document.createElement('button');site.className='ghost';site.type='button';site.textContent='复制网站网址';site.onclick=()=>copy(PUBLIC_SITE,'已复制网址，请在 Safari / 系统浏览器地址栏打开。不要粘贴到下载框。');
  const sources=document.createElement('button');sources.className='ghost';sources.type='button';sources.textContent='复制本轮素材链接';sources.onclick=window.copySourceLinks;
  actions.append(site,sources);panel.append(title,note,actions);document.querySelector('#downloadForm').prepend(panel);
 }
})();
