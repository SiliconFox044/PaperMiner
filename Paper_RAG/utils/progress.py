"""Shared progress logger for LegalRAG — server and retrieval modules共用。

Progress logs are written directly to raw file descriptor (fd 2 = stderr)
to bypass uvicorn's log interception pipeline.
"""
import json
import sys

def progress_log(**kwargs):
    """统一结构化日志输出，写入 stderrfd，绕过 uvicorn 拦截。"""
    sys.stderr.write(json.dumps(kwargs, ensure_ascii=False) + "\n")
    sys.stderr.flush()
