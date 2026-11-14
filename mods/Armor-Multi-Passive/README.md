# 护甲多被动（静止隐匿）/ Armor Multi Passive

给护甲叠加自选的额外被动：进游戏按 `F6` 勾选，`F5` 保存，退出游戏后运行一次 `apply.bat` 即生效。

![封面](thumbnail.png)

## 前提

已安装 MDL3（3.0.0-dev.33 或更新），并把 MDL3 部署在**最高优先级**。本 mod 由 MDL3 启动，没有独立启动方式。

## 使用

1. 完全退出游戏。
2. 用 HD2Arsenal 导入本压缩包，启用，部署。
3. 进游戏（舰船上）按 `F6` 打开面板，`空格` 勾选，`F5` 保存。
4. 退出游戏，双击包里的 `apply.bat` 写入。再启动游戏就是新的配置。

面板左侧是两列共 32 项被动，右侧显示当前项的官方效果文案，以及「已生效 / 待应用 / 待取消」三份名单。

## 应用排队的选择

游戏进程从启动到退出一直独占 `generated_customization_passive_bonuses.dl_bin`，改名和原地覆盖
都会拿到 `Permission denied`，所以模组在游戏内只能排队，写盘要在游戏外完成。

```
apply.bat                              # 双击即可：应用排队内容
apply.bat --dry-run                    # 只报告将写入什么
apply.bat --restore                    # 还原成备份的原版
apply.bat --force                      # 游戏重新生成过该文件时覆盖（先备份）
apply.bat --game-dir "D:\...\Helldivers 2"   # 游戏不在默认位置时
```

需要 Python 3.10 或更新版本（`lupa`：`pip install lupa`）。应用器直接复用包内 `core/` 归档里的
lua 合成明文，与游戏内保存走的是同一套逻辑。

## 按键

| 按键 | 作用 |
| --- | --- |
| `F6` | 打开 / 关闭面板 |
| `↑` `↓` | 移动光标（`[` `]`、`PgUp` `PgDn` 同效） |
| `←` `→` | 跨列移动 |
| `空格` | 勾选 / 取消 |
| `F5` | 保存 |
| `F9` | 关掉全部额外被动（保留护甲自带那条） |
| `Home` | 清空选择 |
| `F7` | 面板内「按键设置」页 |

改键有两个入口：MDL3 设置 →「按键绑定」→ 分类 **军械工坊**，或面板里按 `F7`。只放行功能键
（`F1`–`F12`、`Home`、`End`、`Ins`、`Del`、`PgUp`、`PgDn`、`[` `]`、方向键）；绑到文件里
`%GAME%\ArmoryForge-Multi.keys`，可直接手改。

## 模组设置页

MDL3 的 MOD 设置里会多出 33 个开关，分组 **军械工坊**：32 个被动各一个，外加「保留护甲自带被动」。
在这里点选与在面板里勾选完全等价，两处显示始终同步。

## 会改动什么

只写一个游戏文件：

```
data/game/generated_customization_passive_bonuses.dl_bin
```

- 首次运行把游戏当时生成的原版备份成游戏根目录的 `ArmoryForge-Multi.original.dl_bin`；
  游戏更新重新生成后会自动认领新的那份（日志 `ADOPTED new stock ...`）。
- 游戏内保存只写排队文件 `ArmoryForge-Multi.pending`；真正的 dl_bin 由 `apply.bat` 在游戏关闭时写入。
  没有应用就直接进游戏，日志会是 `APPLY ERROR ... Permission denied`，面板上的「待应用」保持不清。
- 其余状态都在游戏根目录：`ArmoryForge-Multi.state` / `.pending` / `.keys` / `.log`。

## 还原原版

`apply.bat --restore`，或 `Recovery\Restore-Original-Passive.ps1`（右键用 PowerShell 运行）。
两者都用上面那份自动认领的备份写回，PowerShell 版会先把当前文件另存到 `ModBackups\ArmorMulti\`。
没有备份就说明模组还没写过文件，本来就无需还原。

## 已知限制

额外被动是从模组内置的原版明文重建的，叠加出来的文件里**不含游戏那次更新新增的被动数据**，
只保证「你勾的那些被动都在」。目标文件如果换了新结构，自动认领会跳过，日志里说明原因。

## 说明

本包由上游 v1.7.1（MDL3 版）改写：加载入口改为 MDL3 的模组生命周期（`on_enable` / `on_update` /
`on_disable`），资源 id 换成 `mods/sumitm/armor_multi_passive`。**不要与上游包同时启用**——两者写同一个文件。
