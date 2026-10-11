import sys,json,time,re,hashlib,subprocess,os
from pathlib import Path
import yt_dlp
case=json.loads(sys.argv[1]);out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True);start=time.monotonic();warnings=[];deadline=case.get('deadline',140);budget=case.get('limit_mib',160)*1024**2
class Quiet:
 def debug(self,*a):pass
 def warning(self,message):warnings.append(str(message))
 def error(self,*a):pass
class Limit(Exception):pass
def sanitized(value):
 value=re.sub(r'https?://[^\s\"\']+',lambda m:m[0].split('?')[0],str(value))
 return value[:1600]
def digest(path):
 with path.open('rb') as stream:
  h=hashlib.sha256()
  for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
  return h.hexdigest()

def progress(data):
 if time.monotonic()-start>deadline:raise Limit('Test deadline 140 seconds')
 if (data.get('downloaded_bytes') or 0)>budget:raise Limit('Sample exceeded configured test size limit')
def choose(info,incomplete=False):
 if not incomplete:
  if info.get('is_live'):return 'Live stream excluded from bounded file test'
  if (info.get('duration') or 0)>900:return 'Sample duration exceeds 15 minutes; metadata only'
opts={'logger':Quiet(),'socket_timeout':15,'retries':1,'fragment_retries':1,'extractor_retries':0,'noplaylist':True,'playlistend':1,'format':'bv*+ba/b','format_sort':['res','fps','tbr'],'format_sort_force':True,'concurrent_fragment_downloads':4,'outtmpl':str(out/'media.%(ext)s'),'merge_output_format':'mp4','progress_hooks':[progress],'match_filter':choose,'max_filesize':budget,'quiet':True,'noprogress':True,'windowsfilenames':True}
if case.get('check_formats'):opts['check_formats']=True
if case.get('proxy'):opts['proxy']='socks5h://127.0.0.1:10808'
result={**case,'route':'existing-local-socks' if case.get('proxy') else 'default','started_at':time.strftime('%Y-%m-%d %H:%M:%S'),'stages':{}}
try:
 with yt_dlp.YoutubeDL(opts) as ydl:
  sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
  import platform_extractors;platform_extractors.install(ydl)
  info=ydl.extract_info(case['url'],download=False)
  if not info:raise ValueError('No downloadable metadata returned (may exceed test duration limit)')
  if info.get('_type')=='playlist':
   entries=list(info.get('entries') or []);info=next((v for v in entries if v),None)
   if not info:raise ValueError('Empty playlist')
  result.update(title=info.get('title'),duration=info.get('duration'),selected_height=info.get('height'),selected_width=info.get('width'),formats=len(info.get('formats') or []))
  result['stages']['metadata']='passed'
  if (info.get('duration') or 0)>900:raise Limit('Sample exceeds 15 minutes; download not tested')
  ydl.process_info(info)
 completed=[x for x in out.glob('media.*') if x.suffix.lower() in ('.mp4','.webm','.mkv','.mov','.flv','.avi','.m4v','.ogv','.mpg','.mpeg')]
 if not completed:raise ValueError('No completed video file; download may exceed size or duration limit')
 media=completed[0]
 probe=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_name,codec_type,width,height,r_frame_rate:format=duration,size','-of','json',str(media)],capture_output=True,timeout=25,creationflags=subprocess.CREATE_NO_WINDOW)
 if probe.returncode:raise ValueError('ffprobe rejected downloaded media')
 quality=json.loads(probe.stdout);assert any(s.get('codec_type')=='video' for s in quality['streams'])
 decode=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-i',str(media),'-t','1','-f','null','-'],capture_output=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
 if decode.returncode:raise ValueError('First second decode failed')
 result.update(status='downloaded',bytes=media.stat().st_size,sha256=digest(media),media=quality)
 result['stages'].update(download='passed',media_probe='passed',first_second_decode='passed')
except Exception as error:
 result.update(status='not_completed',error=sanitized(error));result['stages'].setdefault('download','not_passed')
result['seconds']=round(time.monotonic()-start,3);result['warnings']=[sanitized(v) for v in warnings[-6:]]
(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:result.get(k) for k in ['platform','status','seconds','bytes','selected_height','error']},ensure_ascii=True),flush=True)
