"""Native desktop builds. Run with Python 3.11 on Windows x64 or Apple Silicon."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".build"
PREFIX = WORK / "native"
# Verified upstream release commits, not moving branches.
SOURCES = {
    "liboqs": ("https://github.com/open-quantum-safe/liboqs.git", "5a1a854b0dc9f2141bdc771c555ee60c37950183"),
    "opus": ("https://github.com/xiph/opus.git", "ddbe48383984d56acd9e1ab6a090c54ca6b735a6"),
}


def run(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, cwd=ROOT, **kwargs)


def build_native():
    for name, (url, revision) in SOURCES.items():
        source = WORK / "sources" / name
        if not source.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            run("git", "init", source)
            run("git", "-C", source, "remote", "add", "origin", url)
            run("git", "-C", source, "fetch", "--depth", "1", "origin", revision)
        run("git", "-C", source, "checkout", "--detach", revision)
        options = ["-DBUILD_SHARED_LIBS=ON", "-DCMAKE_BUILD_TYPE=Release",
                   f"-DCMAKE_INSTALL_PREFIX={PREFIX}"]
        if name == "liboqs":
            options += ["-DOQS_BUILD_ONLY_LIB=ON", "-DOQS_USE_OPENSSL=OFF",
                        "-DOQS_DIST_BUILD=ON",
                        "-DOQS_MINIMAL_BUILD=KEM_ml_kem_768;SIG_ml_dsa_65",
                        "-DCMAKE_WINDOWS_EXPORT_ALL_SYMBOLS=ON"]
        else:
            options += ["-DOPUS_BUILD_TESTING=OFF", "-DOPUS_BUILD_PROGRAMS=OFF"]
        if sys.platform == "darwin":
            options += ["-DCMAKE_OSX_ARCHITECTURES=arm64", "-DCMAKE_OSX_DEPLOYMENT_TARGET=11.0"]
        elif sys.platform == "win32":
            options += ["-A", "x64"]
        build = WORK / f"native-build-{name}"
        run("cmake", "-S", source, "-B", build, *options)
        run("cmake", "--build", build, "--config", "Release", "--parallel", "4")
        run("cmake", "--install", build, "--config", "Release")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-native", action="store_true", help="reuse .build/native")
    parser.add_argument("--native-only", action="store_true")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 11):
        parser.error("Use Python 3.11")
    is_mac = sys.platform == "darwin"
    if not ((is_mac and platform.machine() == "arm64") or
            (sys.platform == "win32" and platform.machine().lower() in ("amd64", "x86_64"))):
        parser.error("Build natively on Apple Silicon or Windows x64")
    if not args.skip_native:
        build_native()
    if args.native_only:
        return
    env = os.environ.copy()
    env["HPQV_NATIVE_PREFIX"] = str(PREFIX)
    # Analysis imports wrappers in subprocesses, so give those native dependencies too.
    env["OQS_INSTALL_PATH"] = str(PREFIX)
    env["PATH"] = str(PREFIX / "bin") + os.pathsep + env.get("PATH", "")
    if is_mac:
        env["DYLD_FALLBACK_LIBRARY_PATH"] = str(PREFIX / "lib")
    env["PYINSTALLER_CONFIG_DIR"] = str(WORK / "pyinstaller-cache")
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", WORK / "frozen", "--workpath", WORK / "pyinstaller",
        ROOT / "packaging" / "hpqv.spec", env=env)
    target = "macos-arm64" if is_mac else "windows-x64"
    stage = WORK / "packages" / f"HPQV-{target}"
    # Only replace this script's generated staging directory.
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    for name in (["HPQV.app", "hpqv-cli"] if is_mac else ["HPQV", "hpqv-cli"]):
        shutil.copytree(WORK / "frozen" / name, stage / name, symlinks=True)
    shutil.copy2(ROOT / "docs" / "DESKTOP-DEMO.md", stage / "START-HERE.md")
    licenses = stage / "licenses"
    licenses.mkdir()
    for name in SOURCES:
        source = WORK / "sources" / name
        for license_name in ("LICENSE", "COPYING"):
            if (source / license_name).exists():
                shutil.copy2(source / license_name, licenses / f"{name}-{license_name}")
    for distribution in importlib.metadata.distributions():
        for file in distribution.files or []:
            if any(word in str(file).lower() for word in ("license", "copying")):
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    dest = licenses / distribution.metadata["Name"] / file
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)
    for candidate in (Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE"):
        if candidate.is_file():
            shutil.copy2(candidate, licenses / "Python-LICENSE.txt")
            break
    versions = {name: importlib.metadata.version(name) for name in
                ("cryptography", "liboqs-python", "numpy", "opuslib", "sounddevice", "pyinstaller")}
    (stage / "BUILD-INFO.json").write_text(json.dumps({
        "platform": target, "python": sys.version, "packages": versions,
        "native_sources": SOURCES,
    }, indent=2), encoding="utf-8")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    archive = dist / f"HPQV-{target}.zip"
    if archive.exists():
        archive.unlink()
    if is_mac:
        # ditto preserves the app's framework symlinks and executable modes.
        run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", stage, archive)
    else:
        shutil.make_archive(str(archive.with_suffix("")), "zip", stage.parent, stage.name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n")
    print(f"Built {archive}")
    print("Extract it and run scripts/smoke_package.py against the extracted CLI before sharing.")


if __name__ == "__main__":
    main()
