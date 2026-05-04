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

# アドオン有効化時コールバック
def register():
    print("レベルエディタが有効化されました。")

# アドオン無効化時コールバック
def unregister():
    print("レベルエディタが無効化されました。")
    
if __name__=="__main__":
    register()