bl_info = {
    "name": "STL Six Views Renderer",
    "author": "OpenAI",
    "version": (1, 4, 0),
    "blender": (4, 0, 0),
    "location": "3D View > Sidebar > STL Six Views",
    "description": "Render Front/Back/Left/Right/Top/Bottom orthographic PNG views for one STL or a folder tree of STL files",
    "category": "Import-Export",
}

import bpy
import traceback
import re
import numpy as np
from pathlib import Path
from mathutils import Vector
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)


VIEW_NAMES = ("Front", "Back", "Left", "Right", "Top", "Bottom")


# ============================================================
# Helpers
# ============================================================

def _path_from_blender(value: str) -> Path:
    return Path(bpy.path.abspath(value)).expanduser()


def _file_is_valid(filepath: Path) -> bool:
    try:
        return filepath.exists() and filepath.is_file() and filepath.stat().st_size > 0
    except Exception:
        return False


def _get_output_paths(model_name: str, output_folder: Path):
    return {
        name: output_folder / f"{model_name}_{name}.png"
        for name in VIEW_NAMES
    }


def _model_is_complete(model_name: str, output_folder: Path) -> bool:
    return all(
        _file_is_valid(path)
        for path in _get_output_paths(model_name, output_folder).values()
    )


def _import_stl(filepath: Path):
    """Import STL and return only the newly created Blender objects."""
    before = set(bpy.data.objects)

    # Current Blender STL importer.
    try:
        bpy.ops.wm.stl_import(filepath=str(filepath))
    except Exception:
        # Older Blender compatibility.
        try:
            bpy.ops.import_mesh.stl(filepath=str(filepath))
        except Exception as exc:
            raise RuntimeError(
                f"无法导入 STL：{filepath}\n"
                "当前 Blender 中没有可用的 STL Import API。"
            ) from exc

    bpy.context.view_layer.update()

    after = set(bpy.data.objects)
    imported_objects = list(after - before)

    if not imported_objects:
        raise RuntimeError(f"STL 导入后没有创建任何对象：{filepath}")

    return imported_objects


def _remove_objects(objects):
    """Remove only objects created by this add-on; do not clear the user's scene."""
    for obj in list(objects):
        try:
            if obj and obj.name in bpy.data.objects:
                bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:
            pass

    # Remove orphan mesh/camera data created during the job.
    for datablocks in (bpy.data.meshes, bpy.data.cameras):
        for datablock in list(datablocks):
            try:
                if datablock.users == 0:
                    datablocks.remove(datablock)
            except Exception:
                pass


