"""Read only domain metadata from the official content bundle; no agent imports."""
import json
from pathlib import Path
DOMAINS_DIR = Path(__file__).resolve().parents[2] / "official-services/domains"
def load_domains():
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(DOMAINS_DIR.glob("*.json"))]
def definitions():
    return [s for d in load_domains() for s in d["services"]]
