"""Exercise disk streaming with real browser OPFS and synthetic original bytes."""
import hashlib,os,re
from playwright.sync_api import sync_playwright
BASE=os.environ.get('TRANSFER_BASE_URL','http://127.0.0.1:8000')
DATA=bytes(range(256))*(20480)+b'original tail' # 5 MiB plus an unaligned final range
SHA=hashlib.sha256(DATA).hexdigest()
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True);context=browser.new_context();page=context.new_page()
 page.route('**/api/jobs*',lambda route:route.fulfill(json=[]));page.route('**/api/catalogs*',lambda route:route.fulfill(json=[]))
 page.goto(BASE);page.wait_for_function("typeof OriginalFileTransfer==='object'")
 mode=['normal'];calls=[];failed=[False]
 def route(r):
  h=r.request.headers.get('range');calls.append(h)
  if not h or mode[0]=='ignored':r.fulfill(body=DATA,headers={'Content-Length':str(len(DATA))});return
  a,b=map(int,re.fullmatch(r'bytes=(\d+)-(\d+)',h).groups());body=DATA[a:b+1]
  if mode[0]=='retry' and a>0 and not failed[0]:failed[0]=True;r.fulfill(status=503,body='retry');return
  if mode[0]=='short' and a>0 and not failed[0]:failed[0]=True;body=body[:7]
  if mode[0]=='oversized' and a>0:body+=b'extra'
  etag='W/"fixture"' if mode[0]=='weak' else '"changed"' if mode[0]=='changed' and a>0 else '"original-v1"'
  cr=f'bytes {a}-{b}/{len(DATA)}'
  if mode[0]=='range' and a>0:cr=f'bytes 0-{b}/{len(DATA)}'
  r.fulfill(status=206,body=body,headers={'Content-Length':str(len(body)),'Content-Range':cr,'ETag':etag})
 page.route('**/__original_fixture',route)
 script='''async opts=>{
  const root=await navigator.storage.getDirectory(),handle=await root.getFileHandle('original-test.bin',{create:true});
  let closed=false,aborted=false,maxWrite=0,totalReceived=0,progressEvents=0;
  const wrapped={createWritable:async()=>{const real=await handle.createWritable();return {
   write:async value=>{maxWrite=Math.max(maxWrite,value.data.byteLength);if(opts.diskError)throw Error('Disk full');return real.write(value);},
   truncate:n=>real.truncate(n),close:async()=>{await real.close();closed=true;},abort:async()=>{await real.abort();aborted=true;}
  }}};
  const controller=new AbortController();let error='';
  try{await OriginalFileTransfer.save('/__original_fixture',wrapped,opts.expected,controller.signal,(n,total)=>{totalReceived=n;progressEvents++;if(opts.cancel&&n>0)controller.abort();});}
  catch(e){error=e.message;}
  const file=await handle.getFile(),digest=await crypto.subtle.digest('SHA-256',await file.arrayBuffer());
  return {closed,aborted,error,size:file.size,maxWrite,totalReceived,progressEvents,sha:Array.from(new Uint8Array(digest)).map(n=>n.toString(16).padStart(2,'0')).join('')};
 }'''
 for case in ['normal','retry','short','ignored','weak','changed','range','oversized','size','disk','cancel']:
  mode[0]=case;failed[0]=False;calls.clear()
  result=page.evaluate(script,{'expected':len(DATA)+(1 if case=='size' else 0),'diskError':case=='disk','cancel':case=='cancel'})
  if case in ['normal','retry','short','ignored','weak']:
   assert result['closed'] and not result['aborted'],(case,result)
   assert result['size']==len(DATA) and result['sha']==SHA,(case,result)
   assert result['totalReceived']==len(DATA),(case,result)
   if case in ['normal','retry','short']:assert result['maxWrite']<=1048576
  else:
   assert result['error'] and result['aborted'] and not result['closed'],(case,result)
   assert result['size']==len(DATA) and result['sha']==SHA,(case,'existing file must survive aborted save',result)
  if case=='retry':assert calls.count('bytes=1048576-2097151')==2,calls
  print('PASS',case,'original integrity / bounded disk save' if not result['error'] else 'aborted without committing incomplete file')
 # A click can launch a picker then execute the actual downloadSelected integration.
 page.unroute('**/__original_fixture');big=bytes(range(256))*131073;big_sha=hashlib.sha256(big).hexdigest()
 def big_route(r):
  a,b=map(int,re.fullmatch(r'bytes=(\d+)-(\d+)',r.request.headers['range']).groups());body=big[a:b+1]
  r.fulfill(status=206,body=body,headers={'Content-Length':str(len(body)),'Content-Range':f'bytes {a}-{b}/{len(big)}','ETag':'"big-original-v1"'})
 page.route('**/api/files/diskfixture/*',big_route)
 page.evaluate('''size=>{clearInterval(timer);cache=[{id:'diskfixture',kind:'web',status:'done',files:[{name:'original.mp4',size}],created:1}];
  window.showSaveFilePicker=async()=>{window.pickerGesture=navigator.userActivation.isActive;const root=await navigator.storage.getDirectory();window.savedHandle=await root.getFileHandle('integrated-original.mp4',{create:true});return savedHandle;};
  const button=document.createElement('button');button.id='diskTestButton';button.onclick=()=>downloadSelected('diskfixture','original.mp4','diskfixture',true,true);document.body.append(button);
 }''',len(big))
 page.click('#diskTestButton');page.wait_for_function("downloadTransfers.get('diskfixture') && !downloadTransfers.get('diskfixture').active",timeout=30000)
 result=page.evaluate('''async()=>{const file=await savedHandle.getFile();const digest=await crypto.subtle.digest('SHA-256',await file.arrayBuffer());const t=downloadTransfers.get('diskfixture');return {gesture:pickerGesture,size:file.size,completed:t.completed,error:t.error,savedToDisk:t.savedToDisk,native:t.nativeCount||0,sha:Array.from(new Uint8Array(digest)).map(n=>n.toString(16).padStart(2,'0')).join('')};}''')
 assert result=={'gesture':True,'size':len(big),'completed':1,'error':'','savedToDisk':True,'native':0,'sha':big_sha},result
 print('PASS 32 MiB original disk write through download button; SHA-256 matches, picker in click gesture, no native duplicate')
 # Cancelling the picker must not silently start a duplicate browser download.
 page.evaluate("window.showSaveFilePicker=async()=>{throw new DOMException('User cancelled','AbortError');};downloadTransfers.clear();")
 page.click('#diskTestButton');page.wait_for_function("downloadTransfers.get('diskfixture') && !downloadTransfers.get('diskfixture').active")
 assert page.evaluate("downloadTransfers.get('diskfixture').completed===0 && !downloadTransfers.get('diskfixture').nativeCount")
 print('PASS picker cancellation stops save without fallback download')
 browser.close()