def _get_bounds(objects):
    """
    Robust bounds calculation.

    Do not rely on Object.bound_box here. Instead, calculate the world-space
    extents directly from mesh vertices. This is more reliable immediately
    after STL import and also gives better diagnostics for malformed files.
    """
    mesh_objects = [obj for obj in objects if obj.type == 'MESH']

    if not mesh_objects:
        raise RuntimeError("导入的 STL 中没有找到 Mesh。")

    min_x = min_y = min_z = float("inf")
    max_x = max_y = max_z = float("-inf")
    vertex_count = 0

    # Ensure the dependency graph is evaluated before reading geometry.
    depsgraph = bpy.context.evaluated_depsgraph_get()
    bpy.context.view_layer.update()

    for obj in mesh_objects:
        eval_obj = obj.evaluated_get(depsgraph)

        # STL normally imports as a direct mesh. Use evaluated mesh so this
        # also remains robust if Blender changes import/evaluation behavior.
        mesh = None
        try:
            mesh = eval_obj.to_mesh()

            if mesh is None:
                continue

            matrix_world = eval_obj.matrix_world

            for vertex in mesh.vertices:
                p = matrix_world @ vertex.co
                vertex_count += 1

                if p.x < min_x:
                    min_x = p.x
                if p.x > max_x:
                    max_x = p.x

                if p.y < min_y:
                    min_y = p.y
                if p.y > max_y:
                    max_y = p.y

                if p.z < min_z:
                    min_z = p.z
                if p.z > max_z:
                    max_z = p.z

        finally:
            if mesh is not None:
                try:
                    eval_obj.to_mesh_clear()
                except Exception:
                    pass

    if vertex_count == 0:
        raise RuntimeError(
            "STL 已导入，但 Mesh 中没有可用于计算尺寸的顶点。"
        )

    size_x = max_x - min_x
    size_y = max_y - min_y
    size_z = max_z - min_z
    max_size = max(size_x, size_y, size_z)

    if max_size <= 1e-12:
        details = []
        for obj in mesh_objects:
            try:
                details.append(
                    f"{obj.name}: vertices={len(obj.data.vertices)}, "
                    f"scale=({obj.scale.x:.6g}, {obj.scale.y:.6g}, {obj.scale.z:.6g}), "
                    f"dimensions=({obj.dimensions.x:.6g}, "
                    f"{obj.dimensions.y:.6g}, {obj.dimensions.z:.6g})"
                )
            except Exception:
                details.append(obj.name)

        raise RuntimeError(
            "模型几何尺寸为 0。所有顶点在世界坐标中重合，或对象缩放为 0。\n"
            + "\n".join(details)
        )

    center = Vector((
        (min_x + max_x) / 2.0,
        (min_y + max_y) / 2.0,
        (min_z + max_z) / 2.0,
    ))

    return center, size_x, size_y, size_z


def _create_camera():
    data = bpy.data.cameras.new("STL6V_Orthographic_Camera")
    data.type = 'ORTHO'

    camera = bpy.data.objects.new("STL6V_Orthographic_Camera", data)
    bpy.context.collection.objects.link(camera)
    return camera


def _point_camera(camera, target):
    direction = target - camera.location
    camera.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()


def _capture_scene_state(scene):
    shading = scene.display.shading

    state = {
        "camera": scene.camera,
        "engine": scene.render.engine,
        "resolution_x": scene.render.resolution_x,
        "resolution_y": scene.render.resolution_y,
        "resolution_percentage": scene.render.resolution_percentage,
        "filepath": scene.render.filepath,
        "file_format": scene.render.image_settings.file_format,
        "color_mode": scene.render.image_settings.color_mode,
        "shading_light": getattr(shading, "light", None),
        "shading_color_type": getattr(shading, "color_type", None),
        "show_shadows": getattr(shading, "show_shadows", None),
        "show_cavity": getattr(shading, "show_cavity", None),
    }
    return state


def _restore_scene_state(scene, state):
    try:
        scene.camera = state["camera"]
    except Exception:
        pass

    for attr, value in (
        ("engine", state["engine"]),
        ("resolution_x", state["resolution_x"]),
        ("resolution_y", state["resolution_y"]),
        ("resolution_percentage", state["resolution_percentage"]),
        ("filepath", state["filepath"]),
    ):
        try:
            setattr(scene.render, attr, value)
        except Exception:
            pass

    try:
        scene.render.image_settings.file_format = state["file_format"]
        scene.render.image_settings.color_mode = state["color_mode"]
    except Exception:
        pass

    shading = scene.display.shading
    for attr, key in (
        ("light", "shading_light"),
        ("color_type", "shading_color_type"),
        ("show_shadows", "show_shadows"),
        ("show_cavity", "show_cavity"),
    ):
        value = state.get(key)
        if value is not None:
            try:
                setattr(shading, attr, value)
            except Exception:
                pass


def _setup_render(scene, image_size: int):
    scene.render.resolution_x = image_size
    scene.render.resolution_y = image_size
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'

    engine_set = False
    for engine_name in ("BLENDER_WORKBENCH", "BLENDER_WORKBENCH_NEXT"):
        try:
            scene.render.engine = engine_name
            engine_set = True
            break
        except Exception:
            pass

    if not engine_set:
        raise RuntimeError(
            "无法启用 Blender Workbench 渲染引擎。"
            "请确认当前 Blender 版本包含 Workbench。"
        )

    try:
        shading = scene.display.shading
        shading.light = 'STUDIO'
        shading.color_type = 'SINGLE'
        shading.show_shadows = True
        shading.show_cavity = True
    except Exception:
        pass


