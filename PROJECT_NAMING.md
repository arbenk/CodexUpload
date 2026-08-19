# 项目命名规范

本仓库中的新项目、项目目录、构建输出和 GitHub Release 附件统一使用以下规则。

## 基本格式

```text
软件-功能[-子功能].扩展名
```

- 使用能表明宿主软件或平台的英文名称作为开头，例如 `Photoshop`、`Blender`。
- 功能名称使用英文 PascalCase 单词；多个语义块使用连字符 `-` 分隔。
- 使用稳定名称，不在文件名或目录名中加入版本号。
- 不在名称中加入 `Setup`、`Extension`、`work`、`final` 等构建状态词。
- 版本号保存在源码清单、安装器元数据和 GitHub Release 标签/说明中。
- 源码目录名、构建产物名和 Release 附件名应尽可能保持一致。

## 示例

```text
Photoshop-FontNavigator/
Photoshop-FontNavigator.exe
Photoshop-FontNavigator-UXP.ccx
Photoshop-PSD-Font-Reporter/
Photoshop-PSD-Font-Reporter.exe
Blender-Plugins/Blender-STL-Six-Views-Renderer/
Blender-STL-Six-Views-Renderer.zip
```

## 新建项目检查

1. 先按 `软件-功能[-子功能]` 创建源码目录。
2. 在构建配置中直接设置同名输出，不在构建后手工重命名。
3. 将构建产物写入根目录的 `dist/`。
4. 源码提交到 Git；`dist/` 只作为 GitHub Release 附件发布。
5. README 和 Release 备注使用项目的简要能力描述。
