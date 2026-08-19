import bpy
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import BoolProperty, EnumProperty, FloatProperty, PointerProperty
from mathutils import Vector


# ============================================================
# 工具函数
# ============================================================

def get_world_bbox_corners(obj, depsgraph):
    """获取评估后包围盒的 8 个角点（世界坐标）。"""
    obj_eval = obj.evaluated_get(depsgraph)
    bbox = obj_eval.bound_box

    if not bbox:
        return None

    flat = [v for corner in bbox for v in corner]
    if flat and all(abs(v + 1.0) < 1e-8 for v in flat):
        return None

    mw = obj_eval.matrix_world
    return [mw @ Vector(corner) for corner in bbox]


def translate_object_world(obj, delta):
    """按世界坐标位移，兼容有父级的物体。"""
    mw = obj.matrix_world.copy()
    mw.translation += delta
    obj.matrix_world = mw


def get_reference_axes(active_obj, space):
    """返回全局轴，或激活物体的局部轴（用世界向量表达）。"""
    if space == 'GLOBAL':
        return (
            Vector((1.0, 0.0, 0.0)),
            Vector((0.0, 1.0, 0.0)),
            Vector((0.0, 0.0, 1.0)),
        )

    q = active_obj.matrix_world.to_quaternion()
    return (
        (q @ Vector((1.0, 0.0, 0.0))).normalized(),
        (q @ Vector((0.0, 1.0, 0.0))).normalized(),
        (q @ Vector((0.0, 0.0, 1.0))).normalized(),
    )


def project_bounds(corners, axis_vec):
    """将世界空间包围盒角点投影到某一参考轴。"""
    values = [p.dot(axis_vec) for p in corners]
    min_v = min(values)
    max_v = max(values)
    center_v = (min_v + max_v) * 0.5
    return min_v, max_v, center_v


def touching_delta(target_bounds, source_bounds):
    """外贴合：保持 source 当前所在侧，让最近边界与 target 接触。"""
    t_min, t_max, t_center = target_bounds
    s_min, s_max, s_center = source_bounds

    if s_center < t_center:
        # source 位于 target 负方向：source.max -> target.min
        return t_min - s_max
    else:
        # source 位于 target 正方向：source.min -> target.max
        return t_max - s_min


def inner_align_delta(target_bounds, source_bounds):
    """
    自动内码齐：

    先像“外贴合”一样判断 source 当前位于 target 的哪一侧，
    然后仍然选择 target 的最近端点，但把 source 的“远端/反方向端点”
    对齐到这个 target 端点。

    source 在 target 负方向：
        source.min -> target.min

    source 在 target 正方向：
        source.max -> target.max

    因此不需要手动选择正向 / 负向。
    """
    t_min, t_max, t_center = target_bounds
    s_min, s_max, s_center = source_bounds

    if s_center < t_center:
        return t_min - s_min
    else:
        return t_max - s_max


def get_bbox_center(corners):
    """世界空间包围盒中心。"""
    center = Vector((0.0, 0.0, 0.0))
    for p in corners:
        center += p
    return center / len(corners)


def get_object_align_point(obj, depsgraph, point_mode):
    """返回用于轴对齐的世界空间参考点。"""
    if point_mode == 'ORIGIN':
        return obj.matrix_world.translation.copy()

    corners = get_world_bbox_corners(obj, depsgraph)
    if point_mode == 'BOUNDS':
        return get_bbox_center(corners) if corners else None

    # 几何质心：评估后网格全部顶点的世界坐标平均值。
    obj_eval = obj.evaluated_get(depsgraph)
    if obj_eval.type == 'MESH':
        mesh = obj_eval.to_mesh()
        try:
            if mesh and mesh.vertices:
                point = Vector((0.0, 0.0, 0.0))
                mw = obj_eval.matrix_world
                for vertex in mesh.vertices:
                    point += mw @ vertex.co
                return point / len(mesh.vertices)
        finally:
            obj_eval.to_mesh_clear()

    # 非网格或空网格没有可计算的几何质心，回退到包围盒中心。
    return get_bbox_center(corners) if corners else None


