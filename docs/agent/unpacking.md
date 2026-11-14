# Helldivers 2 资源解包与 patch 重建指南

适用环境：本机 Steam build **26568081**，游戏目录 `E:/SteamLibrary/steamapps/common/Helldivers 2`。
代码在 `toolchain/hd2tool/`、`toolchain/common/`；工具链总览见 [toolchain.md](toolchain.md)。
本文所有结构、命令和结论都来自本机 `data/` 的实测，并与 Filediver 的实现交叉核对。

---

## 1. 结论先行

旧 `4 mine mixed` 及其衍生包 `Mines-Highlight` 在当前 build 不生效，但**不是因为资源被删或改名**：

| 检查 | 实测 | 判定 |
|---|---|---|
| boot 包名 | `bundle_database.data` 中仍有 `9ba626afa44a3aa3` 及 `.stream`；Filediver 列出 `packages/generated/loadout/contact_mine.package <- 9ba626afa44a3aa3` | 挂载点没变 |
| 四个地雷 unit | `contact_mine` / `gas_mine` / `incendiary_mine` / `tank_mine` 的 ID 在当前 build 一一存在 | 资源没被重命名 |
| 部署链 | `data/` 现有 46 个 `9ba626afa44a3aa3.patch_N`；`patch_0..3` 正是 `Mines-Highlight` 的四组 | Arsenal 部署正常，文件确实落地了 |
| patch 头字段 | 44 个可解析 patch 中 39 个的 `ApproxMainSize / ApproxGPUSize` 等于实际大小；四个地雷 patch 却沿用旧整包值 `200960 / 13958400` | **格式不一致；已修** |
| 资源表 GPU 偏移 | 早期拆分包的 `r4` 沿用原整包 14.7 MB 内的绝对偏移，而子包 `.gpu_resources` 只有 0.25–13.8 MB | **纹理读取全部越界，模型消失；已修** |
| GPU 切片间隙 | 原包四个 unit 的 GPU mesh 之间有 30 / 16 / 28 B 对齐间隙；按 `r9` 紧拼接会让毒气之后的三个 mesh 整体后移 30 B | **三个地雷读到错数据，投放时闪屏；已修** |
| 资源内容 | 旧包文件日期 2026-04-29；当前 build 的 `contact_mine.unit` 已能重新导出为 GLB | 旧 mesh / 贴图可能已过期 |
| EAC | 无法从磁盘文件判断 | 只能游戏内实测 |

因此正确路径不是"给旧包换个编号"，而是：**从当前 build 重新导出资源 → 按当前格式重建 patch → 再验证游戏是否放行**。

---

## 2. 三层容器格式

### 2.1 DSAR（磁盘归档）

`data/` 里的无扩展名文件和 `.nxa` 都是 DSAR，头部 32 字节：

| 偏移 | 类型 | 含义 |
|---|---|---|
| `0x00` | `4s` | magic `DSAR` |
| `0x04` | `4s` | 版本 / 标志字节（实测 `03 00 01 00`） |
| `0x08` | `u32` | chunk 数量 |
| `0x0C` | `u32` | 头部总长 $= 32 + 32 \times \text{chunk 数量}$ |
| `0x10` | `u64` | 解压后总大小 |
| `0x18` | `8s` | 填充标记 `PADDING*` |

从 `0x20` 起每 32 字节一条 chunk 表，结构 `<QQIIBB6s>`：

| 字段 | 含义 |
|---|---|
| `out_offset` / `file_offset` | 解压输出偏移 / 文件内偏移 |
| `usize` / `csize` | 解压大小 / 压缩大小 |
| `compression` | `0x00` 未压缩，`0x03` **LZ4 raw block** |
| `flags` | 资源标志 |
| 尾部 6 字节 | 保留 |

`0x03` 块是**裸 LZ4 block，没有 frame 头**，直接用 LZ4 frame 解码器会失败；`core.py::lz4_block` 是纯标准库实现。

本机盘点：30 个无扩展名 DSAR（索引 bundle，23,296 条资源记录 / 17,590 个不同 ID）、31 个 `.nxa`（资源主体，共 22.3 GB）、`bundles.nxa` 是 DSAA 索引。

