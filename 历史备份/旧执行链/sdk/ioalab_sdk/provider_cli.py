"""Service skill calls this participant-side job capability, never a platform proxy."""
import argparse
import json
import os
from pathlib import Path


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("operation",choices=["inspect","selftest","deliver","quote"])
    p.add_argument("--file",help="JSON file containing a deliverable or quote")
    a=p.parse_args(argv)
    url=os.environ["IOALAB_JOB_URL"]
    body=json.loads(Path(a.file).read_text(encoding="utf-8-sig")) if a.file else {}
    from .provider_capability import ProviderJob
    print(json.dumps(ProviderJob(url,os.environ['IOALAB_JOB_TOKEN']).call(a.operation,body),ensure_ascii=False))


if __name__=="__main__":
    main()
