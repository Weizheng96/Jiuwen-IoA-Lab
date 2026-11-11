"""Platform side of the communication contract: dispatch keys and raised error codes match the interface tables."""
import ast
from pathlib import Path
from types import SimpleNamespace

from contract_tables import ROOT, errors, kinds, split
from ioalab.messaging.delivery import DeliveryBroker
from ioalab.operations import ControlOperations

PLATFORM = ROOT / 'src/platform/ioalab'
MODULES = {'消息中心', '委托中心', '支付中心', '注册中心', 'web服务', '身份中心', '观测', '平台基础'}


def dispatch():
    """Exact and family keys the platform dispatches, read from the real registration without owner objects."""
    controls = ControlOperations(None, None, SimpleNamespace(CONTROLS=DeliveryBroker.CONTROLS))
    owners = dict.fromkeys(('scopes', 'admissions', 'arbitrations', 'payments', 'market', 'publications'))
    controls.register_platform(**owners)
    return controls.kinds, DeliveryBroker.CONTROLS


def literals(prefix):
    found = set()
    for path in PLATFORM.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith(prefix):
                found.add(node.value)
    return found


def test_dispatch_keys_equal_the_platform_handled_kinds():
    rows = kinds()
    handled = {row['种类'] for row in rows if row['类别'] in ('控制', '操作记录') and split(row['处理方']) <= MODULES}
    registered, delivery = dispatch()
    exact = {key for key in registered if not key.endswith('.')}
    families = {key for key in registered if key.endswith('.')}
    dispatched = set(delivery) | exact | {'operation.get'}
    for family in families:
        members = {kind for kind in literals(family) if kind.count('.') == 1 and kind != family}
        assert members, 'Family has no exact kinds in platform code: ' + family
        dispatched |= members
    assert sorted(handled - dispatched) == [], 'Contract kinds the platform does not dispatch'
    assert sorted(dispatched - handled) == [], 'Dispatched kinds missing from the contract table'
    records = {row['种类'] for row in rows if row['类别'] == '操作记录'}
    assert all(registered[kind].apply_in is None for kind in records if kind in registered)


def test_every_platform_error_code_is_in_the_error_table():
    raised = set()
    for path in PLATFORM.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if (isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'LabError' and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                raised.add(node.args[0].value)
    declared = {row['错误码'] for row in errors() if '平台' in split(row['产生方'])}
    assert sorted(raised - declared) == [], 'Platform raises codes missing from the error table'
    assert sorted(declared - raised) == [], 'Error table lists platform codes the platform never raises'
