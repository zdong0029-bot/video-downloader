(() => {
 const box=document.querySelector('#channelStatus');let waiting=false;
 async function api(path,post=false){const r=await fetch('/api/admin/video-accounts'+path,{method:post?'POST':'GET'});const d=await r.json();if(!r.ok)throw Error(d.detail||'连接失败');return d;}
 async function refresh(){if(!ME)return;try{const d=await api('');box.textContent=(d.qr_message||d.message)+(d.checked_at?' · '+new Date(d.checked_at*1000).toLocaleString():'');const qr=document.querySelector('#channelQR');qr.hidden=!d.qr_available;if(d.qr_available)qr.src='/api/admin/video-accounts/qr?t='+Math.floor(Date.now()/10000);if(d.connected)waiting=false;}catch(e){box.textContent=e.message;}}
 async function act(button,path){button.disabled=true;try{const d=await api(path,true);box.textContent=d.message||'授权检测通过';if(path==='/connect'){waiting=true;setTimeout(refresh,1800);}}catch(e){box.textContent=e.message;}finally{button.disabled=false;}}
 window.connectChannels=b=>act(b,'/connect');window.checkChannels=b=>act(b,'/check');

 async function refreshPlatforms(){
  if(!ME)return;const box=document.querySelector('#platformAccountStatus');
  try{const r=await fetch('/api/admin/platform-accounts');const d=await r.json();if(!r.ok)throw Error(d.detail||'授权状态暂不可读取');
   box.replaceChildren(...d.accounts.map(a=>{
    const row=document.createElement('div');row.className='platform-status-row';
    const info=document.createElement('div');info.className='platform-info';
    const name=document.createElement('strong');name.textContent=({'bilibili':'B站','kuaishou':'快手','xiaohongshu':'小红书','tiktok':'TikTok','douyin':'抖音'}[a.provider]||a.provider);
    const state=document.createElement('p');state.textContent=a.message;
    info.append(name,state);const button=document.createElement('button');button.className='ghost';button.textContent='连接 / 更换';button.type='button';button.onclick=()=>window.connectVideoPlatform(button,a.provider);
    row.append(info,button);return row;
   }));
  }catch(e){box.textContent=e.message;}
 }
 window.refreshVideoAccounts=()=>{void refresh();void refreshPlatforms();};

 window.connectVideoPlatform=async(b,provider)=>{b.disabled=true;const box=document.querySelector('#platformAccountStatus');try{const r=await fetch('/api/admin/platform-accounts/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider})});const d=await r.json();if(!r.ok)throw Error(d.detail||'连接失败');box.textContent=d.message;}catch(e){box.textContent=e.message;}finally{b.disabled=false;}};
 setInterval(()=>{if(ME&&!document.querySelector('#adminPanel').hidden){refresh();refreshPlatforms();}},5000);
})();
