# 工具链（`toolchain/`）

面向本机 Steam build **26568081** 的 Helldivers 2 资源解包、反查、诊断与 patch 重建工具。
所有解析逻辑基于本机 `data/` 实测，并与 Filediver 的实现交叉核对过。

**这份文档是给 agent 看的工作说明**：改哪个文件、跑什么命令、哪些结论已经踩实。
容器格式细节、命令逐条解释、重建流程与验证清单在 [unpacking.md](unpacking.md)；
共享库（unit 解析 / 补丁重建 / 渲染器）在 [libs.md](libs.md)；发版脚本在 [release.md](release.md)；
运行时网络对象与 unit 的对应（类型/字段哈希、崩溃红线、诊断日志）在 [network-identity.md](network-identity.md)。

## 目录

```
toolchain/
├─ hd2tool/
│  ├─ core.py       IDString64 / LZ4 block / DSAR / Stingray bundle / patch 重建 / 单 lua 归档 / 名表反查
│  ├─ cli.py        命令行入口
│  └─ __init__.py
├─ common/          共享库：paths / render_tiles / bug_patch / mine_patch（见 libs.md）
├─ tools/
│  └─ update_hashes.py      按社区来源重新合并资源名表
├─ third_party/
│  ├─ filediver/hashes/     资源名表（hashes.txt 及其合并来源）
│  ├─ hd2-repatcher/        第三方 patch 重打包工具（MIT）
│  └─ filediver-cli.exe     本机 Go 编译的 Filediver CLI
├─ rawdump.exe              本机 Go 编译，导出原始资源字节
├─ unitcheck.exe            本机 Go 编译，校验 unit main + GPU
├─ release.py               四个 mod 的升版 / 构建 / 校验入口
└─ out/                     运行产物：索引、诊断、提取结果（不入库）
```

三个 `.exe` 随仓库提供（共约 40 MB），克隆下来即可直接用，缺失时各 mod 的素材脚本会明确报错
并指出缺哪个文件。重新编译需要 Filediver 源码（`go build -o ../../rawdump.exe ./cmd/rawdump`，
`unitcheck` 同理），源码不入库。

## 快速开始

在 `toolchain/` 目录下运行（Python 3.10+，核心逻辑只用标准库）：

```
python -m hd2tool.cli selftest     自检：哈希向量、LZ4、DSAR、bundle、名表、重建幂等
python -m hd2tool.cli index        扫描游戏 data，生成 out/game_index.json
python -m hd2tool.cli diagnose     诊断旧 patch 在当前 build 的资源匹配情况
python -m hd2tool.cli inspect <patch_0 路径>
python -m hd2tool.cli resolve --patch <patch_0 路径>
python -m hd2tool.cli extract --dsar <data/xxx> --out out/xxx.bin
python -m hd2tool.cli extract --bundle <patch_0> --hash <16 位 hex> --out out/xxx/
python -m hd2tool.cli fixheader <patch_0 路径> [--out 新路径]
python -m hd2tool.cli rebuild --patch <旧 patch> --groups <groups.json> --out out/options
```

游戏目录默认 `E:/SteamLibrary/steamapps/common/Helldivers 2`，可用 `--game-dir` 或环境变量
`HD2_GAME_DIR` 覆盖。

导出与校验用另两个本机编译的小工具（同样在 `toolchain/` 下运行）：

```
rawdump.exe <游戏目录> <输出目录> <类型 hash> <资源名>...   原样导出 main/GPU/stream
unitcheck.exe <unit main> <unit gpu>                      用 Filediver 解析器校验结构自洽
```

`rawdump` 解决的是 Filediver 官方 CLI 只导出 `.glb` 的问题：改 mod 需要资源的原始字节，
反编译产物回不去。`unitcheck` 用于改写 unit 之后的回归——布局、网格组、索引范围任何一处写错
都会被解析器直接报出来。

`fixheader` 把 `ApproxMainSize / ApproxGPUSize` 重算为实际大小；`rebuild` 已内置这一步。

## 资源名表

`third_party/filediver/hashes/` 是合并后的名表（`hashes.txt`、`cracked.txt`、
`dl_type_names.txt` 等）。更新用：

