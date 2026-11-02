#!/usr/bin/env python3
"""Check GitHub for a newer Frame HEV and install it with its own installer.

Follows the upstream repository by default. Set FRAME_HEV_UPDATE_REPO (or pass
--repo) to follow a fork, which is how a fork publishes its own releases. The
release archive is checked against the SHA256SUMS published beside it; both come
over the same TLS connection, so this detects a truncated or swapped download,
not a compromised account.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

DEFAULT_REPO = "royal32/Frame-HEV-Power-Level-SFX"
SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
VERSION = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?$")
API = "https://api.github.com/repos/{repo}/releases/latest"
DOWNLOAD = "https://github.com/{repo}/releases/download/v{version}/{name}"
HEADERS = {"User-Agent": "frame-hev-updater"}
TIMEOUT = 30
HERE = Path(__file__).resolve().parent


class UpdateError(RuntimeError):
    pass


def version_key(text):
    """Order releases by their numeric parts; a suffix sorts below the release."""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(.*)$", text.strip().lstrip("v"))
    if not match:
        raise UpdateError(f"cannot understand version {text!r}")
    major, minor, patch, suffix = match.groups()
    return int(major), int(minor), int(patch), 1 if not suffix else 0


def installed_version(root=None):
    try:
        text = ((Path(root) if root else HERE) / "VERSION").read_text().strip()
    except OSError:
        return "0.0.0"
    return text or "0.0.0"


def request(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=TIMEOUT)


def latest_release(repo):
    with request(API.format(repo=repo)) as response:
        report = json.load(response)
    version = str(report.get("tag_name") or "").lstrip("v")
    if not VERSION.match(version):
        raise UpdateError(f"{repo} has no usable release tag ({version!r})")
    return version


def checksum_from(text, name):
    """Pick one entry out of a SHA256SUMS file, or None when it is not listed."""
    for line in text.splitlines():
        digest, _, filename = line.partition("  ")
        if filename.strip().lstrip("*") == name and re.fullmatch(r"[0-9a-fA-F]{64}", digest.strip()):
            return digest.strip().lower()
    return None


def download(url, target):
    digest = hashlib.sha256()
    with request(url) as response, open(target, "wb") as output:
        while chunk := response.read(1 << 16):
            digest.update(chunk)
            output.write(chunk)
    return digest.hexdigest()


def extract(archive, root):
    """Unpack a release ZIP, refusing entries that escape the target directory."""
    root = Path(root).resolve()
    with zipfile.ZipFile(archive) as source:
        for info in source.infolist():
            target = (root / info.filename).resolve()
            if target != root and root not in target.parents:
                raise UpdateError(f"release archive contains a path outside its root: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(info) as data, target.open("wb") as output:
                shutil.copyfileobj(data, output)
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                target.chmod(mode)
    return root


def install_from(archive, work, repo, version):
    """Verify the archive against its published SHA256SUMS and run its installer."""
    name = Path(archive).name
    try:
        with request(DOWNLOAD.format(repo=repo, version=version, name="SHA256SUMS")) as response:
            expected = checksum_from(response.read().decode(errors="replace"), name)
    except (OSError, urllib.error.URLError):
        expected = None
    got = hashlib.sha256(Path(archive).read_bytes()).hexdigest()
    if expected is None:
        print(f"checksum   no SHA256SUMS entry for {name}; continuing on TLS only", file=sys.stderr)
    elif expected != got:
        raise UpdateError(f"checksum mismatch for {name}: expected {expected}, downloaded {got}")
    else:
        print("checksum   verified", file=sys.stderr)
    source = extract(archive, work / "source")
    installer = next(source.glob("*/tools/install.py"), None) or source / "tools/install.py"
    if not installer.is_file():
        raise UpdateError("release archive has no tools/install.py")
    print(f"installing v{version} with {installer}", file=sys.stderr)
    result = subprocess.run([sys.executable, str(installer)], cwd=str(installer.parent.parent))
    if result.returncode:
        raise UpdateError(f"installer exited with status {result.returncode}")
    return version


def apply_update(repo, version):
    work = Path(tempfile.mkdtemp(prefix="frame-hev-update-"))
    try:
        name = f"frame-hev-{version}.zip"
        archive = work / name
        url = DOWNLOAD.format(repo=repo, version=version, name=name)
        print(f"downloading {url}", file=sys.stderr)
        download(url, archive)
        return install_from(archive, work, repo, version)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("FRAME_HEV_UPDATE_REPO", DEFAULT_REPO))
    parser.add_argument("--check", action="store_true", help="report only (the default)")
    parser.add_argument("--apply", action="store_true", help="download and install the newest release")
    parser.add_argument("--json", action="store_true", help="machine-readable result")
    parser.add_argument("--installed", type=Path, help="directory holding the installed VERSION")
    args = parser.parse_args(argv)
    if not SLUG.match(args.repo):
        parser.error("--repo must look like owner/name")
    installed = installed_version(args.installed)
    report = {"repo": args.repo, "installed": installed}
    try:
        latest = latest_release(args.repo)
        newer = version_key(latest) > version_key(installed)
        report.update(latest=latest, newer=newer)
        if args.json:
            print(json.dumps(report))
        elif newer:
            print(f"installed  v{installed}")
            print(f"latest     v{latest}")
            print(f"update available: v{installed} -> v{latest}")
        else:
            print(f"installed  v{installed}")
            print(f"latest     v{latest}")
            print("already up to date")
        if newer and args.apply:
            version = apply_update(args.repo, latest)
            print(f"updated to v{version}")
        return 0
    except (OSError, urllib.error.URLError, UpdateError, zipfile.BadZipFile) as error:
        if args.json:
            print(json.dumps({**report, "error": str(error)}))
        else:
            print(f"update check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
