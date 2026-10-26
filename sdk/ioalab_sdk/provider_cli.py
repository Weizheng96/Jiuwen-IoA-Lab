"""Service skill calls this participant-side job capability, never a platform proxy."""
import argparse
import json
import os
from pathlib import Path
import httpx


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("operation",choices=["inspect","selftest","deliver","quote"])
    p.add_argument("--file",help="JSON file containing a deliverable or quote")
    a=p.parse_args(argv)
    url=os.environ["IOALAB_JOB_URL"]
    headers={"Authorization":"Bearer "+os.environ["IOALAB_JOB_TOKEN"]}
    body=json.loads(Path(a.file).read_text(encoding="utf-8")) if a.file else {}
    with httpx.Client(timeout=120,trust_env=False) as client:
        if a.operation=="inspect":
            r=client.get(url,headers=headers)
        else:
            r=client.post(url+"/"+a.operation,json=body,headers=headers)
        r.raise_for_status()
        print(json.dumps(r.json(),ensure_ascii=False))


if __name__=="__main__":
    main()
