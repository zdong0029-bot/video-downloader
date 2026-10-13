"""Own-site profile -> choose one work -> complete file/hash/decode/isolation."""
import sys,json,time,hashlib,concurrent.futures,subprocess,shutil,re,argparse
from pathlib import Path
from urllib.parse import quote
import requests
# Configuration is explicit: importing this module never starts a download.
ROOT=WEB=OUT=BASE=None
LIMIT=100
CATALOG_TIMEOUT=180
DOWNLOAD_TIMEOUT=900
METADATA_ONLY=False

def save(path,d):path.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
def clean(e):return re.sub(r'https?://\S+',lambda m:m[0].split('?')[0],str(e))[:1200]
def run(case):
 name=case['platform'];result={**case,'status':'running','stages':{}};dest=OUT/name;dest.mkdir(exist_ok=True)
 s=requests.Session();s.trust_env=False;other=requests.Session();other.trust_env=False
 start=time.monotonic();job_id=None;cid=None
 try:
  r=s.post(BASE+'/api/catalogs',json={'url':case['url'],'limit':LIMIT},timeout=30);r.raise_for_status();payload=r.json()
  cid=payload.get('id') or next((c['id'] for c in payload.get('catalogs',[]) if c.get('id')),None)
  if not cid:raise ValueError('; '.join(str(row.get('reason','识别失败')) for row in payload.get('rejected',[])) or '主页接口未返回列表编号')
  until=time.monotonic()+CATALOG_TIMEOUT
  while time.monotonic()<until:
   r=s.get(BASE+'/api/catalogs',timeout=20);r.raise_for_status();cat=next((c for c in r.json() if c['id']==cid),None)
   if cat and cat['status'] in ('done','error'):break
   time.sleep(1)
  if not cat or cat['status']!='done':raise ValueError(clean((cat or {}).get('message','Catalog did not finish')))
  entries=cat['entries'];result.update(catalog_seconds=round(time.monotonic()-start,3),count=len(entries),complete=cat.get('complete',False),pages=cat.get('pages'),sources=cat.get('sources'),completion_reason=cat.get('completion_reason'),limited=cat['limited'],note=cat.get('note',''),method=cat.get('method','native-extractor'))
  result['stages']['official_profile_list']='passed'
  # Prefer a short video from the actual profile; never substitute another URL.
  if not entries:raise ValueError('No selectable works')
  if METADATA_ONLY:
   result['status']='listed';return result
  selected=min(entries,key=lambda e:(0 if 0<float(e.get('duration') or 0)<=180 else 1,float(e.get('duration') or 999999)))
  result['selected_title']=selected['title'];result['source_duration']=selected.get('duration',0)
  save(dest/'catalog-private.json',cat)
  response=s.post(BASE+'/api/catalogs/'+cid+'/download',json={'ids':[selected['id']]},timeout=30);response.raise_for_status();payload=response.json()
  job_id=payload.get('id') or payload.get('job',{}).get('id')
  if not job_id and payload.get('created'):job_id=payload['created'][0].get('id') if isinstance(payload['created'][0],dict) else payload['created'][0]
  if not job_id:raise ValueError('Missing selected download job id: '+str(list(payload)))
  save(dest/'session-private.json',{'cookies':s.cookies.get_dict(),'job_id':job_id,'catalog_id':cid})
  deadline=time.monotonic()+DOWNLOAD_TIMEOUT;last=0
  while time.monotonic()<deadline:
   jobs=s.get(BASE+'/api/jobs',timeout=25).json();job=next((j for j in jobs if j['id']==job_id),None)
   if job and job['status'] in ('done','partial','error','cancelled'):break
   if time.monotonic()-last>=40:
    print(json.dumps({'platform':name,'stage':'selected-download','status':(job or {}).get('status'),'seconds':round(time.monotonic()-start),'message':clean((job or {}).get('message',''))},ensure_ascii=True),flush=True);last=time.monotonic()
   time.sleep(2)
  if not job or job['status']!='done':
   if job and job['status'] not in ('partial','error','cancelled'):
    s.post(BASE+'/api/jobs/'+job_id+'/cancel',timeout=20)
    raise ValueError('Selected complete download exceeded '+str(DOWNLOAD_TIMEOUT)+' seconds; '+clean(job.get('message','')))
   raise ValueError(clean((job or {}).get('message','Selected download deadline')))
  result['download_seconds']=round(time.monotonic()-start-result['catalog_seconds'],3)
  save(dest/'job-private.json',job)
  result['quality_note']=job.get('cloud_note','');result['stages']['selected_complete_download']='passed'
  files=[]
  for item in job['files']:
   src=WEB/'downloads'/job_id/item['name']
   if not src.is_file() or src.stat().st_size!=item['size']:raise ValueError('Completed file absent/size mismatch')
   wanted_hash=hashlib.sha256()
   with src.open('rb') as host_file:
    for block in iter(lambda:host_file.read(1024**2),b''):wanted_hash.update(block)
   wanted=wanted_hash.hexdigest();url=BASE+'/api/files/'+job_id+'/'+quote(item['name'],safe='')
   got=hashlib.sha256();count=0;t=time.monotonic()
   with s.get(url,stream=True,timeout=(10,60)) as r:
    r.raise_for_status()
    for block in r.iter_content(1024**2):got.update(block);count+=len(block)
   if count!=item['size'] or wanted!=got.hexdigest():raise ValueError('Full visitor transfer/hash mismatch')
   isolation=other.get(url,headers={'Range':'bytes=0-0'},timeout=20).status_code
   if isolation!=404:raise ValueError('Another visitor could see this file')
   data={'bytes':count,'sha256':wanted,'visitor_seconds':round(time.monotonic()-t,3),'other_guest_status':isolation}
   if src.suffix.lower() in ('.mp4','.mkv','.webm','.mov','.flv','.mpg','.mpeg','.m4v','.ts'):
    p=subprocess.run([shutil.which('ffprobe'),'-v','error','-show_entries','stream=codec_type,codec_name,width,height:format=duration','-of','json',str(src)],capture_output=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
    if p.returncode:raise ValueError('Cannot probe completed media')
    data['media']=json.loads(p.stdout);duration=float(data['media'].get('format',{}).get('duration') or 0)
    positions=[None] if src.suffix.lower() in ('.mpg','.mpeg') and duration<180 else (0,duration/2,max(0,duration-3))
    for loc in positions:
     flags=[] if loc is None else ['-ss',str(loc)]
     tail=[] if loc is None else ['-t','1']
     p=subprocess.run([shutil.which('ffmpeg'),'-v','error','-xerror',*flags,'-i',str(src),*tail,'-f','null','-'],capture_output=True,timeout=90,creationflags=subprocess.CREATE_NO_WINDOW)
     if p.returncode:raise ValueError('Video decode failure')
    data['decode']='full sequential decode passed' if positions==[None] else 'start/middle/end passed'
   else:
    from PIL import Image
    with Image.open(src) as img:data['image']={'width':img.width,'height':img.height};img.verify()
   files.append(data)
  if not files:raise ValueError('No complete output')
  result.update(status='passed',files=files,bytes=sum(f['bytes'] for f in files));result['stages'].update(full_visitor_transfer='passed',sha256='passed',decode='passed',guest_isolation='passed')
 except Exception as e:
  result.update(status='failed',error=clean(type(e).__name__+': '+str(e)))
  if job_id:
   try:s.post(BASE+'/api/jobs/'+job_id+'/cancel',timeout=15)
   except requests.RequestException:pass
 finally:
  result['seconds']=round(time.monotonic()-start,3);save(dest/'result.json',result)
  print(json.dumps({k:result.get(k) for k in ('platform','status','count','bytes','download_seconds','error')},ensure_ascii=True),flush=True)
  s.close();other.close()
 return result
def main():
 global ROOT,WEB,OUT,BASE,LIMIT,CATALOG_TIMEOUT,DOWNLOAD_TIMEOUT,METADATA_ONLY
 parser=argparse.ArgumentParser(description='Own-site profile listing and selected original media audit; creates isolated guest records.')
 parser.add_argument('--manifest',type=Path,default=Path(__file__).with_name('profile_samples.json'))
 parser.add_argument('--output',type=Path,required=True,help='Private report directory outside the repository/static folder')
 parser.add_argument('--base',default='http://127.0.0.1:8000')
 parser.add_argument('--web',type=Path,default=Path(__file__).resolve().parent.parent,help='Server web directory, required for host/visitor hash comparison')
 parser.add_argument('--platform',action='append',default=[])
 parser.add_argument('--limit',type=int,choices=(0,100,200),default=100,help='0 requests complete listing; 100/200 requests a bounded sample')
 parser.add_argument('--catalog-timeout',type=int,default=180)
 parser.add_argument('--download-timeout',type=int,default=900)
 parser.add_argument('--metadata-only',action='store_true')
 args=parser.parse_args()
 WEB=args.web.resolve();OUT=args.output.resolve();ROOT=OUT;BASE=args.base.rstrip('/')
 if WEB in OUT.parents or OUT==WEB or WEB.parent in OUT.parents:parser.error('Private results must be outside the repository')
 if not 10<=args.catalog_timeout<=1800 or not 10<=args.download_timeout<=7200:parser.error('Invalid deadlines')
 LIMIT=args.limit;CATALOG_TIMEOUT=args.catalog_timeout;DOWNLOAD_TIMEOUT=args.download_timeout;METADATA_ONLY=args.metadata_only
 if not METADATA_ONLY and (not shutil.which('ffprobe') or not shutil.which('ffmpeg')):parser.error('FFmpeg and ffprobe must be on PATH for complete media verification')
 cases=json.loads(args.manifest.read_text(encoding='utf-8'))
 if args.platform:cases=[c for c in cases if c['platform'] in args.platform]
 if not cases:parser.error('No matching cases')
 if any(not re.fullmatch(r'[A-Za-z0-9_-]+',c['platform']) for c in cases):parser.error('Invalid platform label')
 if len({c['platform'] for c in cases})!=len(cases):parser.error('Duplicate platform labels')
 OUT.mkdir(parents=True,exist_ok=True)
 # Two own guest sessions at a time. No existing user record is modified.
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,cases))
 save(OUT/'results.json',results)
 return int(any(r['status']=='failed' for r in results))

if __name__=='__main__':raise SystemExit(main())
