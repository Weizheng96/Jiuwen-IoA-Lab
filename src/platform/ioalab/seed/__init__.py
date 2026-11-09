"""Account and category seeds only; never create an agent runtime."""
from .domains import definitions as load_definitions, load_domains

def seed_all(platform):
    from .accounts import ensure_accounts
    domains = load_domains(platform.s.seed_file)
    accounts = ensure_accounts(platform, domains=domains)
    members = {d["category"]["id"]: [] for d in domains}
    for actor, listing in platform.registry.listings().items():
        if listing["category"] in members:
            members[listing["category"]].append(actor)
    platform.discovery.build_domains([d["category"] for d in domains], members)
    return {"isp": len(accounts["isp"]), "isc": len(accounts["isc"]), "domains": len(domains), "services": sum(map(len,members.values()))}
