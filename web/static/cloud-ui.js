(() => {
  const names = {baidu:'百度网盘', ali:'阿里云盘', quark:'夸克网盘'};
  const msg = document.querySelector('#cloudMessage');
  let opening = false, refreshing = false;
  function node(tag, value, cls) {const e=document.createElement(tag);e.textContent=value;if(cls)e.className=cls;return e;}
  async function api(url, body) {
    const r=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
    const d=await r.json();if(!r.ok)throw Error(d.detail||'请求失败');return d;
  }
  async function status() {
    if(!ME)return;
    try {
      const d=await api('/api/cloud/status'), box=document.querySelector('#cloudStatus');box.replaceChildren();
      for(const p of d.providers) {
        const label=p.auth_ok===false?'需重新扫码':p.connected?(p.auth_ok===true?'已连接':'已保存 · 待检测'):'未连接';
        const pill=node('span',p.name+' · '+label,'cloud-pill');
        if(p.auth_ok===false)pill.classList.add('cloud-error');else if(p.connected)pill.classList.add('cloud-ok');
        box.append(pill);
      }
    } catch(e) {document.querySelector('#cloudStatus').textContent='网盘状态暂时无法读取，请点击刷新';}
  }
  async function action(button, run) {
    button.disabled=true;msg.textContent='正在处理…';
    try {await run();}catch(e){msg.textContent=e.message;}finally{button.disabled=false;await status();}
  }
  async function load() {
    if(refreshing)return;refreshing=true;
    try {
      const d=await api('/api/admin/cloud-accounts'), cards=document.querySelector('#cloudCards');cards.replaceChildren();
      for(const [key,name] of Object.entries(names)) {
        const card=document.createElement('article');card.append(node('h4',name));
        card.append(node('p',key==='baidu'?'使用后台账号下载，任务串行处理；优先直下载；必须转存时只使用本次任务目录，完整下载到主机并校验后自动清理。':key==='quark'?'下载原文件；自己的分享优先直下载；其他分享需转存时，下载校验完成后清理临时目录。':'普通分享优先原文件直下载；平台要求转存时，校验完成后自动清理；过期快传需要换新。','muted'));
        const add=node('button','扫码连接 / 添加账号','primary');add.type='button';
        add.onclick=()=>action(add,async()=>{const r=await api('/api/admin/cloud-accounts/connect',{provider:key});opening=true;msg.textContent=r.message;});card.append(add);
        const list=d.accounts.filter(a=>a.provider===key);
        if(!list.length)card.append(node('p','后台账号未连接。管理员扫码一次后，朋友无需网盘登录。','muted'));
        for(const a of list) {
          const row=node('div','','row'),info=document.createElement('div');info.append(node('strong',a.label));
          const state=a.auth_ok===true?'账号验证通过':a.auth_ok===false?'授权失效，请重新扫码':'授权已保存，等待检测';
          info.append(node('p',a.created+' · '+state,'muted'));
          if(a.checked_at)info.append(node('p',(a.message||'最近检测')+' · '+new Date(a.checked_at*1000).toLocaleString(),'muted'));
          row.append(info);const controls=node('div','','cloud-controls');
          if(d.active[key]===a.id)controls.append(node('span','当前使用','badge'));
          else {const use=node('button','切换到此账号','ghost');use.type='button';use.onclick=()=>action(use,async()=>{await api('/api/admin/cloud-accounts/activate',{id:a.id});msg.textContent='已切换后台账号，新任务使用此账号。';await load();});controls.append(use);}
          const check=node('button','检测账号','ghost');check.type='button';check.onclick=()=>action(check,async()=>{const r=await api('/api/admin/cloud-accounts/check',{id:a.id});msg.textContent=r.message;await load();});controls.append(check);row.append(controls);card.append(row);
        }
        cards.append(card);
      }
      await transfers();
      if(opening && d.accounts.some(a=>a.checked_at && Date.now()/1000-a.checked_at<12)) {msg.textContent='账号已验证并保存。首次连接已自动启用，更换账号请选择「切换到此账号」。';opening=false;}
    } finally {refreshing=false;await status();}
  }
  async function transfers() {
    const d=await api('/api/admin/cloud-transfers'),box=document.querySelector('#cloudTransfers');box.replaceChildren();
    if(!d.items.length){box.append(node('p','当前没有待清理的临时转存。','muted'));return;}
    for(const item of d.items){
      const row=node('div','','row'),info=document.createElement('div');info.append(node('strong',item.provider+' · '+item.job));info.append(node('p',item.message,'muted'));row.append(info);
      if(item.status==='cleanup_pending'){
        const button=node('button','重试清理','ghost');button.type='button';button.onclick=()=>action(button,async()=>{const r=await api('/api/admin/cloud-transfers/retry',{id:item.job});msg.textContent=r.message;await transfers();});row.append(button);
      } else row.append(node('span','下载未全部完成，保留转存','badge'));
      box.append(row);
    }
  }
  window.loadCloudAccounts=()=>load().catch(e=>msg.textContent=e.message);
  window.openCloudAccounts=()=>{
    if(!ME){showGate();return;}
    document.querySelector('#adminPanel').hidden=false;loadUsers();window.loadCloudAccounts();
    document.querySelector('#cloudAccountSection').scrollIntoView({block:'start',behavior:'smooth'});
  };
  document.querySelector('#cloudRefresh').onclick=e=>action(e.target,async()=>{await load();msg.textContent='账号状态已刷新';});
  document.querySelector('#cloudStatusRefresh').onclick=status;
  window.refreshCloudStatus=status;
  status();setInterval(()=>{status();if(opening&&!document.querySelector('#adminPanel').hidden)window.loadCloudAccounts();},opening?3000:5000);
  if(location.hash==='#accounts')setTimeout(window.openCloudAccounts,600);
})();
