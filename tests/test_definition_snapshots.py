"""P41 candidate public seam: real FrozenSkills, local filesystem boundary."""
from pathlib import Path
from dataclasses import replace
import shutil
import json
import tempfile
import unittest

from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.runtime.definition_snapshots import freeze_definition, read_definition_resource


class DefinitionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.binding={'actor':'assistant-a','business':'message-a','profile':'personal'}
        self.arguments={'reference':'rules:private-handler','version':'revision-a',
                        'instructions':'Return the real result only','binding':self.binding}

    def tearDown(self):self.temp.cleanup()

    def test_instruction_only_needs_no_extra_professional_skill(self):
        workspace=self.root/'business-a'
        first=freeze_definition(workspace,**self.arguments)
        restored=freeze_definition(workspace,**self.arguments,resume=True,expected_digest=first.digest)
        self.assertEqual(first,restored)
        self.assertEqual(first.resources,())

    def test_named_reference_is_frozen_and_resume_ignores_changed_latest(self):
        source=self.root/'selected';source.mkdir()
        (source/'SKILL.md').write_text('Original professional instructions',encoding='utf-8')
        workspace=self.root/'business-a'
        first=freeze_definition(workspace,**self.arguments,skill_names=('writing',),sources={'writing':source})
        (source/'SKILL.md').write_text('Changed latest source',encoding='utf-8')
        restored=freeze_definition(workspace,**self.arguments,skill_names=('writing',),resume=True,expected_digest=first.digest)
        self.assertEqual(read_definition_resource(restored,restored.resources[0],binding=self.binding,workspace=workspace),b'Original professional instructions')
        next_business=freeze_definition(self.root/'business-b',**{**self.arguments,'version':'revision-b'},
            skill_names=('writing',),sources={'writing':source})
        self.assertNotEqual(next_business.digest,first.digest)

    def test_missing_reference_or_changed_binding_is_not_false_support(self):
        with self.assertRaises(RuntimeFault):
            freeze_definition(self.root/'missing',**self.arguments,skill_names=('unresolved',))
        first=freeze_definition(self.root/'business',**self.arguments)
        with self.assertRaises(RuntimeFault):
            freeze_definition(self.root/'business',**{**self.arguments,'instructions':'New version'},resume=True,expected_digest=first.digest)
        with self.assertRaises(RuntimeFault):
            freeze_definition(self.root/'business',**self.arguments,resume=True)

    def test_document_reference_needs_no_extra_skill_and_freezes_original_bytes(self):
        source=self.root/'professional.md'
        source.write_text('Selected document',encoding='utf-8')
        arguments={**self.arguments,'instructions':''}
        first=freeze_definition(self.root/'document-business',**arguments,skill_names=('professional-doc',),sources={'professional-doc':source})
        source.write_text('Changed source',encoding='utf-8')
        restored=freeze_definition(self.root/'document-business',**arguments,skill_names=('professional-doc',),resume=True,expected_digest=first.digest)
        self.assertEqual(read_definition_resource(restored,restored.resources[0],binding=self.binding,workspace=self.root/'document-business'),b'Selected document')

    def test_snapshot_tamper_and_foreign_resource_are_rejected(self):
        source=self.root/'professional.md';source.write_text('Original document',encoding='utf-8')
        workspace=self.root/'business'
        first=freeze_definition(workspace,**self.arguments,skill_names=('doc',),sources={'doc':source})
        resource=first.resources[0]
        with self.assertRaises(RuntimeFault):
            read_definition_resource(first,replace(resource,locator=(self.root/'secret').as_uri()),binding=self.binding,workspace=workspace)
        with self.assertRaises(RuntimeFault):
            read_definition_resource(first,resource,binding={**self.binding,'business':'another-business'},workspace=workspace)
        descriptor=json.loads((workspace/'execution-definition.json').read_text(encoding='utf-8'))
        (workspace/descriptor['payload']['paths'][resource.resource_id]).write_text('Tampered snapshot',encoding='utf-8')
        with self.assertRaises(RuntimeFault):
            freeze_definition(workspace,**self.arguments,skill_names=('doc',),resume=True,expected_digest=first.digest)

    def test_moved_cache_preserves_original_definition_and_resource_identity(self):
        source=self.root/'professional.md';source.write_text('Original document',encoding='utf-8')
        original=self.root/'original-cache'
        first=freeze_definition(original,**self.arguments,skill_names=('doc',),sources={'doc':source})
        moved=self.root/'moved-cache'
        shutil.copytree(original,moved)
        source.unlink()
        restored=freeze_definition(moved,**self.arguments,skill_names=('doc',),resume=True,expected_digest=first.digest)
        self.assertEqual(restored,first)
        self.assertEqual(read_definition_resource(restored,restored.resources[0],binding=self.binding,workspace=moved),b'Original document')


if __name__=='__main__':unittest.main(verbosity=2)
