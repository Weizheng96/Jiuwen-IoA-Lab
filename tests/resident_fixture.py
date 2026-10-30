"""Explicit test assembly for a real resident process; never installed as a product."""
import os
from pathlib import Path
import sys
import time

from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_sdk.runtime.scheduling import JobHandler,JobOutcome


def main():
    if os.getenv('IOALAB_TEST_FIXTURE')!='resident':raise RuntimeError('This is only a deterministic process fixture')
    receipt=Path(os.environ['IOALAB_TEST_RECEIPT'])
    release=receipt.with_suffix('.release')
    def work(job):
        with receipt.open('a',encoding='utf-8') as stream:
            stream.write(job['id']+'\n')
            stream.flush()
            os.fsync(stream.fileno())
        while not release.exists():time.sleep(.05)
        return JobOutcome('completed',{'receipt':job['id']},job['session'])
    def enqueue(runtime,operation,args):
        value=runtime.scheduler.submit(runtime.identities[0].agent_id,args['interaction'],'fixture',args.get('payload',{}),
                    operation_id=operation,session=args.get('session'))
        return {'status':'accepted' if value['status']=='queued' else value['status'],'data':value}
    runtime=ResidentRuntime(RuntimeConfig.load(sys.argv[1]),operations={'test.enqueue':enqueue},
                            jobs={'fixture':JobHandler(work,'fixture-v1')})
    try:runtime.run()
    finally:runtime.close()


if __name__=='__main__':main()
