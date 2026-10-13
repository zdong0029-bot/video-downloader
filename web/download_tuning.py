"""Per-cloud-drive download concurrency and range size.

Defaults favour member (VIP/SVIP) accounts on a fast home line. Every value can be
overridden without code changes, in this order (later wins):

1. built-in DEFAULTS below
2. web/data/download_tuning.json, e.g. {"quark": {"workers": 24, "chunk_mib": 8}}
3. environment variables DL_WORKERS_QUARK=24, DL_CHUNK_MIB_QUARK=8

Throttling (HTTP 429/503) is handled at runtime by file_download, which halves the
active connections and slowly grows back, so a high ceiling never makes a transfer
slower than the old fixed setting.
"""
import json
import os
from pathlib import Path

MAX_WORKERS = 32
MIN_CHUNK_MIB = 1
MAX_CHUNK_MIB = 16

# workers: ceiling of parallel range requests; chunk_mib: bytes per range request.
DEFAULTS = {
    'quark':  {'workers': 16, 'chunk_mib': 8},
    'uc':     {'workers': 16, 'chunk_mib': 8},
    'ali':    {'workers': 16, 'chunk_mib': 8},
    # Baidu: bounded 4 MiB ranges; few connections, since non-SVIP accounts get
    # throttled per account rather than with 429. SVIP users may raise it.
    'baidu':  {'workers': 4,  'chunk_mib': 4},
    'xunlei': {'workers': 12, 'chunk_mib': 8},
    'pan123': {'workers': 8,  'chunk_mib': 8},
    'tianyi': {'workers': 8,  'chunk_mib': 8},
    'mobile': {'workers': 8,  'chunk_mib': 8},
}
FALLBACK = {'workers': 8, 'chunk_mib': 8}

CONFIG_PATH = Path(__file__).resolve().parent / 'data' / 'download_tuning.json'


def _clamp(value, low, high, default):
    try:
        return min(max(int(value), low), high)
    except (TypeError, ValueError):
        return default


def _file_overrides():
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def profile(key):
    """Return {'workers': int, 'range_size': bytes} for a provider key."""
    base = DEFAULTS.get(key, FALLBACK)
    workers, chunk = base['workers'], base['chunk_mib']
    name = str(key).upper()
    extra = _file_overrides().get(key)
    extra = extra if isinstance(extra, dict) else {}
    # Each layer only applies when its value is a valid integer.
    for value in (extra.get('workers'), os.environ.get('DL_WORKERS_' + name)):
        workers = _clamp(value, 1, MAX_WORKERS, workers) if value not in (None, '') else workers
    for value in (extra.get('chunk_mib'), os.environ.get('DL_CHUNK_MIB_' + name)):
        chunk = _clamp(value, MIN_CHUNK_MIB, MAX_CHUNK_MIB, chunk) if value not in (None, '') else chunk
    return {'workers': workers, 'range_size': chunk * 1024 ** 2}


def describe(key):
    p = profile(key)
    return f"{p['range_size'] // 1024 ** 2}MB分段、最多{p['workers']}连接"
