import bpy
import math
import mathutils
import json
import bpy_extras
import gpu
import gpu_extras.batch
import copy
import os
import re

# ブレンダーに認識するアドオン情報
bl_info = {
    "name": "レベルエディタ",
    "author": "Taro Kanata",
    "version": (1, 0),
    "blender": (4, 0, 0),
    "location": "",
    "description": "レベルエディタ",
    "warning": "",
    "wiki_url": "",
    "tracker_url": "",
    "category": "Object"
}

# グリッド吸着ハンドラの再入防止フラグ
grid_snap_is_running = False

# 敵プレビューハンドラの再入防止フラグ
enemy_preview_is_running = False

# 前フレーム番号を保持して同一フレームでの重複更新を防ぐ
enemy_preview_last_frame = -1

# 敵プレビューの開始中かどうかを保持する
enemy_preview_running = False
# 敵プレビュー更新用タイマーを保持する
enemy_preview_timer = None

# 敵モデルの参照パス
ENEMY_MODEL_PATH = r"C:\Users\k024g\デスクトップ\CG2\CG2_00_01\project\Resources\enemy\enemy.obj"

# Edit Target で選ばれていないオブジェクトの表示アルファ値
EDIT_TARGET_DIM_ALPHA = 0.2


# 値を指定グリッド幅に吸着する
def snap_value_to_grid(value, grid_size):

    # 0以下だと計算できないので、そのまま返す
    if grid_size <= 0.0:
        return value

    return round(value / grid_size) * grid_size


# ベクトルをグリッドに吸着する
def snap_vector_to_grid(vector, grid_size):

    # 位置ベクトルをコピーして元データを直接壊さない
    snapped = vector.copy()
    snapped.x = snap_value_to_grid(snapped.x, grid_size)
    snapped.y = snap_value_to_grid(snapped.y, grid_size)
    snapped.z = snap_value_to_grid(snapped.z, grid_size)
    return snapped


# 選択中オブジェクトの移動後にグリッド吸着する
def grid_snap_handler(scene, depsgraph):
    global grid_snap_is_running

    # ハンドラの再入を防いで無限更新を避ける
    if grid_snap_is_running:
        return

    # 機能がOFFなら何もしない
    if not scene.myaddon_grid_snap_enabled:
        return

    # グリッド幅が不正なら何もしない
    if scene.myaddon_grid_size <= 0.0:
        return

    # 敵プレビュー移動中は細かい補正でガタつくので吸着しない
    if enemy_preview_running:
        return

    active_object = bpy.context.active_object

    # 編集モード中の座標更新は既存操作とぶつかりやすいので避ける
    if active_object is not None and active_object.mode != 'OBJECT':
        return

    selected_objects = bpy.context.selected_objects

    # 選択オブジェクトが無ければ何もしない
    if not selected_objects:
        return

    grid_snap_is_running = True

    try:
        for object in selected_objects:

            # 位置をグリッド幅に合わせて丸める
            snapped_location = snap_vector_to_grid(
                object.location,
                scene.myaddon_grid_size
            )

            # 既に吸着済みなら余計な更新をしない
            if (object.location - snapped_location).length < 0.0001:
                continue

            object.location = snapped_location

    finally:
        grid_snap_is_running = False


# 連番名の次番号を取得する
def find_next_numbered_name(scene, prefix):

    # 既存オブジェクト名から使用済み番号を集める
    used_numbers = []
    pattern = re.compile(r"^" + re.escape(prefix) + r"(\d+)$")

    for object in scene.objects:
        match = pattern.match(object.name)
        if match is None:
            continue

        used_numbers.append(int(match.group(1)))

    # 未使用の最小番号を返す
    next_number = 0
    while next_number in used_numbers:
        next_number += 1

    return next_number


# 敵ルートオブジェクトかどうかを判定する
def is_enemy_root_object(object):

    return object is not None and bool(object.get("is_enemy_root", False))


def is_enemy_waypoint_object(object):

    # Waypoint は名前規則で判定する
    return object is not None and "_Waypoint_" in object.name


def is_wall_object(object):

    # 壁は保存済みの種類か名前規則で判定する
    if object is None:
        return False

    if object.get("object_kind", "") == "wall":
        return True

    return object.name.startswith("wall_")



def is_nav_mesh_object(object):

    # NavMesh用の見えない歩行エリアかどうかを判定する
    if object is None:
        return False

    if object.get("object_kind", "") == "navmesh":
        return True

    return object.name.startswith("NavMesh")

def is_floor_object(object):

    # 床は保存済みの種類か名前規則で判定する
    if object is None:
        return False


    if is_nav_mesh_object(object):
        return False

    if object.get("object_kind", "") == "floor":
        return True

    if "collider" not in object:
        return False

    if is_wall_object(object):
        return False

    if is_enemy_root_object(object) or is_enemy_waypoint_object(object):
        return False

    if object.get("object_kind", "") == "player" or object.name == "Player":
        return False

    # 既存ステージの床は Cube 名の大きい板として置かれているので優先して拾う
    if object.name in {"Cube", "Plane"}:
        return True

    # file_name が cube / plane で XY に広いものは床扱いにする
    file_name = str(object.get("file_name", ""))
    if file_name in {"cube.obj", "plane.obj"}:
        if abs(object.scale.x) > 2.0 and abs(object.scale.y) > 2.0:
            return True

    if "collider" in object and not is_wall_object(object):
        return True

    return object.name.startswith("floor_")


def get_object_kind(object):

    # 選択フィルター用に編集中の種類を返す
    if object is None:
        return "other"

    if is_enemy_root_object(object) or is_enemy_waypoint_object(object):
        return "enemy"

    if is_wall_object(object):
        return "wall"


    if is_nav_mesh_object(object):
        return "navmesh"

    if is_floor_object(object):
        return "floor"

    if object.get("object_kind", "") == "player" or object.name == "Player":
        return "player"

    return "other"


def refresh_object_kind_tag(object):

    # 既存オブジェクトにも種類タグを補完して選択判定を安定させる
    object_kind = get_object_kind(object)

    if object_kind == "enemy" and not is_enemy_waypoint_object(object):
        object["object_kind"] = "enemy"
    elif object_kind == "wall":
        object["object_kind"] = "wall"
    elif object_kind == "floor":
        object["object_kind"] = "floor"
    elif object_kind == "navmesh":
        object["object_kind"] = "navmesh"
    elif object_kind == "player":
        object["object_kind"] = "player"


def set_object_color_alpha(object, alpha):

    # Preserve the current RGB color and update only the alpha channel
    object.color = (object.color[0], object.color[1], object.color[2], alpha)


def apply_editor_visuals(object):

    # 種類ごとに最低限の見た目を付けて区別しやすくする
    object_kind = get_object_kind(object)

    # Name visibility is controlled by Edit Target, so start hidden here
    object.show_name = False

    if object_kind == "enemy":
        object.show_in_front = True
        object.color = (1.0, 0.35, 0.35, 1.0)

        if is_enemy_waypoint_object(object):
            object.empty_display_size = 0.6

    elif object_kind == "wall":
        # Wall names always stay hidden
        object.show_in_front = True
        object.color = (0.25, 0.65, 1.0, 1.0)

    elif object_kind == "floor":
        object.show_in_front = True
        object.color = (0.9, 0.75, 0.3, 1.0)

    elif object_kind == "player":
        object.show_in_front = True
        object.color = (0.35, 1.0, 0.45, 1.0)


def sync_nav_mesh_visibility(scene):

    # Show NavMesh がOFFならNavMeshを非表示にして選択も外す
    show_nav_mesh = bool(scene.myaddon_show_nav_mesh)

    for object in scene.objects:
        if not is_nav_mesh_object(object):
            continue

        object.hide_viewport = not show_nav_mesh
        object.hide_render = not show_nav_mesh
        object.hide_set(not show_nav_mesh)

        if not show_nav_mesh:
            object.hide_select = True
            if object.select_get():
                object.select_set(False)

            if bpy.context.view_layer.objects.active == object:
                bpy.context.view_layer.objects.active = None


def on_show_nav_mesh_changed(self, context):

    # UIでNavMesh表示を切り替えた瞬間に3Dビューへ反映する
    sync_nav_mesh_visibility(context.scene)
    sync_object_selection_filter(context.scene)

    if context.screen is not None:
        for area in context.screen.areas:
            area.tag_redraw()


def sync_object_selection_filter(scene):

    # 編集対象に応じて選択できる種類だけ残す
    edit_target = scene.myaddon_edit_target

    for object in scene.objects:
        refresh_object_kind_tag(object)
        object_kind = get_object_kind(object)
        is_nav_mesh_hidden = object_kind == "navmesh" and not scene.myaddon_show_nav_mesh
        is_editable_object = object_kind in {"enemy", "wall", "floor", "player", "navmesh"}

        if is_nav_mesh_hidden:
            object.show_name = False
            set_object_color_alpha(object, EDIT_TARGET_DIM_ALPHA)
            object.hide_select = True
            if object.select_get():
                object.select_set(False)
            continue

        # Show names only for the object kind matched by Edit Target
        # Keep wall names hidden because they clutter the view
        if object_kind == "wall":
            object.show_name = False
        elif edit_target == "ALL":
            object.show_name = is_editable_object
        else:
            object.show_name = (object_kind == edit_target.lower())

        # Dim objects outside the current Edit Target to make the focus clearer
        if edit_target == "ALL":
            set_object_color_alpha(object, 1.0)
        elif object_kind == edit_target.lower():
            set_object_color_alpha(object, 1.0)
        else:
            set_object_color_alpha(object, EDIT_TARGET_DIM_ALPHA)

        # ALL のときはロックを解除する
        if edit_target == "ALL":
            if is_editable_object:
                object.hide_select = False
            continue

        allow_select = (object_kind == edit_target.lower())
        object.hide_select = not allow_select

        # 別種類が選ばれていたら外して、動かせない状態にそろえる
        if not allow_select and object.select_get():
            object.select_set(False)

            if bpy.context.view_layer.objects.active == object:
                bpy.context.view_layer.objects.active = None


def on_edit_target_changed(self, context):

    # UI で編集対象を切り替えた瞬間に選択制限を反映する
    sync_object_selection_filter(context.scene)

    # ビューを更新して選択ロックの変化を見えやすくする
    if context.screen is not None:
        for area in context.screen.areas:
            area.tag_redraw()


def selection_filter_handler(scene, depsgraph):

    # オブジェクト追加や複製の後でも選択制限が崩れないように保つ
    sync_object_selection_filter(scene)