### 2.2 Stingray bundle（`0xF0000011`）

DSAR 解压后就是 bundle；mod 的 `patch_N` 三件套用的是同一格式。头部 72 字节，结构 `<III20sQQ24s>`：

| 偏移 | 字段 | 说明 |
|---|---|---|
| `0x00` | `MagicNum` | `11 00 00 F0` |
| `0x04` / `0x08` | `NumTypes` / `NumFiles` | 类型数 / 资源数 |
| `0x0C` | `Unk00[20]` | 未知，重建时原样保留 |
| `0x20` | `ApproxMainSize` | 近似主存大小 |
| `0x28` | `ApproxGPUSize` | 近似 GPU 大小 |
| `0x30` | `Unk01[24]` | 未知，原样保留 |

头之后依次是：类型表（32 B/条，`<IIQIIII`）、资源表（80 B/条，`<7Q6I`）、主存数据区。类型表条目实测下标：

| 下标 | 含义 |
|---|---|
| 0 / 1 | 未知，官方归档里恒为 0 |
| 2 | 类型 hash |
| 3 | 该类型的资源条数 |
| 4 | 未知，恒为 0 |
| 5 / 6 | 单条长度 `16` / 合计 `16 × NumTypes` |

类型表整体前移 8 字节时归档仍读得进来，但资源行的类型在表里没有对应条目，游戏不会把它当 lua 执行。自造单 lua 补丁只用 `hd2tool.build_lua_bundle()`，校验用 `hd2tool.read_lua_bundle()` —— 两者在 72 字节头 + 1 条类型 + 1 行的布局上与官方归档逐字段对齐。

资源表中实测下标对应：

| 下标 | 含义 |
|---|---|
| 0 / 1 | 资源名 hash / 资源类型 hash |
| 2 / 3 / 4 | `Offsets[DataMain / DataStream / DataGPU]`，即主存 / stream / GPU 的文件内偏移 |
| 5 / 6 | `MainBufferOffset / GPUBufferOffset`（现代 patch 为 0） |
| 7 / 8 / 9 | `Sizes[DataMain / DataStream / DataGPU]` |
| 10 / 11 | 主存 / GPU 对齐值 |
| 12 | 资源序号 |

主存与 GPU 数据各按 `r10` / `r11` 对齐后拼接，起点记录在 `r2` / `r4`。**GPU 切片之间可能留有对齐间隙，必须按 `r4` 定位，不能按 `r9` 紧拼接**，否则后面的切片会整体错位。重建子包有两种做法：重排数据时重算 `r2` / `r4` 并把 `r3` / `r5` / `r6` 归零；或保留完整 GPU 缓冲区副本、原样保留 `r4` / `r5` / `r6`，只重算 `r2`。`.stream` 一般为空。

### 2.3 资源命名

资源名和类型名都是 **IDString64**：MurmurHash64A 变体，$\text{seed} = \text{len}(s) \times \text{MIX}$，其中 $\text{MIX} = \texttt{0xC6A4A7935BD1E995}$、右移 47 位。实现见 `core.py::idstring64`。

旧包里全是 hash，反查靠 `third_party/filediver/hashes/` 下的名表（`hashes.txt`、`cracked.txt`、`dl_type_names.txt` 等）。本机实测：索引 bundle 的 1416 条资源 100% 反查成功，全库名表覆盖率约 43.9%。

常用类型 hash：

| hash | 类型 |
|---|---|
| `cd4238c6a0c69e32` | `texture` |
| `eac0b497876adedf` | `material` |
| `18dead01056b72e9` | `bones` |
| `e0a48d0be9a7453f` | `unit` |
| `a14e8dfa2cd117e2` | `lua` |

---

## 3. 工具链用法

在 `toolchain/` 下运行，Python 3.10+，核心逻辑只用标准库。游戏目录默认为
`E:/SteamLibrary/steamapps/common/Helldivers 2`，可用 `--game-dir` 或环境变量 `HD2_GAME_DIR` 覆盖。

