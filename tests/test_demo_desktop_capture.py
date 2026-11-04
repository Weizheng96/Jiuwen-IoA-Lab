"""Exercise recorder lifecycle with fake children; never access a real desktop."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

SCRIPT = Path(__file__).resolve().parents[1] / '.agents/skills/demo-video/scripts/capture_desktop.py'
spec = importlib.util.spec_from_file_location('demo_desktop_capture', SCRIPT)
recorder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recorder)


class DesktopCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.ffmpeg = self.folder / 'fake-ffmpeg.exe'
        self.ffmpeg.write_bytes(b'not executable; tests inject Popen')
        self.out = self.folder / 'new-take'

    def run_fake(self, *, code=0, waits=None, log=b'', progress='frame=30\nprogress=end\n', video=b'original'):
        child = Mock(pid=123)
        child.wait.side_effect = waits if waits is not None else [code]

        def launch(command, **kwargs):
            Path(command[-1]).write_bytes(video)
            Path(command[command.index('-progress') + 1]).write_text(progress, encoding='ascii')
            kwargs['stderr'].write(log)
            self.assertIsNone(kwargs['stdin'])  # q is inherited, never disabled or swallowed.
            self.assertEqual(kwargs['stdout'], subprocess.DEVNULL)
            return child

        popen = Mock(side_effect=launch)
        result = recorder.capture(self.ffmpeg, 1, 0, 3, self.out, popen=popen)
        self.assertEqual(json.loads((self.out / 'manifest.json').read_text()), result)
        return result, child, popen

    def test_completed_process_keeps_explicit_binding_and_requires_pixel_review(self):
        result, child, popen = self.run_fake()
        self.assertEqual(result['wrapperExitCode'], 0)
        self.assertEqual(result['ffmpegExitCode'], 0)
        self.assertFalse(result['recordingIntegrityVerified'])
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index('-init_hw_device') + 1], 'd3d11va=external:1')
        self.assertEqual(command[command.index('-filter_hw_device') + 1], 'external')
        self.assertEqual(command[command.index('-filter_complex') + 1],
                         'ddagrab=output_idx=0:framerate=10:draw_mouse=1[v]')
        self.assertEqual(command[command.index('-c:v') + 1], 'h264_nvenc')
        self.assertNotIn('-gpu', command)  # NVENC ordinal is not the D3D11 ordinal.
        self.assertNotIn('-nostdin', command)
        self.assertIn('-n', command)
        child.wait.assert_called_once_with(timeout=18)
        child.kill.assert_not_called()

    def test_nonzero_exit_preserves_partial_original_and_does_not_retry(self):
        result, child, popen = self.run_fake(code=7, video=b'partial', log=b'encoder failed\n')
        self.assertEqual((result['status'], result['ffmpegExitCode'], result['wrapperExitCode']), ('failed', 7, 1))
        self.assertEqual((self.out / 'continuous.mkv').read_bytes(), b'partial')
        self.assertEqual((self.out / 'capture.stderr.log').read_bytes(), b'encoder failed\n')
        popen.assert_called_once()
        child.kill.assert_not_called()

    def test_timeout_kills_only_owned_child_and_preserves_original(self):
        result, child, popen = self.run_fake(waits=[subprocess.TimeoutExpired('ffmpeg', 18), -9], video=b'partial')
        self.assertEqual(result['status'], 'timed_out')
        self.assertTrue(result['timedOut'])
        self.assertEqual(result['ffmpegExitCode'], -9)
        self.assertEqual(result['wrapperExitCode'], 1)
        self.assertEqual((self.out / 'continuous.mkv').read_bytes(), b'partial')
        child.kill.assert_called_once_with()
        self.assertEqual(child.wait.call_args_list[-1].kwargs, {'timeout': 5})
        popen.assert_called_once()

    def test_existing_directory_is_untouched_even_if_empty(self):
        self.out.mkdir()
        popen = Mock()
        with self.assertRaises(FileExistsError):
            recorder.capture(self.ffmpeg, 1, 0, 3, self.out, popen=popen)
        self.assertEqual(list(self.out.iterdir()), [])
        popen.assert_not_called()
        original = self.out / 'continuous.mkv'
        original.write_bytes(b'old take')
        with self.assertRaises(FileExistsError):
            recorder.capture(self.ffmpeg, 1, 0, 3, self.out, popen=popen)
        self.assertEqual(original.read_bytes(), b'old take')

    def test_launch_failure_is_recorded(self):
        popen = Mock(side_effect=OSError('cannot start'))
        result = recorder.capture(self.ffmpeg, 1, 0, 3, self.out, popen=popen)
        self.assertEqual(result['status'], 'start_failed')
        self.assertIsNone(result['ffmpegExitCode'])
        self.assertEqual(result['wrapperExitCode'], 1)
        self.assertTrue((self.out / 'manifest.json').is_file())

    def test_exit_zero_with_capture_error_or_missing_output_is_not_success(self):
        for name, options, expected in (
            ('error', {'log': b'Failed capture image (error 5)\nI/O error\n'}, 'capture_error'),
            ('empty', {'video': b''}, 'incomplete_output'),
            ('unfinished', {'progress': 'frame=30\nprogress=continue\n'}, 'incomplete_output'),
        ):
            with self.subTest(name=name):
                self.out = self.folder / name
                result, _, _ = self.run_fake(**options)
                self.assertEqual(result['status'], expected)
                self.assertEqual(result['ffmpegExitCode'], 0)
                self.assertEqual(result['wrapperExitCode'], 1)

    def test_invalid_limits_and_relative_executable_do_not_create_directory(self):
        for adapter, output, seconds in ((-1, 0, 3), (1, -1, 3), (1, 0, 0),
                                         (1, 0, float('nan')), (1, 0, float('inf')), (1, 0, 43201)):
            with self.subTest(adapter=adapter, output=output, seconds=seconds):
                with self.assertRaises(ValueError):
                    recorder.capture(self.ffmpeg, adapter, output, seconds, self.out)
                self.assertFalse(self.out.exists())
        with self.assertRaises(ValueError):
            recorder.capture(Path('relative.exe'), 1, 0, 3, self.out)
        self.assertFalse(self.out.exists())


if __name__ == '__main__':
    unittest.main()
