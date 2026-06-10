import bpy
import math
import mathutils
import json
import bpy_extras
import gpu
import gpu_extras.batch
import copy

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

# オペレータ シーン出力
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

    def export_json(self):

        """JSON形式でファイルに出力"""

        # 保存する情報をまとめるdict
        json_object_root = dict()

        # ノード名
        json_object_root["name"] = "scene"

        # オブジェクトリストを作成
        json_object_root["objects"] = list()

               # シーン内の全オブジェクトについて
        for object in bpy.context.scene.objects:

            # 親オブジェクトがあるものはスキップ
            # 代わりに親から呼び出すため
            if object.parent:
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

        print("シーン情報をExportします")

        # JSONファイルに出力
        self.export_json()

        self.report({'INFO'}, "シーン情報をExportしました")

        print("シーン情報をExportしました")

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

# コライダー描画
class DrawCollider:

    # 描画ハンドル
    handle = None

        # 3Dビューに描画する関数
    def draw_collider():

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

        shader.bind()
        shader.uniform_float("color", color)

        # 描画
        batch.draw(shader)
        
# Blenderに登録するクラスリスト
classes = (
    MYADDON_OT_stretch_vertex,
    MYADDON_OT_create_ico_sphere,
    MYADDON_OT_export_scene,
    TOPBAR_MT_my_menu,
    MYADDON_OT_add_filename,
    MYADDON_OT_add_collider,
    OBJECT_PT_file_name,
    OBJECT_PT_collider,
)

#Add-On有効化時コールバック
def register():

    # Blenderにクラスを登録
    for cls in classes:
        bpy.utils.register_class(cls)

    #メニューに項目を追加
    bpy.types.TOPBAR_MT_editor_menus.append(TOPBAR_MT_my_menu.submenu)
    
    # 3Dビューに描画関数を追加
    DrawCollider.handle = bpy.types.SpaceView3D.draw_handler_add(
        DrawCollider.draw_collider,
        (),
        "WINDOW",
        "POST_VIEW"
    )

    print("レベルエディタが有効化されました。")

#Add-On無効化時コールバック
def unregister():

    #メニューから項目を削除
    bpy.types.TOPBAR_MT_editor_menus.remove(
        TOPBAR_MT_my_menu.submenu)

    # 3Dビューから描画関数を削除
    bpy.types.SpaceView3D.draw_handler_remove(
        DrawCollider.handle,
        "WINDOW"
    )

    # Blenderからクラスを削除
    for cls in classes:
        bpy.utils.unregister_class(cls)

    print("レベルエディタが無効化されました。")
    
if __name__=="__main__":
    register()