"""ユニットテストパッケージ。

親の tests パッケージで既にMaya環境が初期化されているはずですが、
念のため初期化状態を確認します。
"""

# 親パッケージの初期化関数を呼び出し
from tests import _initialize_maya_if_needed

_initialize_maya_if_needed()
