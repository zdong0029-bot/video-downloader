"""Normal official share flows that LinkSwift leaves incomplete.

Mobile protocol reference: OpenListTeam/OpenList drivers/139/util.go.
Only download URLs are accepted. Preview and transcoded media are excluded.
"""
import base64,json,re,secrets
from urllib.parse import urlsplit,parse_qs,quote
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad,unpad
from xml.etree import ElementTree
import cloud_providers as providers

MOBILE_KEY=b'PVGDwmcvfs1uV3d1'  # Public web protocol constant, not an account secret.
MOBILE_BASE='https://share-kd-njs.yun.139.com/yun-share/richlifeApp/devapp/IOutLink/'

def tianyi_xml(raw):
    if len(raw)>5*1024**2 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('天翼响应格式无效')
    try:root=ElementTree.fromstring(raw)
    except ElementTree.ParseError:raise ValueError('天翼未返回有效文件信息') from None
    def node(element):
        if not len(element):
            text=element.text or ''
            if text in ('true','false'):return text=='true'
            return text
        data={}
        for child in element:
            tag=child.tag.rsplit('}',1)[-1];value=node(child)
            if tag in data:
                if not isinstance(data[tag],list):data[tag]=[data[tag]]
                data[tag].append(value)
            else:data[tag]=value
        return data
    result=node(root)
    if not isinstance(result,dict):raise ValueError('天翼响应格式无效')
    return result

def mobile_authorization(client):
    from urllib.parse import unquote
    value=next((c.value for c in client.s.cookies if c.name=='authorization'),'')
    value=unquote(value)
    if value.lower().startswith('basic '):value=value[6:]
    try:
        parts=base64.b64decode(value,validate=True).decode().split(':',2)
        account=parts[1] if len(parts)==3 and parts[1] and parts[2] else ''
    except (ValueError,UnicodeDecodeError):account=''
    return value,account

def mobile_call(client,path,body):
    from linkswift_cloud import fail
    auth,account=mobile_authorization(client)
    iv=secrets.token_bytes(16)
    plaintext=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    encoded=base64.b64encode(iv+AES.new(MOBILE_KEY,AES.MODE_CBC,iv).encrypt(pad(plaintext,16))).decode()
    headers={'Accept':'application/json, text/plain, */*','Content-Type':'application/json;charset=UTF-8',
      'X-Deviceinfo':'||9|12.27.0|firefox|140.0|||linux unknown|1920X526|zh-CN|||',
      'hcy-cool-flag':'1','CMS-DEVICE':'default','x-m4c-caller':'PC','X-Yun-Api-Version':'v1',
      'Origin':'https://yun.139.com','Referer':'https://yun.139.com/'}
    if auth:headers['Authorization']='Basic '+auth
    r=client.raw(MOBILE_BASE+path,form=encoded,headers=headers)
    try:
        if r.status_code!=200:fail('mobile',r.status_code,r.status_code==401)
        if len(r.content)>5*1024**2:raise ValueError('移动云盘响应过大')
        data=r.content.strip()
        if data.startswith(b'{'):result=json.loads(data)
        else:
            binary=base64.b64decode(data,validate=True)
            if len(binary)<32 or len(binary)%16:raise ValueError('移动云盘响应格式错误')
            result=json.loads(unpad(AES.new(MOBILE_KEY,AES.MODE_CBC,binary[:16]).decrypt(binary[16:]),16))
        if not isinstance(result,dict) or result.get('success') is not True:
            fail('mobile',result.get('code','响应未通过') if isinstance(result,dict) else '响应未通过')
        return result
    except (UnicodeDecodeError,json.JSONDecodeError):raise ValueError('移动云盘未返回有效文件信息') from None
    finally:r.close()

