"""P34 coordination on the existing resident loop; no inference or startup gate."""
import copy
import math
import time
from .protocol import RuntimeFault


class RuntimeHealth:
    def __init__(self, registry, *, shutdown_seconds=600, clock=time.monotonic,
                 interval=5, on_health=None, request_stop=None):
        if (isinstance(shutdown_seconds,bool) or
                not isinstance(shutdown_seconds,(int,float)) or
                (isinstance(shutdown_seconds,float) and not math.isfinite(shutdown_seconds)) or shutdown_seconds <= 0):
            raise ValueError('Use a positive finite unavailable duration')
        self.registry=registry
        self.shutdown_seconds=shutdown_seconds
        self.clock=clock
        self.interval=interval
        self.on_health=on_health
        self.request_stop=request_stop
        self._next_check=None
        self._since=None
        self._stopping=False
        self._business_failure=(registry.store.checkpoint('host-health',registry.default) or {}).get('businessFailure')
        self._snapshot={'state':'unknown','continuousUnavailableSeconds':0,
                        'shutdownRequested':False}

    def snapshot(self):
        """P12 reads this projection; no host call."""
        return copy.deepcopy(self._snapshot)

    def poll(self):
        """Called only after runtime.start, from the existing resident loop.

        S02 itself must be bounded by its adapter. A long check never supplies
        evidence that the host was unavailable throughout the check interval.
        """
        if self._stopping:return self.snapshot()
        now=self.clock()
        if self._next_check is not None and now < self._next_check:
            if self._since is not None:
                self._snapshot['continuousUnavailableSeconds']=max(0,now-self._since)
            if self._since is None or now-self._since<self.shutdown_seconds:
                return self.snapshot()
            # A short configured threshold may arrive before the next sample.
            # Recheck S02 before committing drain; cached failure alone cannot
            # hide recovery (or a now-busy/unknown host) during this interval.
        try:observation=self.registry.check_default()
        except Exception:
            observation={'state':'unknown','basis':'Runtime machine check could not be verified'}
        self._next_check=self.clock()+self.interval
        return self._observe(observation,now=self.clock())

    def business_feedback(self,host_ref,observation):
        """Trusted normal-business evidence from the original execution owner.

        A per-session failure, user rejection, generic exception or model output
        is not host-global evidence and must not be forwarded as unavailable.
        No alternate host is checked or selected here.
        """
        if (host_ref!=self.registry.default or self._stopping or
                not isinstance(observation,dict) or observation.get('scope')!='host'):
            return self.snapshot()
        if observation.get('state')=='available':self._business_failure=None
        elif observation.get('state')=='unavailable' and observation.get('failureKind') in ('authentication_revoked','account_disabled','policy_disabled'):
            self._business_failure=copy.deepcopy(observation)
        return self._observe(observation,now=self.clock())

    def _observe(self,observation,*,now):
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
            if self._since is None:self._since=now
        else:self._since=None
        elapsed=0 if self._since is None else max(0,now-self._since)
        self._snapshot={**copy.deepcopy(observation),'continuousUnavailableSeconds':elapsed,
                        'shutdownRequested':elapsed>=self.shutdown_seconds,'businessFailure':self._business_failure}
        self.registry.store.checkpoint_set('host-health',self.registry.default,self.snapshot())
        if self.on_health is not None:
            try:self.on_health(self.registry.default,state)
            except RuntimeFault as exc:self._snapshot['policyError']={'code':exc.code,'recovery':exc.recovery}
        self.registry.store.checkpoint_set('host-health',self.registry.default,self.snapshot())
        if self._snapshot['shutdownRequested']:
            self._stop()
        return self.snapshot()

    def _stop(self):
        if self._stopping:return
        self._stopping=True
        self._snapshot['shutdownRequested']=True
        self.registry.store.checkpoint_set('host-health',self.registry.default,self.snapshot())
        if self.request_stop is not None:self.request_stop()
