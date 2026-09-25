# TODO2

- [ ] **IMPORT-REMOVE-LEGACY-SWITCHES：旧読み込み設定と分岐を完全撤去する。** `import.native.use_cpp_fast_load` と `import.native.use_cpp_vp2_ownership` を廃止し、保存済みのOFF値によって新規PMXインポートで `mmdRenderShape` が生成されなくなる問題を解消する。設定キー・既定値・読み書き・UI/APIからの伝播・Python/C++の対応する選択引数と旧経路分岐・関連テスト／文書を整理し、PMXはC++読み込み、DX11では `mmdRenderShape` 生成に統一する。旧optionVarは削除し、設定ファイルの再インポートでも復活させない。PMDおよびOpenGL/macOSに必要な経路は、廃止する設定とは切り離して維持する。完了条件：旧設定の有無・True/Falseに依存せず同じ経路を選び、対象スイッチとそれに依存する分岐が残らないこと。focused testとMaya 2024のDX11実機でPMX新規インポート・描画・保存再読込を確認し、PMD／OpenGL経路も回帰確認する。既存シーンの自動変換は別タスクとする。
