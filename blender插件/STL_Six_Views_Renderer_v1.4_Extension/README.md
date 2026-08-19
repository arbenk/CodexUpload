# STL Six Views Renderer

Blender STL 六视图渲染与图集合并 Extension。

版本：v1.4.0

## ZIP 结构

本 ZIP 按 Blender Extensions 格式打包：

```text
STL_Six_Views_Renderer_v1.4_Extension.zip
├── __init__.py
├── blender_manifest.toml
└── README.md
```

`blender_manifest.toml` 位于 ZIP 根目录。

## 功能

### 六视图渲染

支持单个 STL 与批量文件夹。

输出：

- Front
- Back
- Left
- Right
- Top
- Bottom

方向：

- Front：-Y → +Y
- Back：+Y → -Y
- Left：-X → +X
- Right：+X → -X
- Top：+Z → -Z
- Bottom：-Z → +Z

批量模式会递归扫描 `.stl` 并保持原目录结构。

### 图集合并（可选）

只有点击“生成图集”按钮时执行。

竖版：

```text
Front | Back
Left  | Right
Top   | Bottom
```

横版：

```text
Front | Left  | Top
Back  | Right | Bottom
```

图集输出位置：

- 文件夹内
- 文件夹外
- 搜集文件

“搜集文件”模式会将目录信息加入文件名，降低同名文件覆盖风险。

## 安装

适用于 Blender 4.2+ 的 Extensions 安装方式。

1. 打开 Blender。
2. 进入 `Edit > Preferences`。
3. 打开 Extensions / Add-ons 管理界面。
4. 选择 `Install from Disk`。
5. 直接选择本 ZIP：
   `STL_Six_Views_Renderer_v1.4_Extension.zip`
6. 启用 `STL Six Views Renderer`。
7. 回到 3D View。
8. 按 `N` 打开右侧栏。
9. 点击 `STL 六视图` 标签。

不要解压 ZIP 后再选择其中的 Python 文件。

## 使用

### 单个 STL

1. 选择“单个 STL”。
2. 选择 STL。
3. 选择输出目录。
4. 设置图片尺寸、留白等。
5. 点击“开始渲染”。

### 批量 STL

1. 选择“批量文件夹”。
2. 指定 STL 根目录。
3. 指定输出目录。
4. 点击“开始渲染”。

### 图集

1. 六视图生成后，图集输入自动使用上方输出目录。
2. 选择图集输出位置。
3. 选择竖版或横版。
4. 如选择“搜集文件”，指定搜集目录。
5. 点击“生成图集”。

## 注意事项

- 插件不会清空当前 Blender 场景。
- 六视图按照 Blender 世界坐标定义。
- STL 自身朝向不统一时，插件不会自动识别语义上的正面。
- 图集合并使用 Blender 环境中的 NumPy。
