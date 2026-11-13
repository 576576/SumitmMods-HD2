# 共享库（`toolchain/common/`）

各 mod 的构建脚本共用的代码。**不复制到各 mod 目录**，一律 `sys.path` 指过来用：

```python
ROOT = Path(__file__).resolve().parent          # mods/<Mod>/tools
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO / "toolchain" / "common"))
import paths
import render_tiles as rt      # 需要渲染时
```

## 文件

| 文件 | 职责 | 主要入口 |
|---|---|---|
| `paths.py` | 仓库内所有路径的唯一来源 | `REPO` / `TOOLCHAIN` / `COMMON` / `MODS` / `DIST` / `DOCS`、`RAWDUMP` / `UNITCHECK` / `FILEDIVER` / `HASHES`、`mod(name)` |
| `render_tiles.py` | 把 unit 的 main+GPU 渲成透明底 3D 图（地雷图标用） | `render(main, gpu, tint)`、`dump_units()`、`unit_from_patch()`、CLI |
| `bug_patch.py` | 终结族地雷（虫雷）选项：本体高亮材质 + 光柱 | `build()`、`verify()`、`verify_unit()`、`inject()` |
| `mine_patch.py` | 战备地雷（热熔 / 诱饵 / 铁蒺藜）选项：高亮外壳 + 光柱 | `build()`、`verify()`、`verify_unit()` |

## 约定

- **新加路径只改 `paths.py`**。脚本里不要再出现 `"../common"`、`parents[2]` 这类相对层级，
  换目录结构时只有这一个地方要跟着动。
- 新加一个 mod 就是在 `mods/` 下建目录，代码里用 `paths.mod("<目录名>")` 引用，不需要登记。
- `render_tiles.py` 依赖 numpy + Pillow，并且要 `toolchain/rawdump.exe` 与游戏目录；
  它是**一次性素材工具**，不在构建流程里，产物落到 `Mines-Highlight/images/model_<key>.png`。
- `bug_patch.py` / `mine_patch.py` 里的补丁数据（`options/`、`tools/materials/`、`source*.zip`）
  都属于 `Mines-Highlight`，它们只是被 `build.py` 调用的实现库；换成别的 mod 用要自己带数据。
- `bug_patch.py::idstring64` 与 `hd2tool/core.py` 的实现是同一套 MurmurHash64A，两边结果必须一致。

## 改完怎么验

```bash
python toolchain/release.py build mines     # build.py 内部会跑 verify / verify_unit
```

`verify_unit()` 逐条比对改写前后的 unit：布局、mesh 列表、LOD 组、索引范围，任何一处写错
都会直接报出来。不要只用「游戏能启动」当作通过。
