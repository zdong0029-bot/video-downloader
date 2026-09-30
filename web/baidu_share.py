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
        if not isinstance(result,list):
            raise AuthorizationRequired('已读取文件列表，但百度未提供匿名原文件地址；此文件需要后台账号授权后转存下载，不能标记为完成')
        for entry in result:
            if str(entry.get('fs_id'))==str(item['fs_id']) and entry.get('dlink'):
                return entry['dlink']
        raise ValueError('百度未返回选中文件的下载地址')

    def close(self): self.session.close()