# 選択中から敵ルートオブジェクトを取得する
def get_selected_enemy_root(context):
    active_object = context.active_object

    # 敵本体が選択されている場合
    if is_enemy_root_object(active_object):
        return active_object

    # Waypoint が選択されている場合は名前から親の敵本体を探す
    if active_object is not None and "_Waypoint_" in active_object.name:
        enemy_name = active_object.name.split("_Waypoint_")[0]
        enemy_object = bpy.data.objects.get(enemy_name)
        if is_enemy_root_object(enemy_object):
            return enemy_object

    return None


# 敵に Waypoint を追加する
def add_enemy_waypoint(context, enemy_object):

    # 既存の Waypoint を名前順で集める
    waypoint_objects = []
    waypoint_prefix = enemy_object.name + "_Waypoint_"
    pattern = re.compile(r"^" + re.escape(waypoint_prefix) + r"(\d+)$")

    for object in context.scene.objects:
        if pattern.match(object.name) is None:
            continue
        waypoint_objects.append(object)

    waypoint_objects.sort(key=lambda object: object.name)
    waypoint_index = len(waypoint_objects)
    waypoint_name = waypoint_prefix + f"{waypoint_index:02d}"

    # Waypoint を Empty として追加する
    if len(waypoint_objects) == 0:
        waypoint_location = enemy_object.location.copy()
    else:
        waypoint_location = waypoint_objects[-1].location + mathutils.Vector((2.0, 0.0, 0.0))

    bpy.ops.object.empty_add(
        type='ARROWS',
        location=waypoint_location
    )
    waypoint_object = context.active_object

    # 見やすい設定を入れる
    waypoint_object.name = waypoint_name
    waypoint_object.empty_display_size = 0.6
    waypoint_object.show_in_front = True
    waypoint_object["object_kind"] = "enemy_waypoint"
    apply_editor_visuals(waypoint_object)

    return waypoint_object


# 敵に対応する Waypoint を名前順で取得する
def get_enemy_waypoints(scene, enemy_object):

    # Enemy_00_Waypoint_00 のような連番だけを対象にする
    waypoint_objects = []
    waypoint_prefix = enemy_object.name + "_Waypoint_"
    pattern = re.compile(r"^" + re.escape(waypoint_prefix) + r"(\d+)$")

    for object in scene.objects:
        if pattern.match(object.name) is None:
            continue
        waypoint_objects.append(object)

    # 巡回順が毎回変わらないように名前順で固定する
    waypoint_objects.sort(key=lambda object: object.name)
    return waypoint_objects


# 敵プレビュー用の内部状態を初期化する
def ensure_enemy_preview_state(enemy_object, scene):

    # Waypoint の巡回開始位置を先頭にそろえる
    if "preview_waypoint_index" not in enemy_object:
        enemy_object["preview_waypoint_index"] = 0

# 1体の敵を Waypoint に向かって1フレーム分だけ動かす
def update_enemy_preview_object(scene, enemy_object):

    waypoint_objects = get_enemy_waypoints(scene, enemy_object)

    # Waypoint が無い敵は動かしようがないので何もしない
    if len(waypoint_objects) == 0:
        return

    ensure_enemy_preview_state(enemy_object, scene)

    waypoint_index = int(enemy_object["preview_waypoint_index"])
    waypoint_index %= len(waypoint_objects)
    # 毎フレーム現在のシーン設定を読むことで速度変更を即反映する
    move_speed = max(0.001, float(scene.myaddon_enemy_preview_speed))

    # 重なっている Waypoint は飛ばして、実際に向かう目標を探す
    target_object = None
    target_position = None
    move_vector = None
    distance = 0.0

    for waypoint_offset in range(len(waypoint_objects)):
        check_index = (waypoint_index + waypoint_offset) % len(waypoint_objects)
        check_object = waypoint_objects[check_index]
        check_position = check_object.location.copy()
        check_vector = check_position - enemy_object.location
        check_distance = check_vector.length

        # 少しでも離れている Waypoint を実際の目標にする
        if check_distance >= 0.05:
            waypoint_index = check_index
            target_object = check_object
            target_position = check_position
            move_vector = check_vector
            distance = check_distance
            enemy_object["preview_waypoint_index"] = waypoint_index
            break

    # 全Waypointがほぼ同じ場所なら動かしようがないので終了
    if target_object is None:
        return

    # 1フレーム分だけ進めて通り過ぎないように制限する
    move_direction = move_vector.normalized()
    move_step = min(move_speed, distance)
    enemy_object.location += move_direction * move_step

    # 到着したら次の Waypoint を次回の目標にする
    if distance <= move_speed + 0.05:
        enemy_object["preview_waypoint_index"] = (waypoint_index + 1) % len(waypoint_objects)

    # 移動方向へ向きを合わせて確認しやすくする
    enemy_object.rotation_mode = 'XYZ'
    enemy_object.rotation_euler.z = math.atan2(move_direction.y, move_direction.x)


# 再生中の敵プレビュー移動をまとめて更新する
def update_enemy_preview(scene):

    for object in scene.objects:

        # 敵本体だけを Waypoint に沿って動かす
        if not is_enemy_root_object(object):
            continue

        update_enemy_preview_object(scene, object)


# アニメーション再生中に敵プレビュー移動を更新する
def enemy_preview_handler(scene, depsgraph=None):
    global enemy_preview_is_running
    global enemy_preview_last_frame

    # ハンドラの再入を防いで無限更新を避ける
    if enemy_preview_is_running:
        return

    # 同じフレームで複数回呼ばれても1回だけ更新する
    if enemy_preview_last_frame == scene.frame_current:
        return

    enemy_preview_is_running = True

    try:
        update_enemy_preview(scene)
        enemy_preview_last_frame = scene.frame_current
    finally:
        enemy_preview_is_running = False


# 敵プレビュー状態を初期化して開始位置をそろえる
def reset_enemy_preview_objects(scene):

    for object in scene.objects:

        # 敵本体だけに初期化を入れる
        if not is_enemy_root_object(object):
            continue

        object["preview_waypoint_index"] = 0
        waypoint_objects = get_enemy_waypoints(scene, object)

        # 最初の Waypoint があれば開始位置をそこへそろえる
        if len(waypoint_objects) > 0:
            object.location = waypoint_objects[0].location.copy()


# 敵プレビューを手動リセットしたときに向きも初期状態へそろえる
def reset_enemy_preview_rotation(scene):

    for object in scene.objects:

        # 敵本体だけ向きを初期化する
        if not is_enemy_root_object(object):
            continue

        object.rotation_mode = 'XYZ'
        object.rotation_euler = mathutils.Euler((0.0, 0.0, 0.0), 'XYZ')


# プレビュー対象になる敵が存在するか数える
def count_preview_ready_enemies(scene):

    ready_enemy_count = 0

    for object in scene.objects:
        if not is_enemy_root_object(object):
            continue

        waypoint_objects = get_enemy_waypoints(scene, object)
        if len(waypoint_objects) >= 2:
            ready_enemy_count += 1

    return ready_enemy_count


# 読み込み直後のオブジェクト群からメッシュ本体を優先して取得する
def find_primary_imported_object(imported_objects):

    # 見た目の本体として使いたいので MESH を優先する
    for object in imported_objects:
        if object.type == 'MESH':
            return object

    # メッシュが無ければ先頭を返して既存動作を保つ
    if len(imported_objects) > 0:
        return imported_objects[0]

    return None


# オブジェクト名とメッシュ名をそろえて Blender 上でも分かりやすくする
def rename_imported_object(object, object_name):

    object.name = object_name

    # メッシュ本体も同じ名前にして Cube 表示が残らないようにする
    if object.type == 'MESH' and object.data is not None:
        object.data.name = object_name


# 読み込んだ複数オブジェクトをまとめて分かりやすい名前にそろえる
def rename_imported_object_group(imported_objects, primary_object, root_name):

    # 本体はそのままルート名にする
    rename_imported_object(primary_object, root_name)

    child_index = 0
    for object in imported_objects:

        # 本体はすでに名前を付けたので飛ばす
        if object == primary_object:
            continue

        # 付随オブジェクトも Cube のまま残らないように連番で付け直す
        child_name = f"{root_name}_Part_{child_index:02d}"
        rename_imported_object(object, child_name)
        child_index += 1


# 読み込んだメッシュ群を1つにまとめて配置しやすくする
def merge_imported_mesh_objects(context, imported_objects):

    mesh_objects = []
    for object in imported_objects:
        if object.type == 'MESH':
            mesh_objects.append(object)

    # メッシュが無ければ今のまま返す
    if len(mesh_objects) == 0:
        return find_primary_imported_object(imported_objects)

    # 1個だけなら結合せずそのまま使う
    if len(mesh_objects) == 1:
        return mesh_objects[0]

    # メッシュだけを選択して1オブジェクトに結合する
    bpy.ops.object.select_all(action='DESELECT')
    for object in mesh_objects:
        object.select_set(True)

    context.view_layer.objects.active = mesh_objects[0]
    bpy.ops.object.join()
    return context.view_layer.objects.active


# インポート前後の差分から新規オブジェクトを取得する
def get_new_imported_objects(scene, object_names_before):

    imported_objects = []

    for object in scene.objects:
        if object.name in object_names_before:
            continue
        imported_objects.append(object)

    return imported_objects


# 敵モデルを読み込んで敵本体を作成する
def create_enemy_from_model(context, enemy_type_name):

    # 参照モデルが無ければ作成できない
    if not os.path.exists(ENEMY_MODEL_PATH):
        raise FileNotFoundError(ENEMY_MODEL_PATH)

    enemy_index = find_next_numbered_name(context.scene, "Enemy_")
    enemy_name = f"Enemy_{enemy_index:02d}"

    # 読み込み対象だけ選択状態にする
    bpy.ops.object.select_all(action='DESELECT')
    object_names_before = set()
    for object in context.scene.objects:
        object_names_before.add(object.name)

    # 実際の敵モデルを読み込む
    bpy.ops.wm.obj_import(filepath=ENEMY_MODEL_PATH)
    imported_objects = get_new_imported_objects(context.scene, object_names_before)

    if len(imported_objects) == 0:
        raise RuntimeError("enemy.obj の読み込みに失敗しました")

    # 読み込んだメッシュ群を1つの敵本体にまとめる
    enemy_object = merge_imported_mesh_objects(context, imported_objects)
    if enemy_object is None:
        raise RuntimeError("enemy.obj の本体オブジェクトを取得できませんでした")
    rename_imported_object(enemy_object, enemy_name)

    # Move the enemy slightly above the 3D cursor location.
    enemy_location = context.scene.cursor.location.copy()
    enemy_location.z += 1.0
    enemy_object.location = enemy_location
    enemy_object.hide_select = False
    enemy_object.lock_location[0] = False
    enemy_object.lock_location[1] = False
    enemy_object.lock_location[2] = False

    # JSON 側で使う識別情報を付ける
    enemy_object["file_name"] = "enemy/enemy.obj"
    enemy_object["enemy_type"] = enemy_type_name
    enemy_object["is_enemy_root"] = True
    enemy_object["object_kind"] = "enemy"
    enemy_object["preview_waypoint_index"] = 0
    apply_editor_visuals(enemy_object)

    # 追加直後に初期 Waypoint を1つ作る
    add_enemy_waypoint(context, enemy_object)

    # 敵本体を選択し直す
    bpy.ops.object.select_all(action='DESELECT')
    enemy_object.select_set(True)
    context.view_layer.objects.active = enemy_object
    sync_object_selection_filter(context.scene)

    return enemy_object

