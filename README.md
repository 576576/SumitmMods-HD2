# SumitmMods HD2

Helldivers 2 的 mod 集合，单一仓库。共享的解析、渲染与发版工具都在 `toolchain/`，各 mod 只保留自己的数据与构建脚本。

## 项目

| 目录 | 说明 | 产物 |
|---|---|---|
| `mods/Mines-Highlight/` | 地雷高亮 | `dist/Mines-Highlight.zip` |
| `mods/Sentry-Ammo-Display/` | 炮塔阵地弹药显示（MDL3） | `dist/Sentry-Ammo-Display.zip` |
| `mods/Armor-Multi-Passive/` | 护甲多被动（MDL3 版，自选 32 项额外被动） | `dist/Armor-Multi-Passive.zip` |
| `mods/RS-422-Railgun-Ultra/` | RS-422 Railgun Ultra（磁轨炮命中爆炸） | `dist/RS-422-Railgun-Ultra.zip` |

玩法、安装与选项写在各自目录的 `README.md`。

## 目录

```
toolchain/    解包工具、共享库、发版脚本与资源名表（含预编译 exe）
mods/         各 mod 的源码、素材与构建脚本
dist/         构建产物（不入库）
docs/agent/   给开发 / agent 看的说明
```

## 构建

```
python toolchain/release.py list
python toolchain/release.py build mines
python toolchain/release.py build sentry
python toolchain/release.py build armor
python toolchain/release.py build rs422
```

升版：`python toolchain/release.py bump <项目> patch|minor|major|x.y.z [--build]`。

版本号只写在各 mod 的 `version.txt`，清单里的显示名由各自的构建脚本拼装。

## 文档

- 各 mod 的玩法与安装：`mods/<Mod>/README.md`
- 开发与工具链：[`docs/agent/`](docs/agent/README.md) —— 工具链总览、解包指南、网络对象与 unit 的对应、共享库、发版流程

构建只依赖 Python 标准库与 Pillow；素材重建（模型渲染、战备图标导出）额外需要
`toolchain/` 下的三个 exe 和游戏目录，用法见 [`docs/agent/toolchain.md`](docs/agent/toolchain.md)。
