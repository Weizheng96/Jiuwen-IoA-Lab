"""Record one explicitly selected Windows desktop with DDA/NVENC; q stops normally.

Requires an interactive stdin for q. --seconds is also a hard recording limit
(at most 12 hours); a stalled child is killed after a further 15 seconds.
No device discovery, source/encoder fallback, pixel validation or publication.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import time

MAX_SECONDS = 43200
EXIT_GRACE_SECONDS = 15
KILL_GRACE_SECONDS = 5
MAX_READ = 65536


def validate(ffmpeg, adapter, output, seconds):
    ffmpeg = Path(ffmpeg)
    if not ffmpeg.is_absolute() or not ffmpeg.is_file():
        raise ValueError('--ffmpeg must be an absolute path to an existing executable')
    if type(adapter) is not int or adapter < 0 or type(output) is not int or output < 0:
        raise ValueError('--adapter and --output must be nonnegative integers')
    if not math.isfinite(seconds) or not 0 < seconds <= MAX_SECONDS:
        raise ValueError('--seconds must be finite, positive and at most 43200')
    return ffmpeg


def command_for(ffmpeg, adapter, output, seconds, folder):
    return [str(ffmpeg), '-hide_banner', '-loglevel', 'verbose', '-stdin',
            '-nostats', '-stats_period', '1', '-n',
            '-init_hw_device', f'd3d11va=external:{adapter}',
            '-filter_hw_device', 'external', '-filter_complex_threads', '1',
            '-filter_complex', f'ddagrab=output_idx={output}:framerate=10:draw_mouse=1[v]',
            '-map', '[v]', '-an', '-t', str(seconds),
            '-c:v', 'h264_nvenc', '-preset', 'p4', '-tune', 'hq',
            '-rc', 'vbr', '-cq', '18', '-b:v', '0', '-bf', '0', '-g', '20',
            '-rc-lookahead', '0', '-spatial-aq', '0', '-temporal-aq', '0',
            '-multipass', 'disabled', '-progress', str(folder / 'progress.txt'),
            str(folder / 'continuous.mkv')]


def tail(path):
    try:
        with path.open('rb') as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - MAX_READ))
            return stream.read(MAX_READ).decode('utf-8', 'replace')
    except FileNotFoundError:
        return ''


def final_progress(text):
    latest, block = {}, {}
    for line in text.splitlines():
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        block[key.strip()] = value.strip()
        if key.strip() == 'progress':
            latest, block = block, {}
    return latest


def capture(ffmpeg, adapter, output, seconds, out, *, popen=None):
    ffmpeg = validate(ffmpeg, adapter, output, seconds)
    folder = Path(out).resolve()
    # Atomic refusal includes an existing empty directory; never alter an old take.
    folder.mkdir(parents=True, exist_ok=False)
    command = command_for(ffmpeg, adapter, output, seconds, folder)
    manifest = {'schemaVersion': 1, 'status': 'starting',
                'startedAt': datetime.now(timezone.utc).isoformat(),
                'adapter': adapter, 'output': output, 'framesPerSecond': 10,
                'maximumRecordingSeconds': seconds,
                'maximumWaitSeconds': seconds + EXIT_GRACE_SECONDS,
                'command': command, 'ffmpegExitCode': None,
                'recording': 'continuous.mkv', 'progress': 'progress.txt',
                'stderr': 'capture.stderr.log', 'timedOut': False,
                'recordingIntegrityVerified': False,
                'verificationRequired': 'Fresh advancing progress plus actual source pixels and privacy review'}
    manifest_path = folder / 'manifest.json'

    def save():
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + '\n',
                                 encoding='utf-8')

    save()
    started = time.monotonic()
    process = None
    with (folder / 'capture.stderr.log').open('xb') as stderr:
        try:
            # Inherit stdin: q typed/sent to this session reaches FFmpeg directly.
            process = (popen or subprocess.Popen)(command, stdin=None,
                                                  stdout=subprocess.DEVNULL, stderr=stderr)
            manifest.update(status='recording', pid=process.pid)
            save()
            try:
                manifest['ffmpegExitCode'] = process.wait(timeout=seconds + EXIT_GRACE_SECONDS)
                manifest['status'] = 'process_completed' if manifest['ffmpegExitCode'] == 0 else 'failed'
            except subprocess.TimeoutExpired:
                manifest.update(status='timed_out', timedOut=True)
                process.kill()  # Only this explicitly created child, never a process-name sweep.
                manifest['ffmpegExitCode'] = process.wait(timeout=KILL_GRACE_SECONDS)
            except KeyboardInterrupt:
                manifest['status'] = 'interrupted'
                try:
                    manifest['ffmpegExitCode'] = process.wait(timeout=KILL_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    process.kill()
                    manifest['ffmpegExitCode'] = process.wait(timeout=KILL_GRACE_SECONDS)
        except OSError as exc:
            manifest.update(status='start_failed' if process is None else 'io_failed',
                            error=f'{type(exc).__name__}: {exc}')
        except subprocess.TimeoutExpired:
            manifest.update(status='stop_timeout', error='Child did not exit after kill; inspect the recorded pid')

    progress = final_progress(tail(folder / 'progress.txt'))
    log = tail(folder / 'capture.stderr.log').lower()
    markers = [text for text in ('failed capture image', 'failed to capture', 'i/o error') if text in log]
    video = folder / 'continuous.mkv'
    manifest.update(finalProgress=progress, stderrCaptureErrors=markers,
                    recordingBytes=video.stat().st_size if video.is_file() else 0,
                    elapsedSeconds=round(time.monotonic() - started, 3),
                    endedAt=datetime.now(timezone.utc).isoformat())
    if manifest['status'] == 'process_completed':
        if markers:
            manifest['status'] = 'capture_error'
        elif (manifest['recordingBytes'] == 0 or progress.get('progress') != 'end'
              or not progress.get('frame', '').isdigit() or int(progress['frame']) <= 0):
            manifest['status'] = 'incomplete_output'
    manifest['wrapperExitCode'] = 0 if manifest['status'] == 'process_completed' else 1
    save()
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ffmpeg', type=Path, required=True)
    parser.add_argument('--adapter', type=int, required=True)
    parser.add_argument('--output', type=int, required=True)
    parser.add_argument('--seconds', type=float, required=True)
    parser.add_argument('--out', type=Path, required=True, help='New private take directory; must not exist')
    args = parser.parse_args(argv)
    try:
        result = capture(args.ffmpeg, args.adapter, args.output, args.seconds, args.out)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return result['wrapperExitCode']


if __name__ == '__main__':
    raise SystemExit(main())
