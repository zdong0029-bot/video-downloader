"""Batch tools on downloaded files: trim, convert, change MD5, extract audio.

Video re-encodes use x264 CRF 18 (visually lossless) and keep resolution and
frame rate; remux paths copy streams untouched whenever the container allows.
"""
import json
import secrets
from pathlib import Path

from media_parts import run, tool, probe_audio_codec

VIDEO_FORMATS = ("mp4", "mov", "mkv", "webm", "gif")
AUDIO_FORMATS = ("mp3", "m4a", "wav")
X264 = ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"]


def _ff() -> str:
    ffmpeg = tool("ffmpeg")
    if not ffmpeg:
        raise ValueError("缺少 ffmpeg，无法处理视频")
    return ffmpeg


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        p = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not p.exists():
            return p
    raise ValueError("输出文件重名过多")


def duration(path: Path) -> float:
    ffprobe = tool("ffprobe")
    if not ffprobe:
        raise ValueError("缺少 ffprobe")
    r = run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)], timeout=60)
    try:
        return float(json.loads(r.stdout or b"{}")["format"]["duration"])
    except Exception:  # noqa: BLE001
        raise ValueError("读不到视频时长") from None


def _check(r, dst: Path) -> Path:
    if r.returncode != 0 or not dst.is_file() or dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
        err = (r.stderr or b"").decode(errors="ignore").strip().splitlines()
        raise ValueError("ffmpeg 处理失败" + (f"：{err[-1][:200]}" if err else ""))
    return dst


def trim(src: Path, out_dir: Path, head: float = 0, tail: float = 0, start: float | None = None, end: float | None = None) -> Path:
    total = duration(src)
    a = float(start) if start not in (None, "") else max(0.0, float(head or 0))
    b = float(end) if end not in (None, "") else total - max(0.0, float(tail or 0))
    b = min(b, total)
    if b - a < 0.1:
        raise ValueError(f"剪完不足 0.1 秒（原长 {total:.1f} 秒）")
    dst = _unique(out_dir / f"{src.stem}_剪辑{src.suffix if src.suffix.lower() in ('.mp4', '.mov', '.mkv') else '.mp4'}")
    audio = ["-c:a", "aac", "-b:a", "256k"] if probe_audio_codec(src) else ["-an"]
    r = run([_ff(), "-v", "error", "-nostdin", "-y", "-ss", f"{a:.3f}", "-i", str(src), "-t", f"{b - a:.3f}",
             "-map", "0:v:0", "-map", "0:a:0?", *X264, *audio, "-movflags", "+faststart", str(dst)], timeout=7200)
    return _check(r, dst)


def convert(src: Path, out_dir: Path, fmt: str) -> Path:
    fmt = fmt.lower()
    if fmt not in VIDEO_FORMATS:
        raise ValueError("不支持的格式")
    dst = _unique(out_dir / f"{src.stem}.{fmt}")
    ff = _ff()
    if fmt == "gif":
        r = run([ff, "-v", "error", "-nostdin", "-y", "-i", str(src), "-vf",
                 "fps=15,scale='min(720,iw)':-2:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a",
                 str(dst)], timeout=7200)
        return _check(r, dst)
    if fmt == "webm":
        r = run([ff, "-v", "error", "-nostdin", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?",
                 "-c:v", "libvpx-vp9", "-crf", "24", "-b:v", "0", "-row-mt", "1", "-c:a", "libopus", "-b:a", "192k", str(dst)], timeout=14400)
        return _check(r, dst)
    # mp4 / mov / mkv: lossless remux first, re-encode only if the streams don't fit.
    faststart = ["-movflags", "+faststart"] if fmt in ("mp4", "mov") else []
    r = run([ff, "-v", "error", "-nostdin", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", *faststart, str(dst)], timeout=3600)
    if r.returncode == 0 and dst.is_file() and dst.stat().st_size:
        return dst
    dst.unlink(missing_ok=True)
    audio = ["-c:a", "aac", "-b:a", "256k"] if probe_audio_codec(src) else ["-an"]
    r = run([ff, "-v", "error", "-nostdin", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", *X264, *audio, *faststart, str(dst)], timeout=7200)
    return _check(r, dst)


def remd5(src: Path, out_dir: Path) -> Path:
    """New file hash, same picture and sound: streams are copied, only a metadata tag differs."""
    dst = _unique(out_dir / f"{src.stem}_新MD5{src.suffix}")
    tag = secrets.token_hex(8)
    if src.suffix.lower() in (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".m4a", ".mp3"):
        extra = ["-movflags", "+faststart"] if src.suffix.lower() in (".mp4", ".mov", ".m4v", ".m4a") else []
        r = run([_ff(), "-v", "error", "-nostdin", "-y", "-i", str(src), "-map", "0", "-c", "copy",
                 "-map_metadata", "0", "-metadata", f"comment={tag}", *extra, str(dst)], timeout=3600)
        if r.returncode == 0 and dst.is_file() and dst.stat().st_size:
            return dst
        dst.unlink(missing_ok=True)
    # Images and anything else: trailing bytes are ignored by viewers.
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while chunk := fi.read(1 << 20):
            fo.write(chunk)
        fo.write(tag.encode())
    return dst


def to_audio(src: Path, out_dir: Path, fmt: str = "mp3") -> Path:
    fmt = fmt.lower()
    if fmt not in AUDIO_FORMATS:
        raise ValueError("不支持的音频格式")
    codec = probe_audio_codec(src)
    if not codec:
        raise ValueError("这个文件没有音轨")
    dst = _unique(out_dir / f"{src.stem}.{fmt}")
    if fmt == "mp3":
        args = ["-c:a", "copy"] if codec == "mp3" else ["-c:a", "libmp3lame", "-q:a", "0"]
    elif fmt == "m4a":
        args = ["-c:a", "copy"] if codec in ("aac", "alac") else ["-c:a", "aac", "-b:a", "256k"]
    else:
        args = ["-c:a", "pcm_s16le"]
    r = run([_ff(), "-v", "error", "-nostdin", "-y", "-i", str(src), "-vn", "-map", "0:a:0", *args, str(dst)], timeout=3600)
    return _check(r, dst)


def md5_of(path: Path) -> str:
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()
