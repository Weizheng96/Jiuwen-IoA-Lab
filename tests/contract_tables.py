"""Read the communication contract tables from the web服务 interface pages; shared by the platform and SDK tests."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / '设计/组件/平台/web服务/接口'


def table(page, heading, header):
    """Rows of the table under `## heading` whose header row is exactly `header`, as dicts of plain cells."""
    text = (PAGES / page).read_text(encoding='utf-8')
    section = text.split('## ' + heading + '\n', 1)[1].split('\n## ', 1)[0]
    rows, columns = [], None
    for line in section.splitlines():
        if not line.startswith('|'):
            if columns is not None:
                break
            continue
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if columns is None:
            assert cells == list(header), f'{page} · {heading} header changed: {cells}'
            columns = cells
        elif not set(''.join(cells)) <= set('-: '):
            rows.append({column: cell.replace('`', '') for column, cell in zip(columns, cells)})
    assert rows, f'{page} · {heading} has no rows'
    return rows


def split(cell):
    return {item.strip() for item in re.split('[、,，]', cell) if item.strip()}


def kinds():
    rows = table('WebSocket与消息封套.md', '种类契约', ('种类', '类别', '方向', '发送方', '处理方'))
    names = [row['种类'] for row in rows]
    assert len(names) == len(set(names)), 'Each kind appears once'
    for row in rows:
        assert row['类别'] in ('连接帧', '控制', '操作记录', '消息', '系统消息'), row
        assert row['方向'] in ('参与方→平台', '平台→参与方', '参与方→参与方'), row
    return rows


def errors():
    rows = table('幂等与错误.md', '错误码', ('错误码', '产生方', '含义与恢复'))
    codes = [row['错误码'] for row in rows]
    assert len(codes) == len(set(codes)), 'Each error code appears once'
    for row in rows:
        assert split(row['产生方']) <= {'平台', 'SDK'} and split(row['产生方']), row
    return rows