def find_farthest_pair(items, axis_mask=None):
    """
    从物体中心中寻找距离最远的一对。
    items: [(obj, corners, center), ...]
    """
    best_i = None
    best_j = None
    best_dist_sq = -1.0

    for i in range(len(items) - 1):
        ci = items[i][2]
        for j in range(i + 1, len(items)):
            cj = items[j][2]
            delta = cj - ci
            if axis_mask is not None:
                delta = Vector(tuple(
                    value if enabled else 0.0
                    for value, enabled in zip(delta, axis_mask)
                ))
            dist_sq = delta.length_squared
            if dist_sq > best_dist_sq:
                best_dist_sq = dist_sq
                best_i = i
                best_j = j

    return best_i, best_j, best_dist_sq


# ============================================================
# 设置：使用 PropertyGroup，避免面板属性丢失
# ============================================================

class DGA_Settings(PropertyGroup):
    point_align_mode: EnumProperty(
        name="对齐基准",
        description="选择用于轴对齐的物体参考点",
        items=(
            ('CENTROID', "质心点", "使用评估后网格顶点的平均位置"),
            ('BOUNDS', "中心点", "使用物体世界空间包围盒的宽度中心"),
            ('ORIGIN', "原点", "使用物体原点"),
        ),
        default='BOUNDS',
    )

    align_x: BoolProperty(
        name="X",
        description="沿 X 轴执行对齐",
        default=True,
    )

    align_y: BoolProperty(
        name="Y",
        description="沿 Y 轴执行对齐",
        default=False,
    )

    align_z: BoolProperty(
        name="Z",
        description="沿 Z 轴执行对齐",
        default=False,
    )

    align_space: EnumProperty(
        name="坐标轴",
        description="选择对齐使用的参考坐标轴",
        items=(
            ('GLOBAL', "全局", "使用世界坐标 X / Y / Z"),
            ('LOCAL', "局部", "使用激活物体自身的局部 X / Y / Z"),
        ),
        default='GLOBAL',
    )


    distribute_gap: FloatProperty(
        name="间隙",
        description="非均匀模式下，相邻物体包围盒之间的目标间隙",
        default=0.1,
        precision=4,
        step=1,
        subtype='DISTANCE',
        unit='LENGTH',
    )

    distribute_uniform: BoolProperty(
        name="均匀",
        description="固定最远的两个端点物体，将中间物体平均分布；空间不足时改用中心点等距",
        default=False,
    )

    distribute_x: BoolProperty(
        name="X",
        description="将分散方向限制在世界 X 轴",
        default=False,
    )

    distribute_y: BoolProperty(
        name="Y",
        description="将分散方向限制在世界 Y 轴",
        default=False,
    )

    distribute_z: BoolProperty(
        name="Z",
        description="将分散方向限制在世界 Z 轴",
        default=False,
    )

    show_help: BoolProperty(
        name="展开使用方法",
        description="展开或收起使用方法",
        default=False,
    )



# ============================================================
# 功能 1：放置地面
# ============================================================

class OBJECT_OT_dga_drop_to_ground(Operator):
    """将每个选中物体的世界空间最低点放到 Z=0"""
    bl_idname = "object.dga_drop_to_ground"
    bl_label = "放置地面"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and bool(context.selected_objects)

    def execute(self, context):
        depsgraph = context.evaluated_depsgraph_get()
        moved = 0
        skipped = 0

        for obj in context.selected_objects:
            corners = get_world_bbox_corners(obj, depsgraph)
            if not corners:
                skipped += 1
                continue

            min_z = min(p.z for p in corners)
            translate_object_world(obj, Vector((0.0, 0.0, -min_z)))
            moved += 1

        if moved == 0:
            self.report({'WARNING'}, "选中物体没有可用的包围盒")
            return {'CANCELLED'}

        if skipped:
            self.report({'INFO'}, f"已放置 {moved} 个物体；跳过 {skipped} 个")
        else:
            self.report({'INFO'}, f"已将 {moved} 个物体放到世界 Z=0")

        return {'FINISHED'}


