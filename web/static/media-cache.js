/* Private original-file cache: quota bounded; no server account credentials. */
(() => {
 const DISK_LIMIT=256*1048576,MEM_LIMIT=32*1048576,FALLBACK_LIMIT=128*1048576,TTL=7*86400000;
 let owner='uninitialized',dbPromise=null,diskUnavailable=false,memBytes=0;
 const memory=new Map();
 const key=item=>owner+'|'+item.id+'|'+encodeURIComponent(item.name)+'|'+(item.size||0);
 function memoryPut(k,file){
  const limit=diskUnavailable?FALLBACK_LIMIT:MEM_LIMIT;if(file.size>limit)return;
  if(memory.has(k)){memBytes-=memory.get(k).size;memory.delete(k);}
  while(memBytes+file.size>limit&&memory.size){const oldest=memory.keys().next().value;memBytes-=memory.get(oldest).size;memory.delete(oldest);}
  memory.set(k,file);memBytes+=file.size;
 }
 function database(){
  if(diskUnavailable||!window.indexedDB)return Promise.resolve(null);
  if(!dbPromise)dbPromise=new Promise(resolve=>{
   let settled=false;const finish=db=>{if(settled){db?.close();return;}settled=true;resolve(db);};
   const timeout=setTimeout(()=>{diskUnavailable=true;finish(null);},2000);
   try{const r=indexedDB.open('download-original-cache-v1',1);
    r.onupgradeneeded=()=>r.result.createObjectStore('files',{keyPath:'key'});
    r.onsuccess=()=>{clearTimeout(timeout);r.result.onversionchange=()=>r.result.close();finish(r.result);};
    r.onerror=r.onblocked=()=>{clearTimeout(timeout);diskUnavailable=true;finish(null);};
   }catch(e){clearTimeout(timeout);diskUnavailable=true;finish(null);}
  });return dbPromise;
 }
 function operation(db,mode,run){return new Promise((resolve,reject)=>{const t=db.transaction('files',mode);let value;run(t.objectStore('files'),v=>value=v);t.oncomplete=()=>resolve(value);t.onerror=t.onabort=()=>reject(t.error||Error('Cache unavailable'));});}
 async function get(item){
  const k=key(item);if(memory.has(k)){const file=memory.get(k);memory.delete(k);memory.set(k,file);return file;}
  try{const db=await database();if(!db)return null;
   const row=await operation(db,'readonly',(store,set)=>{const r=store.get(k);r.onsuccess=()=>set(r.result);});
   if(!row||Date.now()-row.time>TTL||!row.file?.size||item.size&&row.file.size!==Number(item.size))return null;
   const file=new File([row.file],item.name,{type:row.file.type});memoryPut(k,file);return file;
  }catch(e){return null;}
 }
 async function put(item,file){
  const k=key(item);
  try{const db=await database();if(db){
   await operation(db,'readwrite',(store)=>store.put({key:k,time:Date.now(),size:file.size,file}));
   await operation(db,'readwrite',store=>{
    const r=store.getAll();r.onsuccess=()=>{const rows=r.result.sort((a,b)=>a.time-b.time);let bytes=rows.reduce((n,a)=>n+a.size,0);
     for(const row of rows)if(Date.now()-row.time>TTL||bytes>DISK_LIMIT){store.delete(row.key);bytes-=row.size;}
    };
   });
  }else diskUnavailable=true;
  }catch(e){diskUnavailable=true;}
  memoryPut(k,file);
 }
 window.MediaOriginalCache={get,put,setOwner(value){if(owner!==value){owner=value;memory.clear();memBytes=0;}},clearMemory(){memory.clear();memBytes=0;}};
})();
