"""Assemble the native app and matching source, without importing user data."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile

def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()

def ordinary(path):
    details=path.lstat()
    if not stat.S_ISREG(details.st_mode) or path.is_symlink() or getattr(details,"st_file_attributes",0)&0x400:
        raise ValueError("package inputs must be regular files")
    if path.suffix.lower() in {".dpapi",".key",".token",".pfx",".p12"}:
        raise ValueError("sensitive package input")

def package(repo,executable,cli,output,dependencies):
    repo=repo.resolve();output=output.resolve()
    for path in [executable,cli,repo/"rust/Cargo.toml",repo/"LICENSE"]:ordinary(path)
    version=re.search(r'^version = "([0-9]+\.[0-9]+\.[0-9]+)"', (repo/"rust/Cargo.toml").read_text(encoding="utf-8"),re.M).group(1)
    provenance=json.loads((repo/"third_party/codex-app-server/RUNTIME.json").read_text(encoding="utf-8"))
    runtime=repo/"third_party/codex-app-server/codex-app-server.exe";ordinary(runtime)
    if runtime.stat().st_size!=provenance["runtime_size"] or digest(runtime)!=provenance["runtime_sha256"]:
        raise ValueError("runtime differs from pinned provenance")
    output.mkdir(parents=True,exist_ok=True)
    if (output/"Mini-Monitor").exists() or (output/"Mini-Monitor.zip").exists():raise FileExistsError("output already exists")
    with tempfile.TemporaryDirectory(prefix="native-package-",dir=output) as temporary:
        tree=Path(temporary)/"Mini-Monitor";tree.mkdir()
        def copy(source,relative):
            ordinary(source);target=tree/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        copy(executable,"Mini-Monitor.exe");copy(cli,"Mini-Monitor-CLI.exe")
        copy(repo/"LICENSE","LICENSE")
        assets=[repo/"assets/app-icon.ico",repo/"assets/app-icon.png",repo/"assets/update-public-key.pem",*sorted((repo/"assets/icons").glob("*.png"))]
        for source in assets:
            relative=source.relative_to(repo);copy(source,relative);copy(source,Path("source")/relative)
        copy(repo/"scripts/Apply-Update.ps1","scripts/Apply-Update.ps1");copy(repo/"scripts/Apply-Update.ps1","source/scripts/Apply-Update.ps1")
        copy(repo/"scripts/Repair-UpdateLinks.ps1","scripts/Repair-UpdateLinks.ps1");copy(repo/"scripts/Repair-UpdateLinks.ps1","source/scripts/Repair-UpdateLinks.ps1")
        for source in sorted((repo/"rust/src").rglob("*.rs")):
            copy(source,Path("source")/source.relative_to(repo))
        for name in ["Cargo.toml","Cargo.lock","build.rs","app.rc","app.manifest"]:
            copy(repo/"rust"/name,Path("source/rust")/name)
        copy(repo/"LICENSE","source/LICENSE")
        for name in ["codex-app-server.exe","RUNTIME.json","SOURCE.md","NOTICE"]:
            source=repo/"third_party/codex-app-server"/name
            copy(source,source.relative_to(repo))
        copy(repo/"LICENSES/python/cryptography/LICENSE.APACHE","LICENSES/Codex-Apache-2.0.txt")
        index=[]
        for dependency in dependencies:
            if dependency["name"]=="mini-monitor-native":continue
            base=Path(dependency["manifest_path"]).parent
            licenses=[p for p in base.iterdir() if p.is_file() and p.name.upper().startswith(("LICENSE","COPYING","NOTICE"))]
            if not licenses:raise ValueError("dependency license text missing: "+dependency["name"])
            folder=Path("LICENSES/native")/(dependency["name"]+"-"+dependency["version"])
            for source in licenses:copy(source,folder/source.name)
            index.append({"name":dependency["name"],"version":dependency["version"],"license":dependency.get("license")})
        (tree/"LICENSES/native-index.json").write_text(json.dumps(index,indent=2)+"\n",encoding="utf-8")
        # Stable online installers use this file solely as version metadata.
        (tree/"source/pyproject.toml").write_text('# Installer compatibility metadata; corresponding application source is rust/.\n[project]\nname = "mini-monitor"\nversion = "'+version+'"\n',encoding="utf-8")
        files={p.relative_to(tree).as_posix():digest(p) for p in sorted(tree.rglob("*")) if p.is_file()}
        fingerprint=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
        (tree/"BUILD-INFO.json").write_text(json.dumps({"schema":1,"engine":"native","version":version,"input_fingerprint":fingerprint},sort_keys=True)+"\n",encoding="utf-8")
        files["BUILD-INFO.json"]=digest(tree/"BUILD-INFO.json")
        (tree/"SHA256SUMS.txt").write_text("".join(f"{value}  {name}\n" for name,value in sorted(files.items())),encoding="utf-8")
        with zipfile.ZipFile(Path(temporary)/"Mini-Monitor.zip","x",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
            for source in sorted(tree.rglob("*")):
                if source.is_file():archive.write(source,"Mini-Monitor/"+source.relative_to(tree).as_posix())
        shutil.move(str(tree),str(output/"Mini-Monitor"));shutil.move(str(Path(temporary)/"Mini-Monitor.zip"),str(output/"Mini-Monitor.zip"))
    return output/"Mini-Monitor.zip"

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--output",required=True,type=Path);args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1]
    metadata=json.loads(subprocess.check_output(["cargo","metadata","--offline","--format-version","1","--filter-platform","x86_64-pc-windows-msvc"],cwd=repo/"rust"))
    archive=package(repo,repo/"rust/target/release/mini-monitor-native.exe",repo/"rust/target/release/cli.exe",args.output,metadata["packages"])
    print(json.dumps({"archive":str(archive),"sha256":digest(archive)}))
if __name__=="__main__":main()