# オペレータ 頂点を伸ばす
class MYADDON_OT_stretch_vertex(bpy.types.Operator):

    bl_idname = "myaddon.myaddon_ot_stretch_vertex"
    bl_label = "頂点を伸ばす"
    bl_description = "頂点座標を引っ張って伸ばします"
    bl_options = {'REGISTER', 'UNDO'}

    # メニューを実行したときに呼ばれるコールバック関数
    def execute(self, context):

        bpy.data.objects["Cube"].data.vertices[0].co.x += 1.0
        print("頂点を伸ばしました。")

        # オペレータの命令終了を通知
        return {'FINISHED'}

# オペレータ ICO球生成
class MYADDON_OT_create_ico_sphere(bpy.types.Operator):

    bl_idname = "myaddon.myaddon_ot_create_object"
    bl_label = "ICO球生成"
    bl_description = "ICO球を生成します"
    bl_options = {'REGISTER', 'UNDO'}

    # メニューを実行したときに呼ばれる関数
    def execute(self, context):

        bpy.ops.mesh.primitive_ico_sphere_add()
        print("ICO球を生成しました。")

        return {'FINISHED'}


# オペレータ アドオン再読み込み
class MYADDON_OT_reload_addon(bpy.types.Operator):

    bl_idname = "myaddon.reload_addon"
    bl_label = "Reload Addon"
    bl_description = "レベルエディタを再読み込みします"
    bl_options = {'REGISTER'}

    # ボタン実行時にスクリプト再読み込みを行う
    def execute(self, context):

        # Blenderのスクリプトを再読み込みして変更を反映する
        bpy.ops.script.reload()
        return {'FINISHED'}


# オペレータ 通常敵追加
class MYADDON_OT_add_normal_enemy(bpy.types.Operator):

    bl_idname = "myaddon.add_normal_enemy"
    bl_label = "Add Enemy"
    bl_description = "enemy.obj を使って通常敵を追加します"
    bl_options = {'REGISTER', 'UNDO'}

    # ボタン実行時に通常敵を追加する
    def execute(self, context):
        try:
            create_enemy_from_model(context, "NormalEnemy")
        except Exception as exception:
            self.report({'ERROR'}, str(exception))
            return {'CANCELLED'}

        return {'FINISHED'}


# オペレータ Waypoint 追加
class MYADDON_OT_add_enemy_waypoint(bpy.types.Operator):

    bl_idname = "myaddon.add_enemy_waypoint"
    bl_label = "Add Waypoint"
    bl_description = "選択中の敵に Waypoint を追加します"
    bl_options = {'REGISTER', 'UNDO'}

    # ボタン実行時に Waypoint を追加する
    def execute(self, context):
        enemy_object = get_selected_enemy_root(context)

        # 敵本体が選択されていないと追加できない
        if enemy_object is None:
            self.report({'ERROR'}, "Enemy_00 形式の敵本体か、その子 Waypoint を選択してください")
            return {'CANCELLED'}

        add_enemy_waypoint(context, enemy_object)
        return {'FINISHED'}


# オペレータ 敵プレビュー開始
class MYADDON_OT_start_enemy_preview(bpy.types.Operator):

    bl_idname = "myaddon.start_enemy_preview"
    bl_label = "Start Preview"
    bl_description = "Waypoint に沿った敵の確認用プレビューを開始します"
    bl_options = {'REGISTER'}

    # モーダル更新で敵プレビューを進める
    def modal(self, context, event):
        global enemy_preview_running
        global enemy_preview_timer

        # 停止要求が入っていたらタイマーを破棄して終了する
        if not enemy_preview_running:
            if enemy_preview_timer is not None:
                context.window_manager.event_timer_remove(enemy_preview_timer)
                enemy_preview_timer = None
            return {'CANCELLED'}

        # タイマーイベントのたびに敵を少しずつ進める
        if event.type == 'TIMER':
            update_enemy_preview(context.scene)
            return {'PASS_THROUGH'}

        return {'PASS_THROUGH'}

    # ボタン実行時に専用プレビューを開始する
    def execute(self, context):
        global enemy_preview_running
        global enemy_preview_timer

        # 既に動作中なら二重起動しない
        if enemy_preview_running:
            self.report({'INFO'}, "Enemy preview is already running")
            return {'FINISHED'}

        # 動ける敵がいないときは原因が分かるように止める
        if count_preview_ready_enemies(context.scene) == 0:
            self.report({'WARNING'}, "Enemy と離れた Waypoint を2個以上用意してください")
            return {'CANCELLED'}

        enemy_preview_running = True
        enemy_preview_timer = context.window_manager.event_timer_add(0.03, window=context.window)
        context.window_manager.modal_handler_add(self)
        self.report({'INFO'}, "Enemy preview started")
        return {'RUNNING_MODAL'}


# オペレータ 敵プレビュー停止
class MYADDON_OT_stop_enemy_preview(bpy.types.Operator):

    bl_idname = "myaddon.stop_enemy_preview"
    bl_label = "Stop Preview"
    bl_description = "Waypoint に沿った敵の確認用プレビューを停止します"
    bl_options = {'REGISTER'}

    # ボタン実行時に専用プレビューを停止する
    def execute(self, context):
        global enemy_preview_running
        global enemy_preview_timer

        enemy_preview_running = False

        # 停止ボタン側からもタイマーを必ず解除する
        if enemy_preview_timer is not None:
            context.window_manager.event_timer_remove(enemy_preview_timer)
            enemy_preview_timer = None

        self.report({'INFO'}, "Enemy preview stopped")
        return {'FINISHED'}


# オペレータ 敵プレビューリセット
class MYADDON_OT_reset_enemy_preview(bpy.types.Operator):

    bl_idname = "myaddon.reset_enemy_preview"
    bl_label = "Reset Preview"
    bl_description = "敵プレビュー位置を最初の Waypoint に戻します"
    bl_options = {'REGISTER'}

    # ボタン実行時に敵プレビュー位置を先頭へ戻す
    def execute(self, context):

        reset_enemy_preview_objects(context.scene)
        reset_enemy_preview_rotation(context.scene)
        self.report({'INFO'}, "Enemy preview reset")
        return {'FINISHED'}


# オペレータ PlayerSpawn 追加
class MYADDON_OT_add_player_spawn(bpy.types.Operator):

    bl_idname = "myaddon.add_player_spawn"
    bl_label = "Add Player"
    bl_description = "Add player start object from player.obj"
    bl_options = {'REGISTER', 'UNDO'}

    # Add Player object from player.obj.
    def execute(self, context):

        # Reuse existing Player object if it already exists.
        existing_object = bpy.data.objects.get("Player")
        if existing_object is not None:
            bpy.ops.object.select_all(action='DESELECT')
            existing_object.select_set(True)
            context.view_layer.objects.active = existing_object
            apply_editor_visuals(existing_object)
            sync_object_selection_filter(context.scene)
            self.report({'INFO'}, "Player already exists")
            return {'FINISHED'}

        # Fixed path to player.obj in this project.
        player_model_path = (
            r"C:\Users\k024g\デスクトップ\CG2\CG2_00_01"
            r"\project\Resources\player\player.obj"
        )

        # Stop if player.obj does not exist.
        if not os.path.exists(player_model_path):
            self.report({'ERROR'}, player_model_path)
            return {'CANCELLED'}

        # Clear selection before import.
        bpy.ops.object.select_all(action='DESELECT')
        object_names_before = set()
        for object in context.scene.objects:
            object_names_before.add(object.name)

        # Import player.obj.
        bpy.ops.wm.obj_import(filepath=player_model_path)
        imported_objects = get_new_imported_objects(context.scene, object_names_before)

        if len(imported_objects) == 0:
            self.report({'ERROR'}, "Failed to import player.obj")
            return {'CANCELLED'}

        # Use the merged mesh object as the player start object.
        player_object = merge_imported_mesh_objects(context, imported_objects)
        if player_object is None:
            self.report({'ERROR'}, "Failed to find imported player object")
            return {'CANCELLED'}
        rename_imported_object(player_object, "Player")

        # Store metadata used by the game side.
        player_object["file_name"] = "player/player.obj"
        player_object["object_kind"] = "player"
        apply_editor_visuals(player_object)

        # Move the object slightly above the 3D cursor location.
        player_location = context.scene.cursor.location.copy()
        player_location.z += 1.0
        player_object.location = player_location

        # Select the imported player object.
        bpy.ops.object.select_all(action='DESELECT')
        player_object.select_set(True)
        context.view_layer.objects.active = player_object
        sync_object_selection_filter(context.scene)

        return {'FINISHED'}

# オペレーター: シーン出力


