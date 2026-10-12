"""Host-authorized cloud shares. Downloads original files, never preview streams."""
import base64, hashlib, html, json, re, shutil, threading, time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import requests
from common import Cancelled
import cloud_accounts as accounts
import file_download

NAMES = {'baidu': '百度网盘', 'ali': '阿里云盘', 'quark': '夸克网盘'}
LOCKS = {p: threading.RLock() for p in ('ali', 'quark')}
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'

class LoginRequired(ValueError):
    pass

class PlatformError(ValueError):
    def __init__(self, message, code):
        super().__init__(message); self.code = code


def provider(url):
    return next((key for key, name in NAMES.items() if file_download.provider(url) == name), None)


def parse_share(url, password=''):
    u = urlsplit(url)
    key = provider(url)
    if not key or u.username or u.password or u.scheme not in ('http', 'https') or u.port not in (None, 80, 443):
        raise ValueError('请提供百度、阿里或夸克的完整分享链接')
    hosts = {'baidu': ('pan.baidu.com', 'yun.baidu.com'), 'ali': ('www.alipan.com', 'alipan.com', 'www.aliyundrive.com', 'aliyundrive.com'), 'quark': ('pan.quark.cn',)}
    if u.hostname not in hosts[key]:
        raise ValueError('不是支持的官方分享地址')
    m = re.fullmatch(r'/(s|t)/([A-Za-z0-9_-]{5,100})/?', u.path)
    if not m or (m[1] == 't' and key != 'ali'):
        raise ValueError('请粘贴 /s/ 开头的网盘分享链接；阿里也支持 /t/ 快传')
    q = parse_qs(u.query)
    return key, m[2], q.get('pwd', q.get('password', [password]))[0][:32], m[1]


def active_profile(key):
    identity, payload = accounts.active_payload(key)
    if not payload:
        raise LoginRequired(NAMES[key] + '后台账号尚未连接，请管理员在首页「网盘账号」扫码连接一次；访客无需登录')
    return identity, json.loads(payload)


