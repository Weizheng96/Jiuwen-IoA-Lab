"""验收器：包格式、沙箱隔离与确定性、请求重放。"""
import pytest

from a2c.codec import digest_bytes
from a2c.errors import A2CError
from a2c.verifier.package import build, load
from a2c.verifier.runner import run


def _pkg(src: str, kind: str = "script", **manifest):
    m = {"id": "t@1", "kind": kind, "runtime": "cpython-3.13-wasi", "entry": "m:verify", "reads": [], **manifest}
    blob = build({"m.py": src}, m)
    return load(blob, {"id": "t@1", "uri": "u", "digest": digest_bytes(blob)}, kind)


def _run(src, kind="script", files=None, **kw):
    return run(_pkg(src, kind), target="d", values={}, files=files or {}, period=1, **kw)


def test_deterministic_clock_and_random():
    """时钟固定、随机数与 hash 种子可复现：两次运行结论一致。"""
    src = '''
import time, os, random
def verify(ctx):
    return {"status": "pass", "checks": [time.time(), os.urandom(4).hex(), hash("x"), random.random()]}
'''
    a, b = _run(src), _run(src)
    assert a.status == "pass" and a.checks == b.checks
    assert a.checks[0] == 946684800.0


@pytest.mark.parametrize("src, needle", [
    ('import socket\ndef verify(ctx):\n    socket.create_connection(("1.1.1.1", 80))\n', "Error"),
    ('def verify(ctx):\n    open("/files/f0", "w").write("x")\n', "PermissionError"),
    ('def verify(ctx):\n    open("/etc/passwd").read()\n', "FileNotFoundError"),
    ('import time\ndef verify(ctx):\n    time.sleep(1)\n    return {"status": "pass"}\n', "Error"),
])
def test_isolation(src, needle):
    """无网络、文件只读、看不到主机路径、不能睡眠：都以出错告终，不会通过。"""
    out = _run(src, files={"a.txt": b"hi"})
    assert out.status == "error" and needle in out.error


def test_fuel_limit():
    out = _run("def verify(ctx):\n    while True:\n        pass\n")
    assert out.status == "error" and "sandbox" in out.error


def test_bad_result_and_wrong_kind():
    """返回值不合规、脚本调用 observe 都是出错。"""
    assert _run("def verify(ctx):\n    return {'status': 'ok'}\n").status == "error"
    out = _run("from a2c_verifier import observe\ndef verify(ctx):\n    observe({})\n    return {'status': 'pass'}\n")
    assert out.status == "error"


def test_replay_by_sequence_and_divergence():
    """重放按调用序号：同一请求三次各问一次；记录与请求不符即出错。"""
    src = '''
from a2c_verifier import observe
def verify(ctx):
    vals = [observe({"q": 1})["v"] for _ in range(3)]
    return {"status": "pass" if sum(vals) == 6 else "fail", "checks": vals}
'''
    answers = iter([1, 2, 3])
    out = _run(src, kind="skill", resolver=lambda need: {"v": next(answers)})
    assert out.status == "pass" and out.checks == [1, 2, 3] and len(out.responses) == 3
    again = _run(src, kind="skill", recorded=out.responses)
    assert again.status == "pass" and again.rounds == 1
    forged = [{"need": {"kind": "observe", "request": {"q": 2}}, "response": {"v": 1}}]
    assert _run(src, kind="skill", recorded=forged).status == "error"


def test_swallowed_need_still_emitted():
    """验收器吞掉「需要」也拦不住：宿主照样拿到请求。"""
    src = '''
from a2c_verifier import observe
def verify(ctx):
    try:
        observe({"q": 1})
    except BaseException:
        pass
    return {"status": "pass"}
'''
    seen = []
    out = _run(src, kind="skill", resolver=lambda need: seen.append(need) or {"v": 1})
    assert seen and out.status == "pass"


def test_package_checks():
    """包与引用的摘要、清单 id、位置 kind、运行时、清单成员都要对。"""
    m = {"id": "t@1", "kind": "script", "runtime": "cpython-3.13-wasi", "entry": "m:verify", "reads": []}
    blob = build({"m.py": "def verify(ctx):\n    return {'status': 'pass'}\n"}, m)
    ref = {"id": "t@1", "uri": "u", "digest": digest_bytes(blob)}
    with pytest.raises(A2CError) as e:
        load(blob, {**ref, "digest": "sha256:" + "0" * 64}, "script")
    assert e.value.code == "digest-mismatch"
    for bad_ref, kind in (({**ref, "id": "t@2"}, "script"), (ref, "skill")):
        with pytest.raises(A2CError) as e:
            load(blob, bad_ref, kind)
        assert e.value.code == "template-invalid"
    blob2 = build({"m.py": "x"}, {**m, "runtime": "cpython-9-wasi"})
    with pytest.raises(A2CError):
        load(blob2, {**ref, "digest": digest_bytes(blob2)}, "script")


def test_runner_observation_context_uses_original_response_ordinal():
    from contextlib import contextmanager
    observed = []
    @contextmanager
    def context(ordinal, need):
        observed.append((ordinal, need.copy()))
        need['request'] = {'untrusted_mutation': True}  # context receives an isolated copy
        yield
    src = """from a2c_verifier import observe
def verify(ctx):
    values = [observe({'q': 1})['v'] for _ in range(3)]
    return {'status': 'pass', 'checks': values}
"""
    original = [{'need': {'kind': 'observe', 'request': {'q': 1}}, 'response': {'v': 1}}]
    calls = []
    def resolve(need):
        calls.append(need)
        assert need == {'kind': 'observe', 'request': {'q': 1}}
        return {'v': len(calls) + 1}
    out = _run(src, kind='skill', recorded=original, resolver=resolve, resolve_context=context)
    assert out.status == 'pass' and out.checks == [1, 2, 3]
    assert [ordinal for ordinal, _ in observed] == [1, 2] and len(calls) == 2


def test_material_observation_context_replays_same_need_after_paused_second_call():
    from contextlib import contextmanager
    src = """from a2c_verifier import observe
def verify(ctx):
    values = [observe({'q': 1})['v'] for _ in range(2)]
    return {'status': 'pass', 'checks': values}
"""
    saved = {}; seen = []; active = []; stopped = [True]; dispatches = []
    @contextmanager
    def context(ordinal, need):
        seen.append((ordinal, need.copy())); active.append(ordinal)
        try:
            if ordinal == 1 and stopped[0]: raise RuntimeError('machine proven not submitted')
            yield
        finally: active.pop()
    def resolve(need):
        ordinal = active[-1]
        if ordinal not in saved:
            dispatches.append(ordinal); saved[ordinal] = {'v': ordinal + 1}
        return saved[ordinal]
    with pytest.raises(RuntimeError): _run(src, kind='skill', resolver=resolve, resolve_context=context)
    assert dispatches == [0]
    stopped[0] = False
    out = _run(src, kind='skill', resolver=resolve, resolve_context=context)
    assert out.status == 'pass' and out.checks == [1, 2] and dispatches == [0, 1]
    assert seen[0] == seen[2] and seen[1] == seen[3]
