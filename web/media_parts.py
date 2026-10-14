"""Per-video companion files: platform cover and audio track, saved next to the video.

Covers keep the platform's own image (converted to JPEG only when it is WebP/HEIC);
audio is stream-copied out of the downloaded video, so nothing is re-encoded when
the source is already AAC.
"""
import os
import shutil
import subprocess
from pathlib import Path

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}


def tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages"
    matches = list(base.glob(f"yt-dlp.FFmpeg*/ffmpeg*/bin/{name}.exe")) if base.is_dir() else []
    return str(matches[0]) if matches else None


def run(args: list[str], timeout: float = 600) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW)


def probe_audio_codec(path: Path) -> str:
    ffprobe = tool("ffprobe")
    if not ffprobe:
        return ""
    r = run([ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=codec_name", "-of", "default=nw=1:nk=1", str(path)], timeout=60)
    return r.stdout.decode(errors="ignore").strip().lower()


def cover_path(video: Path) -> Path:
    return video.with_name(video.stem + ".cover.jpg")


def audio_path(video: Path, ext: str = ".m4a") -> Path:
    return video.with_name(video.stem + ".audio" + ext)


def _to_jpeg(src: Path, dst: Path) -> bool:
    ffmpeg = tool("ffmpeg")
    if not ffmpeg:
        return False
    r = run([ffmpeg, "-v", "error", "-nostdin", "-y", "-i", str(src), "-frames:v", "1", "-q:v", "1", str(dst)], timeout=120)
    return r.returncode == 0 and dst.is_file() and dst.stat().st_size > 0


def make_cover(video: Path, image_bytes: bytes | None = None) -> Path | None:
    dst = cover_path(video)
    if image_bytes:
        if image_bytes[:3] == b"\xff\xd8\xff":
            dst.write_bytes(image_bytes)
            return dst
        tmp = video.with_name(video.stem + ".cover.src")
        tmp.write_bytes(image_bytes)
        try:
            if _to_jpeg(tmp, dst):
                return dst
        finally:
            tmp.unlink(missing_ok=True)
    # No platform cover: use the video's first frame.
    if _to_jpeg(video, dst):
        return dst
    dst.unlink(missing_ok=True)
    return None


def make_audio(video: Path) -> Path | None:
    ffmpeg = tool("ffmpeg")
    if not ffmpeg:
        return None
    codec = probe_audio_codec(video)
    if not codec:
        return None  # silent video
    if codec in ("aac", "alac"):
        dst, args = audio_path(video, ".m4a"), ["-c:a", "copy"]
    elif codec == "mp3":
        dst, args = audio_path(video, ".mp3"), ["-c:a", "copy"]
    elif codec == "opus":
        dst, args = audio_path(video, ".opus"), ["-c:a", "copy"]
    else:
        dst, args = audio_path(video, ".m4a"), ["-c:a", "aac", "-b:a", "256k"]
    r = run([ffmpeg, "-v", "error", "-nostdin", "-y", "-i", str(video), "-vn", "-map", "0:a:0", *args, str(dst)])
    if r.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
        return dst
    dst.unlink(missing_ok=True)
    return None


def make_parts(video: Path, cover_bytes: bytes | None = None) -> list[Path]:
    """Return the companion files created for one video; failures are skipped quietly."""
    if video.suffix.lower() not in VIDEO_EXT:
        return []
    out: list[Path] = []
    for step in (lambda: make_cover(video, cover_bytes), lambda: make_audio(video)):
        try:
            p = step()
        except Exception:  # noqa: BLE001 - companions are optional
            p = None
        if p:
            out.append(p)
    return out