class MYADDON_OT_generate_nav_mesh(bpy.types.Operator):
    bl_idname = "myaddon.generate_nav_mesh"
    bl_label = "Generate NavMesh"
    bl_description = "床と壁から、壁を避けたNavMeshを自動生成します"

    def get_world_bounds_xy(self, object):
        # オブジェクトのワールド座標でのXY範囲を求める
        world_points = [object.matrix_world @ mathutils.Vector(corner) for corner in object.bound_box]
        min_x = min(point.x for point in world_points)
        max_x = max(point.x for point in world_points)
        min_y = min(point.y for point in world_points)
        max_y = max(point.y for point in world_points)
        return min_x, max_x, min_y, max_y

    def is_point_inside_rect(self, x, y, rect):
        # 点が矩形の中にあるか調べる
        min_x, max_x, min_y, max_y = rect
        return min_x <= x <= max_x and min_y <= y <= max_y

    def execute(self, context):
        # NavMeshを壁から離す距離
        wall_margin = 0.5

        # 生成するNavMeshの細かさ
        cell_size = 1.0

        floor_rects = []
        wall_rects = []

        for object in context.scene.objects:
            if object.type != "MESH":
                continue

            if is_nav_mesh_object(object):
                continue

            if is_wall_object(object):
                min_x, max_x, min_y, max_y = self.get_world_bounds_xy(object)
                wall_rects.append((
                    min_x - wall_margin,
                    max_x + wall_margin,
                    min_y - wall_margin,
                    max_y + wall_margin
                ))
                continue

            if is_floor_object(object):
                floor_rects.append(self.get_world_bounds_xy(object))

        if len(floor_rects) == 0:
            self.report({'WARNING'}, "床オブジェクトが見つかりませんでした")
            return {'CANCELLED'}

        # 古い自動生成NavMeshを消してから作り直す
        for object in list(context.scene.objects):
            if object.name.startswith("NavMesh_Auto"):
                bpy.data.objects.remove(object, do_unlink=True)

        vertices = []
        faces = []
        z = 0.05

        for floor_rect in floor_rects:
            floor_min_x, floor_max_x, floor_min_y, floor_max_y = floor_rect

            x = floor_min_x
            while x + cell_size <= floor_max_x:
                y = floor_min_y
                while y + cell_size <= floor_max_y:
                    center_x = x + cell_size * 0.5
                    center_y = y + cell_size * 0.5

                    blocked = False
                    for wall_rect in wall_rects:
                        if self.is_point_inside_rect(center_x, center_y, wall_rect):
                            blocked = True
                            break

                    if not blocked:
                        vertex_index = len(vertices)
                        vertices.append((x, y, z))
                        vertices.append((x + cell_size, y, z))
                        vertices.append((x + cell_size, y + cell_size, z))
                        vertices.append((x, y + cell_size, z))
                        faces.append((
                            vertex_index,
                            vertex_index + 1,
                            vertex_index + 2,
                            vertex_index + 3
                        ))

                    y += cell_size
                x += cell_size

        if len(faces) == 0:
            self.report({'WARNING'}, "NavMeshを生成できませんでした")
            return {'CANCELLED'}

        mesh = bpy.data.meshes.new("NavMesh_Auto_Mesh")
        mesh.from_pydata(vertices, [], faces)
        mesh.update()

        nav_mesh_object = bpy.data.objects.new("NavMesh_Auto", mesh)
        context.collection.objects.link(nav_mesh_object)

        # Export時にNavMeshとして扱うためのタグを付ける
        nav_mesh_object["object_kind"] = "navmesh"
        nav_mesh_object.show_in_front = True
        nav_mesh_object.show_name = True
        nav_mesh_object.color = (0.0, 1.0, 0.25, 0.65)

        # 見つけやすい緑色のマテリアルを付ける
        nav_mesh_material = bpy.data.materials.get("NavMesh_Visible_Green")
        if nav_mesh_material is None:
            nav_mesh_material = bpy.data.materials.new("NavMesh_Visible_Green")
        nav_mesh_material.diffuse_color = (0.0, 1.0, 0.25, 0.65)
        nav_mesh_object.data.materials.append(nav_mesh_material)

        bpy.ops.object.select_all(action='DESELECT')
        nav_mesh_object.select_set(True)
        context.view_layer.objects.active = nav_mesh_object

        self.report({'INFO'}, f"NavMeshを生成しました: {len(faces)}マス")
        return {'FINISHED'}


class MYADDON_OT_add_nav_mesh(bpy.types.Operator):
    bl_idname = "myaddon.add_nav_mesh"
    bl_label = "Add NavMesh"
    bl_description = "NavMesh用のPlaneを作成します。作成後に移動や編集をしてください"

    def execute(self, context):
        # NavMesh編集用のPlaneを作成する
        bpy.ops.mesh.primitive_plane_add(
            size=4.0,
            enter_editmode=False,
            align='WORLD',
            location=(0.0, 0.0, 0.05)
        )

        nav_mesh_object = context.object

        # 最初はNavMesh、2個目以降は番号付きの名前にする
        if bpy.data.objects.get("NavMesh") is None:
            nav_mesh_object.name = "NavMesh"
        else:
            nav_mesh_index = find_next_numbered_name(context.scene, "NavMesh_")
            nav_mesh_object.name = f"NavMesh_{nav_mesh_index:02d}"

        # Export Sceneでnav_mesh欄へ出力されるようにタグを付ける
        nav_mesh_object["object_kind"] = "navmesh"
        nav_mesh_object.show_in_front = True
        nav_mesh_object.show_name = True
        nav_mesh_object.color = (0.0, 1.0, 0.25, 0.65)

        # 3Dビューで見つけやすいように明るい緑色のマテリアルを付ける
        nav_mesh_material = bpy.data.materials.get("NavMesh_Visible_Green")
        if nav_mesh_material is None:
            nav_mesh_material = bpy.data.materials.new("NavMesh_Visible_Green")
            nav_mesh_material.diffuse_color = (0.0, 1.0, 0.25, 0.65)
        else:
            nav_mesh_material.diffuse_color = (0.0, 1.0, 0.25, 0.65)

        nav_mesh_object.data.materials.clear()
        nav_mesh_object.data.materials.append(nav_mesh_material)

        # 作成したNavMeshをすぐ動かせるように選択状態にする
        bpy.ops.object.select_all(action='DESELECT')
        nav_mesh_object.select_set(True)
        context.view_layer.objects.active = nav_mesh_object

        return {'FINISHED'}


class MYADDON_OT_add_wall(bpy.types.Operator):

    bl_idname = "myaddon.add_wall"
    bl_label = "Add Wall"
    bl_description = "Add 1x1 wall block with file_name and collider"
    bl_options = {'REGISTER', 'UNDO'}

    # Add a wall block with the properties required for JSON export.
    def execute(self, context):

        wall_index = find_next_numbered_name(context.scene, "wall_")
        wall_name = f"wall_{wall_index:02d}"

        # Add a normal cube slightly above the 3D cursor location.
        wall_location = context.scene.cursor.location.copy()
        wall_location.z += 1.0
        bpy.ops.mesh.primitive_cube_add(location=wall_location)
        wall_object = context.active_object

        # Set a clear wall name.
        wall_object.name = wall_name

        # Store the model and collider data used by the game side.
        wall_object["file_name"] = "cube.obj"
        wall_object["collider"] = "BOX"
        wall_object["collider_center"] = mathutils.Vector((0, 0, 0))
        wall_object["collider_size"] = mathutils.Vector((2, 2, 2))
        wall_object["object_kind"] = "wall"
        apply_editor_visuals(wall_object)
        sync_object_selection_filter(context.scene)

        return {'FINISHED'}

def requires_collider(object):
    # collider が必須の種類だけ True を返す
    object_kind = get_object_kind(object)
    return object_kind in {"wall", "floor"}


def requires_file_name(object):
    # file_name が必須の種類だけ True を返す
    if is_enemy_root_object(object):
        return True

    object_kind = get_object_kind(object)
    return object_kind in {"wall", "floor", "player"}


def validate_level_scene(scene):
    # Export 前の検証結果を文字列リストで返す
    issues = []
    player_objects = []
    object_name_counts = {}

    for object in scene.objects:
        # 同名オブジェクトの検出用に名前を数える
        object_name_counts[object.name] = object_name_counts.get(object.name, 0) + 1

    for object_name, object_count in object_name_counts.items():
        # 同名オブジェクトがあると参照ミスの原因になる
        if object_count > 1:
            issues.append(f"同名オブジェクトがあります: {object_name}")

    for object in scene.objects:
        # オブジェクト種別を最新状態にそろえる
        refresh_object_kind_tag(object)
        object_kind = get_object_kind(object)

        # Player の存在確認用に保持する
        if object_kind == "player":
            player_objects.append(object)

        # Enemy 本体には Waypoint が必要
        if is_enemy_root_object(object):
            waypoint_objects = get_enemy_waypoints(scene, object)

            # Waypoint が1つも無い敵は巡回経路を作れないのでエラーにする
            if len(waypoint_objects) == 0:
                issues.append(f"{object.name}: Waypoint がありません")



            waypoint_numbers = []

            for waypoint_object in waypoint_objects:
                waypoint_parts = waypoint_object.name.split("_Waypoint_")

                # 名前形式が壊れている Waypoint は、この後の個別チェック側に任せる
                if len(waypoint_parts) != 2:
                    continue

                try:
                    waypoint_numbers.append(int(waypoint_parts[1]))
                except ValueError:
                    issues.append(f"{waypoint_object.name}: Waypoint 番号が数字ではありません")

            waypoint_numbers.sort()

            # Waypoint_00, Waypoint_01, Waypoint_02... のように途中が抜けていないか確認する
            for expected_index, actual_index in enumerate(waypoint_numbers):
                if actual_index != expected_index:
                    issues.append(f"{object.name}: Waypoint_{expected_index:02d} がありません")
                    break

        # Waypoint の命名と親子関係が崩れていないか確認する
        if is_enemy_waypoint_object(object):
            waypoint_parts = object.name.split("_Waypoint_")

            if len(waypoint_parts) != 2:
                issues.append(f"{object.name}: Waypoint 名が不正です")
            else:
                enemy_name = waypoint_parts[0]
                enemy_object = scene.objects.get(enemy_name)

                

        # file_name が必要なものだけ未設定を確認する
        if requires_file_name(object):
            if str(object.get("file_name", "")).strip() == "":
                issues.append(f"{object.name}: file_name が未設定です")

        # collider が必要なものに付いているか確認する
        if requires_collider(object):
            if "collider" not in object:
                issues.append(f"{object.name}: collider が未設定です")

        # collider が付いている場合は center と size の中身も確認する
        if "collider" in object:
            if "collider_center" not in object:
                issues.append(f"{object.name}: collider_center が未設定です")

            if "collider_size" not in object:
                issues.append(f"{object.name}: collider_size が未設定です")
            else:
                collider_size = object["collider_size"]
                if collider_size[0] <= 0.0 or collider_size[1] <= 0.0 or collider_size[2] <= 0.0:
                    issues.append(f"{object.name}: collider_size は 0 より大きくしてください")

        # 壁と床はスケール 0 だと不正データになりやすい
        if object_kind in {"wall", "floor"}:
            if object.scale.x == 0.0 or object.scale.y == 0.0 or object.scale.z == 0.0:
                issues.append(f"{object.name}: scale に 0 が含まれています")

    # Player は 1 個だけ必要
    if len(player_objects) == 0:
        issues.append("Player オブジェクトがありません")
    elif len(player_objects) > 1:
        issues.append(f"Player オブジェクトが複数あります: {len(player_objects)} 個")

    return issues


def update_validation_result(scene, issues):
    # パネルに表示する検証結果文字列を更新する
    if len(issues) == 0:
        scene.myaddon_validation_status = "検証OK"
        scene.myaddon_validation_details = "問題は見つかりませんでした。"
    else:
        scene.myaddon_validation_status = f"検証NG: {len(issues)}件"
        scene.myaddon_validation_details = "\n".join(issues)


