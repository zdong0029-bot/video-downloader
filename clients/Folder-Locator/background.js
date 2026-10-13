const SITE='https://video-downloader.tail49ff9c.ts.net';
function filenamePattern(name){
  const dot=name.lastIndexOf('.');
  const esc=s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  return '[/\\\\]'+esc(name.slice(0,dot))+'(?: \\([0-9]+\\))?'+esc(name.slice(dot))+'$';
}
function fromSite(item){
  return [item.url,item.finalUrl,item.referrer].some(value=>
    typeof value==='string' && (value.startsWith(SITE+'/') || value.startsWith('blob:'+SITE+'/')));
}
chrome.runtime.onMessage.addListener((msg,sender,reply)=>{
  if(sender.id!==chrome.runtime.id || !sender.tab || sender.frameId!==0 ||
     !sender.url?.startsWith(SITE+'/') || msg?.type!=='reveal-video') return;
  const name=msg.name;
  if(typeof name!=='string' || name.length>255 || /[\\/\x00-\x1f]/.test(name) ||
     !/\.(mp4|webm|mkv|mov|m4v)$/i.test(name)){
    reply({ok:false,message:'文件名无效。'});return;
  }
  (async()=>{
    const matches=await chrome.downloads.search({filenameRegex:filenamePattern(name),state:'complete',exists:true,orderBy:['-startTime'],limit:100});
    const item=matches.find(fromSite);
    if(!item){reply({ok:false,message:'未找到本浏览器保存的视频。请先点“保存”并等待完成；若已改名、移动或清除了下载记录，请按 Ctrl+J 查找。'});return;}
    await chrome.downloads.show(item.id);
    reply({ok:true,message:'已打开文件夹并选中最近下载的同名视频。'});
  })().catch(()=>reply({ok:false,message:'无法定位文件，请按 Ctrl+J 检查文件是否仍存在。'}));
  return true;
});
