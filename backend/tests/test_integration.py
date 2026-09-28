"""End-to-end checks against the render/project pipeline (no sockets)."""

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from media import EXPORTS_DIR, ensure_samples, ffprobe, media_path
from projects import load_project, save_project
from render import MANAGER, validate, RenderError
from timeline import Clip, Timeline


def duration(rel: str) -> float:
    return float(ffprobe(ROOT / rel.lstrip("/"))["format"]["duration"])


def wait(job):
    while job.status == "running":
        time.sleep(0.2)
    return job


def make_timeline():
    return Timeline(clips=[
        Clip("c1", "sample-red", 0.5, 2.5),
        Clip("c2", "sample-blue", 1.0, 3.0, speed=1.5),
        Clip("c3", "sample-green", 0.0, 2.0, color="vivid"),
    ])


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_samples()
        cls.tl = make_timeline()
        cls.expected = cls.tl.duration  # 2 + 1.333 + 2

    def test_01_preview_export_match(self):
        jp = wait(MANAGER.start(self.tl, "preview", "itest"))
        je = wait(MANAGER.start(self.tl, "export", "itest"))
        self.assertEqual(jp.status, "done", jp.error)
        self.assertEqual(je.status, "done", je.error)
        self.assertLess(abs(duration(jp.output) - duration(je.output)), 0.08)
        self.assertLess(abs(duration(je.output) - self.expected), 0.1)

    def test_02_project_roundtrip(self):
        save_project("itest", self.tl)
        reopened = load_project("itest")
        rt = Timeline.from_dict(reopened["timeline"])
        self.assertEqual(rt.to_dict(), self.tl.to_dict())
        self.assertAlmostEqual(rt.duration, self.expected, places=2)
        self.assertEqual(reopened["missing_media"], [])

    def test_03_missing_media_kept_editable(self):
        src = media_path("sample-red")
        hidden = src.with_suffix(".hidden")
        save_project("itest-missing", self.tl)
        src.rename(hidden)
        try:
            proj = load_project("itest-missing")
            self.assertIn("sample-red", proj["missing_media"])
            tl2 = Timeline.from_dict(proj["timeline"])
            self.assertEqual(len(tl2.clips), 3)
            with self.assertRaises(RenderError):
                validate(tl2)
            tl2.clips = [c for c in tl2.clips if c.media_id != "sample-red"]
            validate(tl2)
        finally:
            hidden.rename(src)
        self.assertEqual(load_project("itest-missing")["missing_media"], [])

    def test_04_interrupted_export_keeps_project(self):
        save_project("itest-interrupt", self.tl)
        job = MANAGER.start(self.tl, "export", "itest-interrupt")
        time.sleep(0.3)
        MANAGER.cancel(job.id)
        wait(job)
        self.assertEqual(job.status, "failed")
        self.assertEqual(list(EXPORTS_DIR.glob("*.part")), [])
        proj = load_project("itest-interrupt")
        self.assertEqual(len(proj["timeline"]["clips"]), 3)
        again = wait(MANAGER.start(self.tl, "export", "itest-interrupt"))
        self.assertEqual(again.status, "done", again.error)

    def test_05_trim_ripple_boundaries(self):
        tl = make_timeline()
        tl.clips[0].out_point = 1.5
        lay = tl.layout()
        self.assertAlmostEqual(lay[0]["end"], 1.0, places=3)
        self.assertAlmostEqual(lay[1]["start"], 1.0, places=3)
        self.assertAlmostEqual(lay[1]["end"], 1.0 + 2.0 / 1.5, places=3)
        self.assertAlmostEqual(lay[2]["start"], 1.0 + 2.0 / 1.5, places=3)

    def test_06_reject_trim_past_source_end(self):
        tl = make_timeline()
        tl.clips[0].out_point = 999.0
        with self.assertRaises(RenderError):
            validate(tl)


if __name__ == "__main__":
    unittest.main(verbosity=2)
