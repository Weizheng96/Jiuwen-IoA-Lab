"""SDK side of the communication contract: kinds the SDK names and error codes it judges are in the interface tables."""
import ast
from pathlib import Path

from a2c.market import Market
from a2c.payment.psp import PaymentProvider
from a2c.provider import Provider
from a2c.service import CertifiedService

from contract_tables import ROOT, errors, kinds, split
from ioalab_sdk.runtime.catalog import CATALOG

SDK = ROOT / 'src/client/sdk/ioalab_sdk'
PARTICIPANT = {'共用运行时', '个人助手端', '服务方端'}
# Original A2C/AP2 method names travel inside a2c.request bodies; they are not wire kinds.
A2C_METHODS = {*Provider.OPS, *Market.OPS, *PaymentProvider.OPS, *CertifiedService.OPS}


def sources():
    for path in SDK.rglob('*.py'):
        yield path, ast.parse(path.read_text(encoding='utf-8'))


def is_code(expr):
    if isinstance(expr, ast.Attribute) and expr.attr == 'code':
        return True
    if isinstance(expr, ast.Subscript) and isinstance(expr.slice, ast.Constant) and expr.slice.value == 'code':
        return True
    return (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr == 'get'
            and bool(expr.args) and isinstance(expr.args[0], ast.Constant) and expr.args[0].value == 'code')


def wire_kinds():
    """Kinds the SDK puts on the wire by structure, whatever their family: frame types, envelope kinds, control calls."""
    found = {}
    for path, tree in sources():
        rel = path.relative_to(SDK).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                keys = {key.value for key in node.keys if isinstance(key, ast.Constant)}
                for key, value in zip(node.keys, node.values):
                    if (isinstance(key, ast.Constant) and isinstance(value, ast.Constant) and isinstance(value.value, str)
                            and (key.value == 'type' and '.' in value.value or key.value == 'kind' and 'recipient' in keys)):
                        found.setdefault(value.value, set()).add(rel)
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ('call', 'submit')
                    and (getattr(node.func.value, 'attr', None) or getattr(node.func.value, 'id', None)) == 'connection'
                    and len(node.args) > 1 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
                found.setdefault(node.args[1].value, set()).add(rel)
    return found


def test_kinds_named_by_the_sdk_are_in_the_contract_table():
    rows = {row['种类']: row for row in kinds()}
    families = {name.split('.', 1)[0] for name in rows}
    local = {name.split('.', 1)[1] for name in CATALOG if name.startswith(('assistant.', 'provider.'))}
    named = {}
    for path, tree in sources():
        # Confirmation stage and action labels are local names, not wire kinds.
        labels = {id(value) for node in ast.walk(tree) if isinstance(node, ast.Dict)
                  for key, value in zip(node.keys, node.values)
                  if isinstance(key, ast.Constant) and key.value in ('stage', 'action')}
        # A form registration names a local UI workflow, not a platform message kind.
        # wire_kinds below still checks every actual envelope/control/frame occurrence.
        labels.update(id(node.args[0]) for node in ast.walk(tree)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                      and node.func.id in ('FormDefinition', 'WorkflowDefinition') and node.args)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.count('.') == 1
                    and id(node) not in labels
                    and node.value.split('.', 1)[0] in families and node.value.split('.', 1)[1].replace('_', '').isalpha()):
                named.setdefault(node.value, set()).add(path.relative_to(SDK).as_posix())
    unknown = {name: sorted(files) for name, files in named.items()
               if name not in rows and name not in A2C_METHODS and name not in local}
    assert unknown == {}, 'SDK names wire kinds missing from the contract table'
    sent = wire_kinds()
    assert {name: sorted(files) for name, files in sent.items() if name not in rows} == {}, \
        'SDK sends frames, controls or envelopes missing from the contract table'
    for name in (named.keys() | sent.keys()) & rows.keys():
        row = rows[name]
        sides = split(row['发送方']) | split(row['处理方'])
        assert sides & PARTICIPANT, f'{name} has no participant side in the table'


def test_error_codes_judged_by_the_sdk_are_in_the_error_table():
    judged = {}
    for path, tree in sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare) and is_code(node.left):
                for comparator in node.comparators:
                    values = comparator.elts if isinstance(comparator, (ast.Tuple, ast.List, ast.Set)) else [comparator]
                    for value in values:
                        if isinstance(value, ast.Constant) and isinstance(value.value, str):
                            judged.setdefault(value.value, set()).add(path.relative_to(SDK).as_posix())
    declared = {row['错误码'] for row in errors()}
    assert {code: sorted(files) for code, files in judged.items() if code not in declared} == {}
    sdk_only = {row['错误码'] for row in errors() if split(row['产生方']) == {'SDK'}}
    assert sdk_only <= set(judged), 'SDK-only codes must be judged somewhere in the SDK'
