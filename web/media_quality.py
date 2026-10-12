"""Check the completed original streams; never encode or upscale."""
import json
import shutil
import subprocess

def verify(path, expected=None):
    executable = shutil.which('ffprobe')
    if not executable: raise ValueError('\u7f3a\u5c11\u753b\u8d28\u6821\u9a8c\u7ec4\u4ef6 ffprobe\uff0c\u672a\u5c06\u6587\u4ef6\u6807\u4e3a\u5b8c\u6210')
    probe = subprocess.run([executable, '-v', 'error', '-show_entries', 'stream=codec_type,codec_name,width,height:format=duration', '-of', 'json', str(path)], capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    if probe.returncode: raise ValueError('\u4e0b\u8f7d\u6587\u4ef6\u5a92\u4f53\u6821\u9a8c\u5931\u8d25')
    parsed = json.loads(probe.stdout)
    streams = parsed.get('streams') or []
    video = next((s for s in streams if s.get('codec_type') == 'video'), None)
    audio = any(s.get('codec_type') == 'audio' for s in streams)
    expected = expected or {}
    source_duration=expected.get('duration') or 0
    actual_duration=float(parsed.get('format',{}).get('duration') or 0)
    if source_duration and not expected.get('is_live'):
        tolerance=max(2.0,float(source_duration)*.01)
        if actual_duration<float(source_duration)-tolerance:
            raise ValueError('视频时长短于原文件，可能缺少片段，未标为完成')
    if expected.get('vcodec') == 'none':
        if not audio: raise ValueError('\u97f3\u8f68\u4e0b\u8f7d\u4e0d\u5b8c\u6574')
        return '\u539f\u97f3\u8f68\uff1b\u672a\u91cd\u65b0\u7f16\u7801'
    if not video or not video.get('width') or not video.get('height'):
        raise ValueError('\u672a\u83b7\u5f97\u5b8c\u6574\u89c6\u9891\u753b\u9762')
    source_res = min(expected.get('width') or 0, expected.get('height') or 0)
    actual_res = min(video['width'], video['height'])
    if source_res and actual_res < source_res: raise ValueError('\u6210\u54c1\u5206\u8fa8\u7387\u4f4e\u4e8e\u9009\u4e2d\u539f\u6d41\uff0c\u672a\u6807\u4e3a\u5b8c\u6210')
    wants_audio = expected.get('acodec') not in (None, 'none') or any(f.get('vcodec') == 'none' for f in expected.get('requested_formats') or [])
    if wants_audio and not audio: raise ValueError('\u9ad8\u6e05\u753b\u9762\u7684\u914d\u5957\u97f3\u8f68\u7f3a\u5931\uff0c\u672a\u6807\u4e3a\u5b8c\u6210')
    if actual_duration >= 600:
        decoder = shutil.which('ffmpeg')
        if not decoder:
            raise ValueError('缺少长视频完整性校验组件 ffmpeg，未标为完成')
        for location in (0, actual_duration / 2, max(0, actual_duration - 5)):
            try:
                check = subprocess.run([decoder, '-v', 'error', '-xerror', '-ss', str(location),
                                        '-i', str(path), '-t', '2', '-f', 'null', '-'],
                                       capture_output=True, timeout=60,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            except subprocess.TimeoutExpired:
                raise ValueError('长视频解码校验超时，未标为完成') from None
            if check.returncode:
                raise ValueError('长视频开头、中段或结尾解码失败，未标为完成')
    text = f"{video['width']}\u00d7{video['height']}\uff1b\u539f\u7f16\u7801\uff0c\u672a\u91cd\u65b0\u538b\u7f29"
    if actual_res < 720: text += '\uff1b\u5f53\u524d\u6e90\u6d41\u4f4e\u4e8e720p\uff0c\u672a\u653e\u5927'
    if not audio: text += '\uff1b\u6e90\u6d41\u65e0\u97f3\u8f68'
    return text
