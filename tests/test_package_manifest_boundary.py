"""The public package endpoint rejects invalid manifests before storing a reference."""
import httpx
import pytest
from test_provider_authoring import author


def test_public_package_endpoint_does_not_store_a_filename_as_a_declared_read(author,platform):
    before=set(platform.packages.root.glob('*.zip'))
    with pytest.raises(httpx.HTTPStatusError) as error:
        author.account.api('POST','/provider/packages',{'verifierId':'invalid_reads@1.0','source':'def verify(ctx): return {"status":"pass"}',
            'kind':'script','reads':['result.json']})
    assert error.value.response.status_code==400
    assert set(platform.packages.root.glob('*.zip'))==before