```
python -m hd2tool.cli selftest
python -m hd2tool.cli index
python -m hd2tool.cli diagnose
python -m hd2tool.cli inspect <patch_0 路径>
python -m hd2tool.cli resolve --patch <patch_0 路径>
python -m hd2tool.cli resolve ee89cc10d80b6afb 693028644d27ffef
python -m hd2tool.cli extract --dsar <data/xxx> --out out/xxx.bundle.bin
python -m hd2tool.cli extract --bundle <patch_0> --hash ee89cc10d80b6afb --out out/xxx/
python -m hd2tool.cli fixheader <patch_0 路径> [--out <新路径>]
python -m hd2tool.cli rebuild --patch <旧 patch> --groups <groups.json> --out <输出目录>
```

| 子命令 | 作用 |
|---|---|
| `selftest` | 校验哈希向量、DSAR/LZ4 解压、bundle 解析、名表反查、重建幂等 |
| `index` | 扫描 `data/`，输出 `out/game_index.json` |
| `diagnose` | 逐条比对旧 patch 的资源与当前 build，输出 `out/diagnose.json` |
| `inspect` | 打印资源表：序号、hash、类型名、主存/GPU 大小、反查到的名字 |
| `resolve` | 把 hash 反查为资源路径 |
| `extract` | 解压 DSAR，或按 hash 从 bundle 抽出主存与 GPU 原始数据 |
| `fixheader` | **重算 `ApproxMainSize / ApproxGPUSize` 为实际大小**，默认原地写回；给 `--out` 则另存完整三件套 |
| `rebuild` | 按 `groups.json` 把一张大资源表切成多个独立 `patch_0` 三件套，头字段自动按当前格式重算 |

`rebuild` 的约束：每组 `resource_indices` 必须升序、各组并集恰好覆盖全部索引且不重复；不同组不能包含同一 `(name, type)`，否则同时勾选时后者会整条覆盖前者。

---

## 4. 从当前 build 重新导出资源

`third_party/filediver-cli.exe` 是本机用 Go 从源码编译的 CLI（BSD-3-Clause）。它的名表同时被 `hd2tool` 复用。

```
cd toolchain/third_party

# 列出某类资源及其所属归档（格式：路径, 0x<name>.0x<type> <- 归档...）
./filediver-cli.exe --list -i "*contact_mine*" ^
  --gamedir "E:/SteamLibrary/steamapps/common/Helldivers 2"

# 导出为可用格式（unit → GLB，bones/package → JSON）
./filediver-cli.exe -i "*contact_mine*" ^
  --out "../out/filediver_extract" ^
  --gamedir "E:/SteamLibrary/steamapps/common/Helldivers 2"
```

本机实测第二条命令成功导出 4 个文件：

| 文件 | 大小 |
|---|---|
| `content/.../contact_mine/contact_mine.bones.json` | 4 KB |
| `content/.../contact_mine/contact_mine.physics.main` | 8 KB |
| `content/.../contact_mine/contact_mine.unit.glb` | 3,868 KB |
| `packages/generated/loadout/contact_mine.package.json` | 4 KB |

注意：`-i "*contact_mine*" -T texture` 匹配 0 条，因为该地雷的贴图不在同名路径下（其余三种有 `*_array` / `*_nac` 贴图）。要找贴图请用 `--list -i "*mine*"` 后按 `.texture` 过滤。

`--archives` 可限定归档，例如 `--archives 0x9ba626afa44a3aa3` 只取 boot 包。完整参数见 `./filediver-cli.exe --help`。

---

## 5. 四种地雷的精确映射

名表反查 + 已部署 patch 的实测对应关系如下（旧包资源表 0 基索引）。`Mines-Highlight` 重刷版已按此表把选项 key 与标签对齐：

| unit hash | 资源路径 | 地雷 | 旧包索引 | 早期 key | 现行 key | 旧部署编号 |
|---|---|---|---|---|---|---|
| `ee89cc10d80b6afb` | `.../contact_mine/contact_mine.unit` | 反步兵 MD-6 | 31, 34 | `at` | `contact` | `patch_2` |
| `693028644d27ffef` | `.../gas_mine/gas_mine.unit` | 毒气 MD-8 | 32 | `ap` | `gas` | `patch_0` |
| `e49a5ae078aad407` | `.../incendiary_mine/incendiary_mine.unit` | 燃烧 MD-I4 | 35 | `gas` | `incendiary` | `patch_3` |
| `032489cf1d439bba` | `.../tank_mine/tank_mine.unit` | 反坦克 MD-17 | 33 | `incendiary` | `tank` | `patch_1` |

