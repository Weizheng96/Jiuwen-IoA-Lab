"""Agent operation catalog: one declaration table for runtime, role and product-script operations."""
import ast
import re
from pathlib import Path

import pytest

from ioalab_sdk.runtime.catalog import (AGENT, ASSISTANT, CATALOG, CONTROL, EXTERNAL, INTERNAL, LOCAL, OPERATIONS,
                                        PROVIDER, READ, RUNTIME, SCRIPT, handled_by)
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.resident import ResidentRuntime
from test_resident_runtime import configuration

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / 'src/access/skills'
PREFIX = '(?:runtime|identity|account|confirmations|operations|assistant|provider)'
# A full operation name in skill text; ASCII boundaries because CJK text sits directly next to names.
# A trailing ".*" or a quoted trailing dot (`assistant.delegation.`) marks a family prefix.
NAME = re.compile(r'(?<![A-Za-z0-9_.-])(' + PREFIX + r'\.[a-z_][a-z0-9_.-]*[a-z0-9_])(\.\*|\.(?=`))?')
STATUSES = {'accepted', 'running', 'waiting', 'completed', 'rejected', 'failed', 'unknown'}


def provider_task_tools():
    """Per-turn host tool names of the provider role; skills teach them, but they are not operations."""
    names = set()
    for path in (ROOT / 'src/access/sdk/ioalab_sdk/provider').glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if (isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'ToolBinding' and node.args
                    and isinstance(node.args[0], ast.Constant) and str(node.args[0].value).startswith('provider.task.')):
                names.add(node.args[0].value)
    return names


def skill_names(folders):
    """(operation names, wildcard families) mentioned in the product skill pages of these folders."""
    names, families = set(), set()
    for folder in folders:
        for path in (SKILLS / folder).rglob('*.md'):
            for name, wildcard in NAME.findall(path.read_text(encoding='utf-8')):
                (families if wildcard else names).add(name)
    return names, families


def test_catalog_declares_each_operation_once_with_known_values():
    assert len(CATALOG) == len(OPERATIONS)
    for operation in OPERATIONS:
        assert operation.handler in (RUNTIME, SCRIPT, ASSISTANT, PROVIDER)
        assert operation.handler not in (ASSISTANT, PROVIDER) or operation.name.startswith(operation.handler + '.')
        assert operation.effect in (READ, LOCAL, EXTERNAL, CONTROL) and operation.exposure in (AGENT, INTERNAL)
        assert operation.results and set(operation.results) <= STATUSES and isinstance(operation.confirmation, bool)
        assert operation.inputs and all(len(shape) == 2 and not set(shape[0]) & set(shape[1]) for shape in operation.inputs)
        assert operation.confirmation is False or 'waiting' in operation.results


def test_every_operation_named_in_product_skills_is_cataloged():
    names, families = skill_names(('_shared', 'assistant', 'provider'))
    tools = provider_task_tools()
    assert tools, 'Provider task tools must still be declared with ToolBinding'
    # provider.services is the configuration area, not an operation.
    assert sorted(names - set(CATALOG) - tools - {'provider.services'}) == []
    assert all(any(name.startswith(family + '.') for name in CATALOG) for family in families)


@pytest.mark.parametrize('role,folders', [(ASSISTANT, ('assistant', '_shared')), (PROVIDER, ('provider', '_shared')),
                                          (None, ('_shared', 'assistant', 'provider'))])
def test_every_agent_operation_is_taught_by_its_product_skill(role, folders):
    names, _ = skill_names(folders)
    owners = (role,) if role else (RUNTIME, SCRIPT)
    taught = {op.name for op in OPERATIONS if op.exposure == AGENT and op.handler in owners}
    assert sorted(taught - names) == []


def test_product_script_routes_exactly_the_cataloged_script_operations():
    source = (SKILLS / '_shared/ioalab.py').read_text(encoding='utf-8')
    routed = set(re.findall(r"'((?:runtime|account)\.[a-z_.-]*[a-z_])'", source))
    assert routed == set(handled_by(SCRIPT))


def test_runtime_registers_only_cataloged_role_operations(platform, tmp_path):
    path, _ = configuration(platform, tmp_path, service=False)

    class Uncataloged:
        def bind(self, runtime):
            return {'operations': {'assistant.unlisted': lambda *_: {'status': 'completed'}}}
        def start(self): pass
        def wake(self): pass
        def close(self): pass

    with pytest.raises(ValueError, match='operation catalog'):
        ResidentRuntime(RuntimeConfig.load(path), modules=(Uncataloged(),))
    runtime = ResidentRuntime(RuntimeConfig.load(path))
    try:
        assert set(handled_by(RUNTIME)).isdisjoint(runtime.operations)
        for name in handled_by(RUNTIME):
            assert runtime.call(name, 'catalog-probe', {'unexpected': True})['error']['code'] != 'not_ready'
    finally:
        runtime.close()
