import shutil,sys,tempfile,threading,time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import Request

ROOT=Path(__file__).resolve().parent.parent


def test_share_folder_and_baidu_relative():
    sys.path.insert(0,str(ROOT))
    import file_download as fd, baidu_backend as bb
    assert fd.share_folder('课程/第1章/a.mp4')=='课程/第1章'
    assert fd.share_folder('a.mp4')==''
    assert fd.share_folder('../x/./y/a.mp4')=='x/y'
    assert bb._relative({'path':'/分享/课程/第1章/a.mp4','server_filename':'a.mp4'},'/分享')=='课程/第1章/a.mp4'
    assert bb._relative({'path':'/other/a.mp4','server_filename':'a.mp4'},'/分享')=='a.mp4'
    r=fd.CloudResult([],'n',{'x':('a','b')});assert r.folders=={'x':('a','b')} and r.note=='n'


def test_duplicate_cloud_link_and_queue_and_folders():
    saved_path=list(sys.path);saved_modules=dict(sys.modules)
    try:_queue_case()
    finally:
        sys.path[:]=saved_path
        for name in list(sys.modules):
            if name not in saved_modules:del sys.modules[name]
        sys.modules.update(saved_modules)


def _queue_case():
    with tempfile.TemporaryDirectory(prefix='cloud-queue-test-') as folder:
        fixture=Path(folder);(fixture/'static').mkdir()
        for source in ROOT.glob('*.py'):shutil.copy2(source,fixture/source.name)
        sys.path.insert(0,str(fixture))
        for name in list(sys.modules):
            if name in ('app','cloud_backend','file_download','task_history','relay_sync'):del sys.modules[name]
        import app as m
        request=Request({'type':'http','method':'POST','headers':[],'scheme':'http','server':('fixture.test',80),'path':'/api/jobs','query_string':b''})
        me=m.Identity(device='visitor')
        url='https://pan.quark.cn/s/5919ba7324fa'
        with patch.object(m.threading,'Thread',return_value=SimpleNamespace(start=lambda:None)):
            first=m.create_job(m.NewJob(url=url,mode='cloud'),request,me)
            again=m.create_job(m.NewJob(url=url,mode='cloud'),request,me)
        assert len(first['created'])==1 and again['created']==[] and again['existing'][0]['id']==first['created'][0]['id']

        # Two jobs on the same provider run one after another and keep folders.
        started=[];release=threading.Event()
        def fake_run(url,password,out,progress,cancelled):
            started.append(out.name)
            if len(started)==1:release.wait(5)
            p=out/'abc_a.mp4';p.write_bytes(b'x')
            return m.file_download.CloudResult([p],'ok',{p.name:('课程/第1章','a.mp4')})
        a=m.Job(id='aaaaaaaaaaaa',url=url,kind='cloud',owner='v');b=m.Job(id='bbbbbbbbbbbb',url=url+'x',kind='cloud',owner='v')
        m.JOBS[a.id]=a;m.JOBS[b.id]=b
        with patch.object(m.cloud_backend,'run',side_effect=fake_run):
            ta=threading.Thread(target=m._worker,args=(a,));ta.start()
            for _ in range(50):
                if started:break
                time.sleep(.05)
            tb=threading.Thread(target=m._worker,args=(b,));tb.start()
            time.sleep(1.2)
            assert started==[a.id] and b.status=='queued' and '排队中' in b.message
            release.set();ta.join(5);tb.join(5)
        assert started==[a.id,b.id] and a.status=='done' and b.status=='done'
        f=a.public()['files'][0];assert f['folder']=='课程/第1章' and f['label']=='a.mp4'
        meta=(m.DOWNLOADS/a.id/m.META).read_text(encoding='utf-8');assert '课程/第1章' in meta


def test_shortcut_signed_links():
    saved_path=list(sys.path);saved_modules=dict(sys.modules)
    try:
        with tempfile.TemporaryDirectory(prefix='shortcut-test-') as folder:
            fixture=Path(folder);(fixture/'static').mkdir()
            for source in ROOT.glob('*.py'):shutil.copy2(source,fixture/source.name)
            sys.path.insert(0,str(fixture))
            for name in list(sys.modules):
                if name in ('app','cloud_backend','file_download','task_history','relay_sync'):del sys.modules[name]
            import app as m
            from fastapi.testclient import TestClient
            from fastapi import HTTPException
            job=m.Job(id='cccccccccccc',url='u',kind='file',owner='me',status='done')
            d=m._job_dir(job.id);(d/'a.mp4').write_bytes(b'video')
            job.files=[m.FileItem('a.mp4',5,True)];m.JOBS[job.id]=job
            with patch.object(m.relay_sync,'enabled',return_value=False):
                urls=m.shortcut_links(m.ShortcutItems(items=[{'id':job.id,'name':'a.mp4'}]),m.Identity(device='me'))['urls']
            try:m.shortcut_links(m.ShortcutItems(items=[{'id':job.id,'name':'a.mp4'}]),m.Identity(device='other'));raise AssertionError('foreign job')
            except HTTPException as e:assert e.status_code==404
            c=TestClient(m.app)
            r=c.get(urls[0]);assert r.status_code==200 and r.content==b'video'
            assert c.get(urls[0].replace('&s=','&s=0')).status_code==403
    finally:
        sys.path[:]=saved_path
        for name in list(sys.modules):
            if name not in saved_modules:del sys.modules[name]
        sys.modules.update(saved_modules)