早期版本的四个选项标签全部错位：`ap` 装的是毒气、`incendiary` 装的是反坦克、`at` 装的是反步兵、`gas` 装的是燃烧。这是按结构推断分组留下的错误，也是 `ap` 组独占 13.8 MB GPU 数据的原因——那批 4M 大贴图属于毒气地雷。重刷版已修正标签与头字段，并独立复核过每个选项包里的 unit hash 与标签一一对应。完整的当前 build 地雷资源清单见 `out/mine_resource_map.json`。

27 条 `texture` 的 name hash 不在名表里，但引用链可从数据本身解出：每条 unit 内嵌一个 material 的全 hash，每个 material 内嵌三张 texture 的全 hash。unit 的 `r5/r6` 还指向 GPU 缓冲区 700 KB–5 MB 的绝对位置，拆小 GPU 文件会让这些引用越界；因此 `Mines-Highlight` 改为每个选项自带完整的 14,779,098 B GPU 缓冲区，资源表只含共享资源 + 自己的 unit，`Include` 为单一目录。构建源固定为 `tools/source.zip` 内的原始三件套并用 SHA256 锁定，任何还原或重排都会破坏这些间隙。

---

## 6. 重建 patch 并接入 Arsenal

```
# 1. 修正已有 patch 的头字段（可选，rebuild 已自动做这一步）
python -m hd2tool.cli fixheader <旧 patch_0 路径> --out out/fixed/9ba626afa44a3aa3.patch_0

# 2. 按分组切出多个选项包
python -m hd2tool.cli rebuild \
  --patch "<旧 patch_2 路径>" \
  --groups "<groups.json 路径>" \
  --out out/options
```

`groups.json` 形如：

```json
{
  "groups": [
    {"key": "contact",     "resource_indices": [0, 1, 2, 31, 34]},
    {"key": "gas",         "resource_indices": [3, 4, 5, 32]},
    {"key": "incendiary",  "resource_indices": [6, 7, 8, 35]},
    {"key": "tank",        "resource_indices": [9, 10, 11, 33]}
  ]
}
```

每个 `key` 目录下会生成 `9ba626afa44a3aa3.patch_0`、`.gpu_resources`、`.stream` 三件套。

### Arsenal 包布局

ZIP 根目录**必须直接**是 `manifest.json` + 选项目录，不能套一层外层文件夹：

```
Mines-Highlight.zip
├─ manifest.json        Guid / Name / Description / IconPath / Options
├─ thumbnail.png
├─ README.md
├─ images/<key>.png
└─ options/<key>/9ba626afa44a3aa3.patch_0 (+ .gpu_resources / .stream)
```

每个 `Option` 用 `Include` 指向一个选项目录，可加 `Image` 预览图；高亮变体用 `SubOptions` 做互斥单选。`patch_0` 由 Arsenal 在部署时自动重编号为 `patch_N`，不需要手写编号。选项是**部署期开关**，勾选后要完全退出游戏再部署重启。

### 融入旧管理器的独立 mod

Helldivers2ModManager 的 mod 是 `<bundle>.patch_87` 这类固定编号三件套，配一个只有 `Guid/Name/Description` 的 manifest。转成 Arsenal 选项只改三处：文件名改 `patch_0`（编号由 Arsenal 分配）、`ApproxMainSize / ApproxGPUSize` 改为实际大小、补 `Options` 条目。**资源表、主存与 GPU 字节原样保留**，因为 `r4/r5/r6` 常是包内绝对引用。接入前先比对两包的 `(name, type)` 集合，零重叠才能作为独立选项共存；同一 boot bundle 挂多个 `patch_N` 是官方用法，旧管理器正是靠固定编号让多个 mod 并存。

