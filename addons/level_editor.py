import bpy

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

    # 既存のメニューにサブメニューを追加
    def submenu(self, context):

        # ID指定でサブメニューを追加
        self.layout.menu(TOPBAR_MT_my_menu.bl_idname)

# Blenderに登録するクラスリスト
classes = (
    TOPBAR_MT_my_menu,
)

#Add-On有効化時コールバック
def register():

    # Blenderにクラスを登録
    for cls in classes:
        bpy.utils.register_class(cls)

    #メニューに項目を追加
    bpy.types.TOPBAR_MT_editor_menus.append(TOPBAR_MT_my_menu.submenu)

    print("レベルエディタが有効化されました。")

#Add-On無効化時コールバック
def unregister():

    #メニューから項目を削除
    bpy.types.TOPBAR_MT_editor_menus.remove(TOPBAR_MT_my_menu.submenu)

    # Blenderからクラスを削除
    for cls in classes:
        bpy.utils.unregister_class(cls)

    print("レベルエディタが無効化されました。")
    
if __name__=="__main__":
    register()