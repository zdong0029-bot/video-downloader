import shutil,sys,tempfile,json
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from fastapi import Request,HTTPException
def main():
 root=Path(__file__).resolve().parent.parent

 # Isolate all runtime data and authorization stores from the production host.
 with tempfile.TemporaryDirectory(prefix='extract-api-test-') as folder:
  fixture=Path(folder);(fixture/'static').mkdir()
  for source in root.glob('*.py'):shutil.copy2(source,fixture/source.name)
  sys.path.insert(0,str(fixture));import app as m
  request=Request({'type':'http','method':'POST','headers':[],'scheme':'http','server':('fixture.test',80),'path':'/api/jobs','query_string':b''})
  me=m.Identity(device='fixture-visitor')
  note='https://www.xiaohongshu.com/discovery/item/6ac119c000000000014033c23?xsec_token=fixture';profile='https://www.xiaohongshu.com/user/profile/'+'a'*24
  with patch.object(m.threading,'Thread',return_value=SimpleNamespace(start=lambda:None)):
   rejected=m.create_catalog(m.CatalogRequest(url=note),request,me);assert rejected['catalogs']==[] and '识别失败' in rejected['rejected'][0]['reason']
   assert not m._catalogs
   for mode in ['auto','media']:
    result=m.create_job(m.NewJob(url=note,mode=mode),request,me);assert len(result['created'])==1 and not result['catalogs'] and result['created'][0]['kind']=='web'
   try:m.create_job(m.NewJob(url=profile,mode='media'),request,me);raise AssertionError('profile accepted by media mode')
   except HTTPException as e:assert e.status_code==400 and '识别失败' in e.detail
   result=m.create_job(m.NewJob(url=profile,mode='auto'),request,me);assert len(result['catalogs'])==1 and not result['created']
   result=m.create_catalog(m.CatalogRequest(url=profile),request,me);assert len(result['catalogs'])==1
   rows=json.loads((root/'tests/profile_dispatch_cases.json').read_text(encoding='utf-8'))
   for row in rows:
    for mode in ('auto','media'):
     d=m.create_job(m.NewJob(url=row['work_url'],mode=mode),request,me)
     assert not d['catalogs'] and len(d['created'])==1,(row['platform'],mode)
    d=m.create_catalog(m.CatalogRequest(url=row['work_url']),request,me)
    assert not d['catalogs'] and '识别失败' in d['rejected'][0]['reason'],row['platform']
    d=m.create_catalog(m.CatalogRequest(url=row['profile_url']),request,me)
    assert bool(d['catalogs'])==row['has_profile'],row['platform']
    if row['has_profile']:
     d=m.create_job(m.NewJob(url=row['profile_url'],mode='auto'),request,me)
     assert d['catalogs'] and not d['created'],row['platform']
     try:m.create_job(m.NewJob(url=row['profile_url'],mode='media'),request,me);raise AssertionError(row['platform'])
     except HTTPException as e:assert e.status_code==400 and '识别失败' in e.detail
    m._catalogs.clear()
   # Media extraction never consumes cloud share or executable links.
   for url in ['https://pan.quark.cn/s/fixture','https://cdn.example.test/original.apk']:
    try:m.create_job(m.NewJob(url=url,mode='media'),request,me);raise AssertionError('wrong type accepted')
    except HTTPException as e:assert e.status_code==400
   result=m.create_job(m.NewJob(url='https://cdn.example.test/original.mp4',mode='file'),request,me);assert result['created'][0]['kind']=='file'
  print('PASS 31 platforms: actual API work/profile dispatch, strict mismatch rejection, media/file modes; isolated fixture with no credentials or real downloads')

if __name__=="__main__":main()
