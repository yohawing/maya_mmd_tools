import ctypes
from types import SimpleNamespace
from unittest import TestCase, mock

from mmd_tools.core.native.mmd_anim_runtime_handles import MmdRuntimeModel
from mmd_tools.core.native.mmd_anim_runtime_signatures import setup_function_signatures
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_FEATURE_MODEL_DESCRIPTOR,
    MMD_RUNTIME_MODEL_DESCRIPTOR_FLAGS_NONE,
    MMD_RUNTIME_MODEL_DESCRIPTOR_VERSION_V1,
    MmdRuntimeModelAppendDescriptor,
    MmdRuntimeModelBoneMorphOffsetDescriptor,
    MmdRuntimeModelBoneDescriptor,
    MmdRuntimeModelDescriptor,
    MmdRuntimeModelGroupMorphOffsetDescriptor,
    MmdRuntimeModelIkLinkDescriptor,
    MmdRuntimeModelIkSolverDescriptor,
)


def _descriptors(**overrides):
    values = {
        "bones": [
            MmdRuntimeModelBoneDescriptor(
                parent_index=-1,
                rest_position_xyz=(1.0, 2.0, 3.0),
                transform_order=0,
                flags=0,
                fixed_axis_xyz=(0.0, 0.0, 0.0),
                local_axis_x_xyz=(0.0, 0.0, 0.0),
                local_axis_z_xyz=(0.0, 0.0, 0.0),
            )
        ],
        "ik_solvers": [],
        "ik_links": [],
        "append_transforms": [],
        "morph_count": 0,
        "bone_morph_offsets": [],
        "group_morph_offsets": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TestModelDescriptorAbiLayout(TestCase):
    def test_manifest_layout(self):
        expected = {
            MmdRuntimeModelBoneDescriptor: (60, 4, {"parent_index": 0, "rest_position_xyz": 4, "local_axis_z_xyz": 48}),
            MmdRuntimeModelIkSolverDescriptor: (32, 8, {"link_offset": 8, "limit_angle": 28}),
            MmdRuntimeModelIkLinkDescriptor: (32, 4, {"angle_limit_min_xyz": 8, "angle_limit_max_xyz": 20}),
            MmdRuntimeModelAppendDescriptor: (16, 4, {"ratio": 8, "flags": 12}),
            MmdRuntimeModelBoneMorphOffsetDescriptor: (36, 4, {"position_offset_xyz": 8, "rotation_offset_xyzw": 20}),
            MmdRuntimeModelGroupMorphOffsetDescriptor: (12, 4, {"child_morph_index": 4, "ratio": 8}),
            MmdRuntimeModelDescriptor: (120, 8, {"bones": 16, "morph_count": 80, "group_morph_offset_count": 112}),
        }
        for record, (size, alignment, offsets) in expected.items():
            self.assertEqual(ctypes.sizeof(record), size)
            self.assertEqual(ctypes.alignment(record), alignment)
            for name, offset in offsets.items():
                self.assertEqual(getattr(record, name).offset, offset)


class TestModelDescriptorWrapper(TestCase):
    def test_feature_and_symbol_guards(self):
        class FeatureOff:
            @staticmethod
            def mmd_runtime_feature_flags():
                return 0

        with mock.patch.object(MmdRuntimeModel, "_get_library", return_value=FeatureOff()):
            self.assertIsNone(MmdRuntimeModel.from_descriptors(_descriptors()))

        class SymbolMissing:
            @staticmethod
            def mmd_runtime_feature_flags():
                return MMD_RUNTIME_FEATURE_MODEL_DESCRIPTOR

        with mock.patch.object(MmdRuntimeModel, "_get_library", return_value=SymbolMissing()):
            self.assertIsNone(MmdRuntimeModel.from_descriptors(_descriptors()))

    def test_descriptor_header_and_empty_arrays(self):
        captured = []

        class FakeLib:
            @staticmethod
            def mmd_runtime_feature_flags():
                return MMD_RUNTIME_FEATURE_MODEL_DESCRIPTOR

            @staticmethod
            def mmd_runtime_model_create_from_descriptor(pointer):
                descriptor = pointer.contents
                captured.append(descriptor)
                return 17

            @staticmethod
            def mmd_runtime_model_free(_handle):
                pass

        with mock.patch.object(MmdRuntimeModel, "_get_library", return_value=FakeLib()):
            model = MmdRuntimeModel.from_descriptors(_descriptors())
        self.assertIsNotNone(model)
        descriptor = captured[0]
        self.assertEqual(descriptor.struct_size, ctypes.sizeof(MmdRuntimeModelDescriptor))
        self.assertEqual(descriptor.descriptor_version, MMD_RUNTIME_MODEL_DESCRIPTOR_VERSION_V1)
        self.assertEqual(descriptor.flags, MMD_RUNTIME_MODEL_DESCRIPTOR_FLAGS_NONE)
        self.assertEqual(descriptor.reserved, 0)
        self.assertEqual(descriptor.bone_count, 1)
        for field, count in (
            ("ik_solvers", "ik_solver_count"),
            ("ik_links", "ik_link_count"),
            ("append_transforms", "append_transform_count"),
            ("bone_morph_offsets", "bone_morph_offset_count"),
            ("group_morph_offsets", "group_morph_offset_count"),
        ):
            self.assertFalse(bool(getattr(descriptor, field)))
            self.assertEqual(getattr(descriptor, count), 0)
        model.free()

    def test_null_handle_copies_last_error_before_any_other_native_call(self):
        calls = []

        class FakeLib:
            @staticmethod
            def mmd_runtime_feature_flags():
                calls.append("feature")
                return MMD_RUNTIME_FEATURE_MODEL_DESCRIPTOR

            @staticmethod
            def mmd_runtime_model_create_from_descriptor(_pointer):
                calls.append("create")
                return None

            @staticmethod
            def mmd_runtime_last_error_message():
                calls.append("last_error")
                return b"descriptor.bones[0].parent_index: invalid"

        with mock.patch.object(MmdRuntimeModel, "_get_library", return_value=FakeLib()):
            with self.assertLogs("mmd_tools.core.native.mmd_anim_runtime_handles", level="ERROR") as logs:
                self.assertIsNone(MmdRuntimeModel.from_descriptors(_descriptors()))
        self.assertEqual(calls, ["feature", "create", "last_error"])
        self.assertIn("descriptor.bones[0].parent_index: invalid", "\n".join(logs.output))


class TestModelDescriptorSignature(TestCase):
    def test_constructor_signature_is_single_descriptor_pointer(self):
        class Symbol:
            pass

        class FakeLib:
            def __init__(self):
                self._symbols = {}

            def __getattr__(self, name):
                # setup_function_signatures treats the runtime surface as
                # optional except for assigning signatures to core symbols.
                symbol = self._symbols.setdefault(name, Symbol())
                setattr(self, name, symbol)
                return symbol

        lib = FakeLib()
        setup_function_signatures(lib)
        symbol = lib.mmd_runtime_model_create_from_descriptor
        self.assertEqual(symbol.argtypes, [ctypes.POINTER(MmdRuntimeModelDescriptor)])
        self.assertIs(symbol.restype, ctypes.c_void_p)


if __name__ == "__main__":
    import unittest

    unittest.main()