class MYADDON_OT_validate_scene(bpy.types.Operator):
    bl_idname = "myaddon.validate_scene"
    bl_label = "Validate Scene"
    bl_description = "Export 前の検証を行い、問題一覧を表示します"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # シーンを検証して結果をパネルへ反映する
        issues = validate_level_scene(context.scene)
        update_validation_result(context.scene, issues)

        if len(issues) > 0:
            self.report({'WARNING'}, f"検証に失敗しました: {len(issues)}件")
            return {'CANCELLED'}

        self.report({'INFO'}, "検証OKです")
        return {'FINISHED'}

class MYADDON_OT_export_scene(
    bpy.types.Operator,
    bpy_extras.io_utils.ExportHelper):

    bl_idname = "myaddon.myaddon_ot_export_scene"
    bl_label = "シーン出力"
    bl_description = "シーン情報をExportします"

    # 出力するファイルの拡張子
    filename_ext = ".json"

    # 文字列を書き込み + 改行
    def write_and_print(self, file, str):

        print(str)

        file.write(str)
        file.write('\n')

    # シーン解析用再帰関数
    def parse_scene_recursive(self, file, object, level):

        # 再帰インデントする (タブを挿入)
        indent = ""
        for i in range(level):
            indent += '\t'

        # オブジェクト種類を書き込む
        self.write_and_print(file, indent + object.type)

        trans, rot, scale = object.matrix_local.decompose()

        # 回転を Quaternion から Euler (3軸での回転角) に変換
        rot = rot.to_euler()

        # ラジアンから度数法に変換
        rot.x = math.degrees(rot.x)
        rot.y = math.degrees(rot.y)
        rot.z = math.degrees(rot.z)

        # トランスフォーム情報を表示
        self.write_and_print(file,
            indent + "T %f %f %f" %
            (trans.x, trans.y, trans.z))

        self.write_and_print(file,
            indent + "R %f %f %f" %
            (rot.x, rot.y, rot.z))

        self.write_and_print(file,
            indent + "S %f %f %f" %
            (scale.x, scale.y, scale.z))

        # カスタムプロパティ「file_name」
        if "file_name" in object:
            self.write_and_print(file,
                indent + "N %s" % object["file_name"])

        # カスタムプロパティ「collider」
        if "collider" in object:
            self.write_and_print(file,
                indent + "C %s" % object["collider"])

            temp_str = indent + "CC %f %f %f" % (
                object["collider_center"][0],
                object["collider_center"][1],
                object["collider_center"][2]
            )
            self.write_and_print(file, temp_str)

            temp_str = indent + "CS %f %f %f" % (
                object["collider_size"][0],
                object["collider_size"][1],
                object["collider_size"][2]
            )
            self.write_and_print(file, temp_str)

        self.write_and_print(file, indent + "END")
        self.write_and_print(file, "")

        # 子ノードへ進む (深さが1上がる)
        for child in object.children:
            self.parse_scene_recursive(file, child, level + 1)

    # ファイル書き出し
    def export(self):

        """ファイルに出力"""

        print("シーン情報出力開始... %r" % self.filepath)

        # ファイルをテキスト形式で書き出し用にオープン
        # スコープを抜けると自動的にクローズされる
        with open(self.filepath, "wt") as file:

            # ファイルに文字列を書き込む
            file.write("SCENE\n")

            # シーン内の全オブジェクトについて
            for object in bpy.context.scene.objects:

                # 親オブジェクトがあるものはスキップ
                # (代わりに親から呼び出すため)
                if (object.parent):
                    continue

                # シーン直下のオブジェクトをルートノード(深さ0)として、
                # 再帰関数で走査
                self.parse_scene_recursive(file, object, 0)

    def parse_scene_recursive_json(self, data_parent, object, level):

        # シーンのオブジェクト1個分のJSONオブジェクトを生成
        json_object = dict()

        # オブジェクト種類
        json_object["type"] = object.type

        # オブジェクト名
        json_object["name"] = object.name

        # オブジェクトのローカルトランスフォームから平行移動、回転、スケールを抽出
        trans, rot, scale = object.matrix_local.decompose()

        # 回転を Quaternion から Euler に変換
        rot = rot.to_euler()

        # ラジアンから度数法に変換
        rot.x = math.degrees(rot.x)
        rot.y = math.degrees(rot.y)
        rot.z = math.degrees(rot.z)

        # トランスフォーム情報をディクショナリに登録
        transform = dict()
        transform["translation"] = (trans.x, trans.y, trans.z)
        transform["rotation"] = (rot.x, rot.y, rot.z)
        transform["scaling"] = (scale.x, scale.y, scale.z)

        # まとめて1個分のJSONオブジェクトに登録
        json_object["transform"] = transform

        # カスタムプロパティ「file_name」
        if "file_name" in object:
            json_object["file_name"] = object["file_name"]

        # カスタムプロパティ「collider」
        if "collider" in object:
            collider = dict()
            collider["type"] = object["collider"]
            collider["center"] = object["collider_center"].to_list()
            collider["size"] = object["collider_size"].to_list()
            json_object["collider"] = collider

        # 子ノードがあれば
        if len(object.children) > 0:

            # 子ノードリストを作成
            json_object["children"] = list()

            # 子ノードへ進む
            for child in object.children:
                self.parse_scene_recursive_json(
                    json_object["children"],
                    child,
                    level + 1
                )

        # 1個分のJSONオブジェクトを親オブジェクトに登録
        data_parent.append(json_object)


    def make_nav_mesh_json(self):

        # Blender内のNavMeshメッシュを、ゲーム用JSONへ出力する
        nav_mesh = {
            "vertices": [],
            "triangles": []
        }

        vertex_map = {}

        for object in bpy.context.scene.objects:
            if not is_nav_mesh_object(object):
                continue

            if object.type != "MESH":
                continue

            mesh = object.data

            for polygon in mesh.polygons:
                polygon_indices = []

                for vertex_index in polygon.vertices:
                    local_position = mesh.vertices[vertex_index].co
                    world_position = object.matrix_world @ local_position

                    # 同じ座標の頂点は再利用して、JSONの頂点数を増やしすぎないようにする
                    vertex_key = (
                        round(world_position.x, 5),
                        round(world_position.y, 5),
                        round(world_position.z, 5)
                    )

                    if vertex_key not in vertex_map:
                        vertex_map[vertex_key] = len(nav_mesh["vertices"])
                        nav_mesh["vertices"].append([
                            world_position.x,
                            world_position.y,
                            world_position.z
                        ])

                    polygon_indices.append(vertex_map[vertex_key])

                # 四角形以上の面は、先頭頂点を軸にして三角形へ分ける
                for index in range(1, len(polygon_indices) - 1):
                    nav_mesh["triangles"].append([
                        polygon_indices[0],
                        polygon_indices[index],
                        polygon_indices[index + 1]
                    ])

        return nav_mesh

    def export_json(self):

        """JSON形式でファイルに出力"""

        # 保存する情報をまとめるdict
        json_object_root = dict()

        # ノード名
        json_object_root["name"] = "scene"

        # オブジェクトリストを作成
        json_object_root["objects"] = list()


        # NavMeshという名前のメッシュを、敵AI用の歩行データとして出力する
        json_object_root["nav_mesh"] = self.make_nav_mesh_json()

               # シーン内の全オブジェクトについて
        for object in bpy.context.scene.objects:

            # 親オブジェクトがあるものはスキップ
            # 代わりに親から呼び出すため
            if object.parent:
                continue


            # NavMeshはnav_mesh欄へ専用出力するので、通常オブジェクトからは外す
            if is_nav_mesh_object(object):
                continue

            # シーン直下のオブジェクトをルートノードとして、再帰関数で走査
            self.parse_scene_recursive_json(
                json_object_root["objects"],
                object,
                0
            )

                # オブジェクトをJSON文字列にエンコード（改行・インデント付き）
        json_text = json.dumps(
            json_object_root,
            ensure_ascii=False,
            cls=json.JSONEncoder,
            indent=4
        )

        # コンソールに表示してみる
        print(json_text)

        # ファイルをテキスト形式で書き出し用にオープン
        # スコープを抜けると自動的にクローズされる
        with open(self.filepath, "wt", encoding="utf-8") as file:

            # ファイルに文字列を書き込む
            file.write(json_text)

    
    
    def execute(self, context):
        # Export 前にシーンを検証して問題があれば中止する
        issues = validate_level_scene(context.scene)
        update_validation_result(context.scene, issues)

        if len(issues) > 0:
            self.report({'WARNING'}, f"検証に失敗したので Export を中止しました: {len(issues)}件")
            return {'CANCELLED'}

        print("シーン情報をExportします")

        # JSONファイルに出力
        self.export_json()

        self.report({'INFO'}, "シーン情報をExportしました")

        print("シーン情報をExportしました")

        return {'FINISHED'}



