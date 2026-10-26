"""Jiuwen process CLI adapter; instantiated once, executed only for a job."""
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path


class SwarmEngine:
    def __init__(self, command=None):
        self.command=command or json.loads(os.environ.get("IOALAB_SWARM_COMMAND", '["jiuwenswarm-process"]'))
        if not isinstance(self.command,list) or not self.command or not all(isinstance(s,str) and s for s in self.command):
            raise ValueError("IOALAB_SWARM_COMMAND must be a JSON argv array")
        self.processes=set()
        self.lock=threading.Lock()

    def check(self):
        if shutil.which(self.command[0]) is None:
            raise RuntimeError("Jiuwen process CLI is not installed; configure IOALAB_SWARM_COMMAND")
        result=subprocess.run([*self.command,'--help'],capture_output=True,text=True,encoding='utf-8',timeout=20,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode or '--run-json' not in result.stdout:
            raise RuntimeError('Jiuwen process CLI must support --run-json machine mode')

    def run(self, job, definition, workspace, url, token):
        skill=(Path(__file__).parent/"skills/ioalab-provider/SKILL.md").read_text(encoding="utf-8")
        request={"schema_version":"0.1","request_id":job["id"],
                 "input":"执行本单IoA服务任务。先调用SDK inspect，再按任务类型调用quote或selftest与deliver。SDK命令使用环境中的IOALAB_SDK_PYTHON。",
                 "agent":{"name":"ioa_service","instructions":skill,"tools":["*"],"max_iterations":16},
                 "workspace":{"cwd":str(workspace),"project_dir":str(workspace)},"timeout_seconds":300}
        env={**os.environ,"IOALAB_JOB_URL":url,"IOALAB_JOB_TOKEN":token,"PYTHONUTF8":"1"}
        env.setdefault("IOALAB_SDK_PYTHON",__import__('sys').executable)
        proc=subprocess.Popen([*self.command,"--run-json","-"],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,text=True,encoding="utf-8",env=env,cwd=workspace,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        with self.lock:
            self.processes.add(proc)
        try:
            output,errors=proc.communicate(json.dumps(request,ensure_ascii=False),timeout=330)
            records=[json.loads(line) for line in output.splitlines() if line.strip()]
            results=[r for r in records if r.get("type")=="result"]
            if proc.returncode or len(results)!=1 or results[0].get("status")!="completed":
                raise RuntimeError("Swarm task failed or requires interaction; configure service tool permissions")
            return {"session":results[0].get("session_id"),"usage":results[0].get("usage",{})}
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise RuntimeError("Swarm execution deadline exceeded")
        finally:
            with self.lock:
                self.processes.discard(proc)

    def close(self):
        with self.lock:
            for proc in list(self.processes):
                proc.terminate()
