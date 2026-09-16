# Codex Usage Monitor

一个常驻桌面的迷你 Windows 悬浮组件，直接读取本机 Codex 会话日志，不需要 API Key，也不会显示或保存聊天正文。

显示内容：

- 5 小时额度剩余百分比与重置时间
- 每周额度剩余百分比与重置时间
- 286 × 156 像素、无边框、始终置顶，可按住组件空白处拖动

## 运行

双击 `dist/Codex-UsageMonitor.exe`。面板每秒检测一次变化，也可点击“立即刷新”。

源码运行：

```powershell
python -m pip install -r requirements.txt
python app.py
```

构建：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

默认读取 `%USERPROFILE%\.codex\sessions`。如果设置了 `CODEX_HOME` 环境变量，则读取该目录下的 `sessions`。