需要改外观时可以进一步动资源，但要显式校验：`Mines-Highlight` 的机器人地雷选项新增了一个高亮材质资源、改了 unit 的材质映射（`name+type` 表尾的 value），并把光柱底部 346 个徽标三角形退化为零面积（索引置 0）。这类改动会改变资源表、主存偏移和 GPU 字节，不能按“只改头字段”处理；构建时必须单独核对材质数据、unit 材质映射、三角形数量与 `Approx` 字段。

`Mines-Highlight/tools/source_bot.zip` 是机器人地雷光柱的不可变源，`build_highlight.py` 只重写头字段并做 SHA256 锁定、引用链与冲突校验；在 `groups.json` 的 `standalone_options` 里加一条即可再接一种环境雷。

### unit 版本修复

unit 数据 `+0x2C` 是格式版本号，`+0x30 / +0x34` 是 LOD 段起止。版本低于当前游戏时，需要把版本号换成游戏的、并把 layout 表里 `format > 16` 的项 +4；不修会触发 Arsenal 的「旧版 unit」警告，模型也可能不加载。

`Mines-Highlight` 在构建时跑 `tools/repatch_units.py`：按 `groups.json` 的 `game_data`（或环境变量 `HD2_DATA`）索引当前游戏 unit，逐包修正并校验改动只落在 unit 区段、单包不超过 64 字节、GPU 与资源表不变。`tools/repatcher/` 是 [hd2-repatcher](https://github.com/RaidingForPants/hd2-repatcher)（MIT）的副本，其 `slim.py` 的 lz4 依赖已换成本地纯 Python 实现。

---

## 7. 验证清单

离线部分（`build.py` / `rebuild` 已自动执行）：

- [ ] 分组并集恰好覆盖全部资源索引，无重复、无遗漏
- [ ] 逐资源主存与 GPU 数据的 SHA256 与源包一致
- [ ] 跨选项无 `(name, type)` 冲突
- [ ] 每个 `patch_0` 的 `ApproxMainSize == 文件长度`、`ApproxGPUSize == .gpu_resources 长度`
- [ ] 每条资源 `r2 + r7 <= 主存长度`、`r4 + r9 <= GPU 长度`，且 `r2` / `r4` 满足各自对齐值；`r3` 为 0，保留 `r5` / `r6` 时必须落在 GPU 文件范围内
- [ ] 只做格式转换的独立选项，patch 除 `0x20/0x28` 外与源逐字节一致，GPU / stream 一致，且与主源 `(name, type)` 零重叠；有外观改动的选项按各自的构建校验逐项检查
- [ ] 每个 unit 的 `+0x2C` 版本号等于当前游戏数据里的值，且修复改动只落在 unit 区段
- [ ] 类型表的 `Count` 与该类型实际资源数一致
- [ ] ZIP 根目录布局正确，`manifest.json` 的 `Include` 路径都存在

游戏内部分（由你完成）：

- [ ] 停用旧 `4 mine mixed`，导入新 ZIP，勾选后部署并重启
- [ ] 逐个选项确认实际对应哪种地雷，把结果写回 `groups.json`
- [ ] 确认带 GPU 资源的 patch 是否被 EAC 放行

---

## 8. 已知限制

- `.nxa` 主体 22.3 GB 目前只解析了头部与 chunk 表，未做全量解压；按需提取请用 Filediver。
- 名表覆盖率约 43.9%，27 条 texture 的名字仍是未知 hash。
- `ApproxMainSize / ApproxGPUSize` 在 Filediver 源码里标注为「aligned by 256 / weirdly offset」，语义并非严格定义；当前 build 的绝大多数 patch 填的是精确实际大小，本工具链据此对齐，但**这不能证明它就是失效的唯一原因**。
- EAC 是否拒绝带 GPU 资源的 patch，磁盘文件无法判断。若仍异常，先用 `tools/source.zip` 重建并逐资源比对四个 unit 的 GPU mesh 哈希，再从当前 build 重新导出 mesh / 贴图。
- 工具链只读游戏目录，不写入、不启动游戏、不自行部署 mod。`out/` 全是本地副本，可随时删除。