# ミニマップ表示に必要な2D情報だけをJSONへ出力する
class MYADDON_OT_export_minimap(
    bpy.types.Operator,
    bpy_extras.io_utils.ExportHelper):

    bl_idname = "myaddon.export_minimap"
    bl_label = "ミニマップ出力"
    bl_description = "床、壁、プレイヤー、敵をミニマップ用JSONとして出力します"

    # 通常のシーンJSONと同じくUTF-8のJSONファイルを出力する
    filename_ext = ".json"

    def get_minimap_corners(self, object):
        # コライダーがある場合は、その四隅をワールド座標へ変換する
        if "collider" in object and "collider_center" in object and "collider_size" in object:
            center = mathutils.Vector(object["collider_center"])
            size = mathutils.Vector(object["collider_size"])
            half_x = size.x * 0.5
            half_y = size.y * 0.5

            return [
                object.matrix_world @ mathutils.Vector((center.x - half_x, center.y - half_y, center.z)),
                object.matrix_world @ mathutils.Vector((center.x - half_x, center.y + half_y, center.z)),
                object.matrix_world @ mathutils.Vector((center.x + half_x, center.y - half_y, center.z)),
                object.matrix_world @ mathutils.Vector((center.x + half_x, center.y + half_y, center.z)),
            ]

        # コライダーが無いプレイヤーや敵は現在位置を1点として扱う
        return [object.matrix_world.translation.copy()]

    def make_minimap_object(self, object):
        # BlenderのXY平面をゲーム側のXZ平面として2D配列へ変換する
        world_position = object.matrix_world.translation.copy()
        world_rotation = object.matrix_world.to_euler('XYZ')
        world_scale = object.matrix_world.to_scale()
        minimap_size = [0.0, 0.0]
        corners = self.get_minimap_corners(object)

        # コライダー中心と実寸を使い、壁や床の位置と大きさを正確に保存する
        if "collider" in object and "collider_center" in object and "collider_size" in object:
            collider_center = mathutils.Vector(object["collider_center"])
            collider_size = mathutils.Vector(object["collider_size"])
            world_position = object.matrix_world @ collider_center
            minimap_size = [
                abs(collider_size.x * world_scale.x),
                abs(collider_size.y * world_scale.y)
            ]

        return {
            "name": object.name,
            "kind": get_object_kind(object),
            "position": [world_position.x, world_position.y],
            "size": minimap_size,
            "rotation": math.degrees(world_rotation.z)
        }, corners

    def execute(self, context):
        # ミニマップに表示する種類だけを抽出し、Waypointや補助物は除外する
        minimap_objects = []
        all_corners = []

        for object in context.scene.objects:
            object_kind = get_object_kind(object)

            if object_kind not in {"floor", "wall", "player", "enemy"}:
                continue

            if is_enemy_waypoint_object(object):
                continue

            minimap_object, corners = self.make_minimap_object(object)
            minimap_objects.append(minimap_object)
            all_corners.extend(corners)

        # 対象が無い場合は空のJSONを作らず、設定漏れとして通知する
        if len(minimap_objects) == 0:
            self.report({'WARNING'}, "ミニマップへ出力できるオブジェクトがありません")
            return {'CANCELLED'}

        # 全オブジェクトを囲む範囲をゲーム側のX/Z座標で保存する
        min_x = min(corner.x for corner in all_corners)
        max_x = max(corner.x for corner in all_corners)
        min_z = min(corner.y for corner in all_corners)
        max_z = max(corner.y for corner in all_corners)

        minimap_root = {
            "name": "minimap",
            "coordinate_system": "game_xz",
            "bounds": {
                "min": [min_x, min_z],
                "max": [max_x, max_z],
                "center": [(min_x + max_x) * 0.5, (min_z + max_z) * 0.5],
                "size": [max_x - min_x, max_z - min_z]
            },
            "objects": minimap_objects
        }

        # 日本語名を保持できるようにUTF-8かつensure_ascii=Falseで保存する
        with open(self.filepath, "wt", encoding="utf-8") as file:
            json.dump(minimap_root, file, ensure_ascii=False, indent=4)

        self.report({'INFO'}, f"ミニマップJSONを出力しました: {len(minimap_objects)}個")
        return {'FINISHED'}

# オペレータ カスタムプロパティ「file_name」追加
class MYADDON_OT_add_filename(bpy.types.Operator):

    bl_idname = "myaddon.myaddon_ot_add_filename"
    bl_label = "FileName 追加"
    bl_description = "['file_name']カスタムプロパティを追加します"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):

        # ['file_name']カスタムプロパティを追加
        context.object["file_name"] = ""

        return {'FINISHED'}

# オペレータ カスタムプロパティ「collider」追加
class MYADDON_OT_add_collider(bpy.types.Operator):

    bl_idname = "myaddon.myaddon_ot_add_collider"
    bl_label = "コライダー 追加"
    bl_description = "['collider']カスタムプロパティを追加します"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):

        # ['collider']カスタムプロパティを追加
        context.object["collider"] = "BOX"
        context.object["collider_center"] = mathutils.Vector((0, 0, 0))
        context.object["collider_size"] = mathutils.Vector((2, 2, 2))

        return {'FINISHED'}

# オペレータ カスタムプロパティ「collider」追加
class MYADDON_OT_add_collider(bpy.types.Operator):

    bl_idname = "myaddon.myaddon_ot_add_collider"
    bl_label = "コライダー 追加"
    bl_description = "['collider']カスタムプロパティを追加します"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):

        # ['collider']カスタムプロパティを追加
        context.object["collider"] = "BOX"
        context.object["collider_center"] = mathutils.Vector((0, 0, 0))
        context.object["collider_size"] = mathutils.Vector((2, 2, 2))

        return {'FINISHED'}

#　メニュー項目描画
def draw_menu_manual(self, context):
    self.layout.operator("wm.url_open_preset", text="Manual", icon='HELP')

# サブメニュークラス
class TOPBAR_MT_my_menu(bpy.types.Menu):

    # Blenderがクラスを識別する為の固有な文字列
    bl_idname = "TOPBAR_MT_my_menu"

    # メニューのラベルとして表示される文字列
    bl_label = "MyMenu"

    # 著者表示用の文字列
    bl_description = "拡張メニュー by " + bl_info["author"]

    # サブメニューの描画
    def draw(self, context):

        # トップバーの「エディターメニュー」に項目（オペレータ）を追加
        self.layout.operator("wm.url_open_preset",
            text="Manual",
            icon='HELP')

        # トップバーの「エディターメニュー」に項目（オペレータ）を追加
        self.layout.operator(MYADDON_OT_stretch_vertex.bl_idname,
            text=MYADDON_OT_stretch_vertex.bl_label)

        # トップバーの「エディターメニュー」に項目（オペレータ）を追加
        self.layout.operator(MYADDON_OT_create_ico_sphere.bl_idname,
            text=MYADDON_OT_create_ico_sphere.bl_label)

        # トップバーの「エディターメニュー」に項目（オペレータ）を追加
        self.layout.operator(MYADDON_OT_export_scene.bl_idname,
            text=MYADDON_OT_export_scene.bl_label)

    # 既存のメニューにサブメニューを追加
    def submenu(self, context):

        # ID指定でサブメニューを追加
        self.layout.menu(TOPBAR_MT_my_menu.bl_idname)

# パネル ファイル名
class OBJECT_PT_file_name(bpy.types.Panel):

    # パネルの名前
    bl_idname = "OBJECT_PT_file_name"

    # パネルのラベル
    bl_label = "FileName"

    # プロパティウィンドウに表示
    bl_space_type = 'PROPERTIES'

    # オブジェクトプロパティに表示
    bl_region_type = 'WINDOW'
    bl_context = "object"

    # パネルの描画
    def draw(self, context):

        # すでに file_name がある場合
        if "file_name" in context.object:

            # 既存プロパティを表示
            self.layout.prop(context.object, '["file_name"]', text=self.bl_label)

        else:

            # プロパティが無ければ追加ボタンを表示
            self.layout.operator(MYADDON_OT_add_filename.bl_idname)

# パネル コライダー
class OBJECT_PT_collider(bpy.types.Panel):

    bl_idname = "OBJECT_PT_collider"
    bl_label = "Collider"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    # サブメニューの描画
    def draw(self, context):

        # パネルに項目を追加
        if "collider" in context.object:

            # 既にプロパティがあれば、プロパティを表示
            self.layout.prop(context.object, '["collider"]', text="Type")
            self.layout.prop(context.object, '["collider_center"]', text="Center")
            self.layout.prop(context.object, '["collider_size"]', text="Size")

        else:

            # プロパティがなければ、プロパティ追加ボタンを表示
            self.layout.operator(MYADDON_OT_add_collider.bl_idname)


# パネル グリッド吸着
class VIEW3D_PT_grid_snap(bpy.types.Panel):

    bl_idname = "VIEW3D_PT_grid_snap"
    bl_label = "Grid Snap"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "LevelEditor"

    # パネルの描画
    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # 敵追加ボタンを表示する
        layout.operator(MYADDON_OT_add_normal_enemy.bl_idname, text="Add Normal_Enemy")
        layout.operator(MYADDON_OT_add_enemy_waypoint.bl_idname, text="Add Waypoint")
        layout.operator(MYADDON_OT_add_player_spawn.bl_idname, text="Add Player")
        layout.operator(MYADDON_OT_add_wall.bl_idname, text="Add Wall")
        layout.operator(MYADDON_OT_add_nav_mesh.bl_idname, text="Add NavMesh")
        layout.operator(MYADDON_OT_generate_nav_mesh.bl_idname, text="Generate NavMesh")

        layout.prop(scene, "myaddon_edit_target", text="Edit Target")
        layout.separator()

        # 補助グリッド線の表示を切り替える
        layout.prop(scene, "myaddon_show_grid", text="Show Grid")

        # 補助グリッドの表示マス数を変更する
        layout.prop(scene, "myaddon_grid_count", text="Grid Count")

        # コライダー線の表示を切り替える
        layout.prop(scene, "myaddon_show_collider", text="Show Collider")
        # コライダー編集Gizmoの表示を切り替える
        layout.prop(
            scene,
            "myaddon_show_collider_gizmo",
            text="Edit Collider Gizmo"
        )
        # 敵の巡回経路表示を切り替える
        layout.prop(scene, "myaddon_show_enemy_path", text="Show Enemy Path")

        # NavMeshの表示を切り替える
        layout.prop(scene, "myaddon_show_nav_mesh", text="Show NavMesh")

        # グリッド吸着のON/OFFを切り替える
        layout.prop(scene, "myaddon_grid_snap_enabled", text="Enable Snap")

        # 1マスの吸着幅を設定する
        layout.prop(scene, "myaddon_grid_size", text="Snap Size")

        # 敵プレビュー移動の速度を設定する
        layout.prop(scene, "myaddon_enemy_preview_speed", text="Preview Speed")

                # 専用ボタンで敵プレビューの開始と停止を切り替える
        layout.operator(MYADDON_OT_start_enemy_preview.bl_idname, text="Start Preview")
        layout.operator(MYADDON_OT_stop_enemy_preview.bl_idname, text="Stop Preview")
        layout.operator(MYADDON_OT_reset_enemy_preview.bl_idname, text="Reset Preview")
        layout.separator()

        # Export 前検証ボタンを表示する
        layout.operator(MYADDON_OT_validate_scene.bl_idname, text="Validate Scene")
        layout.operator(MYADDON_OT_export_scene.bl_idname, text="Export Scene")
        layout.operator(MYADDON_OT_export_minimap.bl_idname, text="Export Minimap")
        layout.separator()

        # 検証結果を一覧表示する
        if scene.myaddon_validation_status != "":
            validation_box = layout.box()
            validation_box.label(text=scene.myaddon_validation_status)

            if scene.myaddon_validation_details != "":
                for validation_line in scene.myaddon_validation_details.split("\n"):
                    validation_box.label(text=validation_line)

        # 開発用の再読み込みボタンを表示する
        layout.operator(MYADDON_OT_reload_addon.bl_idname, text="Reload Addon")

