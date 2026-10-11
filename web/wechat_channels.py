"""Authorized WeChat Channels streams; preserve original encoded video.
Protocol reference: oliver-zch/wx-video-channel-download (MIT).
ISAAC64 is the ordinary playback transform using the API-provided decodeKey.
"""
import json,shutil,subprocess,struct
from urllib.parse import urlsplit
import requests
import file_download,video_accounts
from common import Cancelled
MASK=(1<<64)-1
class Isaac64:
    def __init__(self,key):
        if not isinstance(key,int) or not 0<=key<=MASK:raise ValueError('视频号播放密钥格式无效')
        self.results=[key]+[0]*255;self.mem=[0]*256;self.a=self.b=self.c=0;v=[0x9e3779b97f4a7c13]*8
        for _ in range(4):v=self.mix(v)
        for source in (self.results,self.mem):
            for i in range(0,256,8):
                v=self.mix([(v[j]+source[i+j])&MASK for j in range(8)])
                self.mem[i:i+8]=v
        self.generate();self.index=255
    @staticmethod
    def mix(v):
        a,b,c,d,e,f,g,h=v
        a=(a-e)&MASK;f^=h>>9;h=(h+a)&MASK
        b=(b-f)&MASK;g^=(a<<9)&MASK;a=(a+b)&MASK
        c=(c-g)&MASK;h^=b>>23;b=(b+c)&MASK
        d=(d-h)&MASK;a^=(c<<15)&MASK;c=(c+d)&MASK
        e=(e-a)&MASK;b^=d>>14;d=(d+e)&MASK
        f=(f-b)&MASK;c^=(e<<20)&MASK;e=(e+f)&MASK
        g=(g-c)&MASK;d^=f>>17;f=(f+g)&MASK
        h=(h-d)&MASK;e^=(g<<14)&MASK;g=(g+h)&MASK
        return [a,b,c,d,e,f,g,h]
    def generate(self):
        self.c=(self.c+1)&MASK;self.b=(self.b+self.c)&MASK
        for i in range(256):
            if i%4==0:self.a=(~(self.a^(self.a<<21)))&MASK
            elif i%4==1:self.a^=self.a>>5
            elif i%4==2:self.a=(self.a^(self.a<<12))&MASK
            else:self.a^=self.a>>33
            self.a=(self.a+self.mem[(i+128)%256])&MASK;x=self.mem[i]
            y=(self.mem[(x>>3)%256]+self.a+self.b)&MASK;self.mem[i]=y
            self.b=(self.mem[(y>>11)%256]+x)&MASK;self.results[i]=self.b
    def random(self):
        result=self.results[self.index]
        if self.index==0:self.generate();self.index=255
        else:self.index-=1
        return result

def decode_head(data,key):
    result=bytearray(data);rng=Isaac64(key)
    for i in range(0,min(len(result),131072),8):
        block=struct.pack('>Q',rng.random())
        for j in range(min(8,len(result)-i,131072-i)):result[i+j]^=block[j]
    return bytes(result)

def resolve(url):
    if not video_accounts.status().get('connected'):raise ValueError('视频号后台授权尚未连接，管理员需在原首页连接元宝官方账号')
    parsed,token,eid=video_accounts.parse(url,video_accounts.load())
    with requests.Session() as session:
        session.trust_env=False
        r=session.post('https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info',json={'baseReq':{'generalToken':token},'exportId':eid},headers={'User-Agent':video_accounts.UA,'Origin':'https://channels.weixin.qq.com','Referer':parsed['playable_url']},timeout=(8,30))
        if r.status_code not in (200,201):raise ValueError('视频号信息接口返回 HTTP '+str(r.status_code))
        data=r.json()
    if data.get('errCode')!=0:raise ValueError('视频号未提供播放信息（代码 '+str(data.get('errCode'))+'）')
    feed=(data.get('data') or {}).get('feedInfo') or {}
    if feed.get('drm') or feed.get('drmInfo'):raise ValueError('该视频需要官方版权授权播放，不能使用普通下载')
    # Prefer the original endpoint; never rewrite quality or authorization parameters.
    candidates=[]
    for name in ('originVideoUrl','videoUrl','h265VideoInfo','h264VideoInfo'):
        value=feed.get(name);item=value if isinstance(value,dict) else {'videoUrl':value}
        if item.get('videoUrl'):
            source=item['videoUrl'];p=urlsplit(source)
            if p.scheme!='https' or not p.hostname or not any(p.hostname==h or p.hostname.endswith('.'+h) for h in ('qq.com','weixin.qq.com','qpic.cn','wxapp.tc.qq.com')):raise ValueError('视频号播放地址不是官方 CDN')
            rank=(int(item.get('width') or 0)*int(item.get('height') or 0),1 if name=='originVideoUrl' else 0,int(item.get('bitrate') or 0))
            candidates.append((rank,source))
    if not candidates:raise ValueError('平台只返回预览文字或封面，未授权提供视频流；文件未下载')
    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    return {'url':candidates[0][1],'key':int(feed.get('decodeKey') or 0),'title':feed.get('description') or parsed.get('desc') or '视频号视频','feed':feed}

def download(url,out,progress,cancelled):
    info=resolve(url);temporary=out/'channels.mp4.part';target=out/(file_download.safe_name(info['title'][:90])+'.mp4')
    conn,response,_=file_download.open_public(info['url'],{'User-Agent':video_accounts.UA,'Referer':'https://channels.weixin.qq.com/'})
    try:
        if (response.getheader('Content-Type') or '').split(';')[0] in ('text/html','application/json'):raise ValueError('视频号返回网页，未提供原视频')
        total=int(response.getheader('Content-Length') or 0);budget=min(file_download.LIMIT,max(0,shutil.disk_usage(out).free-512*1024**2));size=0
        if total>budget:raise ValueError('视频超过可用空间或10GB限制')
        with temporary.open('wb') as f:
            while True:
                if cancelled():raise Cancelled()
                block=response.read(256*1024)
                if not block:break
                size+=len(block)
                if size>budget:raise ValueError('视频超过可用空间或10GB限制')
                f.write(block);progress(size,total)
        if not size or total and size!=total:raise ValueError('视频号传输未完成，请重试')
        with temporary.open('r+b') as f:
            head=f.read(131072)
            if head[4:8]!=b'ftyp':
                if not info['key']:raise ValueError('平台未提供正常播放所需的解码信息')
                f.seek(0);f.write(decode_head(head,info['key']))
        probe=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_type,width,height:format=duration','-of','json',str(temporary)],capture_output=True,timeout=25,creationflags=subprocess.CREATE_NO_WINDOW)
        if probe.returncode:raise ValueError('视频号文件媒体校验失败，不能标记完成')
        media=json.loads(probe.stdout);stream=next((s for s in media.get('streams',[]) if s.get('codec_type')=='video'),None)
        if not stream:raise ValueError('视频号没有有效视频轨道')
        test=subprocess.run(['ffmpeg','-v','error','-i',str(temporary),'-t','1','-f','null','-'],capture_output=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
        if test.returncode:raise ValueError('视频号文件解码校验失败，不能标记完成')
        if cancelled():raise Cancelled()
        temporary.replace(target)
        return info['title'],target,stream
    finally:response.close();conn.close();temporary.unlink(missing_ok=True)
