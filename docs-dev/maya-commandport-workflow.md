# Maya CommandPort 逆引きメモ

Maya GUI 上でしか確認できない Viewport 2.0 / DX11 の状態を、外部 Python から `commandPort` 経由で操作・記録するための逆引き資料です。

## 前提

Maya 側で Python commandPort を開いておきます。

```mel
commandPort -name ":7721" -sourceType "python";
```

外部からは Python コード文字列を TCP で送ります。長い処理は Maya 側で JSON/PNG に書き出して、外部側はファイルを読むのが安定です。

```powershell
@'
import socket

script = r'''
import maya.cmds as cmds
print("MAYA", cmds.about(version=True))
'''

cmd = "exec(" + repr(script) + ")\n"
with socket.create_connection(("127.0.0.1", 7721), timeout=5) as sock:
    sock.sendall(cmd.encode("utf-8"))
print("SENT_OK")
'@ | python -
```

## 接続を確認したい

Windows 側で Listen を確認します。

```powershell
Get-NetTCPConnection -State Listen |
    Where-Object { $_.LocalPort -eq 7721 } |
    Select-Object LocalAddress,LocalPort,OwningProcess
```

Maya 側へ簡単な Python を送ります。

```python
import maya.cmds as cmds
print("COMMANDPORT_OK", cmds.about(version=True))
```

## Maya / Viewport 情報を JSON に dump したい

```python
import json
import os
import maya.cmds as cmds

out = r"F:\Develop\maya_mmd_tools\build\captures\vp2_diag.json"
data = {
    "maya_version": cmds.about(version=True),
    "scene": cmds.file(q=True, sn=True),
    "vp2RenderingEngine": cmds.optionVar(q="vp2RenderingEngine")
    if cmds.optionVar(exists="vp2RenderingEngine")
    else None,
}
try:
    data["ogs_deviceInformation"] = cmds.ogs(deviceInformation=True)
except Exception as exc:
    data["ogs_deviceInformation_error"] = str(exc)

os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
```

DX11 検証では `vp2RenderingEngine == "DirectX11"` と `API : DirectX V.11` を確認します。

## ModelPanel を Textured 表示にしたい

```python
import maya.cmds as cmds

for panel in cmds.getPanel(type="modelPanel") or []:
    cmds.modelEditor(
        panel,
        e=True,
        rendererName="vp2Renderer",
        displayAppearance="smoothShaded",
        displayTextures=True,
        wireframeOnShaded=False,
    )
cmds.refresh(force=True)
```

## PMX を import したい

```python
import sys
import maya.cmds as cmds

repo = r"F:\Develop\maya_mmd_tools"
if repo not in sys.path:
    sys.path.insert(0, repo)

from mmd_tools.core.settings import settings
from mmd_tools.io.mmd_importer import import_mmd_file

cmds.file(new=True, force=True)
settings.set("import.model.create_mmd_shaders", True)
settings.set("import.model.mmd_shader_backend", "dx11")
root = import_mmd_file(r"F:\Develop\maya_mmd_tools\tests\data\mmt_test_model.pmx")
```

standard fallback で比較したい場合は以下にします。

```python
settings.set("import.model.create_mmd_shaders", False)
settings.set("import.model.mmd_shader_backend", "standard")
```

## Viewport スクリーンショットを撮りたい

```python
import os
import maya.cmds as cmds

out = r"F:\Develop\maya_mmd_tools\build\captures\viewport.png"
os.makedirs(os.path.dirname(out), exist_ok=True)

cmds.select(clear=True)
cmds.refresh(force=True)
cmds.playblast(
    completeFilename=out,
    forceOverwrite=True,
    format="image",
    compression="png",
    width=900,
    height=700,
    percent=100,
    showOrnaments=False,
    viewer=False,
    frame=0,
)
```

`playblast` の PNG は alpha が全 0 で保存されることがあります。比較や目視では RGB を見ます。

## モデルを画面に収めたい

```python
import maya.cmds as cmds

cmds.select(root)
cmds.viewFit()
cmds.select(clear=True)
cmds.refresh(force=True)
```

`root` が `None` の import 経路もあるため、その場合は mesh transform や top-level transform を選択して `viewFit()` します。

## dx11Shader の状態を dump したい

```python
import json
import os
import maya.cmds as cmds

out = r"F:\Develop\maya_mmd_tools\build\captures\dx11_shader_diag.json"
items = []
for shader in cmds.ls(type="dx11Shader") or []:
    item = {
        "name": shader,
        "attrs": {},
        "incoming": {},
        "connections": cmds.listConnections(
            shader, s=True, d=True, plugs=True, connections=True
        )
        or [],
    }
    for attr in [
        "shader",
        "technique",
        "Opacity",
        "MainTexture",
        "DiffuseColor",
        "EdgeColor",
        "EdgeSize",
        "mmd_texture_path",
        "mmd_draw_flags",
    ]:
        if not cmds.attributeQuery(attr, node=shader, exists=True):
            continue
        try:
            item["attrs"][attr] = cmds.getAttr(shader + "." + attr)
        except Exception as exc:
            item["attrs"][attr] = "ERR: " + str(exc)
        try:
            item["incoming"][attr] = (
                cmds.listConnections(shader + "." + attr, s=True, d=False, plugs=True)
                or []
            )
        except Exception as exc:
            item["incoming"][attr] = "ERR: " + str(exc)
    items.append(item)

os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    json.dump(items, f, indent=2, ensure_ascii=False)
```

