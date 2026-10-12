"""Official hosts and account entry points for the LinkSwift-supported providers."""
from urllib.parse import urlsplit
PROVIDERS={'baidu':'百度网盘','ali':'阿里云盘','quark':'夸克网盘','uc':'UC网盘','pan123':'123云盘','tianyi':'天翼云盘','mobile':'中国移动云盘','xunlei':'迅雷云盘'}
DOMAINS={'baidu':('pan.baidu.com','yun.baidu.com'), 'ali':('alipan.com','aliyundrive.com'),
 'quark':('pan.quark.cn',),'uc':('drive.uc.cn',),'pan123':('123pan.com','123pan.cn','123684.com','123865.com','123952.com','123912.com'),
 'tianyi':('cloud.189.cn',),'mobile':('yun.139.com','caiyun.139.com'),'xunlei':('pan.xunlei.com',)}
LOGIN_URLS={'baidu':'https://pan.baidu.com/disk/main','ali':'https://www.alipan.com/','quark':'https://pan.quark.cn/',
 'uc':'https://drive.uc.cn/','pan123':'https://yun.123pan.com/','tianyi':'https://cloud.189.cn/web/main/','mobile':'https://yun.139.com/','xunlei':'https://pan.xunlei.com/'}
API_DOMAINS={'uc':('pc-api.uc.cn','drive-h.uc.cn'),'pan123':('www.123pan.com','www.123pan.cn','api.123pan.cn','yun.123pan.com'),
 'tianyi':('cloud.189.cn','api.cloud.189.cn'),'mobile':('yun.139.com','caiyun.139.com'),'xunlei':('api-pan.xunlei.com','xluser-ssl.xunlei.com')}
SOURCE='https://github.com/hmjz100/LinkSwift'
def provider(url):
 try:u=urlsplit(url);host=(u.hostname or '').lower();port=u.port
 except ValueError:return None
 if u.scheme not in ('https','http') or u.username or u.password or port not in (None,80,443):return None
 return next((key for key,domains in DOMAINS.items() if any(host==d or host.endswith('.'+d) for d in domains)),None)
def official(key,url,api=False):
 try:u=urlsplit(url);host=(u.hostname or '').lower();port=u.port
 except ValueError:return False
 domains=API_DOMAINS.get(key,()) if api else DOMAINS.get(key,())
 return u.scheme=='https' and not u.username and not u.password and port in (None,443) and any(host==d or host.endswith('.'+d) for d in domains)
