"""Administrator overview reads composed from current component facts; no writes."""
import time
from importlib.metadata import version

from ..payments import money


class OverviewQueries:
    def __init__(self,db,*,services,discovery,market,settings,bus,discovery_enabled):
        self.db,self.service_views,self.discovery,self.market=db,services,discovery,market
        self.s,self.bus,self.discovery_enabled=settings,bus,discovery_enabled

    def stats(self):
        count = lambda table, where="1": self.db.one(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}")["n"]
        day0=time.mktime(time.strptime(time.strftime('%Y-%m-%d'),'%Y-%m-%d'))
        transfers=self.db.rows('SELECT amount FROM transfers WHERE at>=?',day0)
        visible=self.db.rows('SELECT digest,created FROM observation_contracts')
        effective_facts=self.db.rows('SELECT digest FROM delegation_contracts')
        ledger=self.market.ledger()
        services=self.service_views.list()
        return {"assistants": count("agents", "role='assistant'"), "services": count("agents", "role='service'"),
                "runnable": sum(row["status"]=="online" for row in services), "users": count("users"), "needs": 0,
                "orders_today": sum(row["created"]>=day0 for row in visible), "transfers_today": len(transfers), "completed": sum(ledger.get(row["digest"],{}).get("outcome")=="completed" for row in effective_facts),
                "arbitrated": count("rulings"), "declined": 0, "success_rate": None, "avg_duration_s": None,
                "volume_today": str(sum(money(r["amount"]) for r in transfers)),
                "circulating": str(sum(money(r["balance"]) for r in self.db.rows("SELECT balance FROM wallets"))),
                "llm_calls": None, "tokens": None, "discovery": self.discovery.last_mode, "taxonomy": self.discovery.taxonomy_state(),
                "run": self.bus.run_id, "mode": self.s.mode}

    def components(self):
        stats = self.stats()
        reports=self.service_views.list()
        connected=[r for r in reports if r['connected']]
        message_states={r['state']:r['n'] for r in self.db.rows("SELECT request_state AS state,COUNT(*) AS n FROM delivery_messages WHERE kind='message.request' GROUP BY request_state")}
        return {"messaging":{"interfaces":self.db.one('SELECT COUNT(*) AS n FROM message_interfaces')['n'],"states":message_states,"pending":sum(v for k,v in message_states.items() if k not in ('completed','rejected','expired'))},"registry": {"services": stats["services"], "online": stats["runnable"], "categories": len(self.discovery.categories()),
                             "taxonomy": stats["taxonomy"], "last_mode": self.discovery.last_mode},
                "templates": {"endorsed": self.market.counts()['endorsed'], "snapshots": self.market.counts()['snapshots'],
                              "publications": len(self.db.rows("SELECT actor FROM registry_projection"))},
                "ledger": {"completed": stats["completed"], "rulings": stats["arbitrated"], "declined": 0},
                "payment": {"transfers_today": stats["transfers_today"], "volume_today": stats["volume_today"],
                            "circulating": stats["circulating"], "wallets": stats["users"], "transfers_total":len(self.db.rows('SELECT id FROM transfers'))},
                "services": {"online": stats["runnable"],"observed":len(connected),"unknown":len(reports)-len(connected),
                             "working":None,"queued":None,"sessions":None,
                             "busy": [], "location": "independent-process"},
                "assistants": {"total": stats["assistants"], "external_driven": stats["assistants"], "active_needs": 0,
                               "pending_confirmations": 0, "runtime": False},
                "llm": {"key_configured": self.discovery_enabled, "model": self.s.a2x_model, "a2x_model": self.s.a2x_model,
                        "embed_model": self.s.embedding_model, "calls": None, "tokens": None, "concurrency": self.s.llm_concurrency},
                "sandbox": {"concurrency": self.s.sandbox_concurrency, "available": None},
                "bus": {"run": self.bus.run_id, "seq": (self.db.one("SELECT MAX(seq) AS n FROM events") or {}).get("n") or 0,
                        "events": len(self.bus.recent(2000)), "subscribers": len(self.bus.subscribers)},
                "traffic": {"level": "external", "enabled": False},
                "config": {"mode": self.s.mode, "public_url": self.s.public_url, "registration": self.s.registration,
                           "login_credit": self.s.login_credit, "isc_count": self.s.isc_count,
                           "version": version("ioalab")}}