def _render_one_view(scene, camera, center, camera_location,
                     ortho_scale, output_path: Path, skip_existing: bool):
    if skip_existing and _file_is_valid(output_path):
        return False

    camera.location = camera_location
    camera.data.ortho_scale = max(float(ortho_scale), 0.000001)
    _point_camera(camera, center)

    scene.camera = camera
    scene.render.filepath = str(output_path)

    bpy.context.view_layer.update()
    bpy.ops.render.render(write_still=True)

    return True


def _render_six_views(scene, imported_objects, model_name: str,
                      output_folder: Path, margin: float,
                      skip_existing: bool):
    center, sx, sy, sz = _get_bounds(imported_objects)

    max_size = max(sx, sy, sz)
    if max_size <= 0:
        raise RuntimeError("模型 Bounding Box 尺寸为 0。")

    output_folder.mkdir(parents=True, exist_ok=True)
    output_paths = _get_output_paths(model_name, output_folder)

    camera = _create_camera()
    distance = max_size * 3.0
    camera.data.clip_start = max(max_size / 10000.0, 0.000001)
    camera.data.clip_end = max(max_size * 20.0, distance * 2.0)

    rendered = 0

    views = (
        # name, camera offset, visible plane dimensions
        ("Front",  Vector((0, -distance, 0)), max(sx, sz)),
        ("Back",   Vector((0,  distance, 0)), max(sx, sz)),
        ("Left",   Vector((-distance, 0, 0)), max(sy, sz)),
        ("Right",  Vector(( distance, 0, 0)), max(sy, sz)),
        ("Top",    Vector((0, 0,  distance)), max(sx, sy)),
        ("Bottom", Vector((0, 0, -distance)), max(sx, sy)),
    )

    try:
        for name, offset, visible_size in views:
            did_render = _render_one_view(
                scene=scene,
                camera=camera,
                center=center,
                camera_location=center + offset,
                ortho_scale=visible_size * margin,
                output_path=output_paths[name],
                skip_existing=skip_existing,
            )
            if did_render:
                rendered += 1
    finally:
        _remove_objects([camera])

    return rendered


def _process_one_stl(scene, filepath: Path, output_folder: Path,
                     image_size: int, margin: float, skip_existing: bool):
    model_name = filepath.stem

    if skip_existing and _model_is_complete(model_name, output_folder):
        return "SKIPPED", 0

    imported_objects = []
    try:
        imported_objects = _import_stl(filepath)
        rendered = _render_six_views(
            scene=scene,
            imported_objects=imported_objects,
            model_name=model_name,
            output_folder=output_folder,
            margin=margin,
            skip_existing=skip_existing,
        )
        return "SUCCESS", rendered
    finally:
        _remove_objects(imported_objects)


# ============================================================
# Properties
# ============================================================

