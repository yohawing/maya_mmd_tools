# Tools プラグインの追加方法

`MMD > Tools` 配下の機能は、このディレクトリに置く1ファイルのプラグインとして追加します。ホスト側の `mmd_tools/plugin_main.py` は各ツールを個別に参照せず、起動時に公開モジュールを検出します。

## 登録契約

メニューへ表示するモジュールは、次の3要素を公開してください。

```python
MENU_ITEM_ID = "MMDExampleToolMenuItem"
MENU_LABEL = "Example Tool"


def install_menu_item(*, parent, cmds_module, on_applied=None):
    cmds_module.menuItem(
        MENU_ITEM_ID,
        parent=parent,
        label=MENU_LABEL,
        command=lambda *_: run_tool(on_applied=on_applied),
    )
    return MENU_ITEM_ID
```

- `MENU_ITEM_ID` は Maya セッション内で重複しない固定 ID にします。
- `MENU_LABEL` は英語 UI にそのまま表示できる文字列にします。
- `install_menu_item()` は作成したメニュー ID を返します。
- ツール固有の UI と起動処理は同じファイルに置けます。再利用する業務ロジックは `mmd_tools/core/` へ分離し、依存方向を `tools -> core` の片方向にします。
- ダンプや診断などメニューへ出さないスクリプトは、この3要素を定義しません。

新しいツールのために `plugin_main.py` を編集する必要はありません。検出規約の変更が必要な場合だけ、`mmd_tools/tools/__init__.py` と対応するローダーテストを更新してください。
