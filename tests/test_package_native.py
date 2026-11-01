from pathlib import Path
import importlib.util
import pytest

def module():
    path=Path(__file__).parents[1]/"scripts/package_native.py"
    spec=importlib.util.spec_from_file_location("package_native",path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result

def test_native_package_rejects_missing_inputs_without_modifying_existing_output(tmp_path):
    package=module();output=tmp_path/"output";output.mkdir();sentinel=output/"keep.txt";sentinel.write_text("keep")
    with pytest.raises((ValueError,FileNotFoundError)):
        package.package(tmp_path,tmp_path/"missing.exe",tmp_path/"missing-cli.exe",output,[])
    assert sentinel.read_text()=="keep"

def test_native_package_contains_installer_metadata_source_and_no_extra_runtime_files(tmp_path):
    import hashlib,json,zipfile
    repo=tmp_path/"repo";repo.mkdir();runtime=b"runtime fixture"
    files={"rust/Cargo.toml":b'[package]\nname = "mini-monitor-native"\nversion = "0.3.0"\n',"rust/Cargo.lock":b"fixture","rust/build.rs":b"fn main(){}","rust/app.rc":b"fixture","rust/app.manifest":b"fixture","rust/src/main.rs":b"fn main(){}","LICENSE":b"license","assets/app-icon.ico":b"icon","assets/app-icon.png":b"image","assets/update-public-key.pem":b"public key","scripts/Apply-Update.ps1":b"fixture","LICENSES/python/cryptography/LICENSE.APACHE":b"Apache fixture","third_party/codex-app-server/codex-app-server.exe":runtime,"third_party/codex-app-server/SOURCE.md":b"source","third_party/codex-app-server/NOTICE":b"notice","third_party/codex-app-server/secret.dpapi":b"must not ship","main.exe":b"app","cli.exe":b"cli"}
    for relative,data in files.items():
        path=repo/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    (repo/"scripts/Repair-UpdateLinks.ps1").write_bytes(b"fixture")
    (repo/"third_party/codex-app-server/RUNTIME.json").write_text(json.dumps({"runtime_size":len(runtime),"runtime_sha256":hashlib.sha256(runtime).hexdigest()}))
    archive=module().package(repo,repo/"main.exe",repo/"cli.exe",tmp_path/"out",[])
    with zipfile.ZipFile(archive) as package:
        assert not any("secret" in name for name in package.namelist())
        assert "Mini-Monitor/source/rust/src/main.rs" in package.namelist()
        assert 'version = "0.3.0"' in package.read("Mini-Monitor/source/pyproject.toml").decode()
