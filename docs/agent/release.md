# 发版脚本（`toolchain/release.py`）

四个 mod 的升版、构建、校验都走这一个入口。产物统一落在仓库根 `dist/`（不入库）。

```bash
python toolchain/release.py list                     # 版本、产物、SHA256、清单显示名
python toolchain/release.py build mines              # 重建 + 报告
python toolchain/release.py build armor              # 护甲多被动（内含 tools/build_patch.py）
python toolchain/release.py build rs422 --full       # 连预处理一起跑（重烘字节补丁表，约 3 分钟）
python toolchain/release.py bump sentry patch        # 只升号
python toolchain/release.py bump mines 1.0.2 --build # 升到指定版本并重建
```

## 项目表

`PROJECTS` 是唯一的登记处：目录、显示标签、预处理步骤、构建步骤、产物文件名。

| key | 目录 | 显示名 | 预处理（`--full`） | 构建步骤 | 产物 |
|---|---|---|---|---|---|
| `mines` | `mods/Mines-Highlight` | Mines Highlight | — | `build.py` | `Mines-Highlight.zip` |
| `sentry` | `mods/Sentry-Ammo-Display` | 炮塔阵地弹药显示 MDL3版 | — | `build.py` | `Sentry-Ammo-Display.zip` |
| `armor` | `mods/Armor-Multi-Passive` | 护甲多被动 MDL3版 | — | `build.py`（内含 `tools/build_patch.py`） | `Armor-Multi-Passive.zip` |
| `rs422` | `mods/RS-422-Railgun-Ultra` | RS-422 Railgun Ultra | `tools/bake_lua_patch.py` | `tools/build_lua_mod.py`、`tools/test_packages.py` | `RS-422-Railgun-Ultra.zip` |

构建步骤以 mod 目录为 cwd 依次执行，任何一步非零退出就整体失败。

## 不变式

- **版本号只写在各自 mod 的 `version.txt`**（`x.y.z`，不带 `v`）。脚本不碰清单里的显示名，
  显示名由各 mod 的构建脚本拼 `BASE_NAME + " " + VERSION` 写回 `manifest.json`，
  所以改名字改 `BASE_NAME`，别手改 `manifest.json` 的 `Name`。
- **`Guid` 永不改**。改了管理器就当成新 mod，玩家的部署与选项勾选全丢。
- **内容没有实质变化就不发版**。游戏更新但资源 id、unit 版本没动时，旧包继续有效；
  升版本号只会让玩家白下一次 21 MB。只有「验证已过且需要告示玩家」时才升 patch 位。
- `list` 会按「产物里有版本号，且与 `version.txt` 一致」判断状态，显示 `显示名未同步`
  就说明构建脚本没跑或 `BASE_NAME` 拼错了。

## 加一个新 mod

1. 建 `mods/<Mod>/`，写 `version.txt`、`manifest.json`（带 `Guid`）、`README.md`、
   `thumbnail.png` 和构建脚本；路径一律从 `toolchain/common/paths.py` 取。
2. 在 `PROJECTS` 里加一条（`dir` / `label` / `build` / `artifacts`，需要预处理就加 `full`）。
3. `python toolchain/release.py build <新 key>` 跑通，再在根 `README.md` 的项目表补一行。

## 提交纪律

本地验证通过就可以 `git commit`；**`git push` 要等明确指示**。产物、`build/`、`out/`、
`.workbuddy/` 都不入库。
