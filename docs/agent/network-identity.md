# 网络对象与 unit 身份（Sentry 的经验）

`mods/Sentry-Ammo-Display` 要在游戏里把「一个 unit」和「一个网络对象」对上，才能读到
`rounds_remaining`。这一步没有官方接口，全是推断。下面是踩实过的结论和必须遵守的限制。

## 1. 先分清两个身份

| | unit | 网络对象 |
|---|---|---|
| 来源 | `Unit` / 资源名 | `GameSession` 的 goid |
| 用途 | 位置、模型、渲染 | 字段读写、类型判定 |

`UnitSynchronizer.unit_to_game_object_id` 是官方给出的映射，但**很多 unit 根本没有**。
补给车车体长期是 `SENTRY-NETWORK unit_synchronizer: returned nil`，所以：

- `SENTRY-IDENTITY unbound model=… no_network_objects` **是常态，不是故障**；
- 身份只能退化为「unit 自身坐标 vs 网络对象坐标」的核对，属于推断，
  `identity_basis=static_type_and_unique_vector_match`，不要在文档或 UI 里说成原生映射。

goid 合法区间 `0..2147483647`，**32767 是无效值**，见到就丢。

## 2. 类型 id 与字段名都是 32 位哈希

- 资源/类型 id = `MurmurHash64A(名字, seed=0)`，引擎**只比较高 32 位**。所以可以自造一个
  名字，使其高 32 位撞到目标值，就能当类型名或字段名用：
  `build/_scan/gen/find_many.exe <hash...>` 生成。
- `Network.object_info()` 对自造类型名取不到 schema（返回的 `fields` 为空），
  但 `game_object_is_type` / `game_object_field` 都按 hash 认。
- **`game_object_field` 读该类型没有声明的字段不是安全失败，是直接崩进程。**
  舰桥对象密集，最容易在那里崩。规则只有一条：**只读 schema 里确实存在的字段**
  （位置按 `position_slot`，弹药按 types 表里的 `rounds_slot`），永远不要「拿名字挨个试」。

### network_config 记录布局

```
record + 0            28 字节头：hash | len | schema | 0 | 0 | nf
record + 28 + 4*nf    字段计数（u32）
record + 32 + 4*nf    字段表基址，往上是 nf 个字段 hash
```

**基址是 `+32`，`+28` 处是计数。** 从 `+28` 按 nf 项取会拿到「计数 + nf-1 个字段」，
永远少最后一个。这个错的表现是 `schema()` 在 `index=nf+1` 判形状不符，随后
`discovery_complete` 恒假，**所有型号一起没弹药条**，看起来像整个模组失效。

常用字段：

| 字段 | 高 32 位 | 说明 |
|---|---|---|
| `rounds_remaining` | `d7a5d63e` | 弹药 |
| `c{i(uTh>` | `9a9b14c4` | 位置（高精度 Vector3，语义未验证） |
| `temperature` | — | 激光哨戒 |
| `magazines_remaining` | `ec64918b` | 诊断用 |
| `chamber_empty` | `4a893e74` | 诊断用 |

## 3. 位置匹配分四档

| 场景 | 判据 | 容差 | 例子 |
|---|---|---|---|
| 普通炮塔 | unit 原点 ≈ 对象坐标 | 1 mm | 大部分 hellpod 炮塔 |
| 结构类 | 原点与对象本来错开 | 2 m | 补给舱（差半米） |
| 车载 | 对象坐标固定在落点、**不跟车** | **只比水平 8 m** | 补给车 `6d745627` |
| 无位置字段 | 全图该类型唯一对象 | — | 榴弹兵墙发射器 `17db8107` |

车载为什么只比水平：对象坐标在落点生成后不再跟随载具，实测水平差 0.86–3.5 m，
而 z 差 6.5–22.5 m 且**不随时间变化**（不是跟随延迟）。全三维永远配不上。

放宽只能按**模型**，不能按类型：`bind_all` 会把同一个类型克隆到别的模型上，
按类型放宽会让它们一起去抢同一个对象。

