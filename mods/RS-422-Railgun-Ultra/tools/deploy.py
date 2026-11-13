"""Deploy / restore the retargeted projectile table.

  python tools/deploy.py 52          retarget to that explosion id and write it
  python tools/deploy.py --restore   put the stock file back
  python tools/deploy.py --status    show what is currently installed

Every write keeps a copy of whatever was there first, so a later game update is
handled by running --restore before deploying again (or just re-running deploy,
which re-backs-up the new stock).
"""
import shutil
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "vendor" / "ordnance"))

from nacl.bindings import crypto_box_seal_open  # noqa: E402

import hd2edit  # noqa: E402
from retarget import NAME, PK, SK, RECORD_ID, DIRECT_OFF, MAIN_OFF, RECORDS, STRIDE, build  # noqa: E402

GAME = Path(r"E:/SteamLibrary/steamapps/common/Helldivers 2/data/game")
TARGET = GAME / NAME
STOCK = Path(__file__).resolve().parent.parent / "stock"
BACKUP = STOCK / "generated_projectile_settings.deployed-from.dl_bin"


def digest(path):
    data = path.read_bytes()
    plain = crypto_box_seal_open(data, PK, SK)
    count = struct.unpack_from("<I", plain, 36)[0]
    for i in range(count):
        base = RECORDS + i * STRIDE
        if struct.unpack_from("<I", plain, base)[0] == RECORD_ID:
            return (len(data),
                    struct.unpack_from("<I", plain, base + DIRECT_OFF)[0],
                    struct.unpack_from("<I", plain, base + MAIN_OFF)[0],
                    hd2edit.murmur64(plain))
    return (len(data), None, None, None)


def status():
    if not TARGET.exists():
        print("target missing:", TARGET)
        return
    size, direct, main, h = digest(TARGET)
    print(f"{TARGET.name}: {size:,} bytes, record {RECORD_ID} direct={direct} main={main}")
    print(f"  plaintext murmur64 = {h:#018x}")
    print(f"  backup present: {BACKUP.exists()}")


def restore():
    if not BACKUP.exists():
        raise SystemExit("no backup to restore from")
    shutil.copy2(BACKUP, TARGET)
    print("restored from", BACKUP)
    status()


def deploy(effect_id):
    if not BACKUP.exists():
        shutil.copy2(TARGET, BACKUP)
        print("backed up current file ->", BACKUP)
    else:
        print("backup already present ->", BACKUP)
    built = build(effect_id)
    shutil.copy2(built, TARGET)
    print("installed ->", TARGET)
    status()


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "52"
    if arg in ("--restore", "-r"):
        restore()
    elif arg in ("--status", "-s"):
        status()
    else:
        deploy(int(arg))
