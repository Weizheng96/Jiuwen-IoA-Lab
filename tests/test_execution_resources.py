from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import json

from a2c.codec import digest,digest_bytes
from ioalab_sdk.runtime.host_submission import HostResource
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.runtime.execution_resources import ExecutionResources,ResourceGrant


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.binding={'actor':'provider','contract':'card-a','period':1}
        self.material=HostResource('material-a','input.txt','text/plain',digest_bytes(b'approved'),digest(self.binding),'platform:original-file')
        self.scope=ExecutionResources(binding=self.binding,operation_id='native-op',
            grants=(ResourceGrant(self.material,lambda:b'approved'),),output_root=Path(self.temp.name),authorize=lambda *_:None)

    def tearDown(self):self.temp.cleanup()

    def test_only_original_grant_is_read_and_bytes_are_verified(self):
        self.assertEqual(self.scope.read_resource(self.material),b'approved')
        with self.assertRaises(RuntimeFault):self.scope.read_resource(replace(self.material,locator='file:///private/secret'))
        wrong=ExecutionResources(binding=self.binding,operation_id='native-op',grants=(ResourceGrant(self.material,lambda:b'changed'),),output_root=Path(self.temp.name),authorize=lambda *_:None)
        with self.assertRaises(RuntimeFault):wrong.read_resource(self.material)

    def test_output_recovery_and_new_content_version_keep_old_resource_immutable(self):
        output=self.scope.store_output('result.txt','text/plain',b'actual',digest_bytes(b'actual'))
        restarted=ExecutionResources(binding=self.binding,operation_id='native-op',output_root=Path(self.temp.name),authorize=lambda *_:None)
        self.assertEqual(restarted.read_output(output),b'actual')
        self.assertEqual(restarted.store_output('result.txt','text/plain',b'actual',digest_bytes(b'actual')),output)
        newer=restarted.store_output('result.txt','text/plain',b'new',digest_bytes(b'new'))
        self.assertNotEqual(newer.resource_id,output.resource_id)
        self.assertEqual(restarted.read_output(newer),b'new')
        self.assertEqual(restarted.read_output(output),b'actual')

    def test_model_path_or_other_period_cannot_become_sdk_resource(self):
        with self.assertRaises(RuntimeFault):self.scope.store_output('../secret','text/plain',b'x',digest_bytes(b'x'))
        output=self.scope.store_output('result.txt','text/plain',b'actual',digest_bytes(b'actual'))
        other=ExecutionResources(binding={**self.binding,'period':2},operation_id='native-op',output_root=Path(self.temp.name),authorize=lambda *_:None)
        with self.assertRaises(RuntimeFault):other.read_output(output)

    def test_material_authorization_denial_happens_before_any_bytes_are_read(self):
        reads=[]
        def forbidden(*_):raise RuntimeFault('forbidden','Original material grant expired')
        denied=ExecutionResources(binding=self.binding,operation_id='native-op',output_root=Path(self.temp.name),
            grants=(ResourceGrant(self.material,lambda:reads.append('read') or b'approved'),),authorize=forbidden)
        with self.assertRaises(RuntimeFault):denied.read_resource(self.material)
        self.assertEqual(reads,[])

    def test_cached_output_tampering_is_rejected_without_latest_or_reproduction(self):
        output=self.scope.store_output('result.txt','text/plain',b'original',digest_bytes(b'original'))
        cache=Path(self.temp.name)/(output.resource_id.removeprefix('sha256:')+'.json')
        saved=json.loads(cache.read_text(encoding='utf-8'))
        saved['bytes']='Y2hhbmdlZA=='
        cache.write_text(json.dumps(saved),encoding='utf-8')
        with self.assertRaises(RuntimeFault):self.scope.read_output(output)


if __name__=='__main__':unittest.main(verbosity=2)
