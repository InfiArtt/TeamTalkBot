#!/usr/bin/env python3
"""Download the TeamTalk 5 SDK from BearWare.dk and install the parts the bot needs.

The TeamTalk 5 SDK is proprietary (TeamTalk 5 SDK License Agreement, BearWare.dk),
so it is not shipped with this project. This script fetches the SDK archive for
the current platform and installs:

* the native library: ``TeamTalk_DLL/TeamTalk5.dll`` on Windows, or
  ``libTeamTalk5.so`` next to ``main.sh`` on Linux (main.sh puts that folder
  on LD_LIBRARY_PATH)
* the Python binding: ``TeamTalkPy/``

Both come from the same archive, so the binding always matches the library.

Usage:
  python tools/download_sdk.py
  python tools/download_sdk.py --version v5.19 --platform ubuntu22_x86_64
  python tools/download_sdk.py --archive tt5sdk_v5.19_win64.7z   # downloaded by hand
"""

import argparse
import ctypes
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

BASE_URL = "https://bearware.dk/teamtalksdk"
# SDK series the bot is tested with; the newest release in this series is used
DEFAULT_SERIES = "v5.19"
USER_AGENT = "Mozilla/5.0 (TeamTalkBot SDK downloader)"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def detect_platform() -> str:
    """Return BearWare's archive suffix for this machine."""
    machine = platform.machine().lower()
    if sys.platform == "win32":
        # The DLL must match the Python interpreter, not just the OS
        return "win64" if struct.calcsize("P") * 8 == 64 else "win32"
    if sys.platform.startswith("linux"):
        if machine in ("x86_64", "amd64"):
            return "ubuntu22_x86_64"
        if machine.startswith("armv7") or machine == "armhf":
            return "raspbian_armhf"
    sys.exit(
        f"No known SDK build for {sys.platform}/{machine}. "
        "Pick one with --platform (see the file names on " + BASE_URL + ")."
    )


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def resolve_version(series: str) -> str:
    """Pick the newest release folder of ``series`` (e.g. v5.19 -> v5.19a)."""
    try:
        page = fetch(BASE_URL + "/").decode("utf-8", "ignore")
    except Exception as exc:
        print(f"Could not read the release list ({exc}); trying {series} as is.")
        return series
    found = re.findall(r'href="(' + re.escape(series) + r'[^"/]*)/"', page)
    return found[-1] if found else series


def download(url: str, dest: str) -> None:
    print(f"Downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response, open(dest, "wb") as out:
        shutil.copyfileobj(response, out)
    print(f"Downloaded {os.path.getsize(dest) / 1e6:.1f} MB")


def find_7z() -> str:
    for name in ("7z", "7za", "7zr"):
        path = shutil.which(name)
        if path:
            return path
    default = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "7-Zip", "7z.exe")
    return default if os.path.exists(default) else ""


def extract(archive: str, dest: str) -> None:
    if archive.lower().endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
        return
    try:
        import py7zr  # optional

        with py7zr.SevenZipFile(archive) as sz:
            sz.extractall(dest)
        return
    except ImportError:
        pass
    seven_zip = find_7z()
    if not seven_zip:
        sys.exit(
            "Cannot extract .7z: install 7-Zip (Linux: sudo apt install p7zip-full) "
            "or run: pip install py7zr"
        )
    subprocess.run([seven_zip, "x", archive, f"-o{dest}", "-y"], check=True, stdout=subprocess.DEVNULL)


def find_library_dir(extracted: str) -> str:
    """Return the SDK's Library folder (it holds TeamTalk_DLL and TeamTalkPy)."""
    for dirpath, dirnames, _files in os.walk(extracted):
        if "TeamTalk_DLL" in dirnames and "TeamTalkPy" in dirnames:
            return dirpath
    sys.exit("The archive has no Library/TeamTalk_DLL and Library/TeamTalkPy folders.")


def sdk_version(lib_path: str) -> str:
    try:
        lib = ctypes.CDLL(lib_path)
        lib.TT_GetVersion.restype = ctypes.c_wchar_p if os.name == "nt" else ctypes.c_char_p
        value = lib.TT_GetVersion()
        return value.decode() if isinstance(value, bytes) else str(value)
    except Exception as exc:
        return f"unknown ({exc})"


def install(library_dir: str, windows: bool, force: bool) -> None:
    lib_name = "TeamTalk5.dll" if windows else "libTeamTalk5.so"
    lib_src = os.path.join(library_dir, "TeamTalk_DLL", lib_name)
    if not os.path.exists(lib_src):
        sys.exit(f"{lib_name} not found in the archive; is --platform right?")
    lib_dest = (
        os.path.join(ROOT, "TeamTalk_DLL", lib_name) if windows else os.path.join(ROOT, lib_name)
    )
    py_dest = os.path.join(ROOT, "TeamTalkPy")
    existing = [p for p in (lib_dest, py_dest) if os.path.exists(p)]
    if existing and not force:
        sys.exit(
            "Already installed: " + ", ".join(existing) + "\nRun again with --force to replace."
        )
    os.makedirs(os.path.dirname(lib_dest), exist_ok=True)
    shutil.copy2(lib_src, lib_dest)
    if os.path.exists(py_dest):
        shutil.rmtree(py_dest)
    shutil.copytree(os.path.join(library_dir, "TeamTalkPy"), py_dest)
    print(f"Installed {lib_dest}")
    print(f"Installed {py_dest}")
    print(f"TeamTalk SDK version: {sdk_version(lib_dest)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", help=f"SDK release folder, default: newest {DEFAULT_SERIES}")
    parser.add_argument("--platform", help="win64, win32, ubuntu22_x86_64, raspbian_armhf, ...")
    parser.add_argument("--archive", help="use an SDK archive (.7z or .zip) you already have")
    parser.add_argument("--force", action="store_true", help="replace an existing installation")
    args = parser.parse_args()

    suffix = args.platform or detect_platform()
    windows = suffix.startswith("win")
    print(
        "The TeamTalk 5 SDK is licensed by BearWare.dk under its own terms "
        "(License.txt in the archive); read them before use."
    )
    with tempfile.TemporaryDirectory() as tmp:
        archive = args.archive
        if not archive:
            version = args.version or resolve_version(DEFAULT_SERIES)
            name = f"tt5sdk_{version}_{suffix}.7z"
            archive = os.path.join(tmp, name)
            try:
                download(f"{BASE_URL}/{version}/{name}", archive)
            except Exception as exc:
                sys.exit(
                    f"Download failed: {exc}\nDownload {name} by hand from {BASE_URL}/ "
                    "and run: python tools/download_sdk.py --archive <file>"
                )
        extracted = os.path.join(tmp, "sdk")
        extract(archive, extracted)
        install(find_library_dir(extracted), windows, args.force)


if __name__ == "__main__":
    main()
