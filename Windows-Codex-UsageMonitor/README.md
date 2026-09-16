# Codex 用量悬浮窗

项目稳定名称：`Windows-Codex-UsageMonitor`

一个极简、只读、无网络请求的 Windows 悬浮窗。

- 显示 Codex 5 小时和 7 天窗口的剩余百分比及重置倒计时。
- 监听 `%USERPROFILE%\.codex\sessions` 的 JSONL 文件变更；仅变化时重读最近日志。
- 左上标题栏可拖动；圆点按钮切换置顶；`×` 退出；双击窗口立即刷新。
- 不读取或显示聊天内容，不修改 Codex 文件，不写注册表，不设置开机启动。

> 用量值来自 Codex 本地会话日志中的 `rate_limits`，因此只有 Codex 收到服务端用量数据并写入日志后才会变化。