class STL6V_Properties(bpy.types.PropertyGroup):
    mode: EnumProperty(
        name="模式",
        description="选择渲染单个 STL 或整个文件夹",
        items=(
            ('SINGLE', "单个 STL", "只渲染一个 STL 文件"),
            ('BATCH', "批量文件夹", "递归渲染文件夹中的所有 STL"),
        ),
        default='SINGLE',
    )

    single_stl: StringProperty(
        name="单个 STL",
        description="选择一个 STL 文件进行六视图渲染",
        subtype='FILE_PATH',
    )

    input_dir: StringProperty(
        name="批量输入目录",
        description="递归扫描此目录下所有 STL",
        subtype='DIR_PATH',
    )

    output_dir: StringProperty(
        name="输出目录",
        description="六视图 PNG 的输出根目录",
        subtype='DIR_PATH',
    )

    image_size: IntProperty(
        name="图片尺寸",
        description="输出正方形 PNG 的边长",
        default=1024,
        min=128,
        max=8192,
    )

    margin: FloatProperty(
        name="留白比例",
        description="1.0 接近贴边；数值越大留白越多",
        default=1.12,
        min=1.0,
        max=3.0,
        precision=2,
    )

    skip_existing: BoolProperty(
        name="跳过已完成",
        description="六张图都已存在时跳过该 STL；缺图时只补缺失图片",
        default=True,
    )

    atlas_location: EnumProperty(
        name="图集输出位置",
        description="选择合并图集的保存位置",
        items=(
            (
                'INSIDE',
                "文件夹内",
                "放在每个模型自己的六向图文件夹中",
            ),
            (
                'OUTSIDE',
                "文件夹外",
                "放在每个六向图文件夹的上一级目录中",
            ),
            (
                'COLLECT',
                "搜集文件",
                "把所有合并图集统一放到一个单独选择的文件夹中",
            ),
        ),
        default='INSIDE',
    )

    atlas_collect_dir: StringProperty(
        name="搜集文件夹",
        description="选择所有合并图集统一保存的目录",
        subtype='DIR_PATH',
    )

    atlas_skip_existing: BoolProperty(
        name="跳过已有图集",
        description="目标图集已经存在且非空时不重新生成",
        default=True,
    )

    atlas_layout: EnumProperty(
        name="图集布局",
        description="选择图集布局方式",
        items=(
            (
                'VERTICAL',
                "竖版",
                "Front|Back；Left|Right；Top|Bottom",
            ),
            (
                'HORIZONTAL',
                "横版",
                "Front|Left|Top；Back|Right|Bottom",
            ),
        ),
        default='VERTICAL',
    )



# ============================================================
# Atlas helpers
# ============================================================

_ATLAS_VIEW_ORDER = (
    ("Front", "Back"),
    ("Left", "Right"),
    ("Top", "Bottom"),
)


def _find_six_view_sets(root: Path):
    """
    Recursively find complete six-view image sets.

    A set is recognized by:
        <model>_Front.png
        <model>_Back.png
        <model>_Left.png
        <model>_Right.png
        <model>_Top.png
        <model>_Bottom.png

    Returns a list of:
        (folder, model_name, paths_dict)
    """
    suffix_pattern = re.compile(
        r"^(?P<model>.+)_(?P<view>Front|Back|Left|Right|Top|Bottom)\.png$",
        re.IGNORECASE,
    )

    grouped = {}

    for filepath in root.rglob("*.png"):
        if not filepath.is_file():
            continue

        match = suffix_pattern.match(filepath.name)
        if not match:
            continue

        model_name = match.group("model")
        view_raw = match.group("view").lower()

        canonical_view = {
            "front": "Front",
            "back": "Back",
            "left": "Left",
            "right": "Right",
            "top": "Top",
            "bottom": "Bottom",
        }[view_raw]

        key = (filepath.parent, model_name)
        grouped.setdefault(key, {})[canonical_view] = filepath

    results = []

    for (folder, model_name), paths in grouped.items():
        if all(name in paths for name in VIEW_NAMES):
            results.append((folder, model_name, paths))

    results.sort(
        key=lambda item: (
            str(item[0]).lower(),
            item[1].lower(),
        )
    )

    return results


