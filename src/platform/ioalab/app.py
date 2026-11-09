"""IoA infrastructure only. Contract clients and service workers live outside this process."""
from __future__ import annotations
import asyncio
import logging
from typing import Any
import httpx
from starlette.responses import JSONResponse
from a2c.binding import A2C_EXTENSION, AP2_EXTENSION, A2ATransport, build_app
from a2c.common import Directory
from .config import Settings
from .events import EventBus, TracingTransport, trace_endpoint
from .keys import KeyStore
from .registry.discovery import Discovery
from .delegation.service import MarketService
from .payments import SimPaymentProvider, Wallets
from .identity import Auth
from .registry.models import DiscoveryModels
from .delegation.models import ArbitrationModels
from .delegation.packages import PackageStore
from .storage import DB, DataOwner, SqliteStore
log = logging.getLogger(__name__)

class LoopbackTransport(httpx.AsyncHTTPTransport):
    """发往本平台 PUBLIC_URL 的请求改走回环地址（Host 头保留对外地址）：部署在 Nginx 后面时进程内互调不绕圈、不依赖域名解析。"""

    def __init__(self, public: str, local: str) -> None:
        super().__init__()
        self.public, self.local = httpx.URL(public), httpx.URL(local)

    @staticmethod
    def _port(u: httpx.URL) -> int:
        return u.port or (443 if u.scheme == "https" else 80)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        u = request.url
        if self.public != self.local and u.scheme == self.public.scheme and u.host == self.public.host and self._port(u) == self._port(self.public):
            request.url = u.copy_with(scheme=self.local.scheme, host=self.local.host, port=self._port(self.local))
        return await super().handle_async_request(request)


