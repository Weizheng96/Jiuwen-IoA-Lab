"""Sole background reminder that opens the local confirmation page for pending rows.

It reads only the original confirmation table; no second approval store or
notification queue. An open request is not visibility, reading or a decision.
"""
import threading
import time

from .protocol import RuntimeFault


class UserPending:
    def __init__(self,confirmations,open_page,*,clock=time.monotonic,coalesce=.2,interval=30.,retry=60.,poll=5.):
        self.confirmations,self._open_page,self._clock=confirmations,open_page,clock
        self._coalesce,self._interval,self._retry,self._poll=coalesce,interval,retry,poll
        self._wake=threading.Event();self._stop=threading.Event();self._lock=threading.Lock()
        self._thread=None
        # In-memory only: a restart reminds once for rows still pending.
        self._covered=None
        self._batch=None
        self._next=0.
        self._attempt=None
        confirmations.on_created(self.wake)

    def start(self):
        with self._lock:
            if self._thread is not None:raise RuntimeFault('id_conflict','Pending reminder already started')
            self._thread=threading.Thread(target=self._run,name='ioa-user-pending',daemon=True)
            self._thread.start()
        return self

    def wake(self):
        self._wake.set()

    def marker(self):
        return self.confirmations.pending_marker()['latest']

    def covered(self,latest):
        """Rows up to `latest` were on the page when an open request succeeded."""
        if latest is None:return
        with self._lock:
            if self._covered is None or tuple(latest)>tuple(self._covered):self._covered=tuple(latest)

    def status(self):
        with self._lock:attempt=dict(self._attempt) if self._attempt else None
        return {'pendingCount':self.confirmations.pending_marker()['count'],'lastAttempt':attempt}

    def close(self,*,timeout=5.):
        self._stop.set();self._wake.set()
        with self._lock:thread=self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():raise RuntimeFault('not_ready','Keep the store until the pending reminder stops')

    def _run(self):
        while not self._stop.is_set():
            self._wake.clear()
            try:delay=self._step()
            except RuntimeFault:delay=self._poll
            self._wake.wait(delay)

    def _step(self):
        with self._lock:covered=self._covered
        marker=self.confirmations.pending_marker(after=covered)
        now=self._clock()
        if not marker['fresh']:
            self._batch=None
            return self._poll
        if self._batch is None:self._batch=now+self._coalesce
        due=max(self._batch,self._next)
        if now<due:return min(self._poll,due-now)
        if self._stop.is_set():return 0
        self._next=now+self._interval
        # No store, flow or model lock is held while the OS opens the browser.
        try:
            if not self._open_page():raise RuntimeFault('not_ready','The local user browser could not be opened','configure_browser')
        except RuntimeFault as exc:
            self._next=now+self._retry
            attempt={'status':'failed','code':exc.code}
        else:
            self.covered(marker['latest'])
            self._batch=None
            attempt={'status':'open_requested'}
        with self._lock:self._attempt=attempt
        return min(self._poll,max(0.,self._next-self._clock()))
