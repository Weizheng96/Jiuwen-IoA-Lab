"""Distribution downloads use their configured location, not package parents."""
import zipfile


def test_skill_downloads_from_configured_directory(platform,client,tmp_path,monkeypatch):
    monkeypatch.setattr(platform.s,'bundles_dir',tmp_path)
    for name in ('ioalab-skill.zip','ioalab-client.zip'):
        path=tmp_path/name
        with zipfile.ZipFile(path,'w') as archive:
            archive.writestr('README.md','isolated public test bundle')
        response=client.get('/downloads/'+name)
        assert response.status_code==200
        assert response.headers['content-type']=='application/zip'
        assert response.content==path.read_bytes()


def test_missing_bundle_is_unready_without_source_fallback(platform,client,tmp_path,monkeypatch):
    monkeypatch.setattr(platform.s,'bundles_dir',tmp_path)
    assert client.get('/downloads/ioalab-skill.zip').status_code==503
    assert client.get('/downloads/ioalab-client.zip').status_code==503
