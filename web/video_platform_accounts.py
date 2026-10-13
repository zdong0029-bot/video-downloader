"""Explicitly authorized sessions; platform expiry is never extended locally."""
import base64
import hashlib
import json
import time
import threading
import uuid
import msvcrt
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from cloud_accounts import crypt

ROOT = Path(__file__).resolve().parent / 'data' / 'video-accounts'
PROVIDERS = {
    'kuaishou': ('\u5feb\u624b', 'https://www.kuaishou.com/brilliant', ('kuaishou.com',)),
    'xiaohongshu': ('\u5c0f\u7ea2\u4e66', 'https://www.xiaohongshu.com/explore', ('xiaohongshu.com',)),
    'tiktok': ('TikTok', 'https://www.tiktok.com/login', ('tiktok.com',)),
    'bilibili': ('B站','https://www.bilibili.com/',('bilibili.com',)),
    'douyin': ('抖音', 'https://www.douyin.com/', ('douyin.com','iesdouyin.com')),
}
AUTH_NAMES = {'kuaishou': {'kuaishou.server.web_st', 'kuaishou.server.web_ph', 'kuaishou.server.webday7_st', 'kuaishou.server.webday7_ph'}, 'xiaohongshu': {'web_session'}}
AUTH_NAMES['bilibili']={'SESSDATA'}
AUTH_NAMES.update(tiktok={'sessionid','sessionid_ss'},douyin={'sessionid','sessionid_ss'})
LOCK = threading.RLock()

def allowed_host(host, provider):
    host = (host or '').lstrip('.').lower()
    return any(host == h or host.endswith('.' + h) for h in PROVIDERS[provider][2])

def sanitize(provider, state):
    if provider not in PROVIDERS: raise ValueError('\u4e0d\u652f\u6301\u7684\u8d26\u53f7\u7c7b\u578b')
    return {'cookies': [c for c in state.get('cookies', []) if allowed_host(c.get('domain'), provider)],
            'origins': [o for o in state.get('origins', []) if urlsplit(o.get('origin', '')).scheme == 'https' and allowed_host(urlsplit(o.get('origin', '')).hostname, provider)],
            'ua': state.get('ua', '')}

def live_cookies(state):
    return [c for c in state.get('cookies', []) if c.get('expires', -1) < 0 or c.get('expires', 0) > time.time()]

def has_authorization(provider, state):
    return any(c.get('name') in AUTH_NAMES[provider] and c.get('value') for c in live_cookies(state))

def revision(data):
    return hashlib.sha256(data['secret'].encode('ascii')).hexdigest()

@contextmanager
def locked():
    # Also serialize the interactive helper and the server in separate processes.
    with LOCK:
        ROOT.mkdir(parents=True, exist_ok=True)
        with (ROOT / '.session.lock').open('a+b') as stream:
            if stream.tell() == 0: stream.write(b'0'); stream.flush()
            stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try: yield
            finally:
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

def _write(path, data):
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8'); temp.replace(path)
    finally: temp.unlink(missing_ok=True)

def status(provider):
    if provider not in PROVIDERS: raise ValueError('\u4e0d\u652f\u6301\u7684\u8d26\u53f7\u7c7b\u578b')
    path = ROOT / (provider + '.json')
    if not path.exists(): return {'provider': provider, 'saved': False, 'verified': False, 'message': '\u5c1a\u672a\u8fde\u63a5\u5b98\u65b9\u8d26\u53f7'}
    try: data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError): return {'provider': provider, 'saved': False, 'verified': False, 'message': '\u672c\u673a\u6388\u6743\u4e0d\u53ef\u8bfb\u53d6\uff0c\u8bf7\u91cd\u65b0\u8fde\u63a5'}
    return {k: data.get(k) for k in ('provider', 'saved', 'verified', 'checked_at', 'refreshed_at', 'message')}

def load(provider):
    if provider not in PROVIDERS: raise ValueError('Unsupported provider')
    path = ROOT / (provider + '.json')
    if not path.exists(): return None
    data = json.loads(path.read_text(encoding='utf-8'))
    state = sanitize(provider, json.loads(crypt(base64.b64decode(data['secret']), True)))
    state['_revision'] = revision(data)
    return state

