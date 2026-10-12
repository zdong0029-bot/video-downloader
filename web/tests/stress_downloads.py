"""Real downloader stress tests. Never substitutes short clips for long videos.
Run against your own downloader; manifests and cookies may contain private links.
"""
import argparse, concurrent.futures, hashlib, json, re, shutil, subprocess, sys, time
from pathlib import Path
from urllib.parse import quote
import requests

TERMINAL={'done','partial','error','cancelled'}
def clean(value):
    return re.sub(r'https?://\S+', '[URL omitted]',str(value))[:800]
def save(path, value):
    temporary=path.with_suffix('.writing');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)
def health(base):
    s=requests.Session();s.trust_env=False;t=time.monotonic()
    try:
        r=s.get(base+'/',timeout=(8,15));return {'status':r.status_code,'seconds':round(time.monotonic()-t,3)}
    except requests.RequestException as e:return {'error':type(e).__name__}
    finally:s.close()
def probe(path):
    ffprobe=shutil.which('ffprobe');ffmpeg=shutil.which('ffmpeg')
    if not ffprobe or not ffmpeg:raise ValueError('Missing media verification tools')
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    r=subprocess.run([ffprobe,'-v','error','-show_entries','stream=codec_type,codec_name,width,height,r_frame_rate:format=duration,size','-of','json',str(path)],capture_output=True,timeout=90,creationflags=flags)
    if r.returncode:raise ValueError('ffprobe could not read completed file')
    data=json.loads(r.stdout);duration=float(data.get('format',{}).get('duration') or 0)
    decoded=[]
    for location in (0,duration/2,max(0,duration-5)):
        r=subprocess.run([ffmpeg,'-v','error','-xerror','-ss',str(location),'-i',str(path),'-t','2','-f','null','-'],capture_output=True,timeout=120,creationflags=flags)
        if r.returncode:raise ValueError('Media decode failed at '+str(round(location,2))+' seconds')
        decoded.append(round(location,3))
    data['decoded_positions']=decoded;return data

