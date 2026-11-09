"""Module folder checks against code imports, entry files, test columns and the progress index, on an isolated tree."""
from pathlib import Path

import pytest

from tools import check_modules


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.setattr(check_modules, 'ROOT', tmp_path)
    monkeypatch.setattr(check_modules, 'MODULES', {
        '钱包': ['src/app/wallet'], '账号': ['src/app/account'],
        '基础': ['src/app/storage.py', 'src/app/errors.py'], '装配': ['src/app/app.py']})
    monkeypatch.setattr(check_modules, 'OUTSIDE', {'测试': ['tests']})
    monkeypatch.setattr(check_modules, 'PACKAGES', {'app': 'src/app'})

    def write(name, text):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    write('src/app/__init__.py', '')
    write('src/app/storage.py', 'class DB: pass\n')
    write('src/app/errors.py', 'class LabError(Exception): pass\n')
    write('src/app/account/__init__.py', 'from .keys import thumbprint\n')
    write('src/app/account/keys.py', 'def thumbprint(jwk): return jwk\n')
    write('src/app/wallet/__init__.py', 'from .ledger import Wallets, money\n')
    write('src/app/wallet/ledger.py', 'from ..storage import DB\nfrom ..errors import LabError\nfrom ..account.keys import thumbprint\n')
    write('src/app/wallet/scopes.py', 'class ScopeLedger: pass\n')
    write('src/app/app.py', 'from .wallet import Wallets\nfrom .wallet.scopes import ScopeLedger\n')
    write('tests/test_wallet.py', 'def test_ok():\n    assert True\n')
    write('设计/架构.md', '# 架构\n\n## 模块表\n\n| 模块 | 子模块 | 代码 | 职责 | 文件夹 |\n|---|---|---|---|---|\n'
          '| 平台 | 钱包 | `src/app/wallet` | 账本 | [钱包](组件/平台/钱包/接口.md) |\n| 平台 | 账号 | `account` | 账号 | 页 |\n'
          '| 平台 | 基础 | `storage.py` | 基础 | 页 |\n| 平台 | 装配 | `app.py` | 装配 | 页 |\n\n## 下一节\n')
    write('设计/组件/平台/钱包/接口.md', '# 钱包\n\n## 接口\n\n| 接口 | 用途 | 测试 |\n|---|---|---|\n| `Wallets.balance` | 余量 | wallet |\n| `money` | 定值 | wallet、rule_boundaries |\n')
    write('设计/组件/平台/钱包/依赖.md', '# 钱包 · 依赖\n\n| 依赖模块 | 接口 | 用途 |\n|---|---|---|\n| [账号](../账号/接口.md) | `thumbprint` | 指纹 |\n| 基础 | `DB`、`LabError` | 事务 |\n')
    write('设计/组件/平台/钱包/实现.md', '# 钱包 · 实现\n\n## 入口\n\n| 入口文件 | 公开名 | 说明 |\n|---|---|---|\n| `__init__.py` | `Wallets`、`money` | 账本 |\n| `scopes.py` | `ScopeLedger` | 台账 |\n')
    write('设计/组件/平台/进展/01-归属.md', '# 01：归属\n\n**目标行为：** x\n\n**阻塞于：** 无\n\n**状态：** ready\n')
    write('设计/工程/进展.md', '# 进展\n\n| 模块 | 开放票 | 票据 |\n|---|---|---|\n| 平台 | 1 | [01](../组件/平台/进展/01-归属.md) |\n')
    write('tests/test_rule_boundaries.py', 'def test_ok():\n    assert True\n')
    return write


def test_consistent_tree_passes(tree):
    folders = check_modules.module_folders()
    assert check_modules.check_architecture_table(folders) == []
    assert check_modules.check_dependencies(folders) == []
    assert check_modules.check_tests(folders) == []
    assert check_modules.check_progress() == []


