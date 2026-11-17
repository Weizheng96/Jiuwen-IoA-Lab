"""Content-free, bounded diagnostics owned by one Claude SDK request.

No stderr bodies, prompts, tool arguments/results, credentials or paths persist.
SDK execution facts remain separate from generated content and business effects.
"""
from collections import deque
import re
import threading
import time


class RoundDiagnostics:
    def __init__(self):
        self._start = time.monotonic()
        self._lock = threading.RLock()
        self._events = deque(maxlen=128)
        self._stderr_count = 0
        self._stderr_bytes = 0
        self._signals = set()
        self._counts = {}

    def event(self, kind):
        # Callers pass fixed strings only, never external message fields.
        allowed = {'connecting', 'query_sent', 'session_bound', 'tool_enter',
                   'tool_return', 'tool_error', 'terminal_success',
                   'terminal_error', 'cancel_requested', 'exception', 'timeout',
                   'stream_ended'}
        if kind not in allowed:
            raise ValueError('Unknown diagnostic event')
        with self._lock:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            self._events.append({'kind': kind, 'elapsedMs': int((time.monotonic()-self._start)*1000)})

    def stderr(self, line):
        # Signals are untrusted CLI text observations, never causal verdicts.
        if not isinstance(line, str):
            return
        with self._lock:
            self._stderr_count += 1
            self._stderr_bytes += len(line.encode('utf-8', errors='replace'))
            for code in ('401', '403', '429', '500', '502', '503', '504'):
                if re.search(r'(?<!\d)'+code+r'(?!\d)', line):
                    self._signals.add('observed_numeric_'+code)

    def snapshot(self):
        with self._lock:
            return {'schema': 1, 'elapsedMs': int((time.monotonic()-self._start)*1000),
                    'counts': dict(self._counts), 'events': [dict(v) for v in self._events],
                    'stderrChunks': self._stderr_count, 'stderrBytes': self._stderr_bytes,
                    'stderrSignals': sorted(self._signals)}
