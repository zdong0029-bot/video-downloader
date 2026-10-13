/* Save original bytes to disk with bounded memory. No encoding or recompression. */
(() => {
 const CHUNK=1048576,LANES=4;
 const integrity=message=>Object.assign(Error(message),{integrity:true});
 const stopped=()=>new DOMException('Stopped','AbortError');
 async function pick(name){
  try{return await window.showSaveFilePicker({suggestedName:name});}
  catch(e){if(e.name==='AbortError')throw e;if(['SecurityError','NotAllowedError','NotSupportedError'].includes(e.name))return null;throw e;}
 }
 // source: URL string, or async (renew)=>URL for short-lived signed relay links (re-requested on 403).
 async function save(source,handle,expected,signal,onProgress=()=>{}){
  const resolve=typeof source==='function'?source:async()=>source;let url=await resolve(false),renewing=null;
  const cross=()=>new URL(url,location.href).origin!==location.origin,STEP=cross()?4*CHUNK:CHUNK;
  async function renew(){if(typeof source!=='function'||!cross())return false;renewing=renewing||resolve(true).then(u=>{url=u;}).finally(()=>{renewing=null;});await renewing;return true;}
  const controller=new AbortController();const abort=()=>controller.abort();
  signal.addEventListener('abort',abort,{once:true});if(signal.aborted)abort();
  let writer=null,writeChain=Promise.resolve();
  async function request(headers){
   const local=new AbortController(),cancel=()=>local.abort();controller.signal.addEventListener('abort',cancel,{once:true});
   if(controller.signal.aborted)cancel();let timer=setTimeout(cancel,30000);
   try{const response=await fetch(url,{signal:local.signal,headers,cache:'no-store',credentials:cross()?'omit':'same-origin'});clearTimeout(timer);
    return {response,async read(reader){timer=setTimeout(cancel,90000);try{return await reader.read();}finally{clearTimeout(timer);}},close(){clearTimeout(timer);controller.signal.removeEventListener('abort',cancel);local.abort();}};
   }catch(e){clearTimeout(timer);controller.signal.removeEventListener('abort',cancel);throw e;}
  }
  function enqueue(position,data){
   const operation=writeChain.then(()=>{if(controller.signal.aborted)throw stopped();return writer.write({type:'write',position,data});});
   writeChain=operation.catch(()=>{});return operation;
  }
  try{
   if(controller.signal.aborted)throw stopped();
   writer=await handle.createWritable();
   let probe=await request({Range:'bytes=0-0'});
   if(probe.response.status===403&&await renew()){probe.close();probe=await request({Range:'bytes=0-0'});}
   const r=probe.response;
   const range=r.headers.get('content-range'),etag=r.headers.get('etag');
   const size=r.status===206?Number(/^bytes 0-0\/(\d+)$/.exec(range)?.[1]):Number(r.headers.get('content-length'));
   if(!r.ok||![200,206].includes(r.status)){probe.close();throw Error('原文件读取失败（HTTP '+r.status+'）');}
   if(!Number.isSafeInteger(size)||size<=0||(expected&&size!==expected)){probe.close();throw integrity('原文件大小发生变化，请刷新下载记录后重试。');}
   if(r.status===200||!etag||etag.startsWith('W/')){
    // An origin without strong Range support still streams to disk, never a GB Blob.
    let whole=probe;
    if(r.status===206){probe.close();whole=await request({});}
    if(whole.response.status!==200||Number(whole.response.headers.get('content-length'))!==size){whole.close();throw integrity('原文件响应不完整，请重试。');}
    let count=0;const reader=whole.response.body.getReader();
    try{while(true){const part=await whole.read(reader);if(part.done)break;if(count+part.value.byteLength>size)throw integrity('原文件响应大小异常');await enqueue(count,part.value);count+=part.value.byteLength;onProgress(count,size,'原文件流式保存');}}
    finally{try{await reader.cancel();}catch(e){}reader.releaseLock();whole.close();}
    if(count!==size)throw integrity('原文件传输不完整，请重试。');
   }else{
    const probeReader=r.body.getReader();let probeBytes=0;
    try{while(true){const sample=await probe.read(probeReader);if(sample.done)break;probeBytes+=sample.value.byteLength;if(probeBytes>1)throw integrity('原文件探测响应大小异常');}if(probeBytes!==1)throw integrity('原文件探测响应不完整');}
    finally{try{await probeReader.cancel();}catch(e){}probeReader.releaseLock();probe.close();}
    let next=0,written=0;const inflight=new Map();const count=Math.ceil(size/STEP);
    const report=()=>onProgress(written+[...inflight.values()].reduce((a,b)=>a+b,0),size,'四路分段保存原文件');
    async function worker(){while(next<count){
     if(controller.signal.aborted)throw stopped();
     const index=next++,start=index*STEP,end=Math.min(size,start+STEP)-1,wanted=end-start+1;
     for(let attempt=0;attempt<3;attempt++){
      let part=null,reader=null;
      try{
       part=await request({Range:'bytes='+start+'-'+end,'If-Range':etag});const response=part.response;
       if([408,429,500,502,503,504].includes(response.status))throw Error('HTTP '+response.status);
       if(response.status===403&&await renew())throw Error('下载链接已过期，已重新获取');
       if(response.status!==206||response.headers.get('etag')!==etag||response.headers.get('content-range')!=='bytes '+start+'-'+end+'/'+size)throw integrity('原文件版本或分段变化，已停止保存，请重试。');
       const data=new Uint8Array(wanted);let filled=0;reader=response.body.getReader();
       while(true){const chunk=await part.read(reader);if(chunk.done)break;if(filled+chunk.value.byteLength>wanted)throw integrity('分段响应超出原文件范围');data.set(chunk.value,filled);filled+=chunk.value.byteLength;inflight.set(index,filled);report();}
       if(filled!==wanted)throw Error('分段传输不完整');
       try{await enqueue(start,data);}catch(e){e.integrity=true;throw e;}inflight.delete(index);written+=filled;report();break;
      }catch(e){
       inflight.delete(index);report();
       if(controller.signal.aborted||e.integrity||attempt===2)throw e;
       await new Promise(resolve=>setTimeout(resolve,400*(attempt+1)));
      }finally{if(reader){try{await reader.cancel();}catch(e){}reader.releaseLock();}part?.close();}
     }
    }}
    let failure=null;
    const workers=Array.from({length:Math.min(LANES,count)},()=>worker().catch(e=>{if(!failure)failure=e;controller.abort();throw e;}));
    await Promise.allSettled(workers);if(failure)throw failure;
    if(written!==size)throw integrity('原文件传输不完整');
   }
   await writeChain;if(controller.signal.aborted)throw stopped();
   await writer.truncate(size);if(controller.signal.aborted)throw stopped();await writer.close();writer=null;
   return {size};
  }catch(e){controller.abort();await writeChain;if(writer){try{await writer.abort();}catch(ignore){}}throw e;}
  finally{signal.removeEventListener('abort',abort);}
 }
 window.OriginalFileTransfer={supported:()=>window.isSecureContext&&typeof window.showSaveFilePicker==='function',pick,save};
})();
