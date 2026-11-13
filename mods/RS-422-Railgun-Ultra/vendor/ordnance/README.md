# Ordnance（摘录）

本目录只有一份文件来自上游：[Ordnance](https://github.com/Cavadus/ordnance) 的
`compiler/hd2edit.py` —— `generated_*.dl_bin` 密封表的读写原语（游戏内置密钥对、
MurmurHash64A 重算与哈希修正）。RS-422 的补丁靠它算出目标密文字节。

上游是 Apache-2.0，见 `LICENSE.txt` 与 `NOTICE`。上游其余部分（Tauri 客户端、浏览器扩展、
文档站、加载器）与本 mod 无关，未收录。
