"""Agent operation catalog: every operation a product script or local RPC caller can name, declared once.

The resident runtime registers its fixed operations from this table and accepts a role module's
operations only when the table names that role as handler. Script operations are handled by the
product script or the SDK/host CLI and never reach the resident runtime.
"""
from dataclasses import dataclass

# Exposure: AGENT operations are documented in the product skills; INTERNAL ones are callable but not taught.
AGENT, INTERNAL = 'agent', 'internal'
# Effect: READ only reads local or platform state; LOCAL changes only this instance's state or files;
# EXTERNAL may change platform, contract, message or payment state; CONTROL manages the instance itself.
READ, LOCAL, EXTERNAL, CONTROL = 'read', 'local', 'external', 'control'
# Handler: RUNTIME is ResidentRuntime.call; ASSISTANT and PROVIDER are role modules; SCRIPT is the product script/CLI.
RUNTIME, ASSISTANT, PROVIDER, SCRIPT = 'runtime', 'assistant', 'provider', 'script'

LOCAL_READ = ('completed', 'rejected')
QUERY = ('completed', 'rejected', 'unknown')
CONFIRMED = ('waiting', 'completed', 'rejected', 'unknown')
SEND = ('accepted', 'completed', 'rejected', 'unknown')
JOB = ('accepted', 'running', 'waiting', 'completed', 'rejected', 'failed', 'unknown')
SETUP = ('completed', 'waiting', 'rejected', 'failed', 'unknown')


@dataclass(frozen=True)
class Operation:
    name: str
    handler: str
    inputs: tuple  # accepted argument shapes, each (required fields, optional fields)
    effect: str
    confirmation: bool  # may wait for an SDK user confirmation record before its effect
    results: tuple  # operation statuses this operation can return
    exposure: str


def shape(required=(), optional=()):
    return (tuple(required), tuple(optional))


NONE = (shape(),)
REFERENCE = (shape(('reference',)),)


def _assistant(name, inputs, effect, confirmation, results, exposure=AGENT):
    return Operation('assistant.' + name, ASSISTANT, inputs, effect, confirmation, results, exposure)


def _provider(name, inputs, effect, confirmation, results, exposure=AGENT):
    return Operation('provider.' + name, PROVIDER, inputs, effect, confirmation, results, exposure)


CONTRACT_STEP = ('confirmationRef',)
PERIOD = (shape(('contractRef', 'period'), CONTRACT_STEP),)