无位置字段的类型还要处理「多台同型号」：已配对的单位先钉住自己的对象，剩余对象
唯一时认领，未认领单位数与未占用对象数相等时按 goid 顺序配对。否则第二台落下后
两边都不唯一，整组一起失效。

## 4. 三道防御：一处错不能让全局失效

这一条已经复发三次，每次表现都是「全部型号一起没弹药条」。

1. **单条记录失败只丢自己。** 快照采集逐条检查，坏记录返回 `nil` 加原因，
   **不要整包 reject**。重复 goid 曾触发 `snapshot_duplicate_source_identity`，
   一处配对错就让 `records` 恒为 0。
2. **单个类型出错只停用该类型。** `schema()` 失败打 `SENTRY-SCHEMA unusable type=…`，
   把该类型从候选里摘掉，**不要写进 `candidate_problem`**。
3. **身份配对必须双向唯一**（`unit_count==1 and id_count==1`），且已建立的配对要能
   **沿用到下一帧**：记录只活一帧就等于没有。

## 5. 两个硬限制

- **Lua 5.1 单函数最多 60 个 upvalue**，超了直接编译失败。`build.py` 用 lupa 的
  **lua51** `loadstring` 检查；lupa 默认运行时是 5.4，测不出这个错。新增模块级
  local 要么合成一张表，要么放进真正用到它的函数里。
- **`profile` 是与 MDL3 共享的表，不要往上加新键。** 它是 MDL3 传进来的配置表，
  往里面塞绘制用的常量没有依据，且怀疑与设置面板闪退有关（未证实）。
  绘制模块自己的常量就在绘制模块里定义。

## 6. 诊断日志

| 前缀 | 内容 |
|---|---|
| `SENTRY-CENSUS` | 每个型号的 unit 数，计数变化时重打；`accessory` 表示已知附属部件 |
| `SENTRY-TYPE` | 现场对象 goid / 类型 / 坐标，用于和 unit 坐标对撞 |
| `SENTRY-IDENTITY` | `unbound`（最近类型与距离）、`unresolved`（卡在哪一步）、`vehicle`（车载配对结果） |
| `SENTRY-NETWORK` | 每个类型每 basis 成功采到一次就记一条，失败不记 |
| `SENTRY-DRAW` | 每个型号每种结果 10 秒一次：`shown` / `drop_record` / `drop_unit` / `drop_behind` / `drop_far` / `drop_screen` |
| `SENTRY-AMMO sample` | 每 200 次采样（约 10 秒）列出每个目标的 `model:ok` 或 `model:<原因>` |

`SENTRY-DRAW` 只有记录进了绘制列表才会出现。**某种型号完全没有 DRAW 行，说明它没进
快照，不是画不出来** —— 往上找 `SENTRY-AMMO sample`。

## 7. 图标

- 图集是 512×512 的 4×4 格，`u0 = col/4 + 1/512`。
- 材质 `icons_depth` 采样的就是纹理 `icons_atlas`，所以图集除了图标格，**必须保留
  (0,3) 四角括号（`frame_uv`）与 (3,3) 纯白（`bar_uv`）**；只画图标会让弹药条与外框
  全部透明。
- 取图标先按网络类型，取不到再按 unit 模型兜底（`model_icons`）。同一模型挂多个类型、
  其中只有一个类型有图标的情况很常见。
- 战备图标不一定在名表里。取不到时从本地化 `stratagems_gb.xaml` 的 `nui_stratagem_*`
  反推 `icon_strat_hud_*` 名字，自己算 hash 再按 `0x%016x*` 从归档提取。

## 8. 已知未解

- **补给车落地后采样中断**：1.5.36 实测，车体识别成功（`type=6d745627 pick=475` 稳定），
  字段在下落中采到过一次，落地后 `records` 里就没有它了。
  `network.sample` 失败时只 `return fail(reason)` 不打日志，原因是 1.5.37 才补上的
  `SENTRY-AMMO sample` 行能看到的。未确认之前不要假设是位置问题。
- 火焰 FRV 是限时战备，当前无法实测。
- 分组勾选对 census/identity 无效：两者在 module load 时就把 `profile.models` 快照成内部
  `resources`，`apply_groups()` 要等第一帧 `update` 才清 `profile.models`，已经晚了。