class OBJECT_OT_dga_place_top(Operator):
    """将每个选中物体的世界空间最高点对齐到 Z=0"""
    bl_idname = "object.dga_place_top"
    bl_label = "顶端放置"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and bool(context.selected_objects)

    def execute(self, context):
        depsgraph = context.evaluated_depsgraph_get()
        moved = 0
        skipped = 0

        for obj in context.selected_objects:
            corners = get_world_bbox_corners(obj, depsgraph)
            if not corners:
                skipped += 1
                continue

            max_z = max(p.z for p in corners)
            translate_object_world(obj, Vector((0.0, 0.0, -max_z)))
            moved += 1

        if moved == 0:
            self.report({'WARNING'}, "选中物体没有可用的包围盒")
            return {'CANCELLED'}

        msg = f"已将 {moved} 个物体的最高点对齐到世界 Z=0"
        if skipped:
            msg += f"；跳过 {skipped} 个"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ============================================================
# 共用：对齐执行前检查
# ============================================================

def get_align_context(context):
    settings = context.scene.dga_settings
    active = context.active_object
    selected = context.selected_objects
    use_axes = (settings.align_x, settings.align_y, settings.align_z)

    if active is None or len(selected) < 2:
        return None, "请至少选择两个物体，最后选中的激活物体作为目标"

    if active not in selected:
        return None, "激活物体必须包含在当前选择中"

    if not any(use_axes):
        return None, "请至少选择一个对齐轴：X、Y 或 Z"

    return (settings, active, selected, use_axes), None


# ============================================================
# 功能 2：外贴合
# ============================================================

class OBJECT_OT_dga_touch_align(Operator):
    """让其他选中物体沿指定轴贴到激活物体外侧，边界间距为 0"""
    bl_idname = "object.dga_touch_align"
    bl_label = "外贴合"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        data, error = get_align_context(context)
        if error:
            self.report({'WARNING'}, error)
            return {'CANCELLED'}

        settings, active, selected, use_axes = data
        depsgraph = context.evaluated_depsgraph_get()

        target_corners = get_world_bbox_corners(active, depsgraph)
        if not target_corners:
            self.report({'WARNING'}, "激活物体没有可用的包围盒")
            return {'CANCELLED'}

        axes = get_reference_axes(active, settings.align_space)
        target_bounds = [project_bounds(target_corners, axis) for axis in axes]

        moved = 0
        skipped = 0

        for obj in selected:
            if obj == active:
                continue

            source_corners = get_world_bbox_corners(obj, depsgraph)
            if not source_corners:
                skipped += 1
                continue

            delta_world = Vector((0.0, 0.0, 0.0))

            for axis_index, enabled in enumerate(use_axes):
                if not enabled:
                    continue

                axis_vec = axes[axis_index]
                source_bounds = project_bounds(source_corners, axis_vec)
                delta_scalar = touching_delta(
                    target_bounds[axis_index],
                    source_bounds,
                )
                delta_world += axis_vec * delta_scalar

            translate_object_world(obj, delta_world)
            moved += 1

        if moved == 0:
            self.report({'WARNING'}, "没有可移动的非激活物体")
            return {'CANCELLED'}

        axis_names = "".join(
            name for name, enabled in zip(("X", "Y", "Z"), use_axes)
            if enabled
        )
        space_name = "局部" if settings.align_space == 'LOCAL' else "全局"
        msg = f"外贴合 {moved} 个物体到“{active.name}”；{space_name} {axis_names}"
        if skipped:
            msg += f"；跳过 {skipped} 个"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ============================================================
# 功能 3：内码齐
# ============================================================

