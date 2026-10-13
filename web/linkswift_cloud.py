"""Original-file adapters for the five additional LinkSwift providers.

API protocol reference: https://github.com/hmjz100/LinkSwift (v1.1.3).
The visitor supplies a share, never a bearer token or an API endpoint.
"""
import base64,hashlib,json,re,secrets,time,zlib,datetime
from urllib.parse import urlsplit,parse_qs,unquote,quote,urljoin
import requests
import download_tuning
import cloud_accounts as accounts
import cloud_providers as providers
import file_download
from common import Cancelled
UC_UA=('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
 '(KHTML, like Gecko) uc-cloud-drive/2.5.20 Chrome/100.0.4896.160 '
 'Electron/18.3.5.4-b478491100 Safari/537.36 Channel/pckk_other_ch')
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'

def fail(key,code,login=False):
 from cloud_backend import LoginRequired,PlatformError
 name=providers.PROVIDERS[key]
 if login:raise LoginRequired(name+'后台授权尚未连接或已失效；请管理员在首页连接账号，访客不用登录')
 raise PlatformError(name+'原文件接口未通过（'+str(code)+ '）；未使用转码预览替代',code)

def storage(state,key):
 values={}
 for origin in state.get('origins',[]):
  if not providers.official(key,origin.get('origin','')):continue
  for item in origin.get('localStorage',[]):
   raw=item.get('value','')
   try:values[item['name']]=json.loads(raw)
   except (ValueError,TypeError):values[item['name']]=raw
 return values

def mobile_sign(body,stamp,nonce):
 compact=re.sub(r'\s','',json.dumps(body,ensure_ascii=False,separators=(',',':')))
 encoded=''.join(sorted(quote(compact,safe="~()*!.'-")))
 a=hashlib.md5(base64.b64encode(encoded.encode())).hexdigest()
 b=hashlib.md5((stamp+':'+nonce).encode()).hexdigest()
 return hashlib.md5((a+b).encode()).hexdigest().upper()

def pan123_query(path):
 # Normal web protocol from OpenList's 123_share adapter (CRC32 parameters).
 table='adefghlmyijnopkqrstubcvwsz'
 minute=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d%H%M')
 key=str(zlib.crc32(''.join(table[int(x)] for x in minute).encode()))
 stamp=str(int(time.time()));nonce=str(secrets.randbelow(10000000))
 sign=str(zlib.crc32('|'.join((stamp,nonce,path,'web','3',key)).encode()))
 return {key:stamp+'-'+nonce+'-'+sign}