def _atlas_output_path(
    source_root: Path,
    model_folder: Path,
    model_name: str,
    location: str,
    collect_dir: Path | None,
):
    """
    Naming rules:

    INSIDE:
        .../<model>/<model>_合并.png

    OUTSIDE:
        .../<parent>/<model>_合并.png

    COLLECT:
        <collect>/<relative folders>_<model>_合并.png

    In collect mode, all relative parent folder names are included so models
    with the same filename in different source folders do not overwrite each
    other.
    """
    filename = f"{model_name}_合并.png"

    if location == 'INSIDE':
        return model_folder / filename

    if location == 'OUTSIDE':
        return model_folder.parent / filename

    if location == 'COLLECT':
        if collect_dir is None:
            raise RuntimeError("请选择搜集文件夹。")

        # The model folder itself is normally named after the model.
        # Use its parent path relative to the atlas input root as the folder
        # prefix, then append model name + 合并.
        try:
            relative_parent = model_folder.parent.relative_to(source_root)
            parts = [p for p in relative_parent.parts if p not in ("", ".")]
        except Exception:
            parts = [model_folder.parent.name] if model_folder.parent.name else []

        safe_parts = [
            str(part).replace("/", "_").replace("\\", "_")
            for part in parts
        ]

        if safe_parts:
            collect_name = "_".join(safe_parts + [model_name, "合并"]) + ".png"
        else:
            collect_name = f"{model_name}_合并.png"

        return collect_dir / collect_name

    raise RuntimeError(f"未知图集输出位置：{location}")


def _load_image_pixels(filepath: Path, target_w=None, target_h=None):
    """
    Load an image through Blender and return an HxWx4 float32 NumPy array.

    check_existing=False prevents us from modifying an image datablock that
    may already be used elsewhere in the current .blend.
    """
    image = None

    try:
        image = bpy.data.images.load(str(filepath), check_existing=False)

        width, height = image.size[0], image.size[1]

        if width <= 0 or height <= 0:
            raise RuntimeError(f"无法读取图片尺寸：{filepath}")

        if target_w is not None and target_h is not None:
            if width != target_w or height != target_h:
                image.scale(target_w, target_h)
                width, height = target_w, target_h

        pixels = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        pixels = pixels.reshape((height, width, 4))

        return pixels, width, height

    finally:
        if image is not None:
            try:
                bpy.data.images.remove(image)
            except Exception:
                pass


def _save_atlas(paths, output_path: Path, layout: str):
    """
    Build an atlas from the six views.

    VERTICAL layout:
        Front   | Back
        Left    | Right
        Top     | Bottom

    HORIZONTAL layout:
        Front   | Left   | Top
        Back    | Right  | Bottom
    """
    first_path = paths["Front"]
    first_pixels, tile_w, tile_h = _load_image_pixels(first_path)

    if layout == 'VERTICAL':
        atlas_w = tile_w * 2
        atlas_h = tile_h * 3

        # Blender image pixel arrays use the lower-left as origin.
        # These y slots place Front/Back visually on the top row.
        placements = {
            "Front":  (0, 2),
            "Back":   (1, 2),
            "Left":   (0, 1),
            "Right":  (1, 1),
            "Top":    (0, 0),
            "Bottom": (1, 0),
        }

    elif layout == 'HORIZONTAL':
        atlas_w = tile_w * 3
        atlas_h = tile_h * 2

        # Visual top row:    Front | Left  | Top
        # Visual bottom row: Back  | Right | Bottom
        placements = {
            "Front":  (0, 1),
            "Left":   (1, 1),
            "Top":    (2, 1),
            "Back":   (0, 0),
            "Right":  (1, 0),
            "Bottom": (2, 0),
        }

    else:
        raise RuntimeError(f"未知图集布局：{layout}")

    atlas_pixels = np.zeros(
        (atlas_h, atlas_w, 4),
        dtype=np.float32,
    )

    for view in VIEW_NAMES:
        if view == "Front":
            pixels = first_pixels
        else:
            pixels, _, _ = _load_image_pixels(
                paths[view],
                target_w=tile_w,
                target_h=tile_h,
            )

        col, row = placements[view]

        x0 = col * tile_w
        y0 = row * tile_h

        atlas_pixels[
            y0:y0 + tile_h,
            x0:x0 + tile_w,
            :
        ] = pixels

    output_path.parent.mkdir(parents=True, exist_ok=True)

    atlas_image = None

    try:
        atlas_image = bpy.data.images.new(
            name=f"STL6V_Atlas_{output_path.stem}",
            width=atlas_w,
            height=atlas_h,
            alpha=True,
            float_buffer=False,
        )

        flat = np.ascontiguousarray(atlas_pixels.reshape(-1))
        atlas_image.pixels.foreach_set(flat)

        atlas_image.file_format = 'PNG'
        atlas_image.filepath_raw = str(output_path)
        atlas_image.save()

    finally:
        if atlas_image is not None:
            try:
                bpy.data.images.remove(atlas_image)
            except Exception:
                pass


