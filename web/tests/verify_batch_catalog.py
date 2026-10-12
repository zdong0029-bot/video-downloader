"""Run with the downloader Python environment; all writes go to a temporary copy."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent
# Copy source modules only, never saved account/session files. New adapters
# must be available in the isolated test just as they are in the real app.
MODULES = tuple(p.name for p in WEB.glob('*.py'))
with tempfile.TemporaryDirectory(prefix='downloader-batch-test-') as temporary:
    root = Path(temporary)
    for name in MODULES:
        shutil.copyfile(WEB / name, root / name)
    shutil.copytree(WEB / 'static', root / 'static')
    shutil.copyfile(Path(__file__).with_name('ui_flow.py'), root / 'ui_flow.py')
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                    '-i', 'color=c=teal:s=720x1280:r=24:d=0.2', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', '-y', str(root / 'fixture.mp4')], check=True,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    env = dict(os.environ, ISOLATED_BATCH_TEST=str(root))
    subprocess.run([sys.executable, str(root / 'ui_flow.py')], cwd=root, env=env, check=True)