OPERATIONS = (
    # Resident runtime fixed operations.
    Operation('runtime.status', RUNTIME, NONE, READ, False, LOCAL_READ, AGENT),
    Operation('runtime.stop', RUNTIME, (shape(('bootId',)),), CONTROL, False, ('completed', 'waiting', 'rejected'), AGENT),
    Operation('runtime.open_ui', RUNTIME, NONE, LOCAL, False, LOCAL_READ, AGENT),
    Operation('identity.status', RUNTIME, NONE, READ, False, LOCAL_READ, AGENT),
    Operation('operations.get', RUNTIME, REFERENCE, READ, False, LOCAL_READ, AGENT),
    Operation('operations.resume', RUNTIME, REFERENCE, EXTERNAL, False, ('completed', 'rejected', 'unknown'), AGENT),
    Operation('confirmations.get', RUNTIME, REFERENCE, READ, False, LOCAL_READ, INTERNAL),
    Operation('account.refresh', RUNTIME, NONE, CONTROL, False, QUERY, AGENT),
    # Product script and CLI commands; arguments come from --config or the args-file.
    Operation('runtime.install', SCRIPT, NONE, LOCAL, False, SETUP, AGENT),
    Operation('runtime.install.status', SCRIPT, NONE, READ, False, SETUP, AGENT),
    Operation('runtime.validate', SCRIPT, NONE, READ, False, LOCAL_READ, AGENT),
    Operation('runtime.start', SCRIPT, NONE, CONTROL, False, SETUP, AGENT),
    Operation('runtime.configuration', SCRIPT, NONE, READ, False, LOCAL_READ, AGENT),
    Operation('runtime.configure', SCRIPT, (shape(('expectedDigest', 'changes')),), LOCAL, False, LOCAL_READ, AGENT),
    Operation('runtime.web-extension', SCRIPT, (shape(('profile',)),), LOCAL, False, LOCAL_READ, AGENT),
    Operation('account.login', SCRIPT, NONE, CONTROL, False, QUERY, AGENT),
    Operation('account.setup', SCRIPT, (shape((), ('platform_url','role')),), LOCAL, False, SETUP, AGENT),
    Operation('account.setup.status', SCRIPT, NONE, READ, False, LOCAL_READ, AGENT),
    # Personal assistant role.
    _assistant('tools.list', NONE, READ, False, LOCAL_READ),
    _assistant('rules.draft', (shape(('declaration', 'instructions')),), LOCAL, False, LOCAL_READ),
    _assistant('rules.validate', (shape(('draftRef',)),), READ, False, LOCAL_READ),
    _assistant('rules.preview', (shape(('draftRef',)),), READ, False, QUERY),
    _assistant('rules.publish', (shape(('draftRef', 'contentDigest', 'expectedRevision'), ('confirmationRef',)),), EXTERNAL, True, CONFIRMED),
    _assistant('rules.list', NONE, READ, False, LOCAL_READ),
    _assistant('rules.disable', (shape(('name', 'expectedRevision'), ('confirmationRef',)),), EXTERNAL, True, CONFIRMED),
    _assistant('messaging.interfaces', (shape(('account',)),), READ, False, QUERY),
    _assistant('messaging.send', (shape(('input',), ('interfaceRef', 'interactionRef', 'ttlSeconds')),), EXTERNAL, False, SEND),
    _assistant('messaging.status', (shape(('messageRef',), ('waitSeconds',)),), READ, False, QUERY),
    _assistant('messaging.close', (shape((), ('messageRef', 'interactionRef', 'reason')),), EXTERNAL, False, SEND),
    _assistant('delegation.begin', (shape(('purpose', 'budget', 'proposalFeeLimit'), ('categoryIds', 'validDays')),), EXTERNAL, True, CONFIRMED),
    _assistant('delegation.prepare', (shape(('purpose', 'budget', 'proposalFeeLimit'), ('categoryIds', 'validDays')),), LOCAL, False, LOCAL_READ),
    _assistant('delegation.authorize', (shape(('taskRef',), ('validDays', 'confirmationRef')),), EXTERNAL, True, CONFIRMED, INTERNAL),
    _assistant('delegation.renew', (shape(('taskRef',), ('validDays', 'confirmationRef')),), EXTERNAL, True, CONFIRMED, INTERNAL),
    _assistant('delegation.status', (shape(('quoteRef',)), shape(('contractRef',), ('fulfillmentOperationId',)), shape(('taskRef',))), READ, False, QUERY),
    _assistant('delegation.close', (shape(('taskRef',)),), EXTERNAL, False, QUERY),
    _assistant('delegation.intent', (shape(('taskRef', 'service'), ('request', 'card', 'ttlSeconds')),), EXTERNAL, False, SEND, INTERNAL),
    _assistant('delegation.intent_status', (shape(('messageRef',)),), READ, False, QUERY, INTERNAL),
    _assistant('delegation.request', (shape(('taskRef', 'service', 'templateDigest', 'spec'), ('materials', 'confirmationRef')),), EXTERNAL, True, JOB),
    _assistant('delegation.inspect', (shape(('service',), ('templateDigest',)),), READ, False, QUERY),
    _assistant('delegation.search', (shape(('taskRef', 'query'), ('topK',)),), READ, False, QUERY),
    _assistant('delegation.confirm', (shape(('quoteRef',), ('confirmationRef',)),), EXTERNAL, True, JOB),
    _assistant('delegation.compare', (shape(('taskRef', 'quoteRefs')),), LOCAL, False, JOB),
    _assistant('delegation.start', (shape(('contractRef',), CONTRACT_STEP),), EXTERNAL, False, JOB),
    _assistant('delegation.answer_step', (shape(('contractRef', 'stepRequestDigest'), CONTRACT_STEP),), EXTERNAL, True, JOB),
    _assistant('delegation.fetch', PERIOD, EXTERNAL, False, JOB),
    _assistant('delegation.verify', PERIOD, LOCAL, False, JOB),
    _assistant('delegation.accept', PERIOD, EXTERNAL, True, JOB),
    _assistant('delegation.pay', PERIOD, EXTERNAL, True, JOB),
    _assistant('delegation.termination_quote', (shape(('contractRef',)),), EXTERNAL, False, JOB),
    _assistant('delegation.terminate', (shape(('contractRef', 'mode'), ('terminationQuoteRef', 'reason', 'confirmationRef')),), EXTERNAL, True, JOB),
    _assistant('delegation.dispute', (shape(('contractRef', 'ground'), ('period', 'materials', 'confirmationRef')),
                                      shape(('quoteRef', 'ground'), ('period', 'materials', 'confirmationRef'))), EXTERNAL, True, CONFIRMED),
    _assistant('delegation.dispute_status', (shape(('caseRef',)),), READ, False, QUERY),
    # Service provider role.
    _provider('preview', (shape(('service',)),), LOCAL, False, JOB),
    _provider('publish', (shape(('service', 'contentDigest', 'expectedRevision'), ('confirmationRef',)),), EXTERNAL, True, CONFIRMED),
    _provider('status', (shape(('profile',)),), READ, False, QUERY),
    _provider('reputation', (shape(('profile',)),), READ, False, QUERY),
    _provider('categories', NONE, READ, False, QUERY),
    _provider('verifier.publish', (shape(('profile', 'verifierId', 'source'), ('reads', 'kind')),), EXTERNAL, False, QUERY),
    _provider('tools.list', (shape(('profile',)),), READ, False, LOCAL_READ, INTERNAL),
    _provider('template.draft', (shape(('profile', 'terms')),), LOCAL, False, LOCAL_READ),
    _provider('pause', (shape(('profile',)),), EXTERNAL, False, QUERY),
    _provider('resume', (shape(('profile',)),), EXTERNAL, False, QUERY),
    _provider('tasks.get', (shape(('profile',), ('taskRef',)),), READ, False, QUERY),
    _provider('task.resume', (shape(('profile', 'taskRef'), ('confirmationRef',)),), EXTERNAL, True, JOB),
    _provider('dispute', (shape(('profile', 'taskRef', 'ground'), ('period', 'materials', 'confirmationRef')),), EXTERNAL, True, CONFIRMED),
    _provider('dispute_status', (shape(('profile', 'caseRef')),), READ, False, QUERY),
)

CATALOG = {operation.name: operation for operation in OPERATIONS}
if len(CATALOG) != len(OPERATIONS):
    raise RuntimeError('Duplicate operation in the agent operation catalog')


def handled_by(handler):
    """Operation names one handler registers, in catalog order."""
    return tuple(operation.name for operation in OPERATIONS if operation.handler == handler)


def fields(name):
    """The single (required, optional) argument shape of an operation with one input form."""
    inputs = CATALOG[name].inputs
    if len(inputs) != 1:
        raise RuntimeError('Operation has several input forms; check them where they are chosen: ' + name)
    return inputs[0]