class Client:
 def __init__(self,key,state,identity=None,cancelled=lambda:False):
  self.key,self.state,self.identity,self.cancelled=key,state,identity,cancelled
  self.s=requests.Session();self.s.trust_env=False
  self.s.headers.update({'User-Agent':UA,'Referer':providers.LOGIN_URLS[key],'Accept':'application/json;charset=UTF-8'})
  self.update_browser_state(state)
 def update_browser_state(self,state):
  self.state.update(state);self.s.cookies.clear()
  domains=providers.DOMAINS[self.key]+providers.API_DOMAINS[self.key]
  for cookie in state.get('cookies',[]):
   domain=cookie.get('domain','').lstrip('.')
   if domain and any(domain==d or domain.endswith('.'+d) or d.endswith('.'+domain) for d in domains):
    # A top-level cn/com cookie is never copied to an authenticated request.
    if len(domain.split('.'))<2 or domain in ('cn','com','com.cn'):continue
    self.s.cookies.set(cookie['name'],cookie['value'],domain=cookie.get('domain',domain),path=cookie.get('path','/'),secure=bool(cookie.get('secure')))
  self.values=storage(self.state,self.key)
 def raw(self,url,body=None,params=None,headers=None,form=None):
  if not providers.official(self.key,url,api=True):raise ValueError('不是此网盘的官方接口')
  for attempt in range(3):
   if self.cancelled():raise Cancelled()
   try:r=self.s.request('POST' if body is not None or form is not None else 'GET',url,json=body,data=form,params=params,headers=headers or {},timeout=(10,30),allow_redirects=False)
   except requests.RequestException:raise ValueError(providers.PROVIDERS[self.key]+'网络连接失败') from None
   if r.status_code in (429,500,502,503,504) and attempt<2:
    r.close();time.sleep(1+attempt*2);continue
   return r
 def api(self,url,body=None,params=None,headers=None,form=None):
  r=self.raw(url,body,params,headers,form)
  try:
   try:d=r.json()
   except requests.exceptions.JSONDecodeError:
    if self.key!='tianyi':raise
    from linkswift_shares import tianyi_xml
    d=tianyi_xml(r.content)
   if not isinstance(d,dict):fail(self.key,r.status_code)
   code=d.get('code',d.get('res_code',d.get('error_code',d.get('errorcode',0))))
   if r.status_code>=400 or code not in (None,0,'0') or d.get('success') is False:
    fail(self.key,code or r.status_code,login=r.status_code==401 or code in (31001,31002,5112,9,'InvalidSessionKey','InvalidAccessToken','AccessTokenExpired'))
   return d
  except requests.exceptions.JSONDecodeError:fail(self.key,'网页验证')
  finally:r.close()
 def auth(self):
  if self.key=='pan123':
   value=self.values.get('authorToken')
   return {'Authorization':'Bearer '+value} if isinstance(value,str) and value else {}
  if self.key=='xunlei':
   pair=next(((k,v) for k,v in self.values.items() if k.startswith('credentials_') and isinstance(v,dict) and v.get('access_token')),None)
   credential=pair[1] if pair else None
   client_id=pair[0][len('credentials_'):] if pair else ''
   captcha=self.values.get('captcha_'+client_id)
   if not isinstance(captcha,dict) or not captcha.get('token'):captcha=None
   device=re.search(r'[a-zA-Z0-9]{32}',str(self.values.get('deviceid','')))
   if not credential or not captcha or not device:fail(self.key,'缺少官方会话',True)
   return {'Authorization':credential.get('token_type','Bearer')+' '+credential['access_token'],'X-Captcha-Token':captcha['token'],'X-Device-Id':device[0],'X-Client-Id':client_id}
  return {}
 def pan123_api(self,path,body=None,params=None):
  # LinkSwift's old /api route now returns 404; use the normal current web route.
  route='/b/api'+path
  return self.api('https://yun.123pan.com'+route,body,params={**pan123_query(route),**(params or {})},
   headers={**self.auth(),'Platform':'web','App-Version':'3','Origin':'https://yun.123pan.com','Referer':'https://yun.123pan.com/'})
 def mobile_headers(self,body):
  authorization=next((c.value for c in self.s.cookies if c.name=='authorization'),'')
  if not authorization:fail(self.key,'缺少官方会话',True)
  stamp=time.strftime('%Y-%m-%d %H:%M:%S',time.gmtime(time.time()+8*3600));nonce=secrets.token_urlsafe(12)[:16]
  return {'Authorization':unquote(authorization),'Caller':'web','CMS-DEVICE':'default','Mcloud-Channel':'1000101',
   'Mcloud-Client':'10701','Mcloud-Sign':stamp+','+nonce+','+mobile_sign(body,stamp,nonce),'Mcloud-Version':'7.14.2',
   'X-DeviceInfo':'||9|7.17.0|edge||||windows 10||zh-CN|||','X-Huawei-ChannelSrc':'10000034','X-Inner-Ntwk':'2',
   'X-M4C-Caller':'PC','X-M4C-Src':'10002','X-SvcType':'1','X-Yun-Api-Version':'v1','X-Yun-App-Channel':'10000034',
   'X-Yun-Channel-Source':'10000034','X-Yun-Client-Info':'||9|7.17.0|edge||||windows 10||zh-CN|||||',
   'X-Yun-Module-Type':'100','X-Yun-Svc-Type':'1','X-Yun-Url-Type':'3'}
 def tianyi_token(self):
  token=self.values.get('accessToken') or self.state.get('_tianyi_token')
  if isinstance(token,str) and token:return token
  url='https://api.cloud.189.cn/open/oauth2/ssoH5.action'
  for _ in range(6):
   r=self.raw(url)
   try:
    target=urljoin(url,r.headers.get('Location',''))
    token=parse_qs(urlsplit(target).query).get('accessToken',[''])[0]
    if token:
     if not providers.official(self.key,target) and not providers.official(self.key,target,api=True):fail(self.key,'认证跳转无效')
     self.state['_tianyi_token']=token;return token
    if r.status_code not in (301,302,303,307,308):break
    if not providers.official(self.key,target,api=True):break
    url=target
   finally:r.close()
  fail(self.key,'未取得官方登录令牌',True)
 def verify(self):
  if self.key=='uc':self.api('https://pc-api.uc.cn/1/clouddrive/file/sort',params={'pr':'UCBrowser','fr':'pc','pdir_fid':'0','_page':1,'_size':1})
  elif self.key=='pan123':
   if not self.auth():fail(self.key,'缺少官方会话',True)
   self.pan123_api('/user/info')
  elif self.key=='xunlei':self.api('https://api-pan.xunlei.com/drive/v1/files',params={'parent_id':'','limit':1},headers=self.auth())
  elif self.key=='mobile':
   body={'parentFileId':'root','pageInfo':{'pageSize':1,'pageCursor':''},'orderBy':'updated_at','orderDirection':'DESC'}
   self.api('https://personal-kd-njs.yun.139.com/hcy/file/list',body,headers=self.mobile_headers(body))
  else:
   data=self.api('https://cloud.189.cn/v2/getUserBriefInfo.action')
   if not data.get('sessionKey'):fail(self.key,'缺少官方登录会话',True)
  return True
 def close(self):
  if self.identity:
   old={(c['name'],c.get('domain',''),c.get('path','/')):c for c in self.state.get('cookies',[])}
   for c in self.s.cookies:
    key=(c.name,c.domain,c.path);original=old.get(key,{})
    old[key]={**original,'name':c.name,'value':c.value,'domain':c.domain,'path':c.path,
     'expires':c.expires or original.get('expires',-1),'httpOnly':original.get('httpOnly',True),
     'secure':bool(c.secure) or original.get('secure',False),'sameSite':original.get('sameSite','Lax')}
   self.state['cookies']=list(old.values());accounts.update_payload(self.identity,self.state)
  self.s.close()
 def uc_share(self,url,password):
  match=re.fullmatch(r'/(?:s|share)/([a-zA-Z0-9_-]{5,100})/?',urlsplit(url).path)
  if not match:raise ValueError('请提供 UC /s/ 分享链接')
  sid=match[1];params={'pr':'UCBrowser','fr':'pc'}
  token=self.api('https://pc-api.uc.cn/1/clouddrive/share/sharepage/token',{'pwd_id':sid,'passcode':password},params=params)['data']['stoken']
  files=[];queue=[('0','')];seen=set()
  while queue:
   parent,prefix=queue.pop(0)
   if parent in seen:continue
   seen.add(parent)
   if len(seen)>100:raise ValueError('分享目录超过100个')
   for page in range(1,23):
    data=self.api('https://pc-api.uc.cn/1/clouddrive/share/sharepage/detail',params={**params,'pwd_id':sid,'stoken':token,'pdir_fid':parent,'_page':page,'_size':100,'_fetch_total':1})['data']
    rows=data.get('list',[])
    for item in rows:
     name=str(item.get('file_name','download.bin'))
     if item.get('file') is False:queue.append((item['fid'],prefix+name+'/'))
     else:files.append({'id':str(item['fid']),'name':name,'relative':prefix+name,'size':int(item['size']),'raw':item,'share_id':sid,'stoken':token})
    if len(files)>2000:raise ValueError('分享超过2000个文件')
    if len(rows)<100:break
   else:raise ValueError('UC分页尚未完整结束，未开始下载')
  return files
 def request(self,path,body=None,params=None):
  if self.key!='uc':raise ValueError('此清理接口仅用于 UC 本次任务')
  return self.api('https://pc-api.uc.cn/1/clouddrive'+path,body,params={'pr':'UCBrowser','fr':'pc',**(params or {})})
 def quark_list(self,parent):
  rows=[]
  for page in range(1,23):
   items=self.request('/file/sort',params={'pdir_fid':parent,'_page':page,'_size':100,'_fetch_total':1}).get('data',{}).get('list',[])
   rows.extend(items)
   if len(items)<100:return rows
  raise ValueError('UC任务目录分页尚未完成')
 def uc_transfer(self,item,job):
  """Own one exact task folder; transfer only the requested original file."""
  import cloud_cleanup
  if not self.identity:fail('uc','转存需要后台账号',True)
  self.verify()
  if not getattr(self,'temp_dest',None):
   dest=self.request('/file',{'pdir_fid':'0','file_name':'网站下载任务-'+job,'dir_path':'','dir_init_lock':False}).get('data',{}).get('fid')
   if not dest:raise ValueError('UC未确认任务临时目录创建成功')
   self.temp_dest=str(dest);cloud_cleanup.register(job,self.identity,'uc',self.temp_dest)
  folder=self.request('/file',{'pdir_fid':self.temp_dest,'file_name':hashlib.sha256(item['relative'].encode()).hexdigest()[:16],'dir_path':'','dir_init_lock':False}).get('data',{}).get('fid')
  if not folder:raise ValueError('UC未确认本次文件目录创建成功')
  data=self.request('/share/sharepage/save',{'fid_list':[item['id']],
   'fid_token_list':[item['raw'].get('share_fid_token','')], 'to_pdir_fid':folder,
   'pwd_id':item['share_id'],'stoken':item['stoken'],'pdir_fid':str(item['raw'].get('pdir_fid','0')),
   'pdir_save_all':False,'exclude_fids':[],'scene':'link'}).get('data',{})
  task=data.get('task_id')
  if task:
   for retry in range(120):
    if self.cancelled():raise Cancelled()
    d=self.request('/task',params={'task_id':task,'retry_index':retry}).get('data',{})
    if d.get('status')==2:break
    if d.get('status')==3:raise ValueError('UC临时转存失败，请检查账号容量与分享权限')
    time.sleep(.5)
   else:raise ValueError('UC临时转存等待超时，未开始下载')
  actual=self.quark_list(str(folder))
  if len(actual)!=1 or actual[0].get('file_name')!=item['name'] or int(actual[0].get('size',-1))!=item['size']:
   raise ValueError('UC转存原文件名称或大小不符，未开始下载')
  return {**item,'id':str(actual[0]['fid']),'_owned':True}
 def pan123_share(self,url,password):
  match=re.fullmatch(r'/s/([a-zA-Z0-9_-]{3,120})(?:\.html)?/?',urlsplit(url).path)
  if not match:raise ValueError('请提供123云盘 /s/ 分享链接')
  sid=match[1];queue=[(0,'')];seen=set();files=[]
  while queue:
   parent,prefix=queue.pop(0)
   if parent in seen:continue
   seen.add(parent)
   if len(seen)>100:raise ValueError('分享目录超过100个')
   cursor='0';cursors=set()
   for page in range(1,23):
    d=self.pan123_api('/share/get',params={'limit':100,'next':'0','orderBy':'file_id','orderDirection':'desc','parentFileId':parent,'Page':page,'shareKey':sid,'SharePwd':password})['data']
    rows=d.get('InfoList',[])
    for item in rows:
     name=str(item['FileName'])
     if item['Type']!=0:queue.append((item['FileId'],prefix+name+'/'))
     else:files.append({'id':str(item['FileId']),'name':name,'relative':prefix+name,'size':int(item['Size']),'raw':item,'share_id':sid})
    if len(files)>2000:raise ValueError('分享超过2000个文件')
    cursor=str(d.get('Next','-1'))
    if cursor=='-1' or not rows:break
   else:raise ValueError('123分页尚未完整结束，未开始下载')
  return files
 def xunlei_headers(self):
  # A public share can be loaded before the SDK adds account authorization.
  # Keep its ordinary captcha/client headers, but always bind own operations
  # to the refreshed encrypted account, never an anonymous share response.
  captured=getattr(self,'_share_headers',None)
  if not captured:return self.auth()
  own={k.lower():v for k,v in self.auth().items()}
  return {**own,**{k.lower():v for k,v in captured.items()},'authorization':own['authorization']}
 def xunlei_delete_task(self,remote,job):
  from linkswift_browser import xunlei_delete_task
  return xunlei_delete_task(self,remote,job)
 def xunlei_list(self,parent):
  rows=[];cursor='';seen=set()
  for _ in range(23):
   d=self.api('https://api-pan.xunlei.com/drive/v1/files',params={'parent_id':parent,'limit':100,'page_token':cursor,'filters':json.dumps({'trashed':{'eq':False}})},headers=self.xunlei_headers())
   rows.extend(d.get('files',[]));cursor=d.get('next_page_token','')
   if not cursor:return rows
   if cursor in seen:raise ValueError('迅雷任务目录分页异常')
   seen.add(cursor)
  raise ValueError('迅雷任务目录分页尚未完成')
 def xunlei_transfer(self,item,job):
  import cloud_cleanup
  if not self.identity:fail('xunlei','转存需要后台账号',True)
  def folder(parent,name):
   d=self.api('https://api-pan.xunlei.com/drive/v1/files',{'kind':'drive#folder','name':name,'parent_id':parent},headers=self.xunlei_headers())
   fid=d.get('id') or d.get('file',{}).get('id')
   if not fid:raise ValueError('迅雷未确认任务临时目录创建成功')
   return str(fid)
  if not getattr(self,'temp_dest',None):
   self.temp_dest=folder('','网站下载任务-'+job)
   cloud_cleanup.register(job,self.identity,'xunlei',self.temp_dest)
  dest=folder(self.temp_dest,hashlib.sha256(item['relative'].encode()).hexdigest()[:16])
  d=self.api('https://api-pan.xunlei.com/drive/v1/share/restore',{'parent_id':dest,'share_id':item['share_id'],
   'pass_code_token':item.get('share_token',''),'ancestor_ids':[],'file_ids':[item['id']],
   'specify_parent_id':True},headers=self.xunlei_headers())
  if d.get('restore_status') not in ('RESTORE_COMPLETE','RESTORE_START'):
   raise ValueError('迅雷未确认本次文件转存任务')
  for _ in range(120):
   if self.cancelled():raise Cancelled()
   actual=self.xunlei_list(dest)
   if actual:
    if len(actual)!=1 or actual[0].get('kind')!='drive#file' or actual[0].get('name')!=item['name'] or int(actual[0].get('size',-1))!=item['size']:
     raise ValueError('迅雷转存原文件名称或大小不符，未开始下载')
    if actual[0].get('phase') in (None,'PHASE_TYPE_COMPLETE'):
     copied={**item,'id':str(actual[0]['id']),'_owned':True}
     md5=actual[0].get('md5_checksum','')
     if re.fullmatch(r'[0-9a-fA-F]{32}',md5):copied['md5']=md5
     return copied
   time.sleep(.5)
  raise ValueError('迅雷临时转存等待超时，未开始下载')
 def prepare(self,url,password):
  if self.key=='uc':return self.uc_share(url,password)
  if self.key=='pan123':return self.pan123_share(url,password)
  from linkswift_shares import share_files
  return share_files(self,url,password)
 def link(self,item):
  k=self.key;raw=item.get('raw',{});headers={'User-Agent':UA,'Referer':providers.LOGIN_URLS[k]}
  if k=='uc':
   body={'fids':[item['id']]}
   if not item.get('_owned'):body.update(fids_token=[raw.get('share_fid_token','')],pwd_id=item['share_id'],stoken=item['stoken'])
   data=self.api('https://pc-api.uc.cn/1/clouddrive/file/download',body,params={'entry':'ft','pr':'UCBrowser','fr':'pc'},headers={'User-Agent':UC_UA}).get('data',[])
   if not data:fail(k,'未返回原文件')
   d=data[0]
   if d.get('size') is not None and int(d['size'])!=item['size']:raise ValueError('UC原文件大小与分享不符')
   url=d.get('download_url');headers.update({'User-Agent':UC_UA,'Cookie':'; '.join(c.name+'='+c.value for c in self.s.cookies),'_cookie_domains':('uc.cn',)})
  elif k=='pan123':
   body={'ShareKey':item['share_id'],'FileID':int(item['id']),'S3keyFlag':raw['S3KeyFlag'],'Size':item['size'],'Etag':raw['Etag']}
   data=self.pan123_api('/share/download/info',body)['data']
   url=data.get('DownloadUrl') or data.get('DownloadURL')
   if url:
    encoded=parse_qs(urlsplit(url).query).get('params',[''])[0]
    if encoded:
     try:url=base64.b64decode(encoded,validate=True).decode('utf-8')
     except (ValueError,UnicodeDecodeError):raise ValueError('123原文件跳转地址无效') from None
  elif k=='tianyi':
   token=self.tianyi_token();stamp=str(int(time.time()*1000))
   params={'fileId':item['id']};signed='AccessToken='+token+'&Timestamp='+stamp
   if item.get('share_id'):params.update(dt=1,shareId=item['share_id']);signed+='&dt=1'
   signed+='&fileId='+item['id']
   if item.get('share_id'):signed+='&shareId='+item['share_id']
   data=self.api('https://api.cloud.189.cn/open/file/getFileDownloadUrl.action',params=params,headers={'Sign-Type':'1','Accesstoken':token,'Timestamp':stamp,'Signature':hashlib.md5(signed.encode()).hexdigest()})
   url=data.get('fileDownloadUrl')
  elif k=='mobile':
   if item.get('_owned'):
    body={'fileId':item['id']};d=self.api('https://personal-kd-njs.yun.139.com/hcy/file/getDownloadUrl',body,headers=self.mobile_headers(body))
    url=d.get('data',{}).get('url')
   else:
    from linkswift_shares import original_link
    url=original_link(self,item)
  else:
   if item.get('_owned'):
    d=self.api('https://api-pan.xunlei.com/drive/v1/files/'+quote(item['id'],safe=''),headers=self.xunlei_headers());url=d.get('web_content_link')
    if d.get('size') is not None and int(d['size'])!=item['size']:raise ValueError('迅雷原文件大小与列表不符')
    gcid=str(d.get('hash','')).lower()
    if re.fullmatch(r'[0-9a-f]{40}',gcid):
     if item.get('gcid') and item['gcid'].lower()!=gcid:raise ValueError('迅雷转存原文件GCID与分享不符')
     item['gcid']=gcid
   else:
    from linkswift_shares import original_link
    url=original_link(self,item)
  if not url or urlsplit(url).scheme not in ('http','https'):fail(k,'未取得原文件地址')
  return url,headers

