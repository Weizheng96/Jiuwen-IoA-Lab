"""Build two lightweight role skills; client binaries are installed once via pip."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]

def build(output,*,root=ROOT,python=None,config=None):
    sys.path.insert(0,str(Path(root)/'src/client'))
    from ioalab_client import export_skills
    return export_skills(output,source=Path(root)/'src/client/skills',python=python or sys.executable,config=config)
