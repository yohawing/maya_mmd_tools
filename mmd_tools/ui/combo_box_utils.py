"""Shared QComboBox helpers for dense model-selection UI."""

from .qt_compat import QComboBox, Qt


def configure_model_combo_width(combo, minimum_width: int = 320, minimum_contents_length: int = 36) -> None:
    """Make model-selection combos wide enough for namespace-qualified labels."""
    if hasattr(combo, "setMinimumWidth"):
        combo.setMinimumWidth(minimum_width)
    if hasattr(combo, "setMinimumContentsLength"):
        combo.setMinimumContentsLength(minimum_contents_length)
    if hasattr(combo, "setSizeAdjustPolicy"):
        policy = getattr(QComboBox, "AdjustToContents", None)
        if policy is None and hasattr(QComboBox, "SizeAdjustPolicy"):
            policy = getattr(QComboBox.SizeAdjustPolicy, "AdjustToContents", None)
        if policy is not None:
            combo.setSizeAdjustPolicy(policy)


def add_combo_item_with_tooltip(combo, label: str, user_data=None) -> None:
    """Add a combo item and mirror its full label to the tooltip role."""
    if user_data is None:
        combo.addItem(label)
    else:
        combo.addItem(label, userData=user_data)
    set_item_data = getattr(combo, "setItemData", None)
    count = getattr(combo, "count", None)
    if callable(set_item_data) and callable(count):
        index = count()
        if isinstance(index, int) and index > 0:
            set_item_data(index - 1, label, Qt.ToolTipRole)
