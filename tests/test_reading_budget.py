"""Reading budget of "change one module" measured on an isolated design tree."""
import json

import pytest

from tools import check_modules, reading_budget


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.setattr(check_modules, 'ROOT', tmp_path)
    monkeypatch.setattr(check_modules, 'MODULES', {
        '钱包': ['src/app/wallet'], '账号': ['src/app/account'], '部署': ['deploy']})
    monkeypatch.setattr(check_modules, 'OUTSIDE', {})

    def write(name, text):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    for name in reading_budget.ENTRY:
        write(name, 'e' * 10)
    write('设计/架构.md', '# 架构\n\n前言不计入\n\n## 模块表\n\n'
          '| 模块 | 子模块 | 代码 | 职责 | 文件夹 |\n|---|---|---|---|---|\n'
          '| 应用 | 钱包 | `wallet/` | 记账 | [钱包](组件/应用/钱包/接口.md) |\n'
          '| 应用 | 账号 | `account/` | 账号 | [账号](组件/应用/账号/接口.md) |\n'
          '| 部署 | — | `deploy/` | 安装 | [安装](工程/安装.md) |\n\n## 其他\n\n不计入\n')
    write('设计/组件/应用/术语.md', 't' * 7)
    write('设计/组件/应用/决策/0001-取舍.md', 'd' * 5)
    write('设计/组件/应用/钱包/接口.md', 'i' * 100)
    write('设计/组件/应用/钱包/接口/付款.md', 's' * 1000)
    write('设计/组件/应用/钱包/依赖.md', '| 依赖模块 | 接口 |\n|---|---|\n| 账号 | `thumbprint` |\n')
    write('设计/组件/应用/钱包/实现.md', 'm' * 20)
    write('设计/组件/应用/账号/接口.md', 'a' * 300)
    write('设计/组件/应用/账号/接口/密钥.md', 'k' * 3000)
    write('设计/组件/应用/账号/依赖.md', '| 依赖模块 | 接口 |\n|---|---|\n')
    write('设计/组件/应用/账号/实现.md', 'n' * 30)
    write('设计/工程/安装.md', 'w' * 400)
    write('src/app/wallet/ledger.py', 'x = 1\n')
    write('deploy/install.sh', 'echo\n')
    return tmp_path


def test_migrated_module_reads_own_pages_and_dependency_interfaces(tree):
    result = reading_budget.measure()
    architecture = (tree / '设计/架构.md').read_text(encoding='utf-8')
    assert result['fixed'] == 30 + len(architecture.split('\n## 其他')[0]) - architecture.index('## 模块表')
    wallet = next(row for row in result['modules'] if row['module'] == '钱包')
    own = 100 + len((tree / '设计/组件/应用/钱包/依赖.md').read_text(encoding='utf-8')) + 20 + 7 + 5
    assert wallet['required'] == result['fixed'] + own + 300
    assert wallet['ceiling'] == wallet['required'] + 1000 + 3000
    assert wallet['dependencies'] == ['账号'] and wallet['code'] == 6


def test_unmigrated_module_reads_the_pages_the_architecture_links(tree):
    deploy = next(row for row in reading_budget.measure()['modules'] if row['module'] == '部署')
    assert deploy['migrated'] is False
    assert deploy['required'] == deploy['ceiling'] == reading_budget.measure()['fixed'] + 400


def test_budget_gate_fails_only_over_the_limit(tree, capsys):
    wallet = next(row for row in reading_budget.measure()['modules'] if row['module'] == '钱包')
    assert reading_budget.main(['--max-required', str(wallet['required'])]) == 0
    assert reading_budget.main(['--max-required', str(wallet['required'] - 1)]) == 1
    assert 'Reading budget exceeded: 钱包' in capsys.readouterr().out


def test_json_record_is_new_and_never_overwritten(tree):
    target = tree / 'records/baseline.json'
    assert reading_budget.main(['--json', str(target)]) == 0
    assert json.loads(target.read_text(encoding='utf-8'))['modules'][0]['module'] == '钱包'
    with pytest.raises(FileExistsError):
        reading_budget.main(['--json', str(target)])