def page_authorized(provider, page):
    """A visitor ID or web_session cookie alone is not a logged-in XHS user."""
    if provider=='bilibili':
        if not allowed_host(urlsplit(page.url).hostname,provider):return False
        try:
            return page.evaluate("""async () => {
                const r=await fetch('https://api.bilibili.com/x/web-interface/nav',{credentials:'include'});
                if(!r.ok)return false;const d=await r.json();return d.code===0 && d.data?.isLogin===true && !!d.data?.mid;
            }""") is True
        except Exception:return False
    if provider=='kuaishou':
        if not allowed_host(urlsplit(page.url).hostname,provider):return False
        if not has_authorization(provider,{'cookies':page.context.cookies()}):return False
        try:
            # Official navigation component owns the signed-in account. An
            # author's avatar or a saved webday7 cookie is not login proof.
            return page.locator('div.user').evaluate_all("""els => els.some(el => {
                const vm=el.__vue__;
                return vm?.isLogin === true && !!vm.currentUserModel?.userInfo?.id;
            })""") is True
        except Exception:return False
    if provider=='tiktok':
        if not allowed_host(urlsplit(page.url).hostname,provider):return False
        try:
            return page.evaluate("""async () => {
                const r=await fetch('/passport/web/account/info/?aid=1988&app_language=en&app_name=tiktok_web',{credentials:'include'});
                if(!r.ok)return false;const d=await r.json();return d.message==='success' && !!d.data?.user_id_str;
            }""") is True
        except Exception:return False
    if provider=='douyin':
        if not allowed_host(urlsplit(page.url).hostname,provider):return False
        if not has_authorization(provider,{'cookies':page.context.cookies()}):return False
        # Jingxuan and the older homepage expose different account markers.
        return page.locator('[data-e2e="user-avatar"], [data-e2e="user-avatar-container"], a[href^="/user/self"], a[href^="https://www.douyin.com/user/self"]').count()>0
    if provider != 'xiaohongshu':
        return False
    if not allowed_host(urlsplit(page.url).hostname, provider):
        return False
    try:
        return page.evaluate("""() => {
            const value = window.__INITIAL_STATE__?.user?.loggedIn;
            return (typeof value === 'boolean' ? value : (value?.value ?? value?._value)) === true;
        }""") is True
    except Exception:
        return False

def save(provider, state):
    state = sanitize(provider, state)
    if not has_authorization(provider, state): raise ValueError('\u6b64\u7a97\u53e3\u8fd8\u6ca1\u6709\u767b\u5f55\u6388\u6743\uff0c\u8bf7\u5b8c\u6210\u5b98\u65b9\u626b\u7801\u6216\u9a8c\u8bc1\u7801\u540e\u518d\u4fdd\u5b58')
    with locked():
        data = {'provider': provider, 'saved': True, 'verified': False, 'checked_at': time.time(),
                'message': '\u6388\u6743\u5df2\u52a0\u5bc6\u4fdd\u5b58\uff1b\u5b8c\u6574\u4e0b\u8f7d\u9a8c\u8bc1\u5f85\u5b8c\u6210',
                'secret': base64.b64encode(crypt(json.dumps(state).encode('utf-8'))).decode('ascii')}
        _write(ROOT / (provider + '.json'), data)
        return revision(data)

def refresh(provider, state, expected_revision):
    """Keep only cookies actually renewed by the official site, for this account."""
    state = sanitize(provider, state)
    if not has_authorization(provider, state): return False
    with locked():
        path = ROOT / (provider + '.json')
        if not path.exists(): return False
        data = json.loads(path.read_text(encoding='utf-8'))
        if revision(data) != expected_revision: return False  # A newer manual account wins.
        data.update(secret=base64.b64encode(crypt(json.dumps(state).encode('utf-8'))).decode('ascii'), refreshed_at=time.time())
        _write(path, data)
        return revision(data)

def result(provider, verified, message):
    with locked():
        path = ROOT / (provider + '.json')
        if not path.exists(): return
        data = json.loads(path.read_text(encoding='utf-8'))
        data.update(verified=bool(verified), checked_at=time.time(), message=message)
        _write(path, data)
