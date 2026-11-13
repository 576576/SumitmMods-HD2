# agent 文档

给接手这个仓库的 agent / 开发看的说明。面向玩家的安装与玩法写在根 `README.md` 和各
`mods/<Mod>/README.md`，这里写的是「代码在哪、怎么跑、哪些结论已经踩实」。

## 仓库

单一仓库多 mod，共享代码集中在 `toolchain/`：

```
mods/        各个 mod 的素材与构建脚本（各自有 README、version.txt、manifest.json）
toolchain/   hd2tool 解包 CLI、common 共享库、release.py、预编译 exe、资源名表
dist/        构建产物，不入库
docs/agent/  这份文档
```

| mod | 做什么 | 构建 |
|---|---|---|
| `mods/Mines-Highlight` | 地雷高亮（9 个选项：4 种地雷 + 3 种战备雷 + 机器人/虫雷） | `python toolchain/release.py build mines` |
| `mods/Sentry-Ammo-Display` | 炮塔与阵地显示弹药条、2D 战备图标 | `python toolchain/release.py build sentry` |
| `mods/RS-422-Railgun-Ultra` | RS-422 磁轨炮命中爆炸换成四种轨道打击 | `python toolchain/release.py build rs422` |

## 文档

| 文件 | 内容 |
|---|---|
| [toolchain.md](toolchain.md) | 工具链总览：hd2tool 命令、三个 exe、名表更新、实测结论 |
| [unpacking.md](unpacking.md) | 解包指南：DSAR / Stingray bundle 格式、patch 重建、验证清单、已知限制 |
| [libs.md](libs.md) | `toolchain/common/` 四个共享库的职责与约定 |
| [release.md](release.md) | `release.py` 的项目表、不变式、加新 mod 的步骤 |

## 环境

- Windows；Python 3.10+（本机 3.14）。依赖 Pillow、numpy；跑 RS-422 的端到端测试还要
  `lupa`（真跑 Lua）与 `PyNaCl`。
- 游戏目录默认 `E:/SteamLibrary/steamapps/common/Helldivers 2`，可用 `--game-dir` 或
  `HD2_GAME_DIR` 覆盖。
- 资源 hash、unit 布局、patch 头字段这些结论都基于本机 build **26568081**。换 build 时先跑
  `python -m hd2tool.cli diagnose`（cwd 在 `toolchain/`），别直接沿用旧判断。

## 硬规矩

- **不许猜资源**：位置、hash、图标、字段含义都要有来源（游戏文件反查、骨骼矩阵、名表、本地化
  文本）。找不到就去找来源，不要用「看起来差不多」的资源顶上。
- **改 unit / patch 之后必须跑校验**，`verify_unit()` 与 `test_packages.py` 是底线；
  「游戏能启动」不算通过。
- **注释只写「是什么」和「不这么做会怎么错」**，不写决策过程、试错史、issue 编号。
- 产物、`build/`、`out/`、`.workbuddy/` 不入库；`Guid` 与已推历史不动。
- 本地验证通过即可 `git commit`；`git push` 等明确指示。
