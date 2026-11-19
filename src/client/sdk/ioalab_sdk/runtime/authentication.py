"""Short SDK account controller; never loads role identities, business state or a host."""
import httpx
from .lease import Lease
from .config import RuntimeConfig, read_configuration
from .files import private_json
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
        return authenticate(config)


def authenticate(config):
    """Caller owns client.lock; persist only the verified device session."""
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
        migrate_configuration(config,account)
        return {'status':'completed','data':result,'error':None}
    finally:account.close()


def migrate_configuration(config, account):
    """Only after a verified device login, atomically remove legacy password fields."""
    if config.credential_ref and config.credential_ref!=account.vault.reference:
        raise RuntimeFault('identity_conflict','Credential reference belongs to another installation')
    if not account.verified or not account.account.get('sessionId'):
        raise RuntimeFault('authenticate','Verify the original device session before migrating configuration')
    if config.source is not None and not config.credential_ref:
        from .onboarding import Onboarding
        Onboarding(config.source).migrate_legacy_record(account)
        config.require_current()
        document=read_configuration(config.source)
        document.pop('password',None);document.pop('password_env',None)
        document['credential_ref']=account.vault.reference
        private_json(config.source,document)
        # Compact the SDK-owned credential cache; user backups are untouched.
        account.cache.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        account.cache.db.execute('VACUUM')
        account.cache.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        return RuntimeConfig.load(config.source)
    return config
