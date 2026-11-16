"""Owned A2C package bytes, resources, attribution and fixed Git snapshot inputs."""
import importlib.util
from pathlib import Path
import subprocess
import zipfile

import pytest

from deploy.wheels import verify_wheel

ROOT = Path(__file__).resolve().parents[1]


def fixture_source(tmp_path):
    source = tmp_path / "source"; (source / "a2c").mkdir(parents=True)
    (source / "a2c/__init__.py").write_bytes(b"name='a2c'\n")
    (source / "a2c/runtime.zip").write_bytes(b"original\r\nbinary")
    (source / "NOTICE.md").write_bytes(b"original source attribution\n")
    (source / "VERSION").write_text("v0.0.24\n")
    (source / "pyproject.toml").write_text('''[project]
name="a2c"
[tool.setuptools.packages.find]
where=["."]
include=["a2c*"]
[tool.setuptools.package-data]
a2c=["*.zip"]
[tool.setuptools]
license-files=["NOTICE.md"]
''')
    return source


def wheel(tmp_path, source, fault=None):
    path = tmp_path / "a2c.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name in ["a2c/__init__.py", "a2c/runtime.zip"]:
            if fault == "missing-resource" and name.endswith("zip"): continue
            blob = (source / name).read_bytes()
            if fault == "changed-bytes" and name.endswith("zip"): blob = blob.replace(b"\r\n", b"\n")
            archive.writestr(name, blob)
        if fault == "foreign-module": archive.writestr("a2c/old.py", b"foreign=True")
        archive.writestr("a2c-0.0.24.dist-info/METADATA", "Name: a2c\nVersion: " + ("0.0.23" if fault == "version" else "0.0.24") + "\n")
        archive.writestr("a2c-0.0.24.dist-info/licenses/NOTICE.md", b"changed" if fault == "notice" else (source / "NOTICE.md").read_bytes())
    return path


def test_owned_wheel_checks_every_package_byte_resource_version_and_notice(tmp_path):
    source = fixture_source(tmp_path)
    record = verify_wheel(wheel(tmp_path, source), source)
    assert record["package"] == "a2c" and record["version"] == "0.0.24"
    assert "a2c/runtime.zip" in record["sourceFiles"]


@pytest.mark.parametrize("fault", ["missing-resource", "changed-bytes", "foreign-module", "version", "notice"])
def test_owned_wheel_rejects_changed_or_missing_input(tmp_path, fault):
    source = fixture_source(tmp_path)
    with pytest.raises(RuntimeError): verify_wheel(wheel(tmp_path, source, fault), source)


def test_bootstrap_snapshots_owned_a2c_from_original_commit_not_dirty_checkout(tmp_path):
    spec = importlib.util.spec_from_file_location("bootstrap_a2c", ROOT / "src/client/bootstrap.py")
    bootstrap = importlib.util.module_from_spec(spec); spec.loader.exec_module(bootstrap)
    repo = tmp_path / "repo"; repo.mkdir()
    def git(*args):
        return subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    git("init")
    (repo / ".gitattributes").write_bytes(b"*.py text eol=lf\n")
    for directory in ["src/client", "src/a2c/a2c", "vendor/licenses"]: (repo / directory).mkdir(parents=True)
    (repo / "src/client/pyproject.toml").write_text('[project]\nname="ioalab-client"\n')
    (repo / "src/a2c/a2c/__init__.py").write_bytes(b"original=True\n")
    (repo / "vendor/SOURCES.json").write_text('{"components":[]}')
    (repo / "vendor/licenses/NOTICE").write_text("third-party attribution")
    git("add", "."); git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgSign=false", "commit", "-m", "source")
    commit = git("rev-parse", "HEAD")
    (repo / "src/a2c/a2c/__init__.py").write_bytes(b"dirty=True\r\n")
    target = tmp_path / "snapshot"; target.mkdir()
    bootstrap.snapshot(repo, commit, target)
    assert (target / "src/a2c/a2c/__init__.py").read_bytes() == b"original=True\n"
    assert (repo / "src/a2c/a2c/__init__.py").read_bytes() == b"dirty=True\r\n"


@pytest.mark.parametrize("key", ["a2c", "*"])
@pytest.mark.parametrize("resource", ["README.md", "nested/README.md"])
def test_declared_recursive_resources_include_root_and_nested_files(tmp_path, key, resource):
    source = fixture_source(tmp_path)
    settings = source / "pyproject.toml"
    text = settings.read_text().replace('a2c=["*.zip"]', '"' + key + '"=["*.zip", "**/*.md"]')
    settings.write_text(text)
    path = source / "a2c" / resource; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"declared resource")
    with pytest.raises(RuntimeError, match="resource is missing"):
        verify_wheel(wheel(tmp_path, source), source)