# コライダー描画
class DrawCollider:

    # 描画ハンドル
    handle = None

        # 3Dビューに描画する関数
    def draw_collider():

        # 表示がOFFなら何も描画しない
        if not bpy.context.scene.myaddon_show_collider:
            return

        # 頂点データ
        vertices = {
            "pos": []
        }

        # インデックスデータ
        indices = []

        # 立方体の頂点オフセット
        offsets = [
            [-0.5, -0.5, -0.5],
            [-0.5,  0.5, -0.5],
            [ 0.5,  0.5, -0.5],
            [ 0.5, -0.5, -0.5],

            [-0.5, -0.5,  0.5],
            [-0.5,  0.5,  0.5],
            [ 0.5,  0.5,  0.5],
            [ 0.5, -0.5,  0.5],
        ]

                # シーンの全オブジェクトを走査
        for object in bpy.context.scene.objects:

            # コライダープロパティがなければ、描画をスキップ
            if not "collider" in object:
                continue

            # 中心点、サイズの変数を宣言
            center = mathutils.Vector((0, 0, 0))
            size = mathutils.Vector((2, 2, 2))

            # プロパティから値を取得
            center[0] = object["collider_center"][0]
            center[1] = object["collider_center"][1]
            center[2] = object["collider_center"][2]
            size[0] = object["collider_size"][0]
            size[1] = object["collider_size"][1]
            size[2] = object["collider_size"][2]

            # 現在の頂点数
            start = len(vertices["pos"])

            # 8頂点分追加
            for offset in offsets:

                # オブジェクトのローカル座標をコピー
                pos = copy.copy(center)

                # 中心点を基準に各頂点ごとにずらす
                pos[0] += offset[0] * size[0]
                pos[1] += offset[1] * size[1]
                pos[2] += offset[2] * size[2]

                # ローカル座標からワールド座標に変換
                pos = object.matrix_world @ pos

                # 頂点データリストに座標を追加
                vertices["pos"].append(pos)

            # 前面
            indices.append([start+0, start+1])
            indices.append([start+1, start+2])
            indices.append([start+2, start+3])
            indices.append([start+3, start+0])

            # 後面
            indices.append([start+4, start+5])
            indices.append([start+5, start+6])
            indices.append([start+6, start+7])
            indices.append([start+7, start+4])

            # 接続
            indices.append([start+0, start+4])
            indices.append([start+1, start+5])
            indices.append([start+2, start+6])
            indices.append([start+3, start+7])

        # シェーダ取得
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")

        # バッチ作成
        batch = gpu_extras.batch.batch_for_shader(
            shader,
            "LINES",
            vertices,
            indices=indices
        )

        # 色
        color = (0.5, 1.0, 1.0, 1.0)

        # オブジェクトの裏側にある線は表示しない
        gpu.state.depth_test_set("LESS_EQUAL")

        try:
            shader.bind()
            shader.uniform_float("color", color)

            # 描画
            batch.draw(shader)
        finally:
            # 後続描画へ影響しないように深度設定を戻す
            gpu.state.depth_test_set("NONE")


# 補助グリッド描画
class DrawGrid:

    # 描画ハンドル
    handle = None

    # 3Dビューに補助グリッドを描画する関数
    def draw_grid():

        # 表示がOFFなら何も描画しない
        if not bpy.context.scene.myaddon_show_grid:
            return

        grid_size = bpy.context.scene.myaddon_grid_size

        # グリッド幅が不正なら何も描画しない
        if grid_size <= 0.0:
            return

        # 原点周辺に描画するマス数を設定値から取得する
        grid_count = bpy.context.scene.myaddon_grid_count
        grid_extent = grid_size * grid_count

        # 頂点データ
        vertices = {
            "pos": []
        }

        # インデックスデータ
        indices = []
        index = 0

        for grid_index in range(-grid_count, grid_count + 1):
            line_offset = grid_index * grid_size

            # X方向に伸びる線を追加
            vertices["pos"].append(mathutils.Vector((-grid_extent, line_offset, 0.0)))
            vertices["pos"].append(mathutils.Vector((grid_extent, line_offset, 0.0)))
            indices.append([index, index + 1])
            index += 2

            # Y方向に伸びる線を追加
            vertices["pos"].append(mathutils.Vector((line_offset, -grid_extent, 0.0)))
            vertices["pos"].append(mathutils.Vector((line_offset, grid_extent, 0.0)))
            indices.append([index, index + 1])
            index += 2

        # シェーダ取得
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")

        # バッチ作成
        batch = gpu_extras.batch.batch_for_shader(
            shader,
            "LINES",
            vertices,
            indices=indices
        )

        # 薄めの色で補助線として表示する
        color = (0.35, 0.7, 0.35, 0.35)

        # オブジェクトの裏側にある線は表示しない
        gpu.state.depth_test_set("LESS_EQUAL")

        try:
            shader.bind()
            shader.uniform_float("color", color)

            # 描画
            batch.draw(shader)
        finally:
            # 後続描画へ影響しないように深度設定を戻す
            gpu.state.depth_test_set("NONE")
        
# Blenderに登録するクラスリスト
# 敵とWaypointを結ぶ巡回経路を描画する
class DrawEnemyPath:

    # Blenderへ登録した描画ハンドルを保持する
    handle = None

    # 3Dビューに敵の巡回経路を描画する
    def draw_enemy_path():

        # 表示設定がOFFなら何も描画しない
        if not bpy.context.scene.myaddon_show_enemy_path:
            return

        vertices = {
            "pos": []
        }
        indices = []

        # 2点を結ぶ線を描画データへ追加する
        def add_path_line(start_position, end_position):
            start_index = len(vertices["pos"])
            vertices["pos"].append(start_position.copy())
            vertices["pos"].append(end_position.copy())
            indices.append([start_index, start_index + 1])

        # シーン内の敵本体を順番に確認する
        for enemy_object in bpy.context.scene.objects:

            # 敵本体以外は巡回経路の対象にしない
            if not is_enemy_root_object(enemy_object):
                continue

            # 敵に対応するWaypointを番号順で取得する
            waypoint_objects = get_enemy_waypoints(
                bpy.context.scene,
                enemy_object
            )

            # Waypointが無い敵は線を描画できない
            if len(waypoint_objects) == 0:
                continue

            # Waypoint_00は敵の初期位置なので敵本体とは線で結ばない
            first_waypoint_position = (
                waypoint_objects[0].matrix_world.translation
            )

            # Waypoint同士を番号順に線で結ぶ
            for waypoint_index in range(len(waypoint_objects) - 1):
                current_position = (
                    waypoint_objects[
                        waypoint_index
                    ].matrix_world.translation
                )
                next_position = (
                    waypoint_objects[
                        waypoint_index + 1
                    ].matrix_world.translation
                )

                add_path_line(
                    current_position,
                    next_position
                )

            # 巡回経路として最後のWaypointから最初へ戻る線も引く
            if len(waypoint_objects) >= 2:
                last_waypoint_position = (
                    waypoint_objects[-1].matrix_world.translation
                )

                add_path_line(
                    last_waypoint_position,
                    first_waypoint_position
                )

        # 描画対象の線が無ければ終了する
        if len(indices) == 0:
            return

        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        batch = gpu_extras.batch.batch_for_shader(
            shader,
            "LINES",
            vertices,
            indices=indices
        )

        # 巡回経路を見分けやすい黄色で表示する
        path_color = (1.0, 0.75, 0.1, 1.0)

        # オブジェクトの裏側でも経路が見えるようにする
        gpu.state.depth_test_set("NONE")
        gpu.state.line_width_set(3.0)

        try:
            shader.bind()
            shader.uniform_float("color", path_color)
            batch.draw(shader)
        finally:
            # 後続の描画へ影響しないように線幅を戻す
            gpu.state.line_width_set(1.0)

            # 深度設定を初期状態へ戻す
            gpu.state.depth_test_set("NONE")

# BOXコライダーの中心とサイズを編集するGizmo
class VIEW3D_GGT_collider_edit(bpy.types.GizmoGroup):

    bl_idname = "VIEW3D_GGT_collider_edit"
    bl_label = "Collider Edit Gizmo"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'3D', 'PERSISTENT'}

    @classmethod
    def poll(cls, context):

        # 表示設定がOFFの場合はGizmoを表示しない
        if not context.scene.myaddon_show_collider_gizmo:
            return False

        object = context.object

        # コライダーを持つ選択中オブジェクトだけを編集対象にする
        return (
            object is not None
            and object.mode == 'OBJECT'
            and "collider" in object
            and "collider_center" in object
            and "collider_size" in object
        )

    # オブジェクトの各軸に掛かっているスケールを取得する
    def get_axis_scale(self, object, axis_index):
        world_scale = object.matrix_world.to_scale()
        return max(abs(world_scale[axis_index]), 0.0001)

    # Gizmo操作中の表示をすぐ更新する
    def redraw_view(self, context, object):
        object.update_tag()

        if context.screen is not None:
            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()

    def setup(self, context):
        self.target_object = context.object

        # コライダー中心を3軸で移動するGizmoを作成する
        center_gizmo = self.gizmos.new("GIZMO_GT_move_3d")
        center_gizmo.use_draw_value = True
        center_gizmo.scale_basis = 0.18
        center_gizmo.color = (0.8, 0.8, 0.8)
        center_gizmo.alpha = 0.65
        center_gizmo.color_highlight = (1.0, 1.0, 1.0)
        center_gizmo.alpha_highlight = 1.0

        # コライダー中心をワールド距離へ変換してGizmoへ渡す
        def center_get():
            object = self.target_object
            center = mathutils.Vector(object["collider_center"])

            return tuple(
                center[axis_index]
                * self.get_axis_scale(object, axis_index)
                for axis_index in range(3)
            )

        # Gizmoの移動量をコライダーのローカル中心へ戻す
        def center_set(value):
            object = self.target_object
            new_center = mathutils.Vector((0.0, 0.0, 0.0))

            for axis_index in range(3):
                # Gizmoの移動量をコライダーのローカル座標へ変換する
                local_value = (
                    value[axis_index]
                    / self.get_axis_scale(object, axis_index)
                )

                # Enable SnapがONならSnap Size単位へ吸着する
                if context.scene.myaddon_grid_snap_enabled:
                    local_value = snap_value_to_grid(
                        local_value,
                        context.scene.myaddon_grid_size
                    )

                new_center[axis_index] = local_value

            object["collider_center"] = new_center
            self.redraw_view(context, object)

        center_gizmo.target_set_handler(
            "offset",
            get=center_get,
            set=center_set
        )

        self.center_gizmo = center_gizmo
        self.size_gizmos = []

        # 矢印GizmoのローカルZ軸を各編集方向へ向ける
        axis_settings = [
            (0, 1, mathutils.Matrix.Rotation(math.radians(90.0), 4, 'Y')),
            (0, -1, mathutils.Matrix.Rotation(math.radians(-90.0), 4, 'Y')),
            (1, 1, mathutils.Matrix.Rotation(math.radians(-90.0), 4, 'X')),
            (1, -1, mathutils.Matrix.Rotation(math.radians(90.0), 4, 'X')),
            (2, 1, mathutils.Matrix.Identity(4)),
            (2, -1, mathutils.Matrix.Rotation(math.radians(180.0), 4, 'X')),
        ]

        axis_colors = [
            (1.0, 0.2, 0.2),
            (0.2, 1.0, 0.2),
            (0.2, 0.45, 1.0),
        ]

        for axis_index, direction, rotation_matrix in axis_settings:
            size_gizmo = self.gizmos.new("GIZMO_GT_arrow_3d")
            size_gizmo.draw_style = 'BOX'
            size_gizmo.use_draw_value = True
            size_gizmo.scale_basis = 0.12
            size_gizmo.color = axis_colors[axis_index]
            size_gizmo.alpha = 0.75
            size_gizmo.color_highlight = (1.0, 1.0, 0.2)
            size_gizmo.alpha_highlight = 1.0

            # 現在の半サイズをワールド距離として返す
            def size_get(axis_index=axis_index):
                object = self.target_object
                collider_size = mathutils.Vector(
                    object["collider_size"]
                )

                return (
                    max(collider_size[axis_index], 0.01)
                    * 0.5
                    * self.get_axis_scale(object, axis_index)
                )

            # 矢印の移動量からコライダー全体のサイズを更新する
            def size_set(value, axis_index=axis_index):
                object = self.target_object
                collider_size = mathutils.Vector(
                    object["collider_size"]
                )
                axis_scale = self.get_axis_scale(
                    object,
                    axis_index
                )

                # 矢印の位置からコライダーのローカルサイズを計算する
                local_size = (
                    abs(float(value))
                    * 2.0
                    / axis_scale
                )

                # Enable SnapがONならSnap Size単位へ吸着する
                if context.scene.myaddon_grid_snap_enabled:
                    grid_size = context.scene.myaddon_grid_size
                    local_size = snap_value_to_grid(
                        local_size,
                        grid_size
                    )

                    # サイズが0にならないよう最低1グリッドを維持する
                    local_size = max(
                        local_size,
                        grid_size
                    )
                else:
                    # 自由変更時も完全な0サイズにはしない
                    local_size = max(
                        local_size,
                        0.01
                    )

                collider_size[axis_index] = local_size
                object["collider_size"] = collider_size
                self.redraw_view(context, object)

            size_gizmo.target_set_handler(
                "offset",
                get=size_get,
                set=size_set
            )

            self.size_gizmos.append(
                (
                    size_gizmo,
                    rotation_matrix
                )
            )

    def refresh(self, context):
        self.target_object = context.object
        object = self.target_object

        # オブジェクトの回転だけを使った基準行列を作る
        orientation_matrix = (
            object.matrix_world
            .to_quaternion()
            .to_matrix()
            .to_4x4()
        )
        orientation_matrix.translation = (
            object.matrix_world.translation
        )

        # 中心Gizmoはオブジェクト原点を基準にする
        self.center_gizmo.matrix_basis = (
            orientation_matrix
        )

        collider_center = mathutils.Vector(
            object["collider_center"]
        )
        center_world_position = (
            object.matrix_world @ collider_center
        )

        # サイズGizmoはコライダー中心を基準に各方向へ向ける
        for size_gizmo, rotation_matrix in self.size_gizmos:
            size_matrix = orientation_matrix.copy()
            size_matrix.translation = center_world_position
            size_gizmo.matrix_basis = (
                size_matrix @ rotation_matrix
            )

