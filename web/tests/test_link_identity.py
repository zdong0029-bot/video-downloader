import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import link_identity as L


def test_same_reel_with_different_share_params():
    a = "https://www.instagram.com/reel/DPxXceQiU7f/?srtk=MXg3MGQ1NDU0MTJmdA=="
    b = "https://www.instagram.com/reel/DPxXceQiU7f/?cplk=MXg3MGQ1NDU0MTJmdA=="
    assert L.video_key(a) == L.video_key(b)


def test_other_platforms():
    assert L.video_key("https://www.tiktok.com/@a/video/123?is_from_webapp=1") == L.video_key("https://m.tiktok.com/@a/video/123")
    assert L.video_key("https://youtu.be/abcdefgh?si=x") == L.video_key("https://www.youtube.com/watch?v=abcdefgh&t=3")
    assert L.video_key("https://www.instagram.com/reel/AAA/") != L.video_key("https://www.instagram.com/reel/BBB/")


def test_cloud_password_kept():
    assert L.video_key("https://pan.baidu.com/s/1abc?pwd=1234") != L.video_key("https://pan.baidu.com/s/1abc?pwd=5678")
