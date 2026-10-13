/* Optional mainland relay: same original bytes from a faster server via short-lived signed links; host path stays the fallback. */
(() => {
 const links=new Map(),local=(id,name)=>'/api/files/'+encodeURIComponent(id)+'/'+encodeURIComponent(name);
 const state=(id,name)=>cache.find(j=>j.id===id)?.files?.find(f=>f.name===name)?.relay||null;
 const fresh=d=>d&&d.expires*1000-Date.now()>120000;
 async function link(id,name,renew=false){
  const k=id+'\n'+name,hit=links.get(k);if(!renew&&fresh(hit))return hit;
  // Disabled on the host, or this browser sits on the host/LAN where the direct path is already fast.
  const s=state(id,name);if(!s||LOCAL||location.protocol!=='https:'||document.querySelector('#transferMode')?.value==='server')return null;
  try{
   const r=await fetch(local(id,name)+'/link',{cache:'no-store'});if(!r.ok)return null;
   const d=await r.json();if(d.state!=='ready'||!d.url){links.delete(k);return null;}links.set(k,d);return d;
  }catch(e){return null;}
 }
 // Resolver for OriginalFileTransfer.save: renew=true re-requests a signed link after expiry (403).
 const source=(id,name)=>async renew=>(await link(id,name,renew))?.url||local(id,name);
 window.RelayLinks={state,link,source,url:(id,name)=>source(id,name)(false),local,
  cached:(id,name)=>{const d=links.get(id+'\n'+name);return fresh(d)?d.url:'';}};
 window.relayBadge=f=>{
  const s=f.relay;if(!s||s.state==='none')return '';
  const text=s.state==='ready'?'云端加速可用':s.state==='uploading'?'云端同步 '+(s.progress||0)+'%':s.state==='pending'?'云端同步排队中':s.state==='failed'?'云端同步失败，使用主机传输':'';
  return text?'<br><span class="fsize relay-'+s.state+'" title="朋友下载时优先从国内中转服务器接收同一原文件">'+text+'</span>':'';
 };
})();
