"""Certificate maintenance through the public deployment CLI, with real OpenSSL."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import pytest

from deploy.layout import Layout


@pytest.fixture
def certificate_authority(tmp_path):
    layout = Layout(tmp_path/'runtime', tmp_path/'state', tmp_path/'config')
    layout.claim()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'maintenance test CA')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1))
            .not_valid_after(now+timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(key, hashes.SHA256()))
    ca = tmp_path/'ca.crt'; ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private = tmp_path/'ca.key'
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    private.chmod(0o600)
    source = tmp_path/'authority.json'
    source.write_text(json.dumps({'format':'ioalab.internal-ca/1','ca_certificate':str(ca),
        'ca_private_key':str(private),'ca_sha256':cert.fingerprint(hashes.SHA256()).hex(),
        'server_certificate':str(tmp_path/'server.crt'),'server_private_key':str(tmp_path/'server.key')}))
    source.chmod(0o600)
    core = layout.config/'core'; core.mkdir()
    (core/'platform.json').write_text(json.dumps({'runtime':{'public_url':'https://ioa.test'}}))
    return layout, source, ca, private


def test_issue_export_and_idempotent_renew_preserve_ca_and_private_keys(certificate_authority, monkeypatch, capsys):
    from deploy import tls
    layout, source, ca, private = certificate_authority
    monkeypatch.setattr(tls, 'require_host', lambda: None)
    before = (ca.read_bytes(), private.read_bytes())
    assert tls.main(['issue','--state',str(layout.state),'--authority',str(source),'--hostname','ioa.test']) == 0
    issued = json.loads(capsys.readouterr().out)
    assert issued['status'] == 'completed' and issued['data']['changed'] is True
    assert tls.main(['export-ca','--state',str(layout.state),'--origin','https://ioa.test:443',
                     '--output',str(source.parent/'public')]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported['status'] == 'completed'
    files = list((source.parent/'public').iterdir())
    assert {p.name for p in files} == {'ca.pem','platform-trust.json'}
    assert all(b'PRIVATE KEY' not in path.read_bytes() for path in files)
    assert tls.main(['renew','--state',str(layout.state)]) == 0
    assert json.loads(capsys.readouterr().out)['data']['changed'] is False
    assert before == (ca.read_bytes(), private.read_bytes())


@pytest.mark.parametrize('damage', ['fingerprint', 'key_mismatch', 'same_key_path', 'linked_ca'])
def test_invalid_ca_registration_is_rejected_without_replacing_server(certificate_authority, monkeypatch, capsys, damage):
    from deploy import tls
    layout, source, ca, private = certificate_authority
    monkeypatch.setattr(tls, 'require_host', lambda: None)
    value = json.loads(source.read_text())
    if damage == 'fingerprint': value['ca_sha256'] = '0'*64
    elif damage == 'same_key_path': value['server_private_key'] = value['ca_private_key']
    elif damage == 'key_mismatch':
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private.write_bytes(other.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    else:
        link = ca.with_name('linked.crt')
        try: link.symlink_to(ca)
        except OSError: pytest.skip('No symlink privilege')
        value['ca_certificate'] = str(link)
    source.write_text(json.dumps(value))
    assert tls.main(['issue','--state',str(layout.state),'--authority',str(source),'--hostname','ioa.test']) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'rejected'
    assert not Path(value['server_certificate']).exists()
    assert not (layout.config/'tls/authority.json').exists()


@pytest.mark.skipif(os.name == "nt", reason="Linux systemd literal paths; run on Linux")
def test_renewal_timer_is_bound_to_current_source_and_cannot_take_foreign_units(certificate_authority, monkeypatch, capsys):
    from deploy import tls
    layout, source, _, _ = certificate_authority
    monkeypatch.setattr(tls, 'require_host', lambda: None)
    assert tls.main(['issue','--state',str(layout.state),'--authority',str(source),'--hostname','ioa.test']) == 0
    capsys.readouterr()
    directory = source.parent/'units'; directory.mkdir()
    monkeypatch.setattr(tls, 'SYSTEMD_DIRECTORY', directory)
    seen = []; monkeypatch.setattr(tls, 'systemctl', lambda *args: seen.append(args))
    assert tls.main(['install-renewal','--state',str(layout.state)]) == 0
    result = json.loads(capsys.readouterr().out)
    text = (directory/'ioalab-tls-renew.service').read_text()
    assert result['data']['renewalConfigured'] is True
    assert 'WorkingDirectory=' in text and str(layout.state) in text
    assert '-m' in text and 'deploy.tls' in text and 'renew' in text
    assert ('enable','--now','ioalab-tls-renew.timer') in seen
    (directory/'ioalab-tls-renew.service').write_text('# another deployment\n')
    assert tls.main(['install-renewal','--state',str(layout.state)]) == 1
    assert (directory/'ioalab-tls-renew.service').read_text() == '# another deployment\n'


def test_due_renewal_restores_old_certificate_on_failed_activation_then_recovers(certificate_authority, monkeypatch, capsys):
    from deploy import tls
    layout, source, ca, private = certificate_authority
    monkeypatch.setattr(tls, 'require_host', lambda: None)
    assert tls.main(['issue','--state',str(layout.state),'--authority',str(source),'--hostname','ioa.test']) == 0
    capsys.readouterr()
    value = json.loads(source.read_text()); server = Path(value['server_certificate'])
    key = serialization.load_pem_private_key(Path(value['server_private_key']).read_bytes(), password=None)
    ca_key = serialization.load_pem_private_key(private.read_bytes(), password=None)
    ca_cert = x509.load_pem_x509_certificate(ca.read_bytes()); now = datetime.now(timezone.utc)
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME,'ioa.test')]))
            .issuer_name(ca_cert.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=20))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('ioa.test')]),critical=False)
            .sign(ca_key, hashes.SHA256()))
    old = leaf.public_bytes(serialization.Encoding.PEM); server.write_bytes(old)
    def reject(*args): raise RuntimeError('activation failed at system boundary')
    monkeypatch.setattr(tls.Authority, 'reload_and_verify', reject)
    # Isolate rollback's systemctl/nginx boundary; certificate issuance remains real OpenSSL.
    original_run = tls.subprocess.run
    monkeypatch.setattr(tls.subprocess, 'run', lambda args, **kwargs: type('Result',(),{'returncode':0})()
                        if args[0] in ('nginx','systemctl') else original_run(args,**kwargs))
    assert tls.main(['renew','--state',str(layout.state)]) == 1
    capsys.readouterr(); assert server.read_bytes() == old
    seen = []; monkeypatch.setattr(tls.Authority, 'reload_and_verify', lambda self,host: seen.append(host))
    assert tls.main(['renew','--state',str(layout.state)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['data']['changed'] is True and seen == ['ioa.test']
    assert server.read_bytes() != old and not (layout.config/'tls/pending.json').exists()
    assert tls.main(['renew','--state',str(layout.state)]) == 0
    assert json.loads(capsys.readouterr().out)['data']['changed'] is False and seen == ['ioa.test']
