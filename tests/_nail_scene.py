"""Load the Demo-owned scene source without a global scenario import alias."""
import importlib.util
from pathlib import Path

path=Path(__file__).resolve().parents[1]/'demo/delegation-provider/src/scenario/definitions.py'
spec=importlib.util.spec_from_file_location('demo_nail_definitions',path)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
nail_services=module.nail_services
