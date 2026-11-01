"""Short SDK account controller; never loads role identities, business state or a host."""
import httpx
from ..lease import Lease
from .config import RuntimeConfig
from .files import prepare_home
from .protocol import RuntimeFault
from .session import AccountSession


def login(config_path):
    config=RuntimeConfig.load(config_path)
    prepare_home(config.home)
    try:guard=Lease(config.home/'client.lock',timeout=0)
    except RuntimeError:
        raise RuntimeFault('not_ready','The resident owns this account; safely stop it before explicit login','runtime.stop') from None
    with guard:
        try:account=AccountSession(config.platform,config.username,config.password,config.home,connect=False)
        except httpx.HTTPError:
            raise RuntimeFault('not_ready','The platform account endpoint is unavailable; preserve local state','restore_connection') from None
        try:
            try:result=account.authenticate()
            except (httpx.HTTPError,ValueError) as exc:
                pending=account.cache.get('session','pending')
                if pending and pending.get('phase')=='pending':
                    raise RuntimeFault('result_unknown','Original login result is unverified; preserve its operation','recover_operation',operation_id=pending['operationId']) from None
                raise RuntimeFault('not_ready','The platform credential check is unavailable; keep existing account state','restore_connection') from None
            except RuntimeFault as exc:
                pending=account.cache.get('session','pending')
                if exc.code=='protocol_error' and pending and pending.get('phase')=='pending':
                    raise RuntimeFault('result_unknown','Original login returned an invalid result; preserve its operation','inspect_original_login',operation_id=pending['operationId']) from None
                raise
            return {'status':'completed','data':result,'error':None}
        finally:account.close()
