/* Pause background churn during file preparation and adapt to the visible viewport. */
(() => {
 let lastPoll=0,jobInFlight=false,catalogInFlight=false,pendingJob=false,pendingCatalog=false;
 const originalRefresh=refresh,originalCatalogRefresh=refreshCatalogs;
 refresh=async function(force=false){
  if(!force&&(document.hidden||(MOBILE&&(document.querySelector('#previewDialog').open||window.MEDIA_PREPARING))))return;
  if(jobInFlight){pendingJob=pendingJob||!!force;return;}jobInFlight=true;
  try{return await originalRefresh(force);}finally{jobInFlight=false;if(pendingJob){pendingJob=false;void refresh(true);}}
 };
 refreshCatalogs=async function(force=false){
  if(!force&&(document.hidden||(MOBILE&&(document.querySelector('#previewDialog').open||window.MEDIA_PREPARING))))return;
  if(catalogInFlight){pendingCatalog=pendingCatalog||!!force;return;}catalogInFlight=true;
  try{return await originalCatalogRefresh(force);}finally{catalogInFlight=false;if(pendingCatalog){pendingCatalog=false;void refreshCatalogs(true);}}
 };
 window.scheduleJobRefresh=()=>{
  if(document.hidden||(MOBILE&&document.querySelector('#previewDialog').open))return;
  const active=cache.some(j=>['queued','running'].includes(j.status));
  const interval=active?(MOBILE?4000:1500):(MOBILE?20000:10000);
  if(Date.now()-lastPoll<interval)return;lastPoll=Date.now();void refresh();
 };
 const nativeClose=closePreview;
 closePreview=()=>{nativeClose();window.scheduleJobRefresh();};
 document.addEventListener('visibilitychange',()=>{if(!document.hidden){lastPoll=0;window.scheduleJobRefresh();}});
 if(MOBILE){
  const height=()=>document.documentElement.style.setProperty('--mobile-visual-height',(window.visualViewport?.height||innerHeight)+'px');height();window.visualViewport?.addEventListener('resize',height);
  document.addEventListener('gesturestart',e=>e.preventDefault(),{passive:false});
  document.addEventListener('gesturechange',e=>e.preventDefault(),{passive:false});
  document.addEventListener('touchstart',e=>{if(e.touches.length>1)e.preventDefault();},{passive:false});
 }
})();