def test(case,args):
    tag=case['id'];dest=args.output/tag;dest.mkdir(parents=True,exist_ok=True)
    s=requests.Session();s.trust_env=False;other=requests.Session();other.trust_env=False
    start=time.monotonic();result={'id':tag,'platform':case['platform'],'category':case['category'],'started_at':time.strftime('%Y-%m-%d %H:%M:%S'),'health_before':health(args.base),'status':'running','stages':{},'health_samples':[]}
    job_id=None
    try:
        response=s.post(args.base+'/api/jobs',json={'url':case['url'],'mode':case.get('mode','auto')},timeout=(10,30));response.raise_for_status();created=response.json()['created'];job_id=created[0]['id'] if isinstance(created[0],dict) else created[0]
        save(dest/'session-private.json',{'job_id':job_id,'cookies':s.cookies.get_dict(),'case':case})
        result['stages']['submit']='passed';save(dest/'result.json',result);last_print=0;job=None;seen_job=False
        while time.monotonic()-start<args.deadline:
            response=s.get(args.base+'/api/jobs',timeout=(10,30));response.raise_for_status();jobs=response.json();jobs=jobs.get('jobs',[]) if isinstance(jobs,dict) else jobs;job=next((j for j in jobs if j['id']==job_id),None)
            if job:seen_job=True
            if not job and seen_job:raise ValueError('Test task was cancelled or removed; no complete file')
            if job and job['status'] in TERMINAL:break
            now=time.monotonic()
            if now-last_print>=30:
                result['elapsed_seconds']=round(now-start,3);result['progress']=(job or {}).get('progress',0);result['health_samples'].append(health(args.base));save(dest/'result.json',result)
                print(json.dumps({'id':tag,'status':(job or {}).get('status'),'percent':round(result['progress']*100,1),'elapsed':round(now-start),'message':clean((job or {}).get('message',''))},ensure_ascii=True),flush=True);last_print=now
            time.sleep(2)
        if not job or job['status']!='done':
            if job and job['status'] not in TERMINAL:s.post(args.base+'/api/jobs/'+job_id+'/cancel',timeout=20)
            raise ValueError(clean((job or {}).get('message') or 'Full download deadline exceeded'))
        result['ready_seconds']=round(time.monotonic()-start,3);result['stages']['server_download']='passed';result['quality_note']=clean(job.get('cloud_note',''));save(dest/'job-private.json',job)
        files=[]
        for item in job['files']:
            address=args.base+'/api/files/'+job_id+'/'+quote(item['name'],safe='');path=args.web/'downloads'/job_id/item['name']
            if not path.is_file() or path.stat().st_size!=item['size']:raise ValueError('Website completed file is absent or has wrong size')
            media=None
            if case['category'] in ('long_video','short_video'):
                media=probe(path)
            src_sha=hashlib.sha256()
            with path.open('rb') as f:
                for block in iter(lambda:f.read(4*1024**2),b''):src_sha.update(block)
            received=0;digest=hashlib.sha256();transfer=time.monotonic();last_print=transfer
            with s.get(address,stream=True,timeout=(15,60)) as response:
                response.raise_for_status()
                for block in response.iter_content(1024**2):
                    received+=len(block);digest.update(block)
                    now=time.monotonic()
                    if now-last_print>=30:
                        print(json.dumps({'id':tag,'stage':'full_visitor_transfer','bytes':received,'seconds':round(now-transfer)},ensure_ascii=True),flush=True);last_print=now
            if received!=item['size'] or digest.hexdigest()!=src_sha.hexdigest():raise ValueError('Visitor bytes or SHA256 mismatch')
            expected=case.get('sha256')
            if expected and digest.hexdigest().lower()!=expected.lower():raise ValueError('Source SHA256 mismatch')
            isolation=other.get(address,headers={'Range':'bytes=0-0'},timeout=20).status_code
            if isolation!=404:raise ValueError('Another visitor could read this private test record')
            boundary=None
            if received>2**31:
                offset=2**31+123;r=s.get(address,headers={'Range':f'bytes={offset}-{offset+4095}'},timeout=20)
                with path.open('rb') as f:f.seek(offset);wanted=f.read(4096)
                if r.status_code!=206 or r.content!=wanted:raise ValueError('Download range beyond 2 GiB failed')
                boundary='passed'
            duration=float((media or {}).get('format',{}).get('duration') or 0)
            files.append({'bytes':received,'sha256':digest.hexdigest(),'transfer_seconds':round(time.monotonic()-transfer,3),'media':media,'range_beyond_2g':boundary,'other_guest_status':isolation})
        if not files:raise ValueError('No completed files')
        result.update(files=files,bytes=sum(f['bytes'] for f in files));result['average_MiB_s']=round(result['bytes']/1024**2/result['ready_seconds'],3)
        result['stages'].update(full_visitor_transfer='passed',sha256='passed',guest_isolation='passed')
        if case['category']=='long_video':
            durations=[float(f['media']['format']['duration']) for f in files];result['duration_seconds']=sum(durations)
            if len(durations)!=1 or not 3600<=durations[0]<=7500:raise ValueError('Actual source is not a single 1–2 hour video; full file downloaded but long-duration criterion not met')
            result['stages']['long_duration']='passed'
        if case['category']=='multi_gb':
            if not any(f['bytes']>=2*1024**3 for f in files):raise ValueError('No single completed file is at least 2 GiB')
            result['stages']['multi_gb']='passed'
        if case['category']=='cloud_gb_total':
            if result['bytes']<2*1024**3:raise ValueError('Completed cloud files total less than 2 GiB')
            result['stages']['multi_gb_total']='passed'
        result['status']='passed'
    except Exception as e:
        result.update(status='failed',error=clean(type(e).__name__+': '+str(e)))
    finally:
        result['seconds']=round(time.monotonic()-start,3);result['health_after']=health(args.base);save(dest/'result.json',result);s.close();other.close()
        print(json.dumps({k:result.get(k) for k in ('id','status','bytes','ready_seconds','average_MiB_s','duration_seconds','error')},ensure_ascii=True),flush=True)
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('manifest',type=Path);p.add_argument('--web',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--base',default='http://127.0.0.1:8000');p.add_argument('--deadline',type=int,default=14400);p.add_argument('--workers',type=int,default=1);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    if not 1<=a.workers<=2:raise SystemExit('Use at most two website test workers')
    cases=json.loads(a.manifest.read_text(encoding='utf-8-sig'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:results=list(pool.map(lambda c:test(c,a),cases))
    save(a.output/'results.json',results)
if __name__=='__main__':main()
