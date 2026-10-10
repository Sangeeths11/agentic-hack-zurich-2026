"""Install a pinned, checksum-verified cloudflared into .tools; no system changes."""

import hashlib
import io
import platform
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

VERSION = "2026.10.0"
# Digests published in cloudflare/cloudflared's official GitHub release assets.
DIGESTS = {
    "cloudflared-darwin-amd64.tgz": (
        "903845b81828c8cb3c5d13d816a2de71c06a3da5785469df8eb0e1b736d92f9f"
    ),
    "cloudflared-darwin-arm64.tgz": (
        "a2f79ff7b9420aa537d74af239f376da170bbabeb529aec416002adac6a72e70"
    ),
    "cloudflared-linux-amd64": "d33ff2d14475178d2012c2c56beba87389ac5ded27649519f198a7d3134a99db",
    "cloudflared-linux-arm64": "e6422b9d4f72d3194bc5a38676f13667c06666523217b842a877d72a80b5ac08",
}


def main():
    if existing := shutil.which("cloudflared"):
        subprocess.run([existing, "--version"], check=True, timeout=5)
        return
    target = Path(__file__).resolve().parents[1] / ".tools" / "cloudflared"
    if target.exists():
        result = subprocess.run(
            [str(target), "--version"], check=True, capture_output=True, text=True, timeout=5
        )
        if VERSION in result.stdout:
            print(result.stdout.strip())
            return
    system = platform.system().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(
        platform.machine().lower(), "unsupported"
    )
    asset = f"cloudflared-{system}-{arch}" + (".tgz" if system == "darwin" else "")
    if asset not in DIGESTS:
        raise SystemExit("Unsupported platform. Install cloudflared manually; use WSL2 on Windows.")
    url = f"https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/{asset}"
    print(f"Downloading cloudflared {VERSION} into .tools/ …")
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != DIGESTS[asset]:
        raise SystemExit("Cloudflared checksum mismatch; nothing installed.")
    if asset.endswith(".tgz"):
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            # Read just the executable into a known path; never extract archive paths.
            binary = archive.extractfile("cloudflared")
            if binary is None:
                raise SystemExit("Executable missing from archive")
            data = binary.read()
    target.parent.mkdir(exist_ok=True)
    temporary = target.with_suffix(".download")
    temporary.write_bytes(data)
    temporary.chmod(0o755)
    temporary.replace(target)
    subprocess.run([str(target), "--version"], check=True, timeout=5)


if __name__ == "__main__":
    main()
