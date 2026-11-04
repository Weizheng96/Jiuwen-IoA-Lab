"""Bounded retries for read-only A2A task polling; writes are never replayed."""
import time
import httpx
from a2a.client.errors import A2AClientError
from a2c.binding import A2ATransport


class TaskPollingTransport(A2ATransport):
    def get_task(self, to, task_id):
        for attempt in range(3):
            try:
                return super().get_task(to,task_id)
            except A2AClientError as exc:
                if attempt==2 or not isinstance(exc.__cause__,httpx.RequestError):
                    raise
                # Same existing task, no request.submit, signature, fee, or delivery replay.
                time.sleep(.15*(attempt+1))
