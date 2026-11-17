# Codex fixed IoA entry

Business calls of this role go through one fixed native entry so that the real Codex chat identity reaches the IoA client. Run it from the Codex shell exactly in this form, with absolute paths and nothing else on the line:

~~~text
& "__IOALAB_PYTHON__" "<this native directory>\entry-assistant.py" <operation> --args-file "<absolute arguments file>"
& "__IOALAB_PYTHON__" "<this native directory>\entry-provider.py" <operation> --args-file "<absolute arguments file>"
~~~

Use the entry of the current role. The interpreter must be the bound client interpreter shown above. Keep the original arguments file and its `operationId` for waiting, unknown or retried calls. Do not add session, path, entry or context fields, pipes, redirections, `;`, `&&`, variables or extra arguments: such commands are not the fixed entry and keep Codex's original approval without the chat identity. Control and diagnostic operations may use the same entry; they run without a chat source.

The installed Codex PreToolUse hook (`python -m ioalab_codex.native_install install`) recognizes only this exact command. It appends a one-time private handoff and returns `allow` for this call only, so that Codex accepts the rewritten input; Codex sandbox and approval rules still apply afterwards, and SDK contract, acceptance and payment confirmations still need the protected browser page. Codex runs a new or changed user hook only after you review and trust it in Codex hook settings; reload or open a new chat after installing. Installing files does not prove that the current desktop chat loaded the hook.

Explicit setup uses the same fixed entry and `--args-file` shape, with operation `runtime.entry.bind`. Its JSON file must contain only the original `operationId`; it takes no business or identity arguments. After the original SDK intent, packaged code captures the actual native ancestor CLI, original project and CODEX_HOME directory pointer, claims the original SDK permission, saves under an OS lock and acknowledges that same claim. A saved receipt is recovered before recapturing; another chat or role cannot replace the project. Keep this arguments file when a result is unknown.

Business turns run in this Codex desktop (route A). The IoA client queues each turn into its original chat; for a new external business it first creates a persistent, named chat without a model call and then queues the first message. The queued message carries the frozen turn context. Queued work is consumed only when the desktop has that chat loaded: open the chat to let it run. Native approvals appear only in the desktop UI; the client never answers them.

Inside such a queued turn, SDK tools use the same entry with the operation `assistant.native.tool` or `provider.native.tool` and an arguments file containing exactly `{"name": <declared SDK tool>, "arguments": {...}}`. The hook binds the call to the actual chat and turn; the entry prints the SDK tool result. Do not repeat a tool whose result is unknown.
