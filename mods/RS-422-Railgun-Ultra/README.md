# RS-422 Railgun Ultra

给游戏自带的 RS-422 Railgun 换一种命中爆炸。四种轨道打击做在同一个 mod 的四个选项里，
勾一个即可。

不加载 FFI、不读内存、不改可执行文件。改的是 `data/game/generated_projectile_settings.dl_bin`
里 186 号弹丸的两个爆炸引用，用 Arsenal 正常导入部署即可。

## 选项

| 选项 | 命中爆炸 |
| --- | --- |
| 轨道精准 / Orbital Precision Strike | 单发高爆，落点集中 |
| 轨道毒气 / Orbital Gas Strike | 命中点留下持续毒雾 |
| 轨道炮 / Orbital Railcannon Strike | 单体高伤的贯穿爆炸 |
| 轨道电磁 / Orbital EMS Strike | 命中点形成减速电磁场，伤害很低 |

**四个选项只勾一个。** 改的是同一处数据，同时勾选时最后写入的那个生效。

换选项不用还原：直接在管理器里改勾选、重新部署，启动一次游戏就切过去了。

## 安装

1. Arsenal 导入 `RS-422-Railgun-Ultra.zip`，勾选一个选项，部署。
2. 启动一次游戏。mod 会在启动时改写 `data/game/generated_projectile_settings.dl_bin`。
3. 退出再进，改动生效。

第二步不能省：游戏在启动早期就加载数据表，所以写入发生在本次启动之后。

日志写在游戏根目录 `RS422Retarget.log`。如果游戏更新了数据表，mod 会检测到并跳过（不会写坏文件）。

从旧版升级：旧版是四个独立的包（`RS422改-<弹种>`，各自 Guid），管理器不会把它们当成这个 mod
的旧版本，请先手动删掉。

## 原理

`generated_*.dl_bin` 是 libsodium `crypto_box_seal` 密封的：`epk(32) | tag(16) | cipher`，
nonce 是 `BLAKE2b-24(epk ‖ 接收方公钥)`。

弹丸记录里两个字段决定命中表现：`record+60`（直击）和 `record+144`（主爆炸），
都存爆炸表的记录号。改动会破坏整份明文的 MurmurHash64A，所以离线把「改字段 + 修哈希 + 重算 tag」
算成目标密文字节，烘进 Lua。运行时 Lua 只做一件事：把那 29 个字节写回文件。

每个已知状态（原版 + 四个选项）都留了签名，Lua 先认状态再写：认得出就直接改成目标字节，
认不出就跳过。所以换选项、来回换、重复启动都不需要还原。

## 目录

```
dist/            可导入的 zip
assets/glyphs/   战备图标原稿（选项图的来源）
images/          四个选项图与封面用的武器渲染图，构建时生成
tools/           构建与校验脚本
stock/           原版 dl_bin 备份
vendor/          Ordnance 的 hd2edit.py，dl_bin 密封表的读写原语（Apache-2.0）
```

重建：

```
python tools/render_model.py      # 可选：重渲封面的武器图（需要游戏目录）
python tools/bake_lua_patch.py    # 目标字节 + 状态签名（约 3 分钟）
python tools/build_lua_mod.py     # 出图 + 打单个 zip
python tools/test_packages.py     # 端到端：真跑 Lua、解封校验、切换、拒写
```
