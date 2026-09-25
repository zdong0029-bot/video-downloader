"""后台启动入口（供开机自启的计划任务调用）。

为什么需要这个壳而不是直接 `pythonw -m uvicorn app:app`：
pythonw.exe 没有控制台，sys.stdout / sys.stderr 都是 None，
uvicorn 的日志处理器一往 stdout 写就抛异常，进程直接退出（返回码 1），
表现是"任务显示运行过但端口没监听"。

这里先把标准输出接到日志文件，再启动 uvicorn。
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "server.log"
PORT = 8000
MAX_LOG_BYTES = 5 * 1024 * 1024

os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

# 日志过大就先清掉，避免常年运行把磁盘写满
try:
    if LOG.exists() and LOG.stat().st_size > MAX_LOG_BYTES:
        LOG.unlink()
except OSError:
    pass

if sys.stdout is None or sys.stderr is None:
    _f = open(LOG, "a", encoding="utf-8", buffering=1, errors="replace")
    sys.stdout = _f
    sys.stderr = _f

import uvicorn  # noqa: E402  （必须在接管 stdout 之后再导入）

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=PORT, log_level="info")
