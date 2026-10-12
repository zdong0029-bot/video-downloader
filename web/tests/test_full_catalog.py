"""Full pagination, public scope, failure and cancellation regressions."""
import unittest
from unittest.mock import patch
import video_catalog as catalog
from common import Cancelled
import json

class FakeYDL:
 data={}
 def __init__(self,opts):self.opts=opts
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def extract_info(self,url,download=False):
  return {'title':'fixture','entries':iter(self.data.get(url.rsplit('/',1)[-1],[]))}

class FullCatalogTests(unittest.TestCase):
 def run_catalog(self,**kwargs):
  with patch.object(catalog.yt_dlp,'YoutubeDL',FakeYDL),patch('platform_extractors.install'):
   return catalog.resolve('https://www.youtube.com/@fixture',0,**kwargs)
 def setUp(self):
  FakeYDL.data={'videos':[{'id':str(i),'url':'https://www.youtube.com/watch?v='+str(i)} for i in range(1600)],
               'shorts':[{'id':'s','url':'https://www.youtube.com/watch?v=s'}],
               'streams':[{'id':'0','url':'https://www.youtube.com/watch?v=0'},{'id':'l','url':'https://www.youtube.com/watch?v=l'}]}
 def test_full_mode_exhausts_all_tabs_beyond_old_limits(self):
  result=self.run_catalog();self.assertEqual(len(result['entries']),1602)
  self.assertTrue(result['complete']);self.assertFalse(result['limited'])
  self.assertEqual([s['new_entries'] for s in result['sources']],[1600,1,1])
  self.assertTrue(all(s['finished'] for s in result['sources']))
 def test_cancel_does_not_restart_or_claim_complete(self):
  with self.assertRaises(Cancelled):self.run_catalog(cancelled=lambda:True)
 def test_silent_pagination_failure_is_detected(self):
  logger=catalog.CatalogLogger();logger.warning('Giving up after repeated network errors')
  self.assertTrue(logger.pagination_failed)
  with patch.object(FakeYDL,'extract_info',side_effect=Cancelled()):
   with self.assertRaises(Cancelled):self.run_catalog()
 def test_no_failure_from_quality_advisory(self):
  logger=catalog.CatalogLogger();logger.warning('Some formats are missing a URL')
  self.assertFalse(logger.pagination_failed)
 def test_cctv_counts_official_rows_but_deduplicates_works(self):
  class Response:
   def __init__(self,payload):self.payload=payload
   def read(self,n):return self.payload
   def close(self):pass
  page=b"<title>fixture</title><script>var topicID = 'TOPC123456789';</script>"
  work={'url':'https://tv.cctv.com/2026/10/12/VIDEfixture.shtml','title':'fixture'}
  data=json.dumps({'data':{'total':40,'list':[work]*20}}).encode()
  responses=iter([page,data,data])
  def public(*args):
   response=Response(next(responses));return response,response,None
  with patch('file_download.open_public',side_effect=public),patch.object(catalog.time,'sleep'):
   result=catalog._cctv('https://tv.cctv.com/lm/fixture/index.shtml',0)
  self.assertTrue(result['complete']);self.assertEqual(result['pages'],2)
  self.assertEqual(result['rows_read'],40);self.assertEqual(len(result['entries']),1)
if __name__=='__main__':unittest.main()
