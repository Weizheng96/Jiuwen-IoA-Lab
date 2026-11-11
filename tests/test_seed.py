"""Official content remains validated; core seeds are idle accounts and deterministic categories."""
from ioalab_services.catalog import definitions,load_domains,common_source

class _Ctx:
    def file(self,name): return b'{}'
    def value(self,name): return None

def test_rules_engine_core_checks():
    # The original rule interpreter is retained byte-for-byte; exercise it through its verifier package.
    from tools.check_services import check
    from ioalab.delegation.packages import PackageStore
    from pathlib import Path
    import tempfile
    package=PackageStore(Path(tempfile.mkdtemp()))
    d=next(d for d in definitions() if d['id']=='wechat-posts')
    assert check(d,package)==[]

def test_domain_files_shape():
    domains=load_domains()
    assert len(domains)==8 and all(5<=len(d['services'])<=10 for d in domains)
    items=definitions()
    assert len(items)==52 and len({d['id'] for d in items})==52
    assert {'countersign','proposal'}=={d.get('formation','preapproval') for d in items}
    assert all(d['sample'] and d['verifier']['source'] for d in items)

def test_seed_all_idempotent_and_taxonomy(platform):
    first=platform.ensure_seeded(); second=platform.ensure_seeded()
    assert first==second and first['isp']==1 and first['isc']==10
    assert len(platform.discovery.categories())>=8
    assert not hasattr(platform,'assistants') and not hasattr(platform,'services')


def test_preset_account_names_migrate_without_changing_identity_or_custom_accounts(tmp_path):
    from conftest import platform_settings
    from ioalab.app import Platform
    pf = Platform(platform_settings(data=tmp_path, autoseed=False, isc_count=3))
    try:
        # Existing deployment provenance, including the administrator ISC_001.
        old = pf.accounts.create_account('ISP_001', '001', kind='isp', display_name='内部服务方 · 旧领域', note='部署自带')
        admin = pf.accounts.create_account('ISC_001', '001', kind='isc', display_name='内部用户 001', note='部署自带')
        custom = pf.accounts.create_account('ISC_002', 'custom-password', display_name='我的自定义名')
        outside = pf.accounts.create_account('ISP_999', '999', kind='isp', display_name='清单外名称', note='部署自带')
        pf.db.run("INSERT INTO agents(id,owner_id,role,name,status,meta) VALUES(?,?,?,?,?,?)",
                  'preset-name-test', old['id'], 'service', '保留服务名称', 'registered', '{}')
        before = pf.db.rows('SELECT * FROM users ORDER BY id')
        agents = pf.db.rows('SELECT * FROM agents')
        balance = pf.wallets.balance(old['id'])
        pf.ensure_seeded()
        once = pf.db.rows('SELECT * FROM users ORDER BY id')
        pf.ensure_seeded()
        assert pf.db.rows('SELECT * FROM users ORDER BY id') == once
        by_id = {row['id']: row for row in once}
        for row in before:
            expected = dict(row)
            if row['id'] in (old['id'], admin['id']): expected['display_name'] = row['username']
            assert by_id[row['id']] == expected
        assert pf.auth.authenticate('ISP_001', '001')['id'] == old['id']
        assert pf.wallets.balance(old['id']) == balance
        assert pf.db.rows('SELECT * FROM agents') == agents
        assert pf.auth.roles(admin['id']) == ['admin']
        assert pf.account_views.me(custom['id'])['display_name'] == '我的自定义名'
        assert pf.account_views.me(outside['id'])['display_name'] == '清单外名称'
        people = {row['username']: row for row in pf.account_views.people()}
        assert people['ISP_001']['displayName'] == 'ISP_001'
        assert people['ISC_001']['displayName'] == 'ISC_001'
        assert people['ISC_003']['displayName'] == 'ISC_003'
        assert pf.account_views.me(old['id'])['agents'][0]['name'] == '保留服务名称'
        assert pf.account_views.sdk_me(old['id'])['display_name'] == 'ISP_001'
        assert pf.workbench_views.summary(old['id'])['account']['display_name'] == 'ISP_001'
        assert next(row for row in pf.account_views.admin_users() if row['id'] == old['id'])['display_name'] == 'ISP_001'
    finally:
        pf.close()


def test_new_preset_accounts_use_login_names_from_explicit_manifest(tmp_path):
    from conftest import platform_settings
    from ioalab.app import Platform
    from ioalab.seed.accounts import ensure_accounts
    pf = Platform(platform_settings(data=tmp_path, autoseed=False, isc_count=3))
    try:
        result = ensure_accounts(pf, domains=[{'account': 'ISP_001'}, {'account': 'ISP_007'}, {'account': 'ISP_001'}])
        assert len(result['isp']) == 2 and len(result['isc']) == 3
        assert {row['username']: row['display_name'] for row in pf.account_views.admin_users()} == {
            'ISP_001': 'ISP_001', 'ISP_007': 'ISP_007', 'ISC_001': 'ISC_001', 'ISC_002': 'ISC_002', 'ISC_003': 'ISC_003'}
    finally:
        pf.close()