class OBJECT_OT_dga_inner_align(Operator):
    """
    自动内码齐。

    逻辑与外贴合共用“判断 source 位于 target 哪一侧”的思路：

    外贴合：
        source 近端 -> target 最近端

    内码齐：
        source 远端（反方向端点） -> target 最近端

    例如：
    - source 在 target 的负 Z 一侧：source 最低点 -> target 最低点
    - source 在 target 的正 Z 一侧：source 最高点 -> target 最高点
    """
    bl_idname = "object.dga_inner_align"
    bl_label = "内码齐"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        data, error = get_align_context(context)
        if error:
            self.report({'WARNING'}, error)
            return {'CANCELLED'}

        settings, active, selected, use_axes = data
        depsgraph = context.evaluated_depsgraph_get()

        target_corners = get_world_bbox_corners(active, depsgraph)
        if not target_corners:
            self.report({'WARNING'}, "激活物体没有可用的包围盒")
            return {'CANCELLED'}

        axes = get_reference_axes(active, settings.align_space)
        target_bounds = [project_bounds(target_corners, axis) for axis in axes]

        moved = 0
        skipped = 0

        for obj in selected:
            if obj == active:
                continue

            source_corners = get_world_bbox_corners(obj, depsgraph)
            if not source_corners:
                skipped += 1
                continue

            delta_world = Vector((0.0, 0.0, 0.0))

            for axis_index, enabled in enumerate(use_axes):
                if not enabled:
                    continue

                axis_vec = axes[axis_index]
                source_bounds = project_bounds(source_corners, axis_vec)

                delta_scalar = inner_align_delta(
                    target_bounds[axis_index],
                    source_bounds,
                )
                delta_world += axis_vec * delta_scalar

            translate_object_world(obj, delta_world)
            moved += 1

        if moved == 0:
            self.report({'WARNING'}, "没有可移动的非激活物体")
            return {'CANCELLED'}

        axis_names = "".join(
            name for name, enabled in zip(("X", "Y", "Z"), use_axes)
            if enabled
        )
        space_name = "局部" if settings.align_space == 'LOCAL' else "全局"

        msg = (
            f"内码齐 {moved} 个物体到“{active.name}”；"
            f"{space_name} {axis_names}"
        )
        if skipped:
            msg += f"；跳过 {skipped} 个"

        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ============================================================
# 功能 4：按参考点沿世界轴对齐
# ============================================================