class Client:
    def __init__(self, key, state, identity=None, cancelled=lambda: False):
        self.key, self.state, self.identity, self.cancelled = key, state, identity, cancelled
        self.s = requests.Session(); self.s.trust_env = False
        self.s.headers.update({'User-Agent': UA, 'Accept': 'application/json', 'Referer': 'https://pan.quark.cn/' if key == 'quark' else 'https://www.alipan.com/'})
        for c in state.get('cookies', []):
            domain = c.get('domain', '')
            if key == 'quark' and (domain.lstrip('.') == 'quark.cn' or domain.endswith('.quark.cn')):
                self.s.cookies.set(c['name'], c['value'], domain=domain, path=c.get('path', '/'))
        self.token = None

    def request(self, path, body=None, params=None, host=None, extra=None):
        if self.cancelled(): raise Cancelled()
        base = host or ('https://drive-pc.quark.cn/1/clouddrive' if self.key == 'quark' else 'https://api.alipan.com')
        query = {'pr': 'ucpro', 'fr': 'pc'} if self.key == 'quark' else {}
        query.update(params or {})
        try:
            r = self.s.request('POST' if body is not None else 'GET', base + path, json=body, params=query, headers=extra or {}, timeout=(10, 30), allow_redirects=False)
        except requests.RequestException:
            raise ValueError(NAMES[self.key] + '网络连接失败，请稍后重试') from None
        try: d = r.json()
        except ValueError:
            if r.status_code>=400: raise PlatformError(NAMES[self.key]+'接口返回HTTP '+str(r.status_code)+'，未取得原文件',r.status_code) from None
            raise ValueError(NAMES[self.key] + '返回了网页验证，尚未取得原文件') from None
        code = d.get('code', 0)
        bad = r.status_code >= 400 or (self.key == 'quark' and (d.get('status', 200) >= 400 or code != 0)) or (self.key == 'ali' and bool(code))
        if bad:
            message = str(d.get('message') or '平台拒绝该操作')
            if r.status_code == 401 or code in (31001, 31002, 401, 'AccessTokenInvalid', 'AccessTokenExpired', 'RefreshTokenInvalid') or any(word in message.lower() for word in ('未登录', '登录失效', 'login required', 'invalid token')):
                raise LoginRequired(NAMES[self.key] + '授权已失效，请管理员在首页重新扫码；原账号和已下载文件均保留')
            if self.key=='quark' and code==23018:
                raise PlatformError('夸克官方网页通道限制此文件大小（23018）；账号已登录，需要官方支持的大文件下载通道，不能用预览流代替原文件',code)
            # Do not forward response bodies: they can contain account tokens or signed URLs.
            raise PlatformError(NAMES[self.key] + '操作未通过（代码 ' + str(code or r.status_code) + '）：请在官方分享页检查提取码、有效期、容量或下载权限', code)
        return d

    def ali_auth(self):
        if self.token: return self.token
        token = self.state.get('_ali_token') or {}
        if not token.get('refresh_token'):
            for origin in self.state.get('origins', []):
                if urlsplit(origin.get('origin', '')).hostname not in ('www.alipan.com', 'www.aliyundrive.com'): continue
                for entry in origin.get('localStorage', []):
                    try: candidate = json.loads(entry.get('value', ''))
                    except (ValueError, TypeError): continue
                    if isinstance(candidate, dict) and candidate.get('refresh_token'):
                        token = candidate; break
                if token.get('refresh_token'): break
        if not token.get('refresh_token'): raise LoginRequired('阿里授权缺少登录令牌，请在主机重新扫码并点击保存')
        d = self.request('/v2/account/token', {'refresh_token': token['refresh_token'], 'grant_type': 'refresh_token'}, host='https://auth.alipan.com')
        if not d.get('access_token') or not d.get('refresh_token'): raise LoginRequired('阿里账号验证未通过')
        self.state['_ali_token'] = d
        if self.identity: accounts.update_payload(self.identity, self.state)
        self.token = d
        return d

    def verify(self):
        if self.key == 'quark':
            self.request('/file/sort', params={'pdir_fid': '0', '_page': 1, '_size': 1, '_fetch_total': 1})
        else:
            self.ali_auth()
        return True

    def close(self):
        if self.key == 'quark':
            original = {(c['name'], c.get('domain',''), c.get('path','/')):c for c in self.state.get('cookies', [])}
            for c in self.s.cookies:
                if c.name in ('__puus', '__pus'):
                    original[(c.name, c.domain, c.path)] = {'name':c.name,'value':c.value,'domain':c.domain,'path':c.path,'expires':c.expires or -1,'httpOnly':True,'secure':bool(c.secure),'sameSite':'Lax'}
            self.state['cookies'] = list(original.values())
            if self.identity: accounts.update_payload(self.identity, self.state)
        self.s.close()

    def quark_list(self, parent, share=None):
        result = []
        for page in range(1, 23):
            query = {'pdir_fid': parent, '_page': page, '_size': 100, '_fetch_total': 1}
            path = '/file/sort'
            if share:
                query.update({'pwd_id':share[0], 'stoken':share[1]}); path = '/share/sharepage/detail'
            d = self.request(path, params=query)
            items = d.get('data', {}).get('list', [])
            result.extend(items)
            if len(items) < 100: return result
        raise ValueError('目录文件过多，请分享更小的目录')

    def quark_tree(self, roots, share=None):
        queue = [(f, '') for f in roots]; files = []; dirs = 0; seen = set()
        while queue:
            item, parent = queue.pop(0)
            fid = str(item.get('fid', ''))
            if not fid or fid in seen: continue
            seen.add(fid)
            name = html.unescape(str(item.get('file_name', 'download.bin')))
            relative = parent + name
            if item.get('dir') or item.get('file_type') == 0:
                dirs += 1
                if dirs > 100: raise ValueError('单个分享最多解析100个目录')
                queue.extend((x, relative + '/') for x in self.quark_list(fid, share))
            else:
                files.append({'id':fid, 'name':name, 'relative':relative, 'size':int(item.get('size',0)), 'provider_md5':item.get('md5','')})
            if len(files) + len(queue) > 2000: raise ValueError('单个分享最多解析2000个文件')
        return files

    def quark_prepare(self, share_id, password, job_id):
        token = self.request('/share/sharepage/token', {'pwd_id': share_id, 'passcode': password})['data']['stoken']
        roots = self.quark_list('0', (share_id, token))
        expected = self.quark_tree(roots, (share_id, token)); budget(expected)
        self.verify()
        # An account may download its own share directly. Quark rejects saving
        # one's own share with 41017, so try its original file permission first.
        try:
            direct = self.request('/file/download', {'fids':[expected[0]['id']]})['data']
        except PlatformError:
            direct = []
        if direct and direct[0].get('download_url'):
            expected[0]['_direct_download'] = direct[0]
            return expected
        dest = self.request('/file', {'pdir_fid':'0', 'file_name':'网站下载任务-' + job_id, 'dir_path':'', 'dir_init_lock':False})['data']['fid']
        self.temp_dest = dest
        import cloud_cleanup
        cloud_cleanup.register(job_id,self.identity,'quark',dest)
        task = self.request('/share/sharepage/save', {'fid_list':[f['fid'] for f in roots], 'fid_token_list':[f.get('share_fid_token','') for f in roots], 'to_pdir_fid':dest, 'pwd_id':share_id, 'stoken':token, 'pdir_fid':'0', 'pdir_save_all':False, 'exclude_fids':[], 'scene':'link'})['data'].get('task_id')
        if task:
            for retry in range(120):
                d = self.request('/task', params={'task_id':task,'retry_index':retry}).get('data',{})
                if d.get('status') == 2: break
                if d.get('status') == 3: raise ValueError('夸克转存失败，请检查后台账号容量和分享权限')
                if self.cancelled(): raise Cancelled()
                time.sleep(.5)
            else: raise ValueError('夸克转存等待超时，尚未开始下载')
        actual = self.quark_tree(self.quark_list(dest))
        # Check by path and size. A successful transfer task alone is insufficient.
        expected_map = {(f['relative'], f['size']) for f in expected}
        if expected_map != {(f['relative'],f['size']) for f in actual} or len(expected) != len(actual):
            raise ValueError('夸克转存文件与分享列表不一致，未开始下载')
        return actual

    def ali_prepare(self, share_id, password):
        token = self.ali_auth()['access_token']
        share = self.request('/v2/share_link/get_share_token', {'share_id':share_id, 'share_pwd':password})['share_token']
        self.share_headers = {'Authorization':'Bearer '+token, 'x-share-token':share, 'X-Canary':'client=web,app=share,version=v2.3.1'}
        queue = [('root','')]; files = []; seen = set()
        while queue:
            parent, prefix = queue.pop(0)
            if parent in seen: continue
            seen.add(parent)
            if len(seen) > 100: raise ValueError('单个分享最多解析100个目录')
            marker = ''; markers = set()
            for page in range(23):
                d = self.request('/adrive/v3/file/list', {'share_id':share_id, 'parent_file_id':parent, 'limit':100, 'marker':marker, 'order_by':'name', 'order_direction':'ASC'}, extra=self.share_headers)
                for f in d.get('items',[]):
                    name = str(f.get('name','download.bin'))
                    if f.get('type') == 'folder': queue.append((f['file_id'],prefix+name+'/'))
                    else: files.append({'id':f['file_id'],'name':name,'relative':prefix+name,'size':int(f.get('size',0)),'drive_id':f.get('drive_id',''),'sha1':f.get('content_hash','') if str(f.get('content_hash_name','')).lower() == 'sha1' else ''})
                marker = d.get('next_marker','')
                if not marker: break
                if marker in markers: raise ValueError('阿里文件分页异常，请重试')
                markers.add(marker)
            else: raise ValueError('目录文件过多，请分享更小的目录')
            if len(files) + len(queue) > 2000: raise ValueError('单个分享最多解析2000个文件')
        budget(files)
        return files

    def ali_private(self,path,body):
        token=self.ali_auth()
        return self.request(path,body,extra={'Authorization':'Bearer '+token['access_token'],'X-Canary':'client=web,app=share,version=v2.3.1'})

    def ali_transfer(self,items,share_id,job_id):
        drive=self.ali_auth().get('default_drive_id')
        if not drive: raise ValueError('阿里未提供账号网盘标识，请重新扫码')
        folder=self.ali_private('/adrive/v2/file/createWithFolders',{'drive_id':drive,'parent_file_id':'root','name':'网站下载任务-'+job_id,'type':'folder','check_name_mode':'refuse'})
        dest=folder.get('file_id')
        if not dest:raise ValueError('阿里未确认任务临时目录创建成功')
        self.temp_dest=drive+':'+dest
        import cloud_cleanup
        cloud_cleanup.register(job_id,self.identity,'ali',self.temp_dest)
        saved=[]
        for item in items:
            if self.cancelled():raise Cancelled()
            data=self.request('/adrive/v4/batch',{'requests':[{'body':{'auto_rename':True,'file_id':item['id'],'share_id':share_id,'to_parent_file_id':dest,'to_drive_id':drive},'headers':{'Content-Type':'application/json'},'id':'0','method':'POST','url':'/file/copy'}],'resource':'file'},extra=self.share_headers)
            responses=data.get('responses',[])
            if len(responses)!=1 or responses[0].get('status')!=201:raise ValueError('阿里转存未通过，临时目录保留；请检查容量或分享权限')
            result=responses[0].get('body',{})
            if not result.get('file_id'):raise ValueError('阿里未返回转存文件标识')
            actual=self.ali_private('/v2/file/get',{'drive_id':drive,'file_id':result['file_id']})
            if actual.get('type')!='file' or int(actual.get('size',-1))!=item['size']:raise ValueError('阿里转存文件大小不符，尚未开始下载')
            saved.append({**item,'id':result['file_id'],'drive_id':drive,'_owned':True})
        return saved

    def link(self, item, share_id):
        if self.key == 'quark':
            data = [item.pop('_direct_download')] if item.get('_direct_download') else self.request('/file/download', {'fids':[item['id']]})['data']
            if not data or not data[0].get('download_url'): raise ValueError('夸克未提供原文件下载地址')
            # Quark's API md5 is not the raw file digest for this provider.
            # The original CDN Content-MD5 is verified by fetch_original.
            if data[0].get('md5'): item['provider_md5'] = data[0]['md5']
            cookie = '; '.join(c.name + '=' + c.value for c in self.s.cookies if c.domain.lstrip('.') == 'quark.cn' or c.domain.endswith('.quark.cn'))
            return data[0]['download_url'], {'User-Agent':UA,'Referer':'https://pan.quark.cn/','Cookie':cookie}
        if item.get('_owned'):
            d=self.ali_private('/v2/file/get_download_url',{'drive_id':item['drive_id'],'file_id':item['id'],'expire_sec':600})
            if d.get('size') is not None and int(d['size'])!=item['size']:raise ValueError('阿里原文件大小与分享列表不符')
            if str(d.get('content_hash_name','')).lower()=='sha1' and re.fullmatch(r'[0-9a-fA-F]{40}',str(d.get('content_hash',''))):
                if item.get('sha1') and item['sha1'].lower()!=d['content_hash'].lower():raise ValueError('阿里原文件来源SHA1不符')
                item['sha1']=d['content_hash']
            url=d.get('cdn_url') or d.get('url') or d.get('download_url')
            if not url:raise ValueError('阿里未返回原文件下载地址')
            return url,{'User-Agent':UA,'Referer':'https://www.aliyundrive.com/'}
        d = self.request('/v2/file/get_share_link_download_url', {'share_id':share_id,'file_id':item['id'],'drive_id':item.get('drive_id',''),'expire_sec':600}, extra=self.share_headers)
        if not d.get('download_url'): raise ValueError('阿里未提供原文件下载地址，不能用转码预览替代')
        return d['download_url'], {'User-Agent':UA,'Referer':'https://www.alipan.com/'}


