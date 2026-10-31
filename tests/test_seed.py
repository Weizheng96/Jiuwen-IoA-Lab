"""Official content remains validated; core seeds are idle accounts and deterministic categories."""
from ioalab_services.catalog import definitions,load_domains,common_source

class _Ctx:
    def file(self,name): return b'{}'
    def value(self,name): return None

def test_rules_engine_core_checks():
    # The original rule interpreter is retained byte-for-byte; exercise it through its verifier package.
    from tools.check_services import check
    from ioalab.runtime.packages import PackageStore
    from pathlib import Path
    import tempfile
    package=PackageStore(Path(tempfile.mkdtemp()))
    d=next(d for d in definitions() if d['id']=='wechat-posts')
    assert check(d,package)==[]

def test_domain_files_shape():
    domains=load_domains()
    assert len(domains)==8 and all(5<=len(d['services'])<=10 for d in domains)
    items=definitions()
    assert len(items)==50 and len({d['id'] for d in items})==50
    assert {'countersign','proposal'}=={d.get('formation','preapproval') for d in items}
    assert all(d['sample'] and d['verifier']['source'] for d in items)

def test_seed_all_idempotent_and_taxonomy(platform):
    first=platform.ensure_seeded(); second=platform.ensure_seeded()
    assert first==second and first['isp']==8 and first['isc']==10
    assert len(platform.discovery.categories())>=8
    assert not hasattr(platform,'assistants') and not hasattr(platform,'services')
