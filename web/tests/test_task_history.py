"""Seven-day retention and durable records without touching media or credentials."""
import sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import task_history as history
def job(id="fixture",status="done",completed_at=None):
    return SimpleNamespace(id=id,url="https://example.com/file.zip",kind="file",status=status,title="fixture",
        message="fixture",progress=1,total=1,finished=1,failed=0,created=time.time()-10*86400,
        completed_at=time.time() if completed_at is None else completed_at,owner="device",owner_label="test",
        batch_id="",catalog_id="",cloud_note="",current_entry="",failed_entries={},
        files=[SimpleNamespace(name="original.zip",size=8,done=True,entry_id="source-1")])
class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=history.PATH
        history.PATH=Path(self.tmp.name)/"data"/"records.sqlite"
    def tearDown(self):
        history.PATH=self.old;self.tmp.cleanup()
    def test_completion_time_not_submission_time(self):
        j=job()
        self.assertTrue(history.retained(j.status,j.completed_at,j.created))
        self.assertFalse(history.retained("done",1000,500,now=1000+history.RETENTION))
    def test_active_tasks_never_expire(self):
        self.assertTrue(history.retained("running",0,1,now=10000000))
        self.assertTrue(history.retained("queued",0,1,now=10000000))
    def test_failed_and_cancelled_survive_reopen(self):
        history.remember(job("a","error"));history.remember(job("b","cancelled"))
        rows=history.load();self.assertEqual({x["status"] for x in rows},{"error","cancelled"})
        self.assertEqual(rows[0]["files"][0]["entry_id"],"source-1")
    def test_expired_records_never_remove_original_files(self):
        media=Path(self.tmp.name)/"original.zip";media.write_bytes(b"original")
        history.remember(job("old",completed_at=time.time()-8*86400))
        self.assertEqual(history.load(),[]);self.assertEqual(media.read_bytes(),b"original")
    def test_manual_record_removal_is_durable(self):
        history.remember(job());history.forget("fixture");self.assertEqual(history.load(),[])
    def test_history_does_not_capture_authentication_state(self):
        j=job();j.cookies="must-not-save";j.account_token="must-not-save"
        history.remember(j);self.assertNotIn("must-not-save",str(history.load()))
if __name__=="__main__":unittest.main()