classes = (
    VIEW3D_GGT_collider_edit,
    MYADDON_OT_stretch_vertex,
    MYADDON_OT_create_ico_sphere,
    MYADDON_OT_reload_addon,
    MYADDON_OT_add_normal_enemy,
    MYADDON_OT_add_enemy_waypoint,
    MYADDON_OT_start_enemy_preview,
    MYADDON_OT_stop_enemy_preview,
    MYADDON_OT_reset_enemy_preview,
    MYADDON_OT_add_player_spawn,
    MYADDON_OT_add_nav_mesh,
    MYADDON_OT_generate_nav_mesh,
    MYADDON_OT_add_wall,
    MYADDON_OT_validate_scene,
    MYADDON_OT_export_scene,
    MYADDON_OT_export_minimap,
    TOPBAR_MT_my_menu,
    MYADDON_OT_add_filename,
    MYADDON_OT_add_collider,
    OBJECT_PT_file_name,
    OBJECT_PT_collider,
    VIEW3D_PT_grid_snap,
)

#Add-On有効化時コールバック
def register():

    bpy.types.Scene.myaddon_show_grid = bpy.props.BoolProperty(
        name="Show Grid",
        description="Show the editor grid in the 3D view",
        default=True
    )

    bpy.types.Scene.myaddon_show_collider = bpy.props.BoolProperty(
        name="Show Collider",
        description="Show collider wireframes in the 3D view",
        default=True
    )

    # 敵の巡回経路を表示するか設定する
    bpy.types.Scene.myaddon_show_enemy_path = bpy.props.BoolProperty(
        name="Show Enemy Path",
        description="敵とWaypointの巡回経路を表示します",
        default=True
    )

    # NavMeshを表示するか設定する
    bpy.types.Scene.myaddon_show_nav_mesh = bpy.props.BoolProperty(
        name="Show NavMesh",
        description="NavMeshオブジェクトの表示を切り替えます",
        default=True,
        update=on_show_nav_mesh_changed
    )
    # コライダー編集Gizmoを表示するか設定する
    bpy.types.Scene.myaddon_show_collider_gizmo = bpy.props.BoolProperty(
        name="Edit Collider Gizmo",
        description="選択中オブジェクトのコライダー中心とサイズを編集します",
        default=True
    )
    bpy.types.Scene.myaddon_grid_count = bpy.props.IntProperty(
        name="Grid Count",
        description="Number of grid cells shown in each direction",
        default=20,
        min=1,
        soft_min=5,
        soft_max=100
    )

    bpy.types.Scene.myaddon_grid_snap_enabled = bpy.props.BoolProperty(
        name="Enable Grid Snap",
        description="Snap selected objects to the editor grid after moving",
        default=True
    )

    bpy.types.Scene.myaddon_grid_size = bpy.props.FloatProperty(
        name="Grid Size",
        description="Grid spacing used by the level editor",
        default=1.0,
        min=0.01,
        soft_min=0.1,
        step=10,
        precision=3
    )

    bpy.types.Scene.myaddon_enemy_preview_speed = bpy.props.FloatProperty(
        name="Preview Speed",
        description="Movement amount per frame used by enemy preview playback",
        default=0.1,
        min=0.001,
        soft_min=0.01,
        soft_max=1.0,
        step=1,
        precision=3
    )

    bpy.types.Scene.myaddon_validation_status = bpy.props.StringProperty(
        name="Validation Status",
        description="Latest level validation summary",
        default=""
    )

    bpy.types.Scene.myaddon_validation_details = bpy.props.StringProperty(
        name="Validation Details",
        description="Latest level validation issues",
        default=""
    )


    # Edit target filter used to lock selection to one object kind.
    bpy.types.Scene.myaddon_edit_target = bpy.props.EnumProperty(
        name="Edit Target",
        description="Only the selected object kind can be selected and moved",
        items=[
            ("ALL", "すべて", "敵、壁、床、プレイヤーを編集します"),
            ("ENEMY", "敵", "敵とWaypointだけを編集します"),
            ("WALL", "壁", "壁だけを編集します"),
            ("FLOOR", "床", "床だけを編集します"),
            ("NAVMESH", "NavMesh", "NavMeshだけを編集します"),
            ("PLAYER", "プレイヤー", "プレイヤーだけを編集します"),
        ],
        default="ALL",
        update=on_edit_target_changed
    )

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.TOPBAR_MT_editor_menus.append(TOPBAR_MT_my_menu.submenu)

    DrawCollider.handle = bpy.types.SpaceView3D.draw_handler_add(
        DrawCollider.draw_collider,
        (),
        "WINDOW",
        "POST_VIEW"
    )

    DrawGrid.handle = bpy.types.SpaceView3D.draw_handler_add(
        DrawGrid.draw_grid,
        (),
        "WINDOW",
        "POST_VIEW"
    )

    # 敵の巡回経路描画を3Dビューへ登録する
    DrawEnemyPath.handle = bpy.types.SpaceView3D.draw_handler_add(
        DrawEnemyPath.draw_enemy_path,
        (),
        "WINDOW",
        "POST_VIEW"
    )
    if grid_snap_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(grid_snap_handler)
    bpy.app.handlers.depsgraph_update_post.append(grid_snap_handler)

    if selection_filter_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(selection_filter_handler)
    bpy.app.handlers.depsgraph_update_post.append(selection_filter_handler)

    # Apply visuals and the current selection filter to existing objects.
    for object in bpy.context.scene.objects:
        apply_editor_visuals(object)
    sync_nav_mesh_visibility(bpy.context.scene)
    sync_object_selection_filter(bpy.context.scene)

    print("Level editor enabled")


# Add-On無効化時コールバック
def unregister():

    bpy.types.TOPBAR_MT_editor_menus.remove(TOPBAR_MT_my_menu.submenu)

    bpy.types.SpaceView3D.draw_handler_remove(
        DrawCollider.handle,
        "WINDOW"
    )

    bpy.types.SpaceView3D.draw_handler_remove(
        DrawGrid.handle,
        "WINDOW"
    )


    # 敵の巡回経路描画を解除する
    if DrawEnemyPath.handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(
            DrawEnemyPath.handle,
            "WINDOW"
        )
        DrawEnemyPath.handle = None
    if grid_snap_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(grid_snap_handler)

    if selection_filter_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(selection_filter_handler)

    global enemy_preview_running
    global enemy_preview_timer

    enemy_preview_running = False

    if enemy_preview_timer is not None:
        bpy.context.window_manager.event_timer_remove(enemy_preview_timer)
        enemy_preview_timer = None

    # Clear selection locks before the addon is unloaded.
    for object in bpy.context.scene.objects:
        object.hide_select = False

    for cls in classes:
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.myaddon_show_grid
    del bpy.types.Scene.myaddon_show_collider
    del bpy.types.Scene.myaddon_show_collider_gizmo
    del bpy.types.Scene.myaddon_show_enemy_path
    del bpy.types.Scene.myaddon_show_nav_mesh
    del bpy.types.Scene.myaddon_grid_count
    del bpy.types.Scene.myaddon_grid_snap_enabled
    del bpy.types.Scene.myaddon_grid_size
    del bpy.types.Scene.myaddon_enemy_preview_speed
    del bpy.types.Scene.myaddon_validation_status
    del bpy.types.Scene.myaddon_validation_details
    del bpy.types.Scene.myaddon_edit_target

    print("Level editor disabled")
    
if __name__=="__main__":
    register()

