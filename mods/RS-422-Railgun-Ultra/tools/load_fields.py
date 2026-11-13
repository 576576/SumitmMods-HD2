"""Pull HD2Runtime's offline field dictionary (`apiFieldConstant` -> backing offset)
and cross-check every offset it claims against the *current* build's dl_bin rows.

HD2Runtime itself is a pre-7.2 runtime and will not load today; only its bundled
typelib-derived snapshot is used here, as a hypothesis to be verified.
"""
import json
from collections import Counter
from pathlib import Path

from lupa import LuaRuntime

SNAP = Path(r"C:/Users/16695/AppData/Local/hd2arsenal/mods/"
            r"HD2Runtime-0.40.0-runtime_AR464667/runtime/cd3364bf31d40fc2.lua")
OUT = Path(__file__).resolve().parent.parent / "unsealed"

SCRIPT = r"""
local path = ...
local f = assert(loadfile(path))
local t = f()
local out = {}
local seen = 0
local function walk(node, p)
  if type(node) ~= "table" then return end
  seen = seen + 1
  if seen > 400000 then return end
  if node.backing ~= nil then
    local b = node.backing
    out[#out+1] = {
      offset = b.offset, recordType = b.recordType, row = b.row,
      kind = b.kind, branch = b.branch,
      api = node.apiFieldConstant, accepted = node.acceptedForWrites,
      path = p,
    }
    return
  end
  for k, v in pairs(node) do
    walk(v, p .. "/" .. tostring(k))
  end
end
walk(t, "")
return #out, out
"""


def main():
    lua = LuaRuntime(unpack_returned_tuples=True)
    n, out = lua.execute(SCRIPT, str(SNAP))
    print(f"entries with backing: {n}")

    recs = []
    for i in range(1, len(out) + 1):
        e = out[i]
        recs.append({
            "offset": int(e["offset"]) if e["offset"] is not None else None,
            "recordType": str(e["recordType"]),
            "row": str(e["row"]),
            "kind": str(e["kind"]),
            "branch": str(e["branch"]),
            "api": str(e["api"]),
            "accepted": str(e["accepted"]),
            "path": str(e["path"]),
        })

    print("recordType:", Counter(r["recordType"] for r in recs).most_common(8))
    print("kind:", Counter(r["kind"] for r in recs).most_common(6))

    OUT.mkdir(exist_ok=True)
    (OUT / "hd2_fields.json").write_text(json.dumps(recs, indent=1), encoding="utf-8")
    print("written:", OUT / "hd2_fields.json")

    print("\nentries whose recordType is 133 (projectile settings candidate):")
    hits = [r for r in recs if r["recordType"] == "133"]
    print(f"  {len(hits)} entries")
    for r in hits[:25]:
        print(f"   {r['api']:<58} offset={r['offset']:<4} row={r['row']:<4} kind={r['kind']}")


if __name__ == "__main__":
    main()