# ============================================================
# Operators
# ============================================================

class STL6V_OT_RenderSingle(bpy.types.Operator):
    bl_idname = "stl6v.render_single"
    bl_label = "渲染单个 STL"
    bl_description = "渲染所选 STL 的前后左右上下六视图"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.stl6v_settings
        scene = context.scene

        filepath = _path_from_blender(props.single_stl)
        output_root = _path_from_blender(props.output_dir)

        if not filepath.exists() or not filepath.is_file():
            self.report({'ERROR'}, "请选择有效的 STL 文件。")
            return {'CANCELLED'}

        if filepath.suffix.lower() != ".stl":
            self.report({'ERROR'}, "单个文件必须是 .stl。")
            return {'CANCELLED'}

        if not str(props.output_dir).strip():
            self.report({'ERROR'}, "请先选择输出目录。")
            return {'CANCELLED'}

        output_folder = output_root / filepath.stem
        output_root.mkdir(parents=True, exist_ok=True)

        state = _capture_scene_state(scene)

        try:
            _setup_render(scene, props.image_size)

            status, rendered = _process_one_stl(
                scene=scene,
                filepath=filepath,
                output_folder=output_folder,
                image_size=props.image_size,
                margin=props.margin,
                skip_existing=props.skip_existing,
            )

            if status == "SKIPPED":
                self.report({'INFO'}, "六张图片已经存在，已跳过。")
            else:
                self.report(
                    {'INFO'},
                    f"完成：{filepath.name}，本次生成 {rendered} 张图片。"
                )

            return {'FINISHED'}

        except Exception as exc:
            traceback.print_exc()
            self.report({'ERROR'}, f"渲染失败：{exc}")
            return {'CANCELLED'}

        finally:
            _restore_scene_state(scene, state)


class STL6V_OT_RenderBatch(bpy.types.Operator):
    bl_idname = "stl6v.render_batch"
    bl_label = "批量渲染文件夹"
    bl_description = "递归扫描输入目录并渲染所有 STL 的六视图"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.stl6v_settings
        scene = context.scene
        wm = context.window_manager

        input_dir = _path_from_blender(props.input_dir)
        output_root = _path_from_blender(props.output_dir)

        if not input_dir.exists() or not input_dir.is_dir():
            self.report({'ERROR'}, "请选择有效的批量输入目录。")
            return {'CANCELLED'}

        if not str(props.output_dir).strip():
            self.report({'ERROR'}, "请先选择输出目录。")
            return {'CANCELLED'}

        files = sorted(
            (p for p in input_dir.rglob("*")
             if p.is_file() and p.suffix.lower() == ".stl"),
            key=lambda p: str(p).lower(),
        )

        if not files:
            self.report({'WARNING'}, "输入目录中没有找到 STL。")
            return {'CANCELLED'}

        output_root.mkdir(parents=True, exist_ok=True)

        state = _capture_scene_state(scene)
        success_count = 0
        skipped_count = 0
        error_count = 0
        rendered_images = 0
        errors = []

        wm.progress_begin(0, len(files))

        try:
            _setup_render(scene, props.image_size)

            for index, filepath in enumerate(files, start=1):
                wm.progress_update(index - 1)

                relative_parent = filepath.parent.relative_to(input_dir)
                output_folder = output_root / relative_parent / filepath.stem

                try:
                    status, rendered = _process_one_stl(
                        scene=scene,
                        filepath=filepath,
                        output_folder=output_folder,
                        image_size=props.image_size,
                        margin=props.margin,
                        skip_existing=props.skip_existing,
                    )

                    if status == "SKIPPED":
                        skipped_count += 1
                    else:
                        success_count += 1
                        rendered_images += rendered

                    print(
                        f"[STL Six Views {index}/{len(files)}] "
                        f"{status}: {filepath}"
                    )

                except Exception as exc:
                    error_count += 1
                    tb = traceback.format_exc()
                    errors.append(
                        "=" * 80
                        + f"\nFILE: {filepath}\n"
                        + f"ERROR: {exc}\n\n"
                        + tb
                    )
                    print(tb)

            wm.progress_update(len(files))

            error_log = output_root / "_errors.txt"

            if errors:
                error_log.write_text("\n\n".join(errors), encoding="utf-8")
            else:
                try:
                    if error_log.exists():
                        error_log.unlink()
                except Exception:
                    pass

            self.report(
                {'INFO'},
                (
                    f"批量完成：成功 {success_count}，跳过 {skipped_count}，"
                    f"失败 {error_count}，本次生成 {rendered_images} 张图。"
                )
            )

            return {'FINISHED'}

        except Exception as exc:
            traceback.print_exc()
            self.report({'ERROR'}, f"批量渲染失败：{exc}")
            return {'CANCELLED'}

        finally:
            wm.progress_end()
            _restore_scene_state(scene, state)