def budget(files, out=None):
    if not files: raise ValueError('分享中没有可下载的文件')
    total = sum(f['size'] for f in files)
    if total > file_download.LIMIT: raise ValueError('单个分享任务暂限10GB，请分享更小的目录')
    if out and total > shutil.disk_usage(out).free - 512*1024**2: raise ValueError('主机可用空间不足')


def validate(key, state, identity=None):
    lock = LOCKS[key]
    if not lock.acquire(blocking=False): raise ValueError(NAMES[key] + '正在下载，请完成或取消后检测 / 更换账号')
    c = Client(key, state, identity)
    try:
        c.verify()
        if identity: accounts.health(identity, True, '官方账号验证通过')
        return c.state
    except LoginRequired:
        if identity: accounts.health(identity, False, '登录授权已失效，请重新扫码')
        raise
    finally:
        try: c.close()
        finally: lock.release()


def run(url, password, out, progress, cancelled):
    key, share_id, password, kind = parse_share(url, password)
    if key == 'baidu':
        import baidu_backend
        return baidu_backend.run(url, password, out, progress, cancelled)
    c = None
    lock = LOCKS[key]
    while not lock.acquire(timeout=.5):
        if cancelled(): raise Cancelled()
    try:
        identity, state = active_profile(key)
        c = Client(key, state, identity, cancelled)
        if kind == 't':
            from cloud_browser import download_transport
            files = download_transport(url, c, out, progress, cancelled)
            accounts.health(identity, True, '阿里快传原文件下载通过')
            return file_download.CloudResult(files, '阿里快传原文件下载；未转存')
        items = c.quark_prepare(share_id, password, out.name) if key == 'quark' else c.ali_prepare(share_id, password)
        budget(items, out); accounts.health(identity, True, '官方账号验证通过')
        first_link=None
        if key=='ali':
            try:first_link=c.link(items[0],share_id)
            except PlatformError as error:
                if error.code not in (403,410,'Forbidden','NotFound.File','InvalidParameter.ShareId'):raise
                items=c.ali_transfer(items,share_id,out.name)
        paths = []
        progress(items, paths)
        for item in items:
            if cancelled(): raise Cancelled()
            link, headers = first_link if first_link is not None else c.link(item, share_id)
            first_link=None
            prefix = hashlib.sha256(item['relative'].encode()).hexdigest()[:10]
            name = prefix + '_' + file_download.safe_name(item['name'])
            def report(size):
                progress(items, paths + [p for p in out.iterdir() if p.name==name+'.part' or p.name.startswith(name+'.part.')])
            target = file_download.fetch_original(link, out, name, item['size'], report, cancelled, headers, item)
            paths.append(target);progress(items, paths)
        note = '网盘原文件直下载；未转存'
        if getattr(c,'temp_dest',None):
            import cloud_cleanup
            note = cloud_cleanup.complete(out, identity, key, c.temp_dest, paths, c)
        accounts.health(identity, True, '原文件下载完整性验证通过')
        return file_download.CloudResult(paths, note)
    except LoginRequired:
        if c and c.identity: accounts.health(c.identity, False, '登录授权已失效，请重新扫码')
        raise
    finally:
        try:
            if c: c.close()
        finally: lock.release()
