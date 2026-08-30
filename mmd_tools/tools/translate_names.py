"""Translate imported MMD names using an explicit user dictionary.

The dictionary and planning functions are Maya independent so they can be
used from tests, ``mayapy`` and the Maya menu through the same entry point.
EnglishName writes and Maya node renames intentionally remain separate
options.  The original MMD name attributes are never changed.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from mmd_tools.core.maya_name_utils import sanitize_unique_name


MENU_LABEL = "Translate MMD Names"
MENU_ITEM_NAME = "MMDTranslateNamesMenuItem"


class NameTranslationError(ValueError):
    """Raised when a translation dictionary or Maya target is invalid."""


@dataclass(frozen=True)
class NameEntry:
    """One MMD name-bearing scene element."""

    kind: str
    node: str
    source_name: str
    english_name: str
    english_attr: str
    index: Optional[int] = None
    rename_allowed: bool = True


@dataclass(frozen=True)
class NameChange:
    """The planned changes for one :class:`NameEntry`."""

    entry: NameEntry
    translated_name: Optional[str]
    english_name: Optional[str]
    maya_name: Optional[str]

    @property
    def has_changes(self) -> bool:
        """Return whether this plan item changes either destination."""

        return self.english_name is not None or self.maya_name is not None


def load_translation_dictionary(path: str) -> Dict[str, str]:
    """Load a strict UTF-8 two-column Japanese-to-English CSV dictionary.

    Blank lines are ignored.  A conventional ``日本語,英語`` or
    ``Japanese,English`` header is accepted, but malformed rows and duplicate
    keys fail closed instead of silently choosing one translation.
    """

    translations: Dict[str, str] = {}
    try:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
            rows = csv.reader(stream)
            for line_number, row in enumerate(rows, start=1):
                if not row or not any(cell.strip() for cell in row):
                    continue
                if len(row) != 2:
                    raise NameTranslationError(
                        f"translation dictionary row {line_number} must have exactly two columns"
                    )
                source, translated = (cell.strip() for cell in row)
                if not translations and _is_header(source, translated):
                    continue
                if not source or not translated:
                    raise NameTranslationError(
                        f"translation dictionary row {line_number} cannot contain an empty cell"
                    )
                if source in translations:
                    raise NameTranslationError(
                        f"translation dictionary row {line_number} duplicates {source!r}"
                    )
                translations[source] = translated
    except OSError as exc:
        raise NameTranslationError(f"cannot read translation dictionary {path!r}: {exc}") from exc
    return translations


def _is_header(source: str, translated: str) -> bool:
    return source.casefold() in {"日本語", "japanese", "ja"} and translated.casefold() in {
        "英語",
        "english",
        "en",
    }


def build_translation_plan(
    entries: Iterable[NameEntry],
    translations: Mapping[str, str],
    *,
    set_english: bool = True,
    overwrite: bool = False,
    rename_nodes: bool = False,
    used_names: Optional[Set[str]] = None,
) -> Tuple[NameChange, ...]:
    """Build deterministic EnglishName and optional Maya rename changes.

    ``used_names`` represents names occupied by nodes outside ``entries``.
    Names allocated for entries are added to a private copy in stable order.
    This makes duplicate translations deterministic without applying Maya
    sanitization to EnglishName values.
    """

    allocated = set(used_names or set())
    ordered_entries = sorted(entries, key=_entry_sort_key)
    plan: List[NameChange] = []
    for entry in ordered_entries:
        translated = translations.get(entry.source_name)
        english_name = None
        if set_english and translated and (overwrite or not entry.english_name):
            if translated != entry.english_name:
                english_name = translated

        maya_name = None
        if rename_nodes and entry.rename_allowed:
            rename_source = translated or entry.source_name
            candidate = sanitize_unique_name(
                rename_source,
                allocated,
                fallback=f"{entry.kind}_{entry.index if entry.index is not None else 'node'}",
            )
            if candidate != _node_leaf(entry.node):
                maya_name = candidate

        plan.append(
            NameChange(
                entry=entry,
                translated_name=translated,
                english_name=english_name,
                maya_name=maya_name,
            )
        )
    return tuple(plan)


def _entry_sort_key(entry: NameEntry) -> Tuple[str, int, str]:
    return (entry.kind, entry.index if entry.index is not None else 2**31 - 1, entry.node)


def _node_leaf(node: str) -> str:
    return str(node).rsplit("|", 1)[-1]


def collect_name_entries(model_root: str, *, cmds_module=None) -> Tuple[NameEntry, ...]:
    """Collect bilingual name metadata owned by ``model_root``."""

    cmds = cmds_module or _maya_cmds()
    root = _canonical_node(cmds, model_root)
    if not root:
        raise NameTranslationError(f"model root does not resolve to one Maya node: {model_root!r}")

    entries: List[NameEntry] = []
    _append_entry(
        entries,
        cmds,
        kind="model",
        node=root,
        source_attr="mmd_model_name",
        english_attr="mmd_model_name_en",
        rename_allowed=False,
    )

    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    for joint in sorted(str(node) for node in joints):
        _append_entry(
            entries,
            cmds,
            kind="bone",
            node=joint,
            source_attr="mmd_bone_name",
            english_attr="mmd_bone_name_en",
            index=_read_int_attr(cmds, joint, "mmd_bone_index"),
        )

    for kind, category, source_attr, english_attr in (
        ("material", "material", "mmd_material_name", "mmd_material_name_en"),
        ("morph", "morph", "mmd_morph_name", "mmd_morph_name_en"),
    ):
        for node in _owned_nodes(root, category, source_attr, cmds):
            _append_entry(
                entries,
                cmds,
                kind=kind,
                node=node,
                source_attr=source_attr,
                english_attr=english_attr,
                index=_read_int_attr(cmds, node, "mmd_morph_index"),
            )

    # Physics names live on custom DAG shapes.  Descendant traversal from the
    # selected root is an explicit ownership boundary and avoids any global
    # scene scan.  Shape renaming is intentionally excluded; the existing
    # node-rename option remains limited to normal authoring nodes.
    for kind, node_type in (
        ("rigid_body", "mmdRigidBodyShape"),
        ("joint", "mmdPhysicsJointShape"),
    ):
        shapes = cmds.listRelatives(
            root,
            allDescendents=True,
            type=node_type,
            fullPath=True,
        ) or []
        for shape in sorted(str(node) for node in shapes):
            _append_entry(
                entries,
                cmds,
                kind=kind,
                node=shape,
                source_attr="nameJp",
                english_attr="nameEn",
                index=_read_int_attr(cmds, shape, "pmxIndex"),
                rename_allowed=False,
            )
    return tuple(sorted(entries, key=_entry_sort_key))


def _append_entry(
    entries: List[NameEntry],
    cmds,
    *,
    kind: str,
    node: str,
    source_attr: str,
    english_attr: str,
    index: Optional[int] = None,
    rename_allowed: bool = True,
) -> None:
    if not _has_attr(cmds, node, source_attr):
        return
    entries.append(
        NameEntry(
            kind=kind,
            node=node,
            source_name=_read_string_attr(cmds, node, source_attr),
            english_name=_read_string_attr(cmds, node, english_attr),
            english_attr=english_attr,
            index=index,
            rename_allowed=rename_allowed,
        )
    )


def _owned_nodes(root: str, category: str, source_attr: str, cmds) -> Tuple[str, ...]:
    from mmd_tools.core import model_registry

    members = model_registry.list_model_registry_members(root, category)
    if members is not None:
        return tuple(sorted(_canonical_node(cmds, node) for node in members if _canonical_node(cmds, node)))

    # Legacy scenes have no registry.  Use only the explicit reverse ownership
    # link and the requested metadata attribute; never scan unrelated names.
    owned: List[str] = []
    for node in cmds.ls() or []:
        node = _canonical_node(cmds, node)
        if not node or not _has_attr(cmds, node, source_attr) or not _has_attr(cmds, node, "mmd_model_root"):
            continue
        roots = cmds.listConnections(
            f"{node}.mmd_model_root",
            source=True,
            destination=False,
        ) or []
        if any(_canonical_node(cmds, candidate) == root for candidate in roots):
            owned.append(node)
    return tuple(sorted(set(owned)))


def resolve_model_root(model_root: Optional[str] = None, *, cmds_module=None) -> str:
    """Resolve an explicit root or one unambiguous selected/scene model."""

    cmds = cmds_module or _maya_cmds()
    if model_root:
        root = _canonical_node(cmds, model_root)
        if root:
            return root
        raise NameTranslationError(f"model root does not resolve: {model_root!r}")

    from mmd_tools.services.scene_model_service import SceneModelService

    service = SceneModelService(cmds_module=cmds)
    models = service.list_mmd_models()
    selected = service.resolve_model_from_selection(models)
    if selected:
        return selected
    if len(models) == 1:
        return models[0]
    if not models:
        raise NameTranslationError("no MMD model root was found in the scene")
    raise NameTranslationError("select exactly one MMD model or pass model_root explicitly")


def apply_translation_plan(plan: Sequence[NameChange], *, cmds_module=None) -> Tuple[NameChange, ...]:
    """Apply one validated plan in a single Maya undo chunk."""

    changes = tuple(change for change in plan if change.has_changes)
    if not changes:
        return changes
    cmds = cmds_module or _maya_cmds()
    opened = False
    try:
        cmds.undoInfo(openChunk=True, chunkName="Translate MMD Names")
        opened = True
        for change in changes:
            if change.english_name is not None:
                cmds.setAttr(
                    f"{change.entry.node}.{change.entry.english_attr}",
                    change.english_name,
                    type="string",
                )
        # Rename deepest DAG nodes first so a parent rename never invalidates a
        # descendant path that is still waiting to be applied.
        renames = sorted(
            (change for change in changes if change.maya_name is not None),
            key=lambda change: change.entry.node.count("|"),
            reverse=True,
        )
        for change in renames:
            cmds.rename(change.entry.node, change.maya_name)
    except Exception:
        if opened:
            cmds.undoInfo(closeChunk=True)
            opened = False
            try:
                cmds.undo()
            except Exception:
                pass
        raise
    finally:
        if opened:
            cmds.undoInfo(closeChunk=True)
    return changes


def run(
    *_args,
    dictionary_path: Optional[str] = None,
    model_root: Optional[str] = None,
    dry_run: bool = False,
    set_english: bool = True,
    overwrite: bool = False,
    rename_nodes: bool = False,
    cmds_module=None,
):
    """Run the tool from the menu, Script Editor or a Python caller."""

    cmds = cmds_module or _maya_cmds()
    if not dictionary_path:
        selected = cmds.fileDialog2(
            fileMode=1,
            caption="Select MMD name translation dictionary",
            fileFilter="CSV files (*.csv);;All files (*.*)",
        ) or []
        if not selected:
            return tuple()
        dictionary_path = selected[0]
    root = resolve_model_root(model_root, cmds_module=cmds)
    entries = collect_name_entries(root, cmds_module=cmds)
    target_names = {_node_leaf(entry.node) for entry in entries if entry.rename_allowed}
    scene_names = {_node_leaf(node) for node in (cmds.ls(long=True) or [])}
    plan = build_translation_plan(
        entries,
        load_translation_dictionary(dictionary_path),
        set_english=set_english,
        overwrite=overwrite,
        rename_nodes=rename_nodes,
        used_names=scene_names - target_names,
    )
    changes = tuple(change for change in plan if change.has_changes)
    for line in format_preview(changes):
        print(line)
    if dry_run:
        return changes
    return apply_translation_plan(changes, cmds_module=cmds)


def format_preview(plan: Sequence[NameChange]) -> Tuple[str, ...]:
    """Format stable, human-readable preview lines."""

    lines = []
    for change in plan:
        label = change.entry.kind
        if change.entry.index is not None:
            label = f"{label}[{change.entry.index}]"
        details = [f"{label}: {change.entry.node}"]
        if change.english_name is not None:
            details.append(f"EnglishName={change.english_name!r}")
        if change.maya_name is not None:
            details.append(f"node={change.maya_name!r}")
        lines.append("; ".join(details))
    return tuple(lines)


def _read_string_attr(cmds, node: str, attr: str) -> str:
    if not _has_attr(cmds, node, attr):
        return ""
    return str(cmds.getAttr(f"{node}.{attr}") or "")


def _read_int_attr(cmds, node: str, attr: str) -> Optional[int]:
    if not _has_attr(cmds, node, attr):
        return None
    try:
        return int(cmds.getAttr(f"{node}.{attr}"))
    except (TypeError, ValueError):
        return None


def _has_attr(cmds, node: str, attr: str) -> bool:
    try:
        return bool(cmds.attributeQuery(attr, node=node, exists=True))
    except Exception:
        return False


def _canonical_node(cmds, node: str) -> Optional[str]:
    if not node or not cmds.objExists(node):
        return None
    matches = cmds.ls(node, long=True) or []
    if len(matches) != 1:
        return None
    return str(matches[0])


def _maya_cmds():
    try:
        from maya import cmds
    except ImportError as exc:
        raise NameTranslationError("translate_names.py must run inside Maya or mayapy") from exc
    return cmds


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point for ``mayapy mmd_tools/tools/translate_names.py``."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dictionary", help="UTF-8 two-column Japanese-to-English CSV")
    parser.add_argument("--root", dest="model_root", help="explicit MMD model root")
    parser.add_argument("--dry-run", action="store_true", help="print changes without writing")
    parser.add_argument("--no-english", action="store_true", help="do not update EnglishName attributes")
    parser.add_argument("--overwrite", action="store_true", help="overwrite non-empty EnglishName attributes")
    parser.add_argument("--rename-nodes", action="store_true", help="also rename eligible Maya nodes")
    args = parser.parse_args(argv)
    run(
        dictionary_path=args.dictionary,
        model_root=args.model_root,
        dry_run=args.dry_run,
        set_english=not args.no_english,
        overwrite=args.overwrite,
        rename_nodes=args.rename_nodes,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by mayapy/Script Editor
    raise SystemExit(main())
