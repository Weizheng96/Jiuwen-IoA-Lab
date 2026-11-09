"""Design index coverage through split pages and heading anchors, on an isolated tree."""
from pathlib import Path

import pytest

from tools import check_docs


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.setattr(check_docs, 'ROOT', tmp_path)

    def write(name, text):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    write('设计/README.md', '# 索引\n\n[接口](接入/接口.md)\n')
    write('设计/接入/接口.md', '# 接口\n\n## 子页\n\n| [入口](接口/入口.md) | HTTP 入口 |\n')
    write('设计/接入/接口/入口.md',
          '# 接口 · 入口\n\n本页是[接口](../接口.md#子页)的子页。\n\n## HTTP 入口\n\n### 幂等、错误与恢复\n\n见[上节](#http-入口)。\n')
    return write


def missing(name):
    return 'Design page missing from index: ' + str(Path(name))


@pytest.mark.parametrize('heading,anchor', [
    ('HTTP 入口', 'http-入口'), ('合入 main', '合入-main'), ('操作、返回与恢复', '操作返回与恢复'),
    ('Windows可信本机体验', 'windows可信本机体验'), ('部署、观察与演示', '部署观察与演示'),
    ('[平台配置](x.md)与发放', '平台配置与发放'), ('runtime_delivery / 重试', 'runtime_delivery--重试')])
def test_anchor_follows_rendered_heading(heading, anchor):
    assert check_docs.slug(heading) == anchor


def test_split_page_registers_its_own_sub_pages(tree):
    assert check_docs.check_design_index() == []
    assert check_docs.link_failures(check_docs.design_documents()) == []


def test_sub_page_must_be_listed_by_its_parent(tree):
    tree('设计/接入/接口/孤页.md', '# 接口 · 孤页\n')
    assert check_docs.check_design_index() == [missing('设计/接入/接口/孤页.md')]


def test_parent_registers_only_pages_in_its_own_folder(tree):
    tree('设计/接入/接口.md', '# 接口\n\n[入口](接口/入口.md) [旁页](旁页.md) [深页](接口/细节/深页.md)\n')
    tree('设计/接入/旁页.md', '# 旁页\n')
    tree('设计/接入/接口/细节/深页.md', '# 深页\n')
    assert check_docs.check_design_index() == [
        missing('设计/接入/接口/细节/深页.md'), missing('设计/接入/旁页.md')]


def test_sub_pages_do_not_register_further_pages(tree):
    tree('设计/接入/接口/入口.md', '# 接口 · 入口\n\n[再下一层](入口/细节.md)\n')
    tree('设计/接入/接口/入口/细节.md', '# 细节\n')
    assert check_docs.check_design_index() == [missing('设计/接入/接口/入口/细节.md')]


def test_history_pages_are_outside_the_current_index(tree):
    tree('设计/变更/2026-11-01-v0.0.1-旧事.md', '# 旧事\n\n[已删除](../接入/不存在.md)\n')
    assert check_docs.check_design_index() == []


def test_listed_readme_registers_its_whole_folder(tree):
    tree('设计/README.md', '# 索引\n\n[接口](接入/接口.md) [决策](决策/README.md)\n')
    tree('设计/决策/README.md', '# 决策\n')
    tree('设计/决策/0001-选型.md', '# 选型\n')
    tree('设计/决策/深/0002-细节.md', '# 细节\n')
    assert check_docs.check_design_index() == []


def test_module_folder_is_registered_by_the_architecture_table(tree):
    tree('设计/README.md', '# 索引\n\n[接口](接入/接口.md) [架构](架构.md)\n')
    tree('设计/架构.md', '# 架构\n\n## 模块表\n\n| [平台](组件/平台/README.md) | [支付中心](组件/平台/支付中心/接口.md) |\n')
    tree('设计/组件/平台/README.md', '# 平台\n')
    tree('设计/组件/平台/术语.md', '# 术语\n')
    tree('设计/组件/平台/进展/01-票.md', '# 票\n')
    tree('设计/组件/平台/支付中心/接口.md', '# 接口\n')
    tree('设计/组件/平台/支付中心/依赖.md', '# 依赖\n')
    assert check_docs.check_design_index() == []


def test_module_folder_outside_the_architecture_table_is_reported(tree):
    tree('设计/README.md', '# 索引\n\n[接口](接入/接口.md) [架构](架构.md)\n')
    tree('设计/架构.md', '# 架构\n\n## 模块表\n\n| [部署](组件/部署/接口.md) |\n')
    tree('设计/组件/部署/接口.md', '# 部署\n')
    tree('设计/组件/孤儿/接口.md', '# 孤儿\n')
    assert check_docs.check_design_index() == [
        'Module folder missing from architecture table: ' + str(Path('设计/组件/孤儿')),
        missing('设计/组件/孤儿/接口.md')]


def test_moved_section_leaves_a_reported_anchor_and_missing_file(tree):
    page = tree('设计/流程/使用.md',
                '# 使用\n\n[旧位置](../接入/接口.md#http-入口) [新位置](../接入/接口/入口.md#http-入口) '
                '[子标题](../接入/接口/入口.md#幂等错误与恢复) [已删除](../接入/没有.md) [外部](https://example.com/a#b)\n')
    assert check_docs.link_failures([page]) == [
        'Broken anchor: ' + str(Path('设计/流程/使用.md')) + ' -> ../接入/接口.md#http-入口',
        'Broken link: ' + str(Path('设计/流程/使用.md')) + ' -> ../接入/没有.md']


def test_anchor_ignores_fenced_text_and_numbers_repeated_headings(tree):
    page = tree('设计/流程/重复.md',
                '# 重复\n\n## 步骤\n\n```text\n## 假标题\n[假链接](不存在.md)\n```\n\n## 步骤\n\n'
                '<a id="固定点"></a>\n\n[一](#步骤) [二](#步骤-1) [点](#固定点) [假](#假标题) [三](#步骤-2)\n')
    assert check_docs.link_failures([page]) == [
        'Broken anchor: ' + str(Path('设计/流程/重复.md')) + ' -> #假标题',
        'Broken anchor: ' + str(Path('设计/流程/重复.md')) + ' -> #步骤-2']


def test_listed_document_that_does_not_exist_is_reported(tree):
    assert check_docs.link_failures([check_docs.ROOT / '设计/没有这页.md']) == [
        'Missing documentation entry: ' + str(Path('设计/没有这页.md'))]


def test_repository_documents_pass_the_same_checks():
    assert check_docs.check_design_index() == []
    assert check_docs.link_failures(check_docs.current_documents()) == []