def mobile_id(url):
    u=urlsplit(url);q=parse_qs(u.query);sid=q.get('linkID',q.get('linkId',['']))[0]
    if not sid:
        m=re.fullmatch(r'/w/i/([A-Za-z0-9_-]{5,100})/?',u.path)
        if m:sid=m[1]
        elif u.path in ('/m/i','/m/i/') and re.fullmatch(r'[A-Za-z0-9_-]{5,100}',u.query):sid=u.query
    if not re.fullmatch(r'[A-Za-z0-9_-]{5,100}',sid):raise ValueError('请提供移动云盘完整分享链接（含 linkID 或 /w/i/）')
    return sid

def mobile_files(client,url,password):
    sid=mobile_id(url);_,account=mobile_authorization(client)
    queue=[('root','')];seen=set();files=[]
    while queue:
        parent,prefix=queue.pop(0)
        if parent in seen:continue
        seen.add(parent)
        if len(seen)>100:raise ValueError('分享目录超过100个')
        d=mobile_call(client,'getOutLinkInfoV6',{'getOutLinkInfoReq':{'account':account,'linkID':sid,'passwd':password,'pCaID':parent}}).get('data')
        if not isinstance(d,dict):raise ValueError('移动云盘未返回分享文件列表，请确认链接有效')
        for folder in d.get('caLst',[]):queue.append((folder['caId'],prefix+str(folder['caName'])+'/'))
        for f in d.get('coLst',[]):
            files.append({'id':str(f['coId']),'name':str(f['coName']),'relative':prefix+str(f['coName']),
              'size':int(f['coSize']),'share_id':sid,'share_password':password})
        if len(files)>2000:raise ValueError('分享超过2000个文件')
    return files

def mobile_link(client,item):
    from linkswift_cloud import fail
    _,account=mobile_authorization(client)
    if not account:fail('mobile','分享原文件下载需要后台账号',True)
    d=mobile_call(client,'dlFromOutLinkV3',{'dlFromOutLinkReqV3':{'account':account,'linkID':item['share_id'],
      'passwd':item.get('share_password',''),'coIDLst':{'item':[item['id']]}}})['data']
    # presentURL is a preview and must never be considered here.
    return d.get('extInfo',{}).get('cdnDownloadUrl') or d.get('redrUrl') or d.get('downloadURL')

def tianyi_files(client,url,password):
    u=urlsplit(url);q=parse_qs(u.query)
    code=q.get('code',[''])[0]
    if not code:
        m=re.fullmatch(r'/t/([A-Za-z0-9]{5,100})/?',u.path)
        if m:code=m[1]
    if not re.fullmatch(r'[A-Za-z0-9]{5,100}',code):raise ValueError('请提供天翼云盘 /t/ 或 web/share?code= 分享链接')
    client.s.headers['Referer']='https://cloud.189.cn/web/share?code='+code
    info=client.api('https://cloud.189.cn/api/open/share/getShareInfoByCodeV2.action',params={'shareCode':code})
    if info.get('needAccessCode'):
        if not password:raise ValueError('此天翼分享需要提取码，请粘贴含提取码的分享文字')
        checked=client.api('https://cloud.189.cn/api/open/share/checkAccessCode.action',params={'shareCode':code,'accessCode':password})
        info.update({k:v for k,v in checked.items() if k in ('shareId','fileId','shareMode')})
    sid=str(info.get('shareId') or '')
    if not sid:raise ValueError('天翼未返回分享 ID，未开始下载')
    if not info.get('isFolder'):
        return [{'id':str(info['fileId']),'name':str(info['fileName']),'relative':str(info['fileName']),
          'size':int(info['fileSize']),'share_id':sid}]
    queue=[(str(info['fileId']),'')];seen=set();files=[]
    while queue:
        parent,prefix=queue.pop(0)
        if parent in seen:continue
        seen.add(parent)
        if len(seen)>100:raise ValueError('分享目录超过100个')
        for page in range(1,35):
            d=client.api('https://cloud.189.cn/api/open/share/listShareDir.action',params={'pageNum':page,'pageSize':60,
              'shareId':sid,'shareMode':info.get('shareMode',1),'fileId':parent,'shareDirFileId':info['fileId'],
              'isFolder':'true','iconOption':5,'orderBy':'filename','descending':'false','accessCode':password})
            listing=d.get('fileListAO',{});rows=listing.get('fileList',[]);folders=listing.get('folderList',[])
            for folder in folders:queue.append((str(folder['id']),prefix+str(folder['name'])+'/'))
            for f in rows:
                item={'id':str(f['id']),'name':str(f['name']),'relative':prefix+str(f['name']),'size':int(f['size']),'share_id':sid}
                if re.fullmatch(r'[a-fA-F0-9]{32}',str(f.get('md5',''))):item['md5']=f['md5']
                files.append(item)
            if len(files)>2000:raise ValueError('分享超过2000个文件')
            if len(rows)+len(folders)<60:break
        else:raise ValueError('天翼目录分页未完整结束')
    return files

