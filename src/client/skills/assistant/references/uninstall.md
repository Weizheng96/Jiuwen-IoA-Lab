# 卸载 Windows 客户端

完全卸载会删除客户端程序、内部账号配置和本地业务数据。先用实际 Skill 目录的 `scripts/uninstall.ps1` 预览。旧版 Skill 缺脚本时，从新版官方仓库的 `src/client/skills/assistant/scripts/uninstall.ps1` 运行。终端工作目录应在待删除的目录之外。

```powershell
powershell -NoProfile -ExecutionPolicy RemoteSigned -File "<脚本绝对路径>"
```

用户明确同意删除本地数据后，在同一命令末尾加 `-ConfirmRemoval`。默认处理 `~/.ioalab/client`，其他已标记安装用 `-InstallDir "<客户端绝对路径>"`。为干净接入测试同时清除当前用户的 IoA Lab 共用安装缓存时，卸载默认安装加 `-ClearSharedCaches`。其他客户端可能使用这些缓存，先核对范围。

依据 `status` 判断结果。`completed` 表示清理完成，`rejected` 时按返回的具体原因处理。外置配置或数据需要单独检查，原生九问扩展从宿主移除。平台账号和服务器数据仍可通过原平台查看。
