"""P34 coordination on the existing resident loop; no inference or startup gate.

S02 runs in one bounded sampling unit owned by this coordinator, never inside the
resident loop: ``poll()`` only starts a due sample or consumes a finished one, so
stop signals, scheduling and controls never wait for a host check.
"""
import copy
import math
import threading
import time
from .protocol import RuntimeFault

SAMPLE_INTERVAL=30  # Internal constant (design D38), not a configuration item.


class _Sample:
    def __init__(self,started):
        self.started=started
        self.result=None
        self.abandoned=False
        self.done=threading.Event()


class RuntimeHealth:
    def __init__(self,registry,*,shutdown_seconds=600,clock=time.monotonic,
                 interval=SAMPLE_INTERVAL,check_timeout=None,on_health=None,request_stop=None):
        if (isinstance(shutdown_seconds,bool) or
                not isinstance(shutdown_seconds,(int,float)) or
                (isinstance(shutdown_seconds,float) and not math.isfinite(shutdown_seconds)) or shutdown_seconds <= 0):
            raise ValueError('Use a positive finite unavailable duration')
        self.registry=registry
        self.shutdown_seconds=shutdown_seconds
        self.clock=clock
        self.interval=interval
        self.check_timeout=interval if check_timeout is None else check_timeout
        self.on_health=on_health
        self.request_stop=request_stop
        self._lock=threading.RLock()
        self._sample=None
        self._thread=None
        self._next_check=None
        self._since=None
        self._stopping=False
        self._business_failure=(registry.store.checkpoint('host-health',registry.default) or {}).get('businessFailure')
        self._snapshot={'state':'unknown','continuousUnavailableSeconds':0,
                        'shutdownRequested':False}

    def snapshot(self):
        """P12 reads this projection; no host call, never waits for P35."""
        return copy.deepcopy(self._snapshot)  # replaced whole, never mutated in place

    @property
    def sampling(self):
        """True while an S02 sample (possibly abandoned after its bound) still runs."""
        return self._sample is not None and not self._sample.done.is_set()

    def poll(self):
        """Called only after runtime.start, from the existing resident loop; never blocks on S02.

        A sample that does not finish within its bound is recorded as unknown; its
        late result is discarded. A long or unfinished check never supplies
        evidence that the host was unavailable throughout the interval.
        """
        with self._lock:
            if self._stopping:return self.snapshot()
            now=self.clock()
            sample=self._sample
            if sample is not None:
                if sample.done.is_set():
                    self._sample=None
                    if not sample.abandoned:
                        self._observe(sample.result,at=sample.started)
                        if self._stopping:return self.snapshot()
                    else:
                        # The late raw S02 write must not replace the recorded unknown.
                        self.registry.store.checkpoint_set('host-health',self.registry.default,self.snapshot())
                elif not sample.abandoned and now-sample.started>=self.check_timeout:
                    sample.abandoned=True;self._next_check=now+self.interval
                    self._observe({'state':'unknown','basis':'Runtime machine check did not finish within its bound'},at=now)
            if self._sample is None:
                due=self._next_check is None or now>=self._next_check
                # A short threshold may arrive before the next sample. Recheck S02
                # before committing drain; cached failure alone cannot hide recovery.
                recheck=self._since is not None and now-self._since>=self.shutdown_seconds
                if due or recheck:
                    self._start(now)
                    return self.snapshot()
            if self._since is not None:
                self._snapshot={**self._snapshot,'continuousUnavailableSeconds':max(0,now-self._since)}
            return self.snapshot()

    def _start(self,now):
        sample=_Sample(now)
        self._sample=sample;self._next_check=now+self.interval
        def run():
            try:sample.result=self.registry.check_default()
            except Exception:
                sample.result={'state':'unknown','basis':'Runtime machine check could not be verified'}
            finally:sample.done.set()
        self._thread=threading.Thread(target=run,name='ioa-host-health',daemon=True)
        self._thread.start()

    def close(self,timeout=0.1):
        """P13: stop sampling; True once no S02 sample is still running."""
        with self._lock:
            self._stopping=True
            thread=self._thread
        if thread is not None:thread.join(timeout)
        return not (thread is not None and thread.is_alive())

    def business_feedback(self,host_ref,observation):
        """Trusted normal-business evidence from the original execution owner.

        A per-session failure, user rejection, generic exception or model output
        is not host-global evidence and must not be forwarded as unavailable.
        No alternate host is checked or selected here.
        """
        with self._lock:
            if (host_ref!=self.registry.default or self._stopping or
                    not isinstance(observation,dict) or observation.get('scope')!='host'):
                return self.snapshot()
            if observation.get('state')=='available':self._business_failure=None
            elif observation.get('state')=='unavailable' and observation.get('failureKind') in ('authentication_revoked','account_disabled','policy_disabled'):
                self._business_failure=copy.deepcopy(observation)
            return self._observe(observation,at=self.clock())

    def _observe(self,observation,*,at):
        valid=(isinstance(observation,dict) and
               observation.get('state') in ('available','busy','unavailable','unknown'))
        if not valid:
            observation={'state':'unknown','basis':'Invalid runtime health evidence'}
        state=observation['state']
        if state=='available' and self._business_failure:
            observation=copy.deepcopy(self._business_failure)
            state='unavailable'
        if state=='unavailable' and not (observation.get('basis') or observation.get('evidence')):
            observation={'state':'unknown','basis':'Missing confirmed unavailability evidence'}
            state='unknown'
        if state=='unavailable':
            if self._since is None:self._since=at
        else:self._since=None
        # Continuous unavailability is measured between sample start times.
        elapsed=0 if self._since is None else max(0,at-self._since)
        shutdown=elapsed>=self.shutdown_seconds
        self._snapshot={**copy.deepcopy(observation),'continuousUnavailableSeconds':elapsed,
                        'shutdownRequested':shutdown,'businessFailure':self._business_failure}
        # Recorded before P35 so its readiness fence reads this sample.
        self.registry.store.checkpoint_set('host-health',self.registry.default,self.snapshot())
        if self.on_health is not None:
            try:self.on_health(self.registry.default,state)
            except RuntimeFault as exc:
                self._snapshot={**self._snapshot,'policyError':{'code':exc.code,'recovery':exc.recovery}}
                self.registry.store.checkpoint_set('host-health',self.registry.default,self.snapshot())
        if shutdown and not self._stopping:
            self._stopping=True
            if self.request_stop is not None:self.request_stop()
        return self.snapshot()
