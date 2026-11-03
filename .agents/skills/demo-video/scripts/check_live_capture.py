"""Check one FFmpeg progress file; no waits, video decoding or business calls."""
import argparse
import json
import math
import os
from pathlib import Path
import time

MAX_READ = 65536


def check_capture(path, previous_frame, *, max_age=3.0, now=None):
    if type(previous_frame) is not int or previous_frame < 0 or not math.isfinite(max_age) or max_age <= 0:
        return {'status': 'bad', 'reason': 'invalid_limits'}
    try:
        with Path(path).open('rb') as stream:
            stat = os.fstat(stream.fileno())
            stream.seek(max(0, stat.st_size - MAX_READ))
            tail = stream.read(MAX_READ).decode('ascii')
    except FileNotFoundError:
        return {'status': 'missing', 'reason': 'progress_missing'}
    except (OSError, UnicodeError):
        return {'status': 'bad', 'reason': 'progress_unreadable'}
    latest, block = None, {}
    for line in tail.splitlines():
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        block[key.strip()] = value.strip()
        if key.strip() == 'progress':
            latest, block = block, {}
    if not latest or latest.get('progress') not in ('continue', 'end'):
        return {'status': 'bad', 'reason': 'complete_progress_block_missing'}
    raw_frame = latest.get('frame', '')
    if not raw_frame.isdigit() or len(raw_frame) > 20:
        return {'status': 'bad', 'reason': 'invalid_frame'}
    frame = int(raw_frame)
    age = (time.time() if now is None else now) - stat.st_mtime
    if not math.isfinite(age) or age < -1:
        return {'status': 'bad', 'reason': 'invalid_progress_time'}
    result = {'frame': frame, 'previousFrame': previous_frame,
              'ageSeconds': round(max(0, age), 3), 'progress': latest['progress']}
    if latest['progress'] == 'end':
        return {**result, 'status': 'ended', 'reason': 'capture_not_live'}
    if age > max_age:
        return {**result, 'status': 'stale', 'reason': 'progress_not_fresh'}
    if frame <= previous_frame:
        return {**result, 'status': 'no_advance', 'reason': 'frame_did_not_increase'}
    return {**result, 'status': 'healthy', 'reason': 'fresh_frames_advanced'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--progress', type=Path, required=True)
    parser.add_argument('--after-frame', type=int, required=True, help='Frame from the previous check of this take')
    parser.add_argument('--max-age-seconds', type=float, default=3.0)
    args = parser.parse_args()
    result = check_capture(args.progress, args.after_frame, max_age=args.max_age_seconds)
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0 if result['status'] == 'healthy' else 1


if __name__ == '__main__':
    raise SystemExit(main())
