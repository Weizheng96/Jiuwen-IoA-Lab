"""Explicit verification entry for the single GitCode tag installation."""
import runpy
from pathlib import Path

if __name__=='__main__':
    runpy.run_path(str(Path(__file__).resolve().parents[1]/'src/client/bootstrap.py'),run_name='__main__')
