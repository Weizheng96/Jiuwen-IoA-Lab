"""Pure port public seam; original Registry authorization is a boundary fixture."""
import unittest

from ioalab_sdk.runtime.host_submission import SubmissionAttempt
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.runtime.submission_resources import SubmissionResourcePort


class ResourcePortTests(unittest.TestCase):
    def setUp(self):
        self.port=SubmissionResourcePort()
        self.attempt=SubmissionAttempt('nonce-a','scope-a','actor-a','profile-a')

    def test_construction_and_host_factory_do_not_lookup_or_probe(self):
        calls=[]
        factory=self.port.for_host('host-a')
        with self.assertRaises(RuntimeFault) as error:factory('operation-a',self.attempt)
        self.assertEqual(error.exception.code,'not_ready')
        self.port.bind(lambda *args:calls.append(args) or (lambda ref:b'original',lambda *args:'output'))
        self.assertEqual(calls,[])
        reader,sink=factory('operation-a',self.attempt)
        self.assertEqual(reader('ref'),b'original')
        self.assertEqual(sink('actual bytes'),'output')
        self.assertEqual(calls,[('host-a','operation-a',self.attempt)])
        self.assertIs(calls[0][2],self.attempt)

    def test_same_bound_delegate_is_idempotent_but_other_owner_is_rejected(self):
        class Registry:
            def lookup(self,*args):return (lambda _:b'original',lambda *_:None)
        original,other=Registry(),Registry()
        self.port.bind(original.lookup)
        self.port.bind(original.lookup)
        with self.assertRaises(RuntimeFault):self.port.bind(other.lookup)
        with self.assertRaises(RuntimeFault):self.port.bind(lambda *_:None)
        reader,_=self.port.for_host('host-a')('op-a',self.attempt)
        self.assertEqual(reader('ref'),b'original')

    def test_fixed_host_full_attempt_and_delegate_errors_are_preserved(self):
        calls=[]
        def original(host,operation,attempt):
            calls.append((host,operation,attempt))
            raise RuntimeFault('identity_conflict','Original Registry scope mismatch')
        self.port.bind(original)
        factory=self.port.for_host('fixed-host')
        with self.assertRaises(TypeError):factory('op-a',self.attempt,host_ref='other-host')
        self.assertEqual(calls,[])
        with self.assertRaises(RuntimeFault) as error:factory('op-a',self.attempt)
        self.assertEqual(error.exception.code,'identity_conflict')
        self.assertEqual(calls,[('fixed-host','op-a',self.attempt)])

    def test_missing_io_or_model_attempt_shape_is_not_a_fallback(self):
        calls=[]
        self.port.bind(lambda *args:calls.append(args) or None)
        factory=self.port.for_host('host-a')
        with self.assertRaises(RuntimeFault):factory('op-a',{'nonce':'model-chosen'})
        self.assertEqual(calls,[])
        with self.assertRaises(RuntimeFault) as error:factory('op-a',self.attempt)
        self.assertEqual(error.exception.code,'not_ready')


if __name__=='__main__':unittest.main(verbosity=2)
