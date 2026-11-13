#!/usr/bin/env python3
"""从 tools/source.zip 解出原始三件套到 tools/source/，不改一个字节。"""

import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_ZIP = ROOT / "tools" / "source.zip"
OUT_DIR = ROOT / "tools" / "source"

EXPECTED = {
    "9ba626afa44a3aa3.patch_2":
        "62543F01EDACCFF1B51BAC15A4753A2DA9A20B99312B9FF4F813DA9BDE4F325E",
    "9ba626afa44a3aa3.patch_2.gpu_resources":
        "D34632FC77D97D7B65F0EEB715000CD350D5BC3AAADB7040417D46092CD196EB",
    "9ba626afa44a3aa3.patch_2.stream":
        "E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855",
}


def main():
    if not SOURCE_ZIP.is_file():
        raise SystemExit("missing immutable source: %s" % SOURCE_ZIP)
    with zipfile.ZipFile(SOURCE_ZIP) as z:
        names = set(z.namelist())
        if names != set(EXPECTED):
            raise SystemExit("unexpected files in %s: %s" % (SOURCE_ZIP, sorted(names)))
        payload = {}
        for name, want in EXPECTED.items():
            data = z.read(name)
            got = hashlib.sha256(data).hexdigest().upper()
            if got != want:
                raise SystemExit("source checksum mismatch for %s: %s" % (name, got))
            payload[name] = data

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for p in OUT_DIR.iterdir():
        if p.is_file():
            p.unlink()
    for name, data in payload.items():
        (OUT_DIR / name).write_bytes(data)
    print("source restored verbatim: %d files, patch=%d gpu=%d" % (
        len(payload), len(payload["9ba626afa44a3aa3.patch_2"]),
        len(payload["9ba626afa44a3aa3.patch_2.gpu_resources"])))


if __name__ == "__main__":
    main()
