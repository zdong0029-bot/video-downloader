/* Two bounded readers; retry only failed ranges, never mix versions or alter bytes. */
(() => {
 const CHUNK=4*1048576,MAX=128*1048576;
 const failure=(message)=>Object.assign(Error(message),{integrity:true});
 const retryable=status=>[408,429,500,502,503,504].includes(status);
 async function bytes(response,signal,onBytes){
  const reader=response.body?.getReader();if(!reader)return new Uint8Array(await response.arrayBuffer());
  const chunks=[];let size=0;
  try{while(true){
   const timeout=setTimeout(()=>reader.cancel('stall'),30000);
   let data;try{data=await reader.read();}finally{clearTimeout(timeout);}
   if(signal.aborted)throw new DOMException('Stopped','AbortError');
   if(data.done)break;chunks.push(data.value);size+=data.value.length;onBytes(size);
  }}finally{try{await reader.cancel();}catch(e){}reader.releaseLock();}
  const joined=new Uint8Array(size);let offset=0;for(const chunk of chunks){joined.set(chunk,offset);offset+=chunk.length;}return joined;
 }
 window.fetchAlbumOriginal=async(item,signal,onProgress)=>{
  const size=Number(item.size)||0;
  // CDN CORS and authorization differ by source; preserve the established direct path.
  if(size<2*1048576||size>MAX||cache.find(j=>j.id===item.id)?.kind==='symphony')return fetchVideo(item.id,item.name,signal);
  // A ready relay copy is preferred; the signed link carries no cookies (credentials omitted).
  const relay=await window.RelayLinks?.link(item.id,item.name);
  const url=relay?relay.url:'/api/files/'+encodeURIComponent(item.id)+'/'+encodeURIComponent(item.name),credentials=relay?'omit':'same-origin';
  const probe=await fetch(url,{signal,credentials,headers:{Range:'bytes=0-0'}});
  if(probe.status===200)return probe; // Range ignored: reuse the full original response.
  if(probe.status!==206){await probe.body?.cancel();if(!probe.ok)throw Error('原文件读取失败（HTTP '+probe.status+'）');return fetchVideo(item.id,item.name,signal);}
  const etag=probe.headers.get('etag'),range=probe.headers.get('content-range');
  const one=await probe.arrayBuffer();
  if(range!=='bytes 0-0/'+size||one.byteLength!==1)throw failure('原文件大小已变化，请刷新记录后重试。');
  if(!etag||etag.startsWith('W/'))return fetchVideo(item.id,item.name,signal);
  const controller=new AbortController();const abort=()=>controller.abort();signal.addEventListener('abort',abort,{once:true});if(signal.aborted)abort();
  const chunkSize=Math.min(CHUNK,Math.ceil(size/2)),count=Math.ceil(size/chunkSize),parts=new Array(count),received=new Array(count).fill(0);let next=0;
  const report=()=>onProgress(received.reduce((a,b)=>a+b,0),size,'两路原文件传输');
  async function worker(){while(next<count){
   const index=next++,start=index*chunkSize,end=Math.min(size,start+chunkSize)-1;
   for(let attempt=0;attempt<3;attempt++){
    try{
     const r=await fetch(url,{signal:controller.signal,credentials,headers:{Range:'bytes='+start+'-'+end,'If-Range':etag}});
     if(retryable(r.status)){await r.body?.cancel();throw Error('HTTP '+r.status);}
     if(r.status!==206||r.headers.get('etag')!==etag||r.headers.get('content-range')!=='bytes '+start+'-'+end+'/'+size){await r.body?.cancel();throw failure('原文件版本或分段发生变化，已停止保存，请重试。');}
     const data=await bytes(r,controller.signal,n=>{received[index]=n;report();});
     if(data.length!==end-start+1)throw Error('原文件分段未完整接收');
     parts[index]=data;received[index]=data.length;report();break;
    }catch(e){
     if(controller.signal.aborted||e.integrity||attempt===2)throw e;
     received[index]=0;report();await new Promise(resolve=>setTimeout(resolve,400*(attempt+1)));
    }
   }
  }}
  try{
   const workers=[worker(),worker()].map(p=>p.catch(e=>{controller.abort();throw e;}));
   const result=await Promise.allSettled(workers);const failed=result.find(r=>r.status==='rejected'&&r.reason.name!=='AbortError')||result.find(r=>r.status==='rejected');if(failed)throw failed.reason;
   const blob=new Blob(parts,{type:probe.headers.get('content-type')||'application/octet-stream'});if(blob.size!==size)throw failure('原文件接收不完整');
   const response=new Response(blob,{headers:{'Content-Type':blob.type,'Content-Length':String(size),'X-Transfer-Route':'parallel2'}});response.originalBlob=blob;return response;
  }finally{signal.removeEventListener('abort',abort);}
 };
})();