class A2ARouter:
    """ASGI：/a2a/<name>/… 分发到已装配的公共协议与身份应用。"""

    def __init__(self, serve) -> None:
        self.serve = serve
        self.apps: dict[str, Any] = {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        from starlette.routing import get_route_path
        path = get_route_path(scope)                 # 相对本挂载点（/a2a）的路径；scope["path"] 保持完整
        parts = path.strip("/").split("/", 2) if path.strip("/") else []
        name = parts[0] if parts else ""
        app = self.apps.get(name)
        if app is None:
            resp = JSONResponse({"error": "no such A2A endpoint"}, status_code=404)
            await resp(scope, receive, send)
            return
        sub = dict(scope)
        sub["root_path"] = scope.get("root_path", "") + "/" + name    # 子应用按 root_path 之后的部分匹配路由
        await self.serve(app, sub, receive, send)


class Platform:
    def __init__(self, settings: Settings, *, configuration=None):
        self.s = settings
        self.configuration = configuration
        self._owner = DataOwner(settings.data)
        self._closed = False
        self._closing = False
        self._workers = set()
        self._shutdown_task = None
        self.started = False
        self.background = []
        self.transports = []
        try:
            self._initialize()
        except BaseException:
            self.close()
            raise

    def _initialize(self):
        settings = self.s
        settings.dirs()
        self._pin_public_url()
        settings.write_a2x_key()
        self.db = DB(settings.data / "ioalab.sqlite")
        from .migrations import migrate
        migrate(self.db)
        self.bus = EventBus(self.db, settings.data / "runs")
        if self.configuration is not None:
            self.configuration.audit=lambda actor,previous,revision,changed:self.bus.emit('configuration','configuration.update',sender=actor,
                outcome='saved',detail={'previousRevision':previous,'revision':revision,'fields':changed})
        self.wallets, self.auth = Wallets(self.db), Auth(self.db, settings)
        from .operations import AccountOperations,LedgerOperations
        self.accounts=AccountOperations(self.db,self.auth,self.wallets,settings,self.bus.emit)
        self.ledger_operations=LedgerOperations(self.wallets,self.bus.emit)
        from .identity import IdentityDirectory
        self.identities=IdentityDirectory(self.db,self.auth,settings)
        from .registry import RegistryView
        self.registry=RegistryView(self.db,self.identities)
        self.packages = PackageStore(settings.data / "packages")
        self.discovery_models = DiscoveryModels(settings)
        self.arbitration_models = ArbitrationModels(settings)
        self.router = A2ARouter(self._serve_owned)
        self.transports=[]
        self.keys = Directory(fetch=self._transport("directory").jwks)
        self.keystore = KeyStore(settings.data / "keys")
        self.market_id, self.pay_id = settings.base_url + "/a2a/market", settings.base_url + "/a2a/pay"
        self.payment_provider = {"id": self.pay_id, "jwksUri": self.pay_id + "/.well-known/jwks.json"}
        self.market_signer = self.keystore.ed25519(self.market_id, name="market", kid="m1")
        self.pay_signer = self.keystore.ed25519(self.pay_id, name="pay", kid="pay1")
        self.pay_ec = self.keystore.ec("pay")
        self.keys.add_signer(self.market_signer)
        self.keys.add_signer(self.pay_signer)
        self.discovery = Discovery(settings.data / "a2x", a2x_home=settings.a2x_home,
                                   llm_configured=settings.a2x_configured(), embed_fn=self.discovery_models.embed if self.discovery_models.enabled else None)
        self.market = MarketService(self.market_signer, keys=self.keys, fetch=self.packages.fetch,
                                    store=SqliteStore(self.db, "market"), db=self.db, models=self.arbitration_models.ports())
        from .payments.scopes import ScopeLedger
        self.scopes=ScopeLedger(self.db,self.identities,settings.base_url,keys=self.keys,
                                template=self.market.template,category=self.registry.category_in)
        from .identity import EnrollmentRegistry
        saved_keys,_=self.identities.saved_keys()
        self.identities.enrollment=EnrollmentRegistry(self.db,settings,existing_keys=saved_keys)
        from .web.identity import public_identity_app
        from .registry.publication import ServicePublications
        from .registry.categories import CategoryPreview
        from .operations import ServiceOperations
        self.category_preview=CategoryPreview(self.discovery.categories,lambda service,categories:self.discovery_models.classify_category(service,categories))
        from .registry.authorizations import PublicationAuthorizations
        self.publication_authorizations=PublicationAuthorizations(self.db,self.identities,self.auth)
        self.publications=ServicePublications(self.db,self.identities,self.discovery.categories,authorizations=self.publication_authorizations)
        from .files import PrivateFiles
        self.files=PrivateFiles(self.db,self.identities,settings.data/'private-files')
        self.service_operations=ServiceOperations(self.db,self.publications,self.market,self.discovery,self.payment_provider)
        self.router.apps['agents']=public_identity_app(self.identities.enrollment,self.publications)
        trace_endpoint(self.market.market, self.bus, self.market_id)
        self.psp = SimPaymentProvider(self.pay_signer, keys=self.keys, ap2_key=self.pay_ec, parties=self.identities.payment_party,
                                      wallets=self.wallets, client_matches=self.identities.matches_party, db=self.db,
                                      on_transfer=lambda t: self.bus.emit("ap2", "transfer", sender=t["payer"], receiver=t["payee"],
                                          card_digest=t["ref"], period=t.get("period"), amount=t["amount"], outcome=t["purpose"]))
        self.router.apps["market"] = build_app(self.market.market, name="IoA Lab Agent 交易市场", base_url=self.market_id,
                                               role="market", jwks={"keys": [self.market_signer.jwk]})
        self.router.apps["pay"] = build_app(self.psp, name="模拟支付方", base_url=self.pay_id, role="payment",
                                            jwks={"keys": [self.pay_signer.jwk,self.pay_ec.jwk]}, extensions=(A2C_EXTENSION, AP2_EXTENSION))
        self.started = False
        self.background = []
        from .messaging.directory import InterfaceDirectory
        self.interface_directory=InterfaceDirectory(self.db,self.identities)
        from .messaging.delivery import DeliveryBroker
        self.delivery=DeliveryBroker(self.db,self.identities,settings,
            record_in=lambda conn,**event:self.bus.record_in(conn,**event),
            publish=lambda event:self.bus.publish(event))
        from .delegation import Admissions
        from .operations import PaymentOperations,MarketOperations
        self.admissions=Admissions(self.db,self.identities,self.publications,self.scopes,self.payment_provider)
        self.admissions.active=self.delivery.active_in
        from .delegation.contracts import ContractAuthority
        self.contracts=ContractAuthority(self.db,self.identities,self.admissions,self.scopes,self.market,self.psp)
        self.admissions.contracts=self.contracts
        from .observation import ContractProjection
        self.contract_views=ContractProjection(self.db,self.identities,self.market)
        self.payment_operations=PaymentOperations(self.db,self.identities,self.psp,self.admissions,self.scopes,record=self.contract_views.record_in)
        self.market_operations=MarketOperations(self.db,self.identities,self.admissions,self.market,contracts=self.contracts,psp=self.psp,
            close=self.delivery.contract_closed_in,record=self.contract_views.record_in)
        self.delivery.admissions=self.admissions
        self.psp.requires_admission=self.identities.bound_service
        self.market.market.requires_admission=self.identities.bound_service
        from .delegation.relay import ProtocolRelay
        self.delivery.protocol=ProtocolRelay(self.db,self.identities,self.admissions,self.scopes,self.files,clock=self.delivery.clock,contracts=self.contracts,record=self.contract_views.record_in)
        from .delegation.arbitration_evidence import ArbitrationEvidence
        self.arbitration_evidence=ArbitrationEvidence(self.db,self.identities,self.admissions,self.contracts,self.psp,
            self.delivery.protocol,self.delivery,clock=self.admissions.clock)
        from .delegation.arbitration_cases import ArbitrationCases
        self.arbitration_cases=ArbitrationCases(self.db,self.identities,self.auth,self.admissions,
            self.arbitration_evidence,self.files,model_binding=self.arbitration_models.binding,
            clock=lambda:self.arbitration_evidence.clock(),market=self.market,
            notify_in=self.delivery.ruling_in,notify=self.delivery.notify,
            contracts=self.contracts,close_in=self.delivery.contract_closed_in)
        self.delivery.protocol.arbitrations=self.arbitration_cases
        self.payment_operations.arbitrations=self.arbitration_cases
        self.market_operations.arbitrations=self.arbitration_cases
        self.market.request_outcomes=self.arbitration_cases.request_outcomes
        self.contract_views.rebuild()
        from .observation.activity import ActivityViews
        self.activity_views=ActivityViews(self.contract_views)
        self.delivery.observe_in=self.activity_views.capture_in
        from .operations import ControlOperations
        self.controls=ControlOperations(self.db,self.identities,self.delivery)
        self.controls.register_platform(scopes=self.scopes,admissions=self.admissions,arbitrations=self.arbitration_cases,
            payments=self.payment_operations,market=self.market_operations,publications=self.publications)
        from .observation import MessageViews,ServiceViews
        self.message_views=MessageViews(self.db)
        self.service_views=ServiceViews(self.db)
        from .observation.accounts import AccountQueries
        from .observation.workbench import WorkbenchQueries
        self.account_views=AccountQueries(self.db,auth=self.auth,wallets=self.wallets,contracts=self.contract_views,
            services=self.service_views,messages=self.message_views,bus=self.bus,identities=self.identities)
        self.workbench_views=WorkbenchQueries(self.db,auth=self.auth,wallets=self.wallets,contracts=self.contract_views,
            services=self.service_views,discovery=self.discovery,messages=self.message_views,interfaces=self.interface_directory)
        from .operations import DiscoveryOperations
        self.discovery_operations=DiscoveryOperations(self.discovery,self.service_views,self.bus.emit,self.market_id)
        from .observation.overview import OverviewQueries
        self.overview=OverviewQueries(self.db,services=self.service_views,discovery=self.discovery,market=self.market,
            settings=settings,bus=self.bus,discovery_enabled=self.discovery_models.enabled)


    async def _owned(self, create):
        if self._closing or self._closed:
            from .errors import LabError
            raise LabError('closed', 'Core is stopping; recover the original operation after restart',
                           status=503, recovery='retry_original')
        task = asyncio.create_task(create())
        self._workers.add(task)
        def finished(original):
            self._workers.discard(original)
            if not original.cancelled():
                original.exception()  # Retain failures without an unobserved-task warning.
        task.add_done_callback(finished)
        # The observer can disappear; the actual request/thread remains owned.
        return await asyncio.shield(task)

    async def _worker(self, function, *args, **kwargs):
        return await self._owned(lambda: asyncio.to_thread(function, *args, **kwargs))

    async def _serve_owned(self, app, scope, receive, send):
        # A2C's executor and task-store effects must finish before DB/OS unlock.
        return await self._owned(lambda: app(scope, receive, send))

    async def startup(self):
        if self._closing or self._closed:
            raise RuntimeError('Core is stopping or closed')
        if self.started:
            return
        try:
            self.bus.new_run()
            await self._worker(self.discovery.startup)
            if self.s.autoseed:
                await self._worker(self.ensure_seeded)
            if self._closing:
                raise RuntimeError('Core stopped during startup')
            await self._worker(self.arbitration_cases.recover)
            self.started = True
            self.background.append(asyncio.create_task(self._sync_loop()))
            self.background.append(asyncio.create_task(self._arbitration_loop()))
        except BaseException:
            await self.shutdown()
            raise

    async def shutdown(self):
        if self._closed:
            return
        if self._shutdown_task is None:
            self._closing = True
            self._shutdown_task = asyncio.create_task(self._shutdown())
        # A canceled observer cannot release data while the actual writers run.
        await asyncio.shield(self._shutdown_task)

    async def _shutdown(self):
        for task in self.background:
            task.cancel()
        await asyncio.gather(*self.background, return_exceptions=True)
        if self._workers:
            await asyncio.gather(*tuple(self._workers), return_exceptions=True)
        await asyncio.to_thread(self._close_resources)

    def close(self):
        """Close an unstarted/fully drained Core, including failed construction."""
        if self._closed:
            return
        if self._workers or any(not task.done() for task in self.background):
            raise RuntimeError('Core still has actual workers; await shutdown()')
        if self._shutdown_task is not None and not self._shutdown_task.done():
            raise RuntimeError('Core shutdown is still running')
        self._closing = True
        self._close_resources()

    def _close_resources(self):
        if self._closed:
            return
        discovery = getattr(self, 'discovery', None)
        if discovery is not None:
            discovery.close()
        for transport in tuple(self.transports):
            transport.close()
            self.transports.remove(transport)
        bus = getattr(self, 'bus', None)
        if bus is not None:
            bus.close()
        database = getattr(self, 'db', None)
        if database is not None:
            database.close()
        self._owner.close()
        self.started = False
        self._closed = True

    def ensure_seeded(self):
        from .seed import seed_all
        return seed_all(self)

    def service_definitions(self):
        """Runnable service summaries from the explicit seed manifest; none without a manifest."""
        if self.s.seed_file is None:
            return []
        from .seed import load_definitions
        return [{"id": d["id"], "name": d["name"], "description": d["description"], "formation": d.get("formation", "preapproval"),
                 "runnable": bool(d.get("runnable")), "price": d.get("pricing", {}).get("price"), "category": d.get("category")}
                for d in load_definitions(self.s.seed_file) if d.get("runnable")]


    async def _arbitration_loop(self):
        while True:
            await asyncio.sleep(.25)
            try:
                await self._worker(self.arbitration_cases.process_one)
            except Exception:
                log.exception("Original case worker stopped this attempt; recorded results remain recoverable")

    async def _sync_loop(self):
        while True:
            await asyncio.sleep(1)
            self.delivery.cleanup()


    def _transport(self, party: str) -> TracingTransport:
        # trust_env=False：回环请求不走系统代理（代理会把 127.0.0.1 变成 502）
        public, local = self.s.public_url, self.s.local_url
        transport = TracingTransport(A2ATransport(http=lambda: httpx.AsyncClient(timeout=120, trust_env=False,
                                                                             transport=LoopbackTransport(public, local))),
                                self.bus, party)
        self.transports.append(transport)
        return transport


    def _pin_public_url(self) -> None:
        """参与方 id 内嵌对外地址：首次运行记到 data/public_url，之后不一致就拒绝启动（改地址须 reset 重来）。"""
        p = self.s.data / "public_url"
        if p.exists():
            old = p.read_text(encoding="utf-8").strip()
            if old and old != self.s.public_url:
                raise SystemExit(f"[ioalab] runtime.public_url 已从 {old} 改为 {self.s.public_url}：参与方 id 内嵌旧地址，"
                                 f"请改回，或 `ioalab reset` 后重新 init")
        else:
            p.write_text(self.s.public_url, encoding="utf-8")