class OBJECT_OT_dga_point_axis_align(Operator):
    """按所选参考点，将其他选中物体沿世界轴对齐到激活物体"""
    bl_idname = "object.dga_point_axis_align"
    bl_label = "轴对齐"
    bl_options = {'REGISTER', 'UNDO'}

    axis: EnumProperty(
        name="轴",
        items=(
            ('X', "X", "沿世界 X 轴对齐"),
            ('Y', "Y", "沿世界 Y 轴对齐"),
            ('Z', "Z", "沿世界 Z 轴对齐"),
        ),
        default='X',
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        settings = context.scene.dga_settings
        active = context.active_object
        selected = list(context.selected_objects)

        if active is None or active not in selected or len(selected) < 2:
            self.report({'WARNING'}, "请至少选择两个物体，最后选中的激活物体作为目标")
            return {'CANCELLED'}

        depsgraph = context.evaluated_depsgraph_get()
        target_point = get_object_align_point(
            active, depsgraph, settings.point_align_mode
        )
        if target_point is None:
            self.report({'WARNING'}, "激活物体没有可用的对齐参考点")
            return {'CANCELLED'}

        axis_index = {'X': 0, 'Y': 1, 'Z': 2}[self.axis]
        moved = 0
        skipped = 0

        for obj in selected:
            if obj == active:
                continue
            source_point = get_object_align_point(
                obj, depsgraph, settings.point_align_mode
            )
            if source_point is None:
                skipped += 1
                continue

            delta = Vector((0.0, 0.0, 0.0))
            delta[axis_index] = target_point[axis_index] - source_point[axis_index]
            translate_object_world(obj, delta)
            moved += 1

        if moved == 0:
            self.report({'WARNING'}, "没有可对齐的非激活物体")
            return {'CANCELLED'}

        mode_name = {
            'CENTROID': "质心点",
            'BOUNDS': "中心点",
            'ORIGIN': "原点",
        }[settings.point_align_mode]
        msg = f"已沿世界 {self.axis} 轴按{mode_name}对齐 {moved} 个物体"
        if skipped:
            msg += f"；跳过 {skipped} 个"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ============================================================
# 功能 5：分散对齐
# ============================================================

class OBJECT_OT_dga_distribute(Operator):
    """
    自动寻找选中物体中心距离最远的一对，以两者连线作为分散轴。

    普通模式：
        保持轴负方向的端点物体不动；
        其余物体按当前顺序沿轴排列；
        相邻物体的包围盒边界间隙 = 用户输入间隙。

    均匀模式：
        保持两个最远端点物体不动；
        优先根据每个物体沿轴方向的实际包围盒宽度，
        计算相等的“边界间隙”并排列中间物体。

        如果首尾之间的空间不足以容纳所有物体宽度，
        则自动退回“包围盒中心点等距”排列，
        避免宽物体造成错误的负间隙计算。
    """
    bl_idname = "object.dga_distribute"
    bl_label = "分散对齐"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        settings = context.scene.dga_settings
        selected = list(context.selected_objects)

        if len(selected) < 2:
            self.report({'WARNING'}, "请至少选择两个物体")
            return {'CANCELLED'}

        depsgraph = context.evaluated_depsgraph_get()

        # 收集有效包围盒及中心。
        items = []
        skipped = 0

        for obj in selected:
            corners = get_world_bbox_corners(obj, depsgraph)
            if not corners:
                skipped += 1
                continue

            center = get_bbox_center(corners)
            items.append((obj, corners, center))

        if len(items) < 2:
            self.report({'WARNING'}, "有效物体不足两个")
            return {'CANCELLED'}

        # 未选择轴时保持原来的三维最远点算法；选择后只比较所选世界轴。
        axis_mask = (
            settings.distribute_x,
            settings.distribute_y,
            settings.distribute_z,
        )
        constrained = any(axis_mask)
        i, j, dist_sq = find_farthest_pair(
            items,
            axis_mask if constrained else None,
        )

        if i is None or j is None or dist_sq <= 1e-16:
            self.report({'WARNING'}, "无法确定分散轴：物体中心在所选轴上的位置重合")
            return {'CANCELLED'}

        center_a = items[i][2]
        center_b = items[j][2]
        axis_delta = center_b - center_a
        if constrained:
            axis_delta = Vector(tuple(
                value if enabled else 0.0
                for value, enabled in zip(axis_delta, axis_mask)
            ))
        axis = axis_delta.normalized()

        # 投影所有物体。
        distribution = []

        for obj, corners, center in items:
            min_v, max_v, center_v = project_bounds(corners, axis)
            distribution.append({
                "obj": obj,
                "min": min_v,
                "max": max_v,
                "center": center_v,
                "width": max_v - min_v,
            })

        # 沿自动轴按中心位置排序。
        distribution.sort(key=lambda item: item["center"])

        count = len(distribution)

        if count == 2 and settings.distribute_uniform:
            self.report({'INFO'}, "仅有两个物体：均匀分散无需移动")
            return {'FINISHED'}

        moved = 0
        used_center_fallback = False

        # ----------------------------------------------------
        # 均匀：固定首尾，根据首尾当前跨度分布中间物体
        # ----------------------------------------------------
        if settings.distribute_uniform:
            first = distribution[0]
            last = distribution[-1]

            internal = distribution[1:-1]

            # 首尾“内侧边界”之间的空间。
            inside_span = last["min"] - first["max"]
            internal_width = sum(item["width"] for item in internal)

            # n 个物体共有 n-1 个边界间隙。
            equal_gap = (
                (inside_span - internal_width) / (count - 1)
                if count > 1 else 0.0
            )

            if equal_gap >= 0.0:
                # 空间足够：真正按物体尺寸计算相同边界间隙。
                cursor = first["max"] + equal_gap

                for item in internal:
                    target_min = cursor
                    delta_scalar = target_min - item["min"]

                    if abs(delta_scalar) > 1e-12:
                        translate_object_world(item["obj"], axis * delta_scalar)
                        moved += 1

                    cursor = target_min + item["width"] + equal_gap

            else:
                # 空间不足：改用包围盒左右宽度中心点等距。
                used_center_fallback = True

                first_center = first["center"]
                last_center = last["center"]
                center_step = (last_center - first_center) / (count - 1)

                for index, item in enumerate(internal, start=1):
                    target_center = first_center + center_step * index
                    delta_scalar = target_center - item["center"]

                    if abs(delta_scalar) > 1e-12:
                        translate_object_world(item["obj"], axis * delta_scalar)
                        moved += 1

        # ----------------------------------------------------
        # 手动间隙：固定第一个端点，其余依次排列
        # ----------------------------------------------------
        else:
            gap = settings.distribute_gap
            first = distribution[0]

            cursor_max = first["max"]

            for item in distribution[1:]:
                target_min = cursor_max + gap
                delta_scalar = target_min - item["min"]

                if abs(delta_scalar) > 1e-12:
                    translate_object_world(item["obj"], axis * delta_scalar)
                    moved += 1

                # 位移后新的 max，可直接由 width 推出。
                cursor_max = target_min + item["width"]

        mode_name = "均匀" if settings.distribute_uniform else f"间隙 {settings.distribute_gap:.4g}"
        axis_name = "自动轴" if not constrained else "".join(
            name for name, enabled in zip("XYZ", axis_mask) if enabled
        )
        msg = f"分散对齐完成：{count} 个物体；{mode_name}；轴向 {axis_name}"

        if used_center_fallback:
            msg += "；空间不足，已使用中心点等距"

        if skipped:
            msg += f"；跳过 {skipped} 个无包围盒物体"

        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ============================================================
# UI
# ============================================================

class WM_OT_dga_toggle_help(Operator):
    """使用方法：\n1. 放置地面/顶端放置分别将最低点/最高点对齐到世界 Z=0。\n2. 轴对齐可按质心点、包围盒中心点或原点，将物体沿世界 X/Y/Z 对齐到激活物体。\n3. 外贴合和内码齐以最后选中的激活物体为目标。\n4. 分散轴 X/Y/Z 全不选时自动使用最远两物体的连线；勾选后限制在所选世界轴。\n5. 均匀分散固定首尾，空间不足时按中心等距。"""
    bl_idname = "wm.dga_toggle_help"
    bl_label = "使用方法"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        settings = context.scene.dga_settings
        settings.show_help = not settings.show_help
        return {'FINISHED'}


class VIEW3D_PT_dga_panel(Panel):
    bl_label = "放置 & 对齐"
    bl_idname = "VIEW3D_PT_dga_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "对齐"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.dga_settings

        # 放置地面
        box = layout.box()
        box.label(text="放置")
        row = box.row(align=True)
        row.scale_y = 1.35
        row.operator("object.dga_drop_to_ground", text="放置地面")
        row.operator("object.dga_place_top", text="顶端放置")
        box.label(text="最低点 / 最高点对齐到世界 Z=0")

        # 对齐
        box = layout.box()
        box.label(text="对齐")

        # 轴选择：独立一整行，避免窄面板挤没
        axis_col = box.column(align=True)
        axis_col.label(text="轴：")
        axis_row = axis_col.row(align=True)
        axis_row.prop(settings, "align_x", text="X", toggle=True)
        axis_row.prop(settings, "align_y", text="Y", toggle=True)
        axis_row.prop(settings, "align_z", text="Z", toggle=True)

        # 坐标空间：独立一整行
        space_col = box.column(align=True)
        space_col.label(text="坐标轴：")
        space_row = space_col.row(align=True)
        space_row.prop(settings, "align_space", expand=True)

        # 两个动作按钮
        action_row = box.row(align=True)
        action_row.scale_y = 1.35
        action_row.operator("object.dga_touch_align", text="外贴合")
        action_row.operator("object.dga_inner_align", text="内码齐")

        # 按物体参考点沿世界坐标轴对齐
        box = layout.box()
        box.label(text="轴对齐")
        box.prop(settings, "point_align_mode", text="基准")
        axis_row = box.row(align=True)
        axis_row.scale_y = 1.35
        for axis_name in "XYZ":
            op = axis_row.operator(
                "object.dga_point_axis_align", text=axis_name
            )
            op.axis = axis_name
        if settings.point_align_mode == 'BOUNDS':
            box.label(text="中心点：世界包围盒的宽度中心")

        # 分散对齐
        box = layout.box()
        box.label(text="分散对齐")

        axis_row = box.row(align=True)
        axis_row.label(text="限制轴向：")
        axis_row.prop(settings, "distribute_x", text="X", toggle=True)
        axis_row.prop(settings, "distribute_y", text="Y", toggle=True)
        axis_row.prop(settings, "distribute_z", text="Z", toggle=True)

        if not any((settings.distribute_x, settings.distribute_y, settings.distribute_z)):
            box.label(text="未选轴：自动使用最远两物体的连线")

        row = box.row(align=True)
        row.prop(settings, "distribute_gap", text="间隙")
        row.prop(settings, "distribute_uniform", text="均匀")

        if settings.distribute_uniform:
            box.label(text="固定最远两端，自动平均分布")
        else:
            box.label(text="按包围盒边界保持输入间隙")

        row = box.row()
        row.scale_y = 1.35
        row.operator("object.dga_distribute", text="分散对齐")

        help_row = layout.row(align=True)
        help_row.alignment = 'RIGHT'
        help_row.operator("wm.dga_toggle_help", text="", icon='QUESTION')

        if settings.show_help:
            info = layout.box()
            info.label(text="使用方法")
            info.label(text="1. 放置：最低点或最高点对齐世界 Z=0")
            info.label(text="2. 轴对齐：按质心/中心/原点沿 XYZ 对齐")
            info.label(text="3. 对齐：最后选中的激活物体作为目标")
            info.label(text="4. 外贴合：两个边界刚好接触")
            info.label(text="5. 内码齐：远端对齐目标最近端点")
            info.separator()
            info.label(text="分散：未选轴时自动使用最远物体连线")
            info.label(text="勾选 X/Y/Z 后，计算和移动限制到所选轴")
            info.label(text="均匀：首尾固定，空间不足则中心等距")


# ============================================================
# 注册 / 注销
# ============================================================

classes = (
    DGA_Settings,
    OBJECT_OT_dga_drop_to_ground,
    OBJECT_OT_dga_place_top,
    OBJECT_OT_dga_touch_align,
    OBJECT_OT_dga_inner_align,
    OBJECT_OT_dga_point_axis_align,
    OBJECT_OT_dga_distribute,
    WM_OT_dga_toggle_help,
    VIEW3D_PT_dga_panel,
)


def register():
    # PropertyGroup 必须先注册
    bpy.utils.register_class(DGA_Settings)

    # 然后把设置挂到 Scene，保证 Panel 绘制时属性已经存在
    bpy.types.Scene.dga_settings = PointerProperty(type=DGA_Settings)

    # 最后注册 Operators 和 Panel
    for cls in classes[1:]:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes[1:]):
        bpy.utils.unregister_class(cls)

    if hasattr(bpy.types.Scene, "dga_settings"):
        del bpy.types.Scene.dga_settings

    bpy.utils.unregister_class(DGA_Settings)


if __name__ == "__main__":
    register()
