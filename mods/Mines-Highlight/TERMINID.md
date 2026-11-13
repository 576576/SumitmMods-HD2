# 终结族地雷：高亮与光柱

## 结论

- 虫雷是 `content/env_bugs/assets/bug_web/bug_cluster_01` 和 `bug_cluster_02`，两个变体全替换。
  模型是中心球体加一圈放射长针，和黄顶带刺的孢囊外观一致。
- 高亮与光柱都由 `tools/bug_patch.py` 生成，构建时自动跑。
- 光柱不是从游戏里搬来的：虫雷资源里没有柱体网格，按机器人地雷的实测尺寸生成。

## 怎么找到的

前两版认错了资源（先 `spore_cluster_01..04`，再 `spore_light_01/02`），游戏内都没有变化。
按名字猜不管用，最后靠两步定位：

1. `filediver --include "<候选>" --out <dir>` 导出 `.glb`，读 POSITION 顶点算包围盒，按宽高筛；
2. 用同一批数据画三视图，看轮廓是不是「球 + 放射刺」。

`bug_cluster` 两兄弟一个 1.6 m、一个 1.0 m 见方，正对上游戏里大小不一的那几个。反照率贴图
`bug_cluster_01_albedo` 是黄底加深色条纹，也吻合。

## 资源事实（build 26251122）

两个 unit 的材质映射相同，三个槽：

| 槽（thin） | 材质 | 用途 |
|---|---|---|
| `63f5aacd` | `m_shadow` (`5990e5efbca8ae21`) | 阴影代理 |
| `7b054b09` | `m_bug_cluster_01` (`0514ea2d55572187`) | **可见本体** |
| `e9871763` | `m_collision` (`84b327056baf1c6d`) | 碰撞代理 |

`m_bug_cluster_01` 是 400 字节格式（shader + `albedo/mra/normal` 三张贴图 + 参数表），和机器人
地雷那套 208 字节的 marker 材质不是一回事。

unit 结构（`+0x2C` 版本号，两个都是 `0xA4CD36`）：

| 变体 | main | GPU | mesh | 布局 |
|---|---|---|---|---|
| 01 | 5136 | 276668 | 8 | 2（本体 stride 20 带 UV，阴影 stride 16 不带） |
| 02 | 4672 | 281928 | 8 | 1（本体与阴影共用 stride 20） |

本体顶点布局三项：`position(vec3f) + normal(packed) + uv(vec2f16)`，共 20 字节。光柱顶点按布局
逐项写，属性顺序和格式从布局里读。

机器人地雷的光柱实测：32 边圆柱，半径 0.042，原始 z 从 -0.366 到 4.421（高 4.7868），64 个三角形。

两种光柱当前统一压到原高度的 80%，即 3.8294：机器人那根以柱体底部为基准压 z；虫雷那根直接
按 3.8294 生成，底部是各自孢囊的最低点往下 0.02。

## 做法

高亮：新增 `m_bug_mine_highlight`（发光 15.0，颜色 `#9EF238`），把 unit 材质表里本体槽的值改
成它。阴影与碰撞槽不动。

光柱：两个变体用两种挂法，都不新建布局，顶点格式跟本体一致
（`position(3f) + packed normal + float16 UV`，stride 20）。

| 变体 | 挂法 | 高度 |
|---|---|---|
| 01（大） | 独立 mesh，登记进 LOD | 3.829 |
| 02（小） | 并进本体 mesh 的第二个组 | **2.681（大刺球的 70%）** |

材质用自己的 `m_bug_beam_highlight`（黄绿 `#8CFF59`，发光 20）。

不用机器人那套光柱材质（`f60f3f933a3e8832`）：它的贴图是红的，光柱会显示成机器人配色；
而且它跑在 stride 28、三套 UV 的布局上，跟本体布局对不上。

02 原先也用独立 mesh，游戏里本体有高光、光柱却不出现，数据层查不出问题（LOD 已登记、
材质槽在位、包围盒正确）。改成并进本体组后与机器人地雷同构，这是它原本的写法。

独立 mesh 那条路要同时照顾四张表，缺一张就解析失败或不被渲染：

| 表 | 处理 |
|---|---|
| 布局列表 | 追加一份布局块（复制本体布局，只改顶点/索引计数与偏移），整表重建到 main 末尾 |
| mesh 列表 | 追加 mesh 块（材质表 + 组表跟着块走，偏移相对块起点） |
| MeshData 表 | 只有一条计数，必须等于 mesh 数，另建一份加一的表 |
| LOD 组列表 | 每个 LOD 级的 mesh 列表里都要登记光柱 mesh，否则不渲染 |

本体顶点与索引一个字节都不动，光柱的顶点/索引追加在 GPU 缓冲末尾。
01 有 5 个本体 LOD、02 也是 5 个，光柱是独立 mesh，任何距离切 LOD 都还在。

## 校验

- `tools/bug_patch.py verify`：七个资源、两个 unit、材质槽归属、光柱布局项、组指向、索引范围、
  光柱高度与半径、LOD 是否登记了光柱 mesh、MeshData 计数、共享资源逐字节比对。
- `toolchain/unitcheck.exe`：用 filediver 自己的 `unit.LoadInfo` / `unit.LoadMeshes` 完整解析生成
  的 unit（两个都 OK，9 mesh、3/2 layout、光柱 mesh 的包围盒高度正好 3.829）。
- `tools/repatch_units.py` 对已是最新版本的 unit 直接跳过：repatcher 会按游戏原版重写整份
  unit，结构改动会被抹掉。
- 游戏内表现仍需实测：光柱 UV 是按「u 绕一周、v 沿高度」生成的，材质贴图实际怎么取样没有实
  测依据，颜色/拉伸可能还要调。

## 发光强度

原始 marker 材质（机器人的本体）只有 0.2，第一版调到 3.0 后游戏里仍嫌不够亮、缺少泛光。
参考值：战备雷用的 marker 材质（`711e88a860a66993`，同为 208 字节格式）是 **100**（但只覆盖
很小的指示几何），4 mine mixed 那个 mod 的整体替换材质里是 **75**。当前两种高亮材质都取
**15**，还没到参考值的量级，偏保守；嫌不够就把两个 `.material` 里偏移 192 的那个 float 往上加。

## 工具

- `toolchain/rawdump.exe`（源码 `toolchain/third_party/filediver/cmd/rawdump`）：从游戏归档原样导出
  资源的 main / GPU / stream。filediver 官方 CLI 只给 `.glb`，反编译产物回不去；名字参数支持
  `0x` 开头的 16 位 hash，便于导出没有名字的材质。
- `toolchain/unitcheck.exe`（源码 `.../cmd/unitcheck`）：用 filediver 解析器校验 unit 的 main + GPU。

## 机器人地雷没有变体

`content/env_cyborg/gameplay_structures/` 下只有 `cy_mine_01`，同目录的 `cy_landingpad_01`、
`cy_refinery_01`、`cy_explosive_battery_01` 也都是单份，`_01` 只是命名习惯，不是序号。
