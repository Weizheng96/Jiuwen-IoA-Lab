"""Review-draft generation is independent of movies, credentials and business."""
import copy

import pytest

from demo import script_documents
from demo import review_story
from demo.review_story import DRAFTS


def test_generate_and_check_new_drafts_without_published_media_or_legacy_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(script_documents, 'DEMO', tmp_path)
    def no_published(*_args):
        raise AssertionError('Review draft must not read published movies or voice data')
    monkeypatch.setattr(script_documents, 'published', no_published)
    old = {name: tmp_path / name / '剧本.md' for name in ('全局视角', '美甲委托', '助手交互')}
    for path in old.values():
        path.parent.mkdir(); path.write_text('existing published screenplay', encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['script_documents.py', '--drafts'])
    script_documents.main()
    monkeypatch.setattr('sys.argv', ['script_documents.py', '--drafts', '--check'])
    script_documents.main()
    for case, scene in DRAFTS.items():
        rendered = (tmp_path / case / '剧本.md').read_text(encoding='utf-8')
        assert '本版台词与旁白已获用户批准 · 未录制' in rendered
        assert '待用户确认' not in rendered
        assert 120 <= sum(shot['seconds'] for shot in scene['shots']) <= 180
        for shot in scene['shots']:
            assert shot['voice'] in rendered
            assert shot['evidence'] in rendered
    assert all(path.read_text(encoding='utf-8') == 'existing published screenplay' for path in old.values())


def test_approved_script_content_change_cannot_reuse_user_approval(monkeypatch):
    assert review_story.APPROVAL['statement'] == '确认三稿，按稿制作'
    scenes = copy.deepcopy(DRAFTS)
    scenes['messaging']['shots'][0]['voice'] += ' altered narration'
    monkeypatch.setattr(review_story, 'DRAFTS', scenes)
    with pytest.raises(ValueError, match='Approved screenplay content changed'):
        script_documents.render_draft('messaging')


def test_provider_first_publication_order_preserves_approved_words_and_other_films():
    original=copy.deepcopy(DRAFTS)
    current=review_story.approved_content()
    provider=current['delegation-provider']
    ids=[shot['id'] for shot in provider['shots']]
    assert ids.index('publish')<ids.index('ready')
    assert {shot['id']:shot for shot in provider['shots']}=={shot['id']:shot for shot in original['delegation-provider']['shots']}
    assert provider['recordingAdjustment']['sourceApprovalSha256']==review_story.APPROVAL['contentSha256']
    assert current['delegation-assistant']==original['delegation-assistant']
    assert current['messaging']==original['messaging']
    assert DRAFTS==original
    rendered=script_documents.render_draft('delegation-provider')
    assert rendered.index('| publish |')<rendered.index('| ready |')


def test_missing_or_edited_draft_fails_check_instead_of_repairing_it(tmp_path, monkeypatch):
    monkeypatch.setattr(script_documents, 'DEMO', tmp_path)
    case = next(iter(DRAFTS))
    with pytest.raises(ValueError, match='Regenerate review draft'):
        script_documents.write_draft(case, check=True)
    target = script_documents.write_draft(case)
    target.write_text('changed narration', encoding='utf-8')
    with pytest.raises(ValueError, match='Regenerate review draft'):
        script_documents.write_draft(case, check=True)
    assert target.read_text(encoding='utf-8') == 'changed narration'


@pytest.mark.parametrize('defect', ['duplicate', 'narration', 'duration'])
def test_incomplete_or_invalid_draft_source_is_rejected(defect, monkeypatch):
    scenes = copy.deepcopy(DRAFTS)
    scene = scenes['messaging']
    if defect == 'duplicate':
        scene['shots'][1]['id'] = scene['shots'][0]['id']
    elif defect == 'narration':
        scene['shots'][0]['voice'] = ''
    else:
        scene['shots'][0]['seconds'] = 200
    monkeypatch.setattr(script_documents, 'draft_scenes', lambda: scenes)
    with pytest.raises(ValueError):
        script_documents.render_draft('messaging')