class STL6V_OT_StartRender(bpy.types.Operator):
    bl_idname = "stl6v.start_render"
    bl_label = "开始渲染"
    bl_description = "按照当前选择的模式开始六视图渲染"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.stl6v_settings

        if props.mode == 'SINGLE':
            result = bpy.ops.stl6v.render_single('EXEC_DEFAULT')
        else:
            result = bpy.ops.stl6v.render_batch('EXEC_DEFAULT')

        if 'CANCELLED' in result:
            return {'CANCELLED'}

        return {'FINISHED'}



class STL6V_OT_CreateAtlases(bpy.types.Operator):
    bl_idname = "stl6v.create_atlases"
    bl_label = "生成图集"
    bl_description = "把输出目录中的六向图合并为单张图集"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.stl6v_settings

        source_root = _path_from_blender(props.output_dir)

        if not str(props.output_dir).strip():
            self.report({'ERROR'}, "请先设置上方的输出目录。")
            return {'CANCELLED'}

        if not source_root.exists() or not source_root.is_dir():
            self.report({'ERROR'}, "上方输出目录不存在或不是文件夹。")
            return {'CANCELLED'}

        collect_dir = None

        if props.atlas_location == 'COLLECT':
            if not str(props.atlas_collect_dir).strip():
                self.report({'ERROR'}, "请选择搜集文件夹。")
                return {'CANCELLED'}

            collect_dir = _path_from_blender(props.atlas_collect_dir)
            collect_dir.mkdir(parents=True, exist_ok=True)

        sets = _find_six_view_sets(source_root)

        if not sets:
            self.report(
                {'WARNING'},
                "没有找到完整的六向图组合。需要 Front/Back/Left/Right/Top/Bottom 六张图。"
            )
            return {'CANCELLED'}

        wm = context.window_manager
        wm.progress_begin(0, len(sets))

        created = 0
        skipped = 0
        failed = 0
        errors = []

        try:
            for index, (model_folder, model_name, paths) in enumerate(
                sets,
                start=1,
            ):
                wm.progress_update(index - 1)

                try:
                    output_path = _atlas_output_path(
                        source_root=source_root,
                        model_folder=model_folder,
                        model_name=model_name,
                        location=props.atlas_location,
                        collect_dir=collect_dir,
                    )

                    if (
                        props.atlas_skip_existing
                        and _file_is_valid(output_path)
                    ):
                        skipped += 1
                        print(
                            f"[STL Atlas {index}/{len(sets)}] "
                            f"SKIP: {output_path}"
                        )
                        continue

                    _save_atlas(
                        paths,
                        output_path,
                        props.atlas_layout,
                    )
                    created += 1

                    print(
                        f"[STL Atlas {index}/{len(sets)}] "
                        f"SAVED: {output_path}"
                    )

                except Exception as exc:
                    failed += 1
                    tb = traceback.format_exc()
                    errors.append(
                        "=" * 80
                        + f"\nMODEL: {model_name}\n"
                        + f"FOLDER: {model_folder}\n"
                        + f"ERROR: {exc}\n\n"
                        + tb
                    )
                    print(tb)

            wm.progress_update(len(sets))

            if errors:
                log_root = (
                    collect_dir
                    if props.atlas_location == 'COLLECT' and collect_dir
                    else source_root
                )
                try:
                    (log_root / "_atlas_errors.txt").write_text(
                        "\n\n".join(errors),
                        encoding="utf-8",
                    )
                except Exception:
                    pass

            self.report(
                {'INFO'},
                f"图集完成：生成 {created}，跳过 {skipped}，失败 {failed}。"
            )

            return {'FINISHED'}

        finally:
            wm.progress_end()