def xunlei_files(client,url,password):
    from linkswift_cloud import fail
    m=re.fullmatch(r'/s/([A-Za-z0-9_-]{5,100})/?',urlsplit(url).path)
    if not m:raise ValueError('请提供迅雷 /s/ 分享链接')
    sid=m[1]
    from linkswift_browser import xunlei_share_context
    d=xunlei_share_context(client,url,sid)
    if d.get('share_status') not in ('OK','SHARE_STATUS_OK',None,''):
        raise ValueError('迅雷分享已失效、未通过提取码或没有下载权限')
    token=d.get('pass_code_token','');queue=[('', '', d)];seen=set();files=[]
    while queue:
        parent,prefix,page_data=queue.pop(0)
        if parent in seen:continue
        seen.add(parent)
        if len(seen)>100:raise ValueError('分享目录超过100个')
        if page_data is None:
            page_data=client.api('https://api-pan.xunlei.com/drive/v1/share/detail',params={'share_id':sid,
              'parent_id':parent,'pass_code_token':token,'limit':100},headers=client._share_headers)
        cursors=set()
        for _ in range(22):
            for f in page_data.get('files',[]):
                name=str(f['name'])
                if f.get('kind')=='drive#folder':queue.append((str(f['id']),prefix+name+'/',None))
                elif f.get('kind')=='drive#file':
                    item={'id':str(f['id']),'name':name,'relative':prefix+name,'size':int(f['size']),
                      'share_id':sid,'share_token':token}
                    if re.fullmatch(r'[0-9a-fA-F]{40}',str(f.get('hash',''))):item['gcid']=f['hash'].lower()
                    files.append(item)
            if len(files)>2000:raise ValueError('分享超过2000个文件')
            cursor=page_data.get('next_page_token','')
            if not cursor:break
            if cursor in cursors:raise ValueError('迅雷分页循环，未开始下载')
            cursors.add(cursor)
            page_data=client.api('https://api-pan.xunlei.com/drive/v1/share/detail',params={'share_id':sid,'parent_id':parent,
              'pass_code_token':token,'limit':100,'page_token':cursor},headers=client._share_headers)
        else:raise ValueError('迅雷分页尚未完整结束')
    return files

def xunlei_link(client,item):
    from linkswift_cloud import fail
    d=client.api('https://api-pan.xunlei.com/drive/v1/share/file_info',params={'share_id':item['share_id'],
       'file_id':item['id'],'pass_code_token':item.get('share_token','')},headers=getattr(client,'_share_headers',None) or client.auth())
    f=d.get('file_info',d)
    if f.get('size') is not None and int(f['size'])!=item['size']:raise ValueError('迅雷原文件大小与分享不符')
    # medias and play_url are transcoded playback resources, not originals.
    return f.get('web_content_link')

def share_files(client,url,password):
    if not providers.official(client.key,url):raise ValueError('不是此网盘的官方分享链接')
    return {'mobile':mobile_files,'tianyi':tianyi_files,'xunlei':xunlei_files}[client.key](client,url,password)

def original_link(client,item):
    return {'mobile':mobile_link,'xunlei':xunlei_link}[client.key](client,item)
