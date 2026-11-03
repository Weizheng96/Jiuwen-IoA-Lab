"""Cheap FFmpeg progress checks use fake files, never a screen or recorder."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / '.agents/skills/demo-video/scripts/check_live_capture.py'
spec = importlib.util.spec_from_file_location('live_capture_health', SCRIPT)
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


class LiveCaptureHealthTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'progress.txt'

    def progress(self, text, age=0):
        self.path.write_text(text, encoding='ascii')
        at = 1000 - age
        os.utime(self.path, (at, at))

    def test_fresh_advancing_and_latest_complete_block(self):
        self.progress('frame=10\nprogress=continue\nframe=12\nprogress=continue\nframe=13\n')
        result = health.check_capture(self.path, 10, now=1000)
        self.assertEqual(result['status'], 'healthy')
        self.assertEqual(result['frame'], 12)  # Partial writes cannot certify new frames.

    def test_stale(self):
        self.progress('frame=12\nprogress=continue\n', age=10)
        self.assertEqual(health.check_capture(self.path, 10, now=1000)['status'], 'stale')

    def test_end_is_unhealthy_even_when_fresh_and_advancing(self):
        self.progress('frame=99\nprogress=end\n')
        self.assertEqual(health.check_capture(self.path, 10, now=1000)['status'], 'ended')

    def test_no_advance_and_reset(self):
        self.progress('frame=12\nprogress=continue\n')
        for previous in (12, 99):
            self.assertEqual(health.check_capture(self.path, previous, now=1000)['status'], 'no_advance')

    def test_missing_and_bad(self):
        self.assertEqual(health.check_capture(self.path, 10, now=1000)['status'], 'missing')
        for text in ('', 'frame=12\n', 'frame=oops\nprogress=continue\n', 'progress=end\n', 'frame=12\nprogress=oops\n'):
            with self.subTest(text=text):
                self.progress(text)
                self.assertEqual(health.check_capture(self.path, 10, now=1000)['status'], 'bad')

    def test_bounded_tail_and_limits(self):
        self.progress('x' * 1000000 + '\nframe=13\nprogress=continue\n')
        self.assertEqual(health.check_capture(self.path, 12, now=1000)['status'], 'healthy')
        for previous, age in ((-1, 3), (0, 0), (0, float('nan'))):
            self.assertEqual(health.check_capture(self.path, previous, max_age=age, now=1000)['status'], 'bad')
        self.assertEqual(health.check_capture(self.path, 12, now=990)['status'], 'bad')

    def test_cli_success_failure_json_and_speed(self):
        self.path.write_text('frame=12\nprogress=continue\n', encoding='ascii')
        for previous, expected in ((10, 0), (12, 1)):
            started = time.monotonic()
            result = subprocess.run([sys.executable, str(SCRIPT), '--progress', str(self.path),
                                     '--after-frame', str(previous)], capture_output=True, text=True, timeout=2)
            self.assertEqual(result.returncode, expected, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'healthy' if expected == 0 else 'no_advance')
            self.assertLess(time.monotonic() - started, 1.0)


if __name__ == '__main__':
    unittest.main()