def test_undeclared_import_is_reported_once_per_statement(tree):
    tree('设计/组件/平台/钱包/依赖.md', '# 钱包 · 依赖\n\n| 依赖模块 | 接口 |\n|---|---|\n| 基础 | `DB` |\n')
    failures = check_modules.check_dependencies(check_modules.module_folders())
    assert failures == ['Undeclared dependency: 钱包 -> 账号 in ' + str(Path('src/app/wallet/ledger.py'))
                        + ': from ..account.keys import thumbprint']


def test_import_bypassing_entry_files_is_reported(tree):
    tree('src/app/app.py', 'from .wallet import Wallets\nfrom .wallet.ledger import money\n')
    failures = check_modules.check_dependencies(check_modules.module_folders())
    assert failures == ['Import bypasses entry files of 钱包: ' + str(Path('src/app/app.py')) + ': from .wallet.ledger import money']


def test_missing_dependency_or_implementation_page_is_reported(tree):
    (check_modules.ROOT / '设计/组件/平台/钱包/实现.md').unlink()
    failures = check_modules.check_dependencies(check_modules.module_folders())
    assert failures == ['Missing implementation page: ' + str(Path('设计/组件/平台/钱包/实现.md'))]


def test_test_column_names_must_exist(tree):
    tree('设计/组件/平台/钱包/接口.md', '# 钱包\n\n| 接口 | 测试 |\n|---|---|\n| `a` | wallet、missing_one |\n| `b` | tests/test_wallet.py::test_ok |\n| `c` | — |\n')
    assert check_modules.check_tests(check_modules.module_folders()) == [
        'Test missing for interface row: ' + str(Path('设计/组件/平台/钱包/接口.md')) + ': missing_one']


def test_split_interface_sub_pages_are_checked_too(tree):
    tree('设计/组件/平台/钱包/接口/账本.md', '# 钱包 · 账本\n\n| 接口 | 测试 |\n|---|---|\n| `a` | wallet |\n| `b` | absent_case |\n')
    assert check_modules.check_tests(check_modules.module_folders()) == [
        'Test missing for interface row: ' + str(Path('设计/组件/平台/钱包/接口/账本.md')) + ': absent_case']


def test_architecture_table_and_code_mapping_must_agree(tree):
    tree('设计/架构.md', '# 架构\n\n## 模块表\n\n| 模块 | 子模块 | 代码 | 职责 | 文件夹 |\n|---|---|---|---|---|\n'
         '| 平台 | 钱包 | `w` | 账本 | 页 |\n| 平台 | 账号 | `a` | 账号 | 页 |\n| 平台 | 基础 | `s` | 基础 | 页 |\n| 平台 | 多余 | `x` | 多余 | 页 |\n')
    assert check_modules.check_architecture_table(check_modules.module_folders()) == [
        'Module missing from architecture table: 装配',
        'Architecture table row without a code mapping in tools/check_modules.py: 多余']


def test_progress_index_counts_links_and_blockers(tree):
    tree('设计/组件/平台/进展/02-后续.md', '# 02：后续\n\n**阻塞于：** 01、07\n\n**状态：** claimed（Claude）\n')
    tree('设计/组件/平台/进展/03-坏票.md', '# 03\n\n没有状态行\n')
    assert check_modules.check_progress() == [
        'Progress index counts 1 tickets for 平台; folder has 3',
        'Ticket missing from progress index: ' + str(Path('设计/组件/平台/进展/02-后续.md')),
        'Ticket blocked by a missing ticket 07: ' + str(Path('设计/组件/平台/进展/02-后续.md')),
        'Ticket missing from progress index: ' + str(Path('设计/组件/平台/进展/03-坏票.md')),
        'Ticket lacks 阻塞于 or 状态 line: ' + str(Path('设计/组件/平台/进展/03-坏票.md'))]


def test_repository_module_folders_pass():
    folders = check_modules.module_folders()
    assert check_modules.check_architecture_table(folders) == []
    assert check_modules.check_dependencies(folders) == []
    assert check_modules.check_tests(folders) == []
    assert check_modules.check_progress() == []