```
python tools/update_hashes.py
```

它从 5 个社区来源拉取后合并，**带防缩水保护**：新结果条数低于现有 90% 时拒绝写入，并留
`hashes.txt.bak`。拉取用 `curl` 而不是 urllib —— 本机 urllib 不走系统代理，直连会握手失败；
必要时命令里带 `--noproxy '*'`。

名表覆盖不到的资源（例如 Urban Legends 债券里的战备图标）不要靠猜：拿本地化 key 反推资源名，
自己用 `core.py::idstring64` 算 hash，再按 `0x%016x*` 从归档里按 hash 提取。

## 当前实测结论

- DSAR 是 DirectStorage 风格的分块容器，`0x03` = LZ4 raw block、`0x00` = 未压缩块；纯 Python 已能完整解压。
- 30 个无扩展名 DSAR 是索引 bundle（23,296 条资源记录 / 17,590 个不同 ID）；资源主体在 31 个 `.nxa`，共 22.3 GB。
- boot 包名 `9ba626afa44a3aa3` 与四个地雷 unit 的资源 ID 在当前 build **都存在**，资源没有被删除或重命名。
- `data/` 现有 46 个 `patch_N`，其中 `patch_0..3` 正是 `Mines-Highlight` 的四组——部署链正常，文件确实落地了却不生效。
- 44 个可解析 patch 中 39 个的 `ApproxMainSize / ApproxGPUSize` 填实际大小；四个地雷 patch 沿用旧整包值 `200960 / 13958400`，与自身数据严重不符。工具链与重刷版 `Mines-Highlight` 均已修正。
- 四种地雷的 unit hash 已反查为 `contact_mine` / `gas_mine` / `incendiary_mine` / `tank_mine`；据此发现早期版本 `Mines-Highlight` 的四个选项标签全部错位（重刷版已修正）。
- Filediver 已从当前 build 成功导出 `contact_mine.unit.glb`（3.8 MB），"重新解包再重做"的路径可用。
- EAC 是否放行带 GPU 资源的 patch 仍需游戏内实测。详见 [unpacking.md](unpacking.md) 的「结论先行」与「已知限制」。

以上都是**磁盘侧**（容器、资源、patch 头）的结论，绑定到本机 build。运行时读字段、
认网络对象那部分不在这里，见 [network-identity.md](network-identity.md)。

## 产物

`out/` 全部是本地副本，不入库、可随时删。

| 文件 | 内容 |
|---|---|
| `out/game_index.json` | 30 个索引 DSAR、31 个 NXA、46 个已部署 patch 的清单 |
| `out/diagnose.json` | 旧 patch 36 条资源的逐条反查与匹配结果 |
| `out/mine_resource_map.json` | 四种地雷 unit 的 hash → 路径 → 旧包索引映射 |
| `out/filediver_mine_list.txt` | Filediver 列出的当前 build 地雷相关资源 |
| `out/filediver_extract/` | 当前 build 导出的 `contact_mine` bones / physics / unit.glb / package |
| `out/extracted_contact_mine/` | 按 hash 从旧包提取的示例（bones、unit、GPU 数据） |
| `out/02582f3da1f8daf5.bundle.bin` | 一个索引 DSAR 完整解压后的 bundle |

## 依赖与许可

- Python 标准库即可运行核心工具链；`common/render_tiles.py` 额外要 numpy + Pillow。
- `third_party/filediver/hashes/` 为 Filediver 的资源名表（BSD-3-Clause 项目产出）；
  `filediver-cli.exe` 由本机 Go 从 Filediver 源码编译，源码不入库。
- 名表来源见 `filediver/hashes/hashes.txt` 头部说明（Hellextractor、hd2-name-db、Helldivers2-Hashlist 等）。

## 边界

工具链只读取游戏目录，不写入、不启动游戏、不自行部署 mod。`.nxa` 主体（22.3 GB）目前只解析了
头部与 chunk 表，未做全量解压；按需提取需要配合 Filediver。27 条 texture 的 name hash 不在名表中，
贴图归属仍需游戏内实测校正。