`dx11Shader` の compound uniform は `cmds.getAttr()` が例外を返すことがあります。例外文字列も診断情報として残します。

## shader ファイルを差し替えて before/after を撮りたい

```python
import maya.cmds as cmds

before_fx = r"F:\Develop\maya_mmd_tools\build\captures\gui-dx11-maya2026\MMDShader.before.fx"
after_fx = r"F:\Develop\maya_mmd_tools\build\captures\gui-dx11-maya2026\MMDShader.after.fx"

for label, fx_path in [("before", before_fx), ("after", after_fx)]:
    for shader in cmds.ls(type="dx11Shader") or []:
        if cmds.attributeQuery("shader", node=shader, exists=True):
            cmds.setAttr(shader + ".shader", fx_path, type="string")
        if cmds.attributeQuery("technique", node=shader, exists=True):
            cmds.setAttr(shader + ".technique", "MMDTechnique", type="string")
        if cmds.attributeQuery("Opacity", node=shader, exists=True):
            cmds.setAttr(shader + ".Opacity", 1.0)
    cmds.refresh(force=True)
    # ここで label ごとに playblast する
```

## テクスチャ接続を確認したい

```python
import maya.cmds as cmds

for file_node in cmds.ls(type="file") or []:
    print("file", file_node)
    print("  path", cmds.getAttr(file_node + ".fileTextureName"))
    print("  outAlpha", cmds.getAttr(file_node + ".outAlpha"))
    print("  connections", cmds.listConnections(
        file_node, s=True, d=True, plugs=True, connections=True
    ) or [])
```

DX11 shader では `file.outColor -> dx11Shader.MainTexture` の接続を確認します。

## 画像を比較したい

外部 Python で RGB 差分を確認します。

```powershell
@'
from PIL import Image, ImageChops, ImageStat

a = Image.open(r"F:\Develop\maya_mmd_tools\build\captures\a.png").convert("RGBA")
b = Image.open(r"F:\Develop\maya_mmd_tools\build\captures\b.png").convert("RGBA")
diff = ImageChops.difference(a, b)
print({
    "size": a.size,
    "mean_diff": ImageStat.Stat(diff).mean,
    "bbox": diff.getbbox(),
})
'@ | python -
```

## 既知の落とし穴

- Maya GUI の自動起動は licensing error で落ちることがあります。起動は手動または `explorer.exe` 経由を使います。
- 長い処理の結果は `print()` だけに頼らず、JSON/PNG として保存します。
- 長い `.py` を `commandPort` 経由で実行するときは、`exec(open(...).read())` ではなく globals/locals を同じ dict にした `exec(..., ns, ns)` を使います。そうしないと、関数内からトップレベル変数が見えず `NameError` になることがあります。
  ```python
  import pathlib

  script_path = r"F:\Develop\maya_mmd_tools\build\captures\capture.py"
  code = pathlib.Path(script_path).read_text(encoding="utf-8")
  ns = {"__name__": "__maya_commandport_script__", "__file__": script_path}
  exec(compile(code, script_path, "exec"), ns, ns)
  ```
- `commandPort` から送った処理内の `cmds.evalDeferred()` は、期待したタイミングで idle 実行されないことがあります。検証中に後処理が必要な場合は、deferred に頼らず次の commandPort コマンドで明示的に関数を呼びます。
- `dx11Shader` は同じ `.fx` パスの effect を Maya プロセス内でキャッシュすることがあります。シェーダーファイルを編集した直後に挙動が変わらない場合は、Maya を再起動するか、検証用に `.fx` を別名コピーして shader 属性へ割り当てます。
- `playblast` PNG の alpha が全 0 でも、RGB には表示結果が入っていることがあります。
- Textured 表示を見るときは `displayTextures=True` と `cmds.refresh(force=True)` を忘れないでください。
- 複数の `modelPanel` がある Maya GUI では、画面で見ている panel と `playblast` が撮る panel がずれることがあります。Viewport で青く見えているのに capture が緑になる場合は、`cmds.playblast(editorPanelName=<panel>)` で撮影対象 panel を明示し、対象 panel の `displayTextures=True` / `useDefaultMaterial=False` / `rendererName="vp2Renderer"` を diagnostics に残します。
- dx11Shader の CLI compile check は Maya の effect compile に合わせて `fx_5_0` と backwards compatibility を使います。例: `fxc /T fx_5_0 /Gec /D _MAYA_=1 /D MAYA_DX11=1 MMDShader.fx`。pixel shader 単体 (`ps_5_0`) の検査は Maya の実行条件とずれることがあります。
- 診断後に一時 `userSetup.mel` を使った場合は削除してください。
