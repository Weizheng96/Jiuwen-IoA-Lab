"""P40 control seam; P20 routing and role effects are explicit boundary fixtures."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest

from ioalab_sdk.runtime.host import ToolBinding
from ioalab_sdk.runtime.host_submission import HostToolCall, HostToolDeclaration
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.runtime.scheduling import CURRENT_JOB
from ioalab_sdk.runtime.sdk_tool_callbacks import CallbackPool, SdkToolCallbacks


class ToolCallbackTests(unittest.TestCase):
    def setUp(self):
        self.pool=CallbackPool(1)
        self.effects=[]
        self.marker=(object(),'original-model-job')
        self.tool=ToolBinding('ioa_reply','original reply',{'type':'object',
            'properties':{'output':{'type':'integer'}},'required':['output'],'additionalProperties':False},lambda *_:None)
        token=CURRENT_JOB.set(self.marker)
        try:self.channel=SdkToolCallbacks(tools=(self.tool,),on_tool=self.original,pool=self.pool)
        finally:CURRENT_JOB.reset(token)

    def original(self,call):
        self.assertEqual(CURRENT_JOB.get(),self.marker)
        self.effects.append((call.name,call.arguments,call.call_id))
        return {'status':'submitted','receipt':'original-sdk-receipt'}

    def call(self,call_id='native-call',arguments=None):
        return HostToolCall('operation','attempt','session','request',call_id,'ioa_reply',arguments or {'output':1})

    def tearDown(self):
        self.channel.seal()
        self.assertTrue(self.channel.drain(2))
        self.pool.seal()
        self.assertTrue(self.pool.drain(2))
        self.pool.close()

    def test_original_context_result_replay_and_conflicting_parameters(self):
        original=self.channel.start(self.call())
        self.assertEqual(original.result(2),{'status':'submitted','receipt':'original-sdk-receipt'})
        self.channel.seal()
        self.assertIs(self.channel.start(self.call()),original)
        with self.assertRaises(RuntimeFault):self.channel.start(self.call(arguments={'output':2}))
        self.assertEqual(len(self.effects),1)

    def test_schema_and_frozen_declaration_fail_before_effect(self):
        with self.assertRaises(RuntimeFault):self.channel.handle(self.call(arguments={'output':'invalid'}))
        remote=ToolBinding('bad','remote',{'$ref':'https://example.invalid/schema'},lambda *_:None)
        with self.assertRaises(RuntimeFault):SdkToolCallbacks(tools=(remote,),on_tool=self.original,pool=self.pool)
        self.tool.input_schema['properties']['output']['type']='string'
        self.assertEqual(self.channel.handle(self.call()).value['receipt'],'original-sdk-receipt')
        self.assertEqual(len(self.effects),1)

    def test_busy_seal_and_bounded_drain_wait_for_actual_callback(self):
        entered,release=threading.Event(),threading.Event()
        def callback(*args):
            entered.set()
            self.assertTrue(release.wait(2))
            return {'status':'unknown','reference':'original-effect'}
        self.channel.on_tool=callback
        future=self.channel.start(self.call())
        self.assertTrue(entered.wait(2))
        self.assertFalse(future.cancel())
        with self.assertRaises(RuntimeFault):self.channel.start(self.call('another-call'))
        self.channel.seal()
        self.pool.seal()
        self.assertFalse(self.channel.drain(0))
        self.assertFalse(self.pool.drain(0))
        with self.assertRaises(RuntimeFault):self.pool.close()
        release.set()
        self.assertEqual(future.result(2)['reference'],'original-effect')
        self.assertTrue(self.channel.drain(2))

    def test_cancel_before_start_recovers_the_single_control_slot(self):
        self.pool.close()
        entered,release=threading.Event(),threading.Event()
        def initialize():
            entered.set()
            release.wait(2)
        self.pool=CallbackPool(1,executor=ThreadPoolExecutor(max_workers=1,initializer=initialize))
        self.channel.pool=self.pool
        queued=self.channel.start(self.call('cancelled-call'))
        self.assertTrue(entered.wait(2))
        self.assertTrue(queued.cancel())
        self.assertTrue(self.channel.drain(2))
        self.assertEqual(self.effects,[])
        next_call=self.channel.start(self.call('actual-call'))
        release.set()
        self.assertEqual(next_call.result(2)['status'],'submitted')
        self.assertEqual(len(self.effects),1)

    def test_callback_exception_is_not_reinvoked_or_rewritten_as_success(self):
        def unknown(*args):
            self.effects.append('original-attempt')
            raise RuntimeFault('unknown_result','Original effect is unresolved')
        self.channel.on_tool=unknown
        first=self.channel.start(self.call())
        with self.assertRaises(RuntimeFault):first.result(2)
        self.assertIs(self.channel.start(self.call()),first)
        self.assertEqual(self.effects,['original-attempt'])

    def test_callback_can_seal_own_channel_without_lock_deadlock(self):
        def callback(*args):
            self.channel.seal()
            return {'status':'recorded'}
        self.channel.on_tool=callback
        self.assertEqual(self.channel.handle(self.call()).value,{'status':'recorded'})
        self.assertTrue(self.channel.drain(2))

    def test_invalid_declared_output_preserves_role_effect_and_same_call_does_not_repeat(self):
        declaration=HostToolDeclaration('ioa_reply','SDK result',self.tool.input_schema,
            {'type':'object','properties':{'receipt':{'type':'string'}},'required':['receipt'],'additionalProperties':False})
        def original_effect(call):
            result={'receipt':7}
            self.effects.append({'call':call.call_id,'originalResult':result})
            return result
        self.channel=SdkToolCallbacks(tools=(declaration,),on_tool=original_effect,pool=self.pool)
        future=self.channel.start(self.call())
        with self.assertRaises(RuntimeFault) as error:future.result(2)
        self.assertEqual(error.exception.code,'invalid_tool_output')
        self.assertIs(self.channel.start(self.call()),future)
        with self.assertRaises(RuntimeFault):self.channel.handle(self.call())
        self.assertEqual(self.effects,[{'call':'native-call','originalResult':{'receipt':7}}])

    def test_valid_output_uses_frozen_declared_contract(self):
        declaration=HostToolDeclaration('ioa_reply','SDK result',self.tool.input_schema,
            {'type':'object','properties':{'receipt':{'type':'string'}},'required':['receipt'],'additionalProperties':False})
        self.channel=SdkToolCallbacks(tools=(declaration,),on_tool=lambda call:{'receipt':'real-receipt'},pool=self.pool)
        declaration.output_schema['properties']['receipt']['type']='integer'
        self.assertEqual(self.channel.handle(self.call()).value,{'receipt':'real-receipt'})

    def test_invalid_output_declaration_is_rejected_before_any_effect(self):
        declaration=HostToolDeclaration('ioa_reply','SDK result',self.tool.input_schema,
            {'$ref':'https://example.invalid/remote-output-contract'})
        with self.assertRaises(RuntimeFault):
            SdkToolCallbacks(tools=(declaration,),on_tool=self.original,pool=self.pool)
        self.assertEqual(self.effects,[])


if __name__=='__main__':unittest.main(verbosity=2)