# ============================================================
# UI
# ============================================================

class STL6V_PT_MainPanel(bpy.types.Panel):
    bl_label = "STL 六视图"
    bl_idname = "STL6V_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "STL 六视图"

    def draw(self, context):
        layout = self.layout
        props = context.scene.stl6v_settings

        # ----------------------------------------------------
        # 六视图渲染
        # ----------------------------------------------------
        box = layout.box()
        box.label(text="六视图渲染")

        row = box.row(align=True)
        row.prop(props, "mode", expand=True)

        if props.mode == 'SINGLE':
            box.prop(props, "single_stl", text="")
        else:
            box.prop(props, "input_dir", text="")

        # ----------------------------------------------------
        # 输出与渲染设置
        # ----------------------------------------------------
        box = layout.box()
        box.label(text="输出与渲染")
        box.prop(props, "output_dir", text="")
        box.prop(props, "image_size")
        box.prop(props, "margin")
        box.prop(props, "skip_existing")

        row = layout.row()
        row.scale_y = 1.8
        row.operator(
            "stl6v.start_render",
            text="开始渲染",
            icon='RENDER_STILL'
        )

        layout.label(text="Front / Back / Left / Right")
        layout.label(text="Top / Bottom，全部为正交视图")

        # ----------------------------------------------------
        # Optional atlas feature
        # ----------------------------------------------------
        layout.separator()

        box = layout.box()
        box.label(text="六视图图集（可选）")

        # The atlas input is intentionally the SAME output_dir used above.
        col = box.column()
        col.enabled = False
        col.prop(props, "output_dir", text="图集输入")

        box.prop(props, "atlas_location")
        box.prop(props, "atlas_layout")

        if props.atlas_location == 'COLLECT':
            box.prop(props, "atlas_collect_dir", text="搜集文件夹")

        box.prop(props, "atlas_skip_existing")

        row = box.row()
        row.scale_y = 1.5
        row.operator(
            "stl6v.create_atlases",
            text="生成图集",
            icon='IMAGE_DATA'
        )

        if props.atlas_layout == 'VERTICAL':
            box.label(text="布局：Front | Back")
            box.label(text="      Left  | Right")
            box.label(text="      Top   | Bottom")
        else:
            box.label(text="布局：Front | Left | Top")
            box.label(text="      Back  | Right | Bottom")


# ============================================================
# Register
# ============================================================

classes = (
    STL6V_Properties,
    STL6V_OT_RenderSingle,
    STL6V_OT_RenderBatch,
    STL6V_OT_StartRender,
    STL6V_OT_CreateAtlases,
    STL6V_PT_MainPanel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.stl6v_settings = PointerProperty(type=STL6V_Properties)


def unregister():
    if hasattr(bpy.types.Scene, "stl6v_settings"):
        del bpy.types.Scene.stl6v_settings

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
