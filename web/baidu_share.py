"""Baidu share API adapter. Never treats a listing as a completed download."""
import json
import re
from urllib.parse import urlsplit, parse_qs, unquote
import requests
from common import Cancelled

class AuthorizationRequired(RuntimeError):
    pass

class Share:
    def __init__(self, url, password='', cancelled=lambda: False):
        parsed=urlsplit(url)
        if parsed.hostname not in ('pan.baidu.com','yun.baidu.com'):
            raise ValueError('不是百度网盘分享链接')
        match=re.fullmatch(r'/s/1([\w-]+)',parsed.path.rstrip('/'))
        if not match: raise ValueError('请使用百度 /s/ 开头的完整分享链接')
        self.short=match[1]
        self.url='https://pan.baidu.com/s/1'+self.short
        self.password=parse_qs(parsed.query).get('pwd',[password])[0]
        self.cancelled=cancelled
        self.session=requests.Session()
        self.session.trust_env=False
        self.session.headers.update({'User-Agent':'Mozilla/5.0','Referer':self.url})
        self.secret=''

    def request(self,path,**kwargs):
        if self.cancelled(): raise Cancelled()
        method='POST' if 'data' in kwargs else 'GET'
        response=self.session.request(method,'https://pan.baidu.com'+path,timeout=(8,25),**kwargs)
        response.raise_for_status()
        return response

    def check(self,data,stage):
        code=data.get('errno')
        if code!=0:
            if code in (-20,-6,112,115):
                raise AuthorizationRequired(f'百度要求登录授权或网页验证（{stage}，代码 {code}）；文件尚未下载')
            if code in (-9,-12): raise ValueError('提取码不正确或分享验证已失效')
            raise ValueError(f'百度{stage}失败（代码 {code}），请在原分享页检查有效期或验证提示')
        return data

    def open(self):
        page=self.request('/s/1'+self.short)
        if self.password:
            data=self.check(self.request('/share/verify',params={'surl':self.short,'web':1,'clienttype':0},
                  data={'pwd':self.password,'vcode':'','vcode_str':''}).json(),'验证提取码')
            self.secret=unquote(data.get('randsk',''))
            page=self.request('/s/1'+self.short)
        for block in re.finditer(r'<script[^>]*id=[\"\']locals-data[\"\'][^>]*>(.*?)</script>',page.text,re.S):
            data=json.loads(block[1])
            if 'file_list' in data:
                self.uk=data['share_uk']; self.shareid=data['shareid']
                return data['file_list']
        for match in re.finditer(r'locals\.mset\(',page.text):
            try: data,_=json.JSONDecoder().raw_decode(page.text[match.end():])
            except ValueError: continue
            if 'file_list' in data:
                self.uk=data['share_uk']; self.shareid=data['shareid']
                return data['file_list']
        raise ValueError('无法读取文件列表：分享可能失效、缺少提取码或需要网页验证')

    def files(self):
        pending=list(self.open()); files=[]; seen=set(); directories=0
        # Parent of the shared top-level items; paths below it keep the share's folders.
        self.root_parent=str(pending[0].get('path','')).rsplit('/',1)[0] if pending and pending[0].get('path') else ''
        while pending:
            if self.cancelled(): raise Cancelled()
            item=pending.pop(0)
            identity=str(item['fs_id'])
            if identity in seen: continue
            seen.add(identity)
            if item.get('isdir'):
                directories+=1
                if directories>100: raise ValueError('目录过多，单次最多解析 100 个目录')
                for page in range(1,22):
                    data=self.check(self.request('/share/list',params={'uk':self.uk,'shareid':self.shareid,
                        'dir':item['path'],'page':page,'num':100,'web':1,'order':'name','desc':0}).json(),'读取目录')
                    entries=data.get('list',[]); pending.extend(entries)
                    if len(entries)<100: break
                else: raise ValueError('目录超过单次解析上限，请使用更小的分享目录')
            else: files.append(item)
            if len(files)+len(pending)>2000: raise ValueError('单次最多解析 2000 个文件')
        return files

    def download_url(self,item):
        sign=self.check(self.request('/share/tplconfig',params={'shareid':self.shareid,'uk':self.uk,
            'fields':'sign,timestamp','web':1,'clienttype':0,'app_id':250528}).json(),'获取签名')['data']
        data=self.check(self.request('/api/sharedownload',params={'sign':sign['sign'],
            'timestamp':sign['timestamp'],'web':1,'clienttype':0,'app_id':250528,'channel':'chunlei'},
            data={'encrypt':0,'product':'share','uk':self.uk,'primaryid':self.shareid,
                  'fid_list':json.dumps([item['fs_id']]),'extra':json.dumps({'sekey':self.secret})}).json(),'获取原文件')
        result=data.get('list')
        if isinstance(result,str):
            try:result=json.loads(result)
            except (ValueError,TypeError):
                if result.strip():
                    raise AuthorizationRequired('百度已返回编码后的下载响应，当前直下载解析器尚不兼容；转用已连接账号的后台下载通道，文件尚未完成。这不等同于账号未登录')
                result=None
        if not isinstance(result,list):
            raise AuthorizationRequired('百度未向当前授权返回原文件下载地址；需要使用后台账号转存下载，文件尚未完成')
        for entry in result:
            if str(entry.get('fs_id'))==str(item['fs_id']) and entry.get('dlink'):
                return entry['dlink']
        raise ValueError('百度未返回选中文件的下载地址')

    @staticmethod
    def web_sign(key, value):
        # The signed-in official web page's RC4 signature; uses this account only.
        import base64
        box=list(range(256));j=0;key=key.encode();value=value.encode()
        for i in range(256):
            j=(j+box[i]+key[i%len(key)])%256;box[i],box[j]=box[j],box[i]
        i=j=0;result=bytearray()
        for byte in value:
            i=(i+1)%256;j=(j+box[i])%256;box[i],box[j]=box[j],box[i]
            result.append(byte^box[(box[i]+box[j])%256])
        return base64.b64encode(result).decode()

    def owned_files(self, remote):
        if not re.fullmatch(r'/网站下载任务/[0-9a-f]{12}', remote):
            raise ValueError('下载目录不属于本次临时任务')
        pending=[remote];seen=set();files=[]
        while pending:
            directory=pending.pop(0)
            if directory in seen:continue
            seen.add(directory)
            if len(seen)>100:raise ValueError('临时任务目录过多')
            for page in range(1,23):
                data=self.check(self.request('/api/list',params={'dir':directory,'page':page,'num':100,'order':'name','desc':0,'web':1,'clienttype':0}).json(),'读取临时任务')
                entries=data.get('list',[])
                for item in entries:
                    if not str(item.get('path','')).startswith(remote+'/') or '..' in str(item.get('path','')).split('/'):
                        raise ValueError('平台返回了临时任务以外的文件')
                    if item.get('isdir'):pending.append(item['path'])
                    else:files.append(item)
                if len(files)+len(pending)>2000:raise ValueError('临时任务文件过多')
                if len(entries)<100:break
            else:raise ValueError('临时任务目录超过单次读取上限')
        return files

    def owned_download_url(self, item):
        data=self.check(self.request('/api/gettemplatevariable',params={'fields':json.dumps(['bdstoken','sign1','sign3','timestamp']),'web':1,'clienttype':0,'app_id':250528}).json(),'读取官方网页下载授权')['result']
        if not all(data.get(k) for k in ('bdstoken','sign1','sign3','timestamp')):
            raise AuthorizationRequired('百度未提供完整官方网页下载授权')
        result=self.check(self.request('/api/download',params={'web':1,'clienttype':0,'channel':'chunlei','bdstoken':data['bdstoken']},data={'fidlist':json.dumps([item['fs_id']]),'type':'dlink','sign':self.web_sign(data['sign3'],data['sign1']),'timestamp':data['timestamp']}).json(),'获取临时原文件')
        for entry in result.get('dlink') or []:
            if str(entry.get('fs_id'))==str(item['fs_id']) and entry.get('dlink'):
                return entry['dlink']
        raise AuthorizationRequired('百度未提供临时原文件下载地址')

    def close(self): self.session.close()
