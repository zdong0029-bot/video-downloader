import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stats


def test_one_visit_per_browser_per_day(tmp_path, monkeypatch):
    monkeypatch.setattr(stats, "PATH", tmp_path / "s.sqlite")
    monkeypatch.setattr(stats, "_seen", {})
    monkeypatch.setattr(stats, "_last", {})
    day1 = 1_760_000_000
    for _ in range(5):
        stats.record_visit("phone", "Mozilla iPhone Mobile Safari", False, now=day1)
    stats.record_visit("pc", "Windows NT Chrome", False, now=day1)
    stats.record_visit("boss", "Windows NT Chrome", True, now=day1)
    stats.record_visit("phone", "Mozilla iPhone Mobile Safari", False, now=day1 + 86400)
    stats.add_usage("video", links=2, files=3, size=100, now=day1 + 86400)
    stats.add_usage("cloud", files=1, size=50, now=day1 + 86400)
    s = stats.summary(2, now=day1 + 86400)
    today, yesterday = s["today"], s["yesterday"]
    assert (yesterday["visitors"], yesterday["mobile"], yesterday["desktop"], yesterday["admin"], yesterday["new"]) == (2, 1, 1, 1, 2)
    assert (today["visitors"], today["new"]) == (1, 0)
    assert (today["video_links"], today["video_files"], today["video_bytes"], today["cloud_files"]) == (2, 3, 100, 1)
