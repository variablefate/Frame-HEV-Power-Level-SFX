#!/usr/bin/env python3
"""Check GitHub for a newer Frame HEV and install it with its own installer.

Follows the project's latest GitHub release, the same assets FrameDrop installs:
`releases/latest/download/SHA256SUMS` names the source archive and its digest,
so no API quota is involved. Set FRAME_HEV_UPDATE_REPO (or pass --repo) to
follow a fork. The archive and its checksum come over the same TLS connection,
so the checksum detects a truncated or swapped download, not a compromised
account.
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

DEFAULT_REPO = "royal32/Frame-HEV-Power-Level-SFX"  # sync with frame_hev.DEFAULT_UPDATE_REPO
SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
API = "https://github.com/{repo}/releases/latest/download/{name}"
ZIP_NAME = re.compile(r"^frame-hev-(\d+\.\d+\.\d+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?)\.zip$")
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
    """Read VERSION beside the updater, or one level up when run from tools/."""
    base = Path(root) if root else HERE
    for candidate in (base, base.parent):
        try:
            text = (candidate / "VERSION").read_text().strip()
        except OSError:
            continue
        if text:
            return text
    return "0.0.0"


def request(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=TIMEOUT)


def parse_sums(text):
    """Parse a SHA256SUMS file into {filename: digest}."""
    sums = {}
    for line in text.splitlines():
        digest, _, filename = line.partition("  ")
        name = filename.strip().lstrip("*")
        if name and re.fullmatch(r"[0-9a-fA-F]{64}", digest.strip()):
            sums[name] = digest.strip().lower()
    return sums


def release_assets(repo):
    """Return (version, source archive name, digest) from the latest release."""
    url = API.format(repo=repo, name="SHA256SUMS")
    with request(url) as response:
        sums = parse_sums(response.read().decode(errors="replace"))
    candidates = [(match.group(1), name) for name in sums
                  if (match := ZIP_NAME.match(name)) and "-linux-arm64" not in name]
    if not candidates:
        raise UpdateError(f"{repo} latest release has no frame-hev-<version>.zip")
    version, name = max(candidates, key=lambda item: version_key(item[0]))
    return version, name, sums[name]


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


def install_from(archive, work, name):
    """Unpack a verified release archive and run its own installer."""
    match = ZIP_NAME.fullmatch(name)
    if not match:
        raise UpdateError(f"unexpected release asset name {name!r}")
    version = match.group(1)
    source = extract(archive, work / "source")
    installer = next(source.glob("*/tools/install.py"), None) or source / "tools/install.py"
    if not installer.is_file():
        raise UpdateError("release archive has no tools/install.py")
    print(f"installing v{version} with {installer}", file=sys.stderr)
    result = subprocess.run([sys.executable, str(installer)], cwd=str(installer.parent.parent))
    if result.returncode:
        raise UpdateError(f"installer exited with status {result.returncode}")
    return version


def apply_update(repo, name, digest):
    work = Path(tempfile.mkdtemp(prefix="frame-hev-update-"))
    try:
        archive = work / name
        url = API.format(repo=repo, name=name)
        print(f"downloading {url}", file=sys.stderr)
        if download(url, archive) != digest:
            raise UpdateError(f"checksum mismatch for {name}; refusing to install")
        print("checksum   verified", file=sys.stderr)
        return install_from(archive, work, name)
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
        version, name, digest = release_assets(args.repo)
        newer = version_key(version) > version_key(installed)
        report.update(latest=version, newer=newer)
        if not args.json:
            print(f"installed  v{installed}")
            print(f"latest     v{version}")
            print(f"update available: v{installed} -> v{version}" if newer else "already up to date")
        if newer and args.apply:
            report["applied"] = apply_update(args.repo, name, digest)
            if not args.json:
                print(f"updated to v{report['applied']}")
        if args.json:
            print(json.dumps(report))
        return 0
    except (OSError, ValueError, urllib.error.URLError, UpdateError, zipfile.BadZipFile) as error:
        if args.json:
            print(json.dumps({**report, "error": str(error)}))
        else:
            print(f"update check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
