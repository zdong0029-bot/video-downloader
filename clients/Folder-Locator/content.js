document.documentElement.dataset.folderLocator='1';
document.addEventListener('click',async event=>{
  const button=event.target.closest?.('[data-folder-file]');
  if(!button || !event.isTrusted) return;
  event.preventDefault();event.stopImmediatePropagation();
  if(button.disabled) return;
  button.disabled=true;button.textContent='定位中…';
  let message;
  try{
    const result=await chrome.runtime.sendMessage({type:'reveal-video',name:button.dataset.folderFile});
    message=result?.message || '扩展未响应，请刷新网页重试。';
  }catch{message='扩展已更新或失效，请刷新网页重试。';}
  finally{button.disabled=false;button.textContent='📁 定位';}
  button.title=message;
  const hint=document.querySelector('#hint');if(hint) hint.textContent=message;
},true);
