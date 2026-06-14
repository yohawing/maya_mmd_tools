# Material Split Import Design

## Current Behavior

`import.model.separate_meshes_by_material=false` is the default and recommended import path. It creates one mesh and assigns materials per face.

`import.model.separate_meshes_by_material=true` is an opt-in path for workflows that need material-level mesh editing or visibility control. PMX split meshes are compact: each material mesh keeps only vertices referenced by that material's face range and stores the original PMX vertex indices in `mmd_source_vertex_indices`.

The compact split path depends on these mappings:

- `MeshConverter` builds local mesh vertices from PMX material face ranges.
- `MorphConverter` maps PMX vertex morph offsets from source vertex index to local vertex index.
- `BoneConverter` applies skin weights in local vertex order using `mmd_source_vertex_indices`.

## Decision: Keep Material Split Separate From Morph Group Split

Do not overload `separate_meshes_by_material` with morph-driven grouping.

Material split and morph split have different purposes:

- Material split is an editing/display feature. It guarantees one mesh per material with one material assigned to all faces.
- Morph group split is a performance feature. Its goal is to reduce mesh/blendShape combinations while preserving morph evaluation.

Mixing the two would make the setting name misleading and would break expectations for users who explicitly ask for material-level mesh objects.

## Proposed Morph Group Split

The experimental option is:

```python
settings.set("import.model.split_meshes_by_morph_groups", False)
```

The first implementation is PMX-only and does not replace the default unified mesh path.

Recommended grouping:

1. Build `material_vertex_sets` from PMX material `face_count` ranges.
2. For each vertex morph, compute the set of material indices touched by its `vertex_index` offsets.
3. Group vertex morphs by identical touched-material sets.
4. Create one compact mesh per group, containing the faces and vertices for the union of touched materials in that group.
5. Keep materials assigned per face inside each group mesh.

This reduces duplicated blendShape targets when many morphs affect the same limited material set. It may still duplicate faces across groups if different morph groups overlap, so the first prototype must report mesh count, vertex slots, blendShape count, and import time.

## Non-Goals For The First Prototype

- Do not include PMD. PMD morph indexing differs enough that it should be handled after PMX proves useful.
- Do not include material morphs in the grouping key. Material morphs affect shader parameters, not vertex geometry.
- Do not include bone morphs. Bone morphs are imported as metadata nodes and do not require mesh splitting.
- Do not attempt UV morph or additional UV morph support until vertex morph grouping is validated.

## Validation Plan

Compare three paths on the same manifest cases:

- Unified mesh: `--no-separate-meshes`
- Material split: `--separate-meshes`
- Morph group split: future dedicated option

Record at minimum:

- `profile.import_elapsed_sec`
- `profile.mesh_transform_count`
- `profile.skin_cluster_count`
- `profile.blend_shape_count`
- `profile.importer.mesh_converter.mesh_vertex_slots_estimated`
- `profile.importer.morph_result.morphs_converted`
- `profile.importer.morph_result.vertex_morphs_skipped_by_material`

The first target case is `stage07black__Eye_morph`, because it has already exposed material split import cost and has baseline results for unified and material split paths.

Initial `stage07black__Eye_morph` results:

| path | import | mesh transforms | blendShape | skinCluster | vertex slots | morph phase |
|------|--------|-----------------|------------|-------------|--------------|-------------|
| unified | 0.986s | 11 | 1 | 1 | 27,523 | 0.360s |
| material split compact | 0.845s | 26 | 5 | 9 | 27,779 | 0.247s |
| morph group split | 0.750s | 14 | 3 | 4 | 27,523 | 0.152s |
