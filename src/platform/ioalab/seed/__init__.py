"""Account and category seeds only; never create an agent runtime."""
from .domains import definitions as load_definitions, load_domains

def seed_all(platform, only=None):
    from .accounts import ensure_accounts
    domains = load_domains(platform.s.seed_file)
    accounts = ensure_accounts(platform, domains=domains)
    members = {d["category"]["id"]: [] for d in domains}
    for row in platform.db.rows("SELECT id,definition FROM agents WHERE role='service'"):
        from ..store import jload
        category = jload(row["definition"]).get("category")
        if category in members:
            members[category].append(row["id"])
    platform.discovery.build_domains([d["category"] for d in domains], members)
    return {"isp": len(accounts["isp"]), "isc": len(accounts["isc"]), "domains": len(domains), "services": sum(map(len,members.values()))}