def validate(key,state,identity=None):
 c=Client(key,state,identity)
 try:
  c.verify()
  if identity:accounts.health(identity,True,'官方账号验证通过；具体下载仍需文件实测')
  return c.state
 finally:c.close()

def run(url,password,out,progress,cancelled):
 from cloud_backend import LOCKS,budget,PlatformError
 key=providers.provider(url);lock=LOCKS[key]
 while not lock.acquire(timeout=.25):
  if cancelled():raise Cancelled()
 c=None
 try:
  identity,payload=accounts.active_payload(key);state=json.loads(payload) if payload else {}
  c=Client(key,state,identity,cancelled)
  password=parse_qs(urlsplit(url).query).get('pwd',parse_qs(urlsplit(url).query).get('password',[password]))[0]
  items=c.prepare(url,password);budget(items,out);paths=[];folders={};progress(items,paths)
  for item in items:
   if cancelled():raise Cancelled()
   relative,original=item['relative'],item['name']
   try:link,headers=c.link(item)
   except PlatformError as error:
    if key!='xunlei' or error.code!='未取得原文件地址':raise
    item=c.xunlei_transfer(item,out.name);link,headers=c.link(item)
   name=hashlib.sha256(relative.encode()).hexdigest()[:10]+'_'+file_download.safe_name(original)
   def update(n):progress(items,paths+[p for p in out.iterdir() if p.name==name+'.part' or p.name.startswith(name+'.part.')])
   try:
    path=file_download.fetch_original(link,out,name,item['size'],update,cancelled,headers,item,prefer_chunks=True,workers=download_tuning.profile(key)['workers'],
     range_size=512*1024 if key=='xunlei' and item['size']<8*1024**2 else download_tuning.profile(key)['range_size'],renew_url=lambda:c.link(item))
   except file_download.TransferHTTPError as error:
    if key!='uc' or error.status!=403 or item.get('_owned'):raise
    item=c.uc_transfer(item,out.name);link,headers=c.link(item)
    path=file_download.fetch_original(link,out,name,item['size'],update,cancelled,headers,item,prefer_chunks=True,workers=download_tuning.profile(key)['workers'],
     range_size=download_tuning.profile(key)['range_size'],renew_url=lambda:c.link(item))
   paths.append(path);folders[path.name]=(file_download.share_folder(relative),original);progress(items,paths)
  if identity:accounts.health(identity,True,'原文件完整下载及校验通过')
  note='官方网盘原文件直下载；未转存'
  if getattr(c,'temp_dest',None):
   import cloud_cleanup
   note=cloud_cleanup.complete(out,identity,key,c.temp_dest,paths,c)
  return file_download.CloudResult(paths,note,folders)
 finally:
  try:
   if c:c.close()
  finally:lock.release()
