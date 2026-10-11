"""Manual bounded public-sample audit. Run explicitly with downloader Python.
Two concurrent platforms; no cloud account, login cookies, private shares or QR.
Downloaded samples remain locally in tests/results/public-samples (Git ignored).
"""
import sys,json,time,subprocess,concurrent.futures,re
from pathlib import Path
root=Path(__file__).resolve().parent;cases=json.loads((root/'platform_samples.json').read_text(encoding='utf-8'))
def run(case):
 out=root/'results'/'public-samples'/case['platform'];out.mkdir(parents=True,exist_ok=True);log=out/'runner.log';start=time.monotonic()
 with log.open('wb') as stream:
  process=subprocess.Popen([sys.executable,str(root/'audit_platform_worker.py'),json.dumps(case),str(out)],stdout=stream,stderr=stream,creationflags=subprocess.CREATE_NO_WINDOW)
  try:process.wait(timeout=180)
  except subprocess.TimeoutExpired:
   subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
   result={**case,'status':'not_completed','error':'Independent worker timed out after 180 seconds','seconds':round(time.monotonic()-start,2)};(out/'result.json').write_text(json.dumps(result),encoding='utf-8')
 resultfile=out/'result.json'
 if resultfile.exists():result=json.loads(resultfile.read_text(encoding='utf-8'))
 else:result={**case,'status':'not_completed','error':'Worker did not produce a result','seconds':round(time.monotonic()-start,2)}
 print(json.dumps({k:result.get(k) for k in ['platform','status','seconds','bytes','selected_height','error']},ensure_ascii=True),flush=True);return result
results=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 for future in concurrent.futures.as_completed([pool.submit(run,c) for c in cases]):
  results.append(future.result());(root/'results'/'public-samples-in-progress.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
(root/'results'/'public-samples.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8');print('FINISHED',len(results),flush=True)
