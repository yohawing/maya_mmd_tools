import struct

from mmd_tools.core.exceptions import MMDParseException


class PmxVertex:
    """
    PMXファイルの頂点データを保持するクラス。
    """
    def __init__(self, header):
        self.header = header
        self.position = (0.0, 0.0, 0.0)
        self.normal = (0.0, 0.0, 0.0)
        self.uv = (0.0, 0.0)
        self.additional_uvs = []
        self.weight_transform_type = 0
        self.bone_indices = []
        self.bone_weights = []
        self.sdef_c = (0.0, 0.0, 0.0)
        self.sdef_r0 = (0.0, 0.0, 0.0)
        self.sdef_r1 = (0.0, 0.0, 0.0)
        self.edge_magnification = 0.0

    def parse(self, f):
        """
        ファイルハンドルからPMX頂点データを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。

        Raises:
            MMDParseException: 頂点データの解析に失敗した場合。
        """
        try:
            self.position = struct.unpack('<fff', f.read(12))
            self.normal = struct.unpack('<fff', f.read(12))
            self.uv = struct.unpack('<ff', f.read(8))

            # Additional UVs
            for _ in range(self.header.additional_uv):
                self.additional_uvs.append(struct.unpack('<ffff', f.read(16)))

            self.weight_transform_type = struct.unpack('<B', f.read(1))[0]

            bone_index_format = {1: '<B', 2: '<H', 4: '<I'}[self.header.bone_index_size]

            if self.weight_transform_type == 0:  # BDEF1
                self.bone_indices.append(struct.unpack(bone_index_format, f.read(self.header.bone_index_size))[0])
            elif self.weight_transform_type == 1:  # BDEF2
                self.bone_indices.append(struct.unpack(bone_index_format, f.read(self.header.bone_index_size))[0])
                self.bone_indices.append(struct.unpack(bone_index_format, f.read(self.header.bone_index_size))[0])
                self.bone_weights.append(struct.unpack('<f', f.read(4))[0])
            elif self.weight_transform_type == 2:  # BDEF4
                for _ in range(4):
                    self.bone_indices.append(struct.unpack(bone_index_format, f.read(self.header.bone_index_size))[0])
                for _ in range(4):
                    self.bone_weights.append(struct.unpack('<f', f.read(4))[0])
            elif self.weight_transform_type == 3:  # SDEF
                self.bone_indices.append(struct.unpack(bone_index_format, f.read(self.header.bone_index_size))[0])
                self.bone_indices.append(struct.unpack(bone_index_format, f.read(self.header.bone_index_size))[0])
                self.bone_weights.append(struct.unpack('<f', f.read(4))[0])
                self.sdef_c = struct.unpack('<fff', f.read(12))
                self.sdef_r0 = struct.unpack('<fff', f.read(12))
                self.sdef_r1 = struct.unpack('<fff', f.read(12))
            elif self.weight_transform_type == 4:  # QDEF (PMX 2.1 only)
                if self.header.version < 2.1:
                    raise ValueError("QDEF weight transform type is only supported in PMX 2.1 and later.")
                for _ in range(4):
                    self.bone_indices.append(struct.unpack(bone_index_format, f.read(self.header.bone_index_size))[0])
                for _ in range(4):
                    self.bone_weights.append(struct.unpack('<f', f.read(4))[0])
            else:
                raise ValueError(f"Unknown weight transform type: {self.weight_transform_type}")

            self.edge_magnification = struct.unpack('<f', f.read(4))[0]
        except struct.error as e:
            raise MMDParseException(f"Failed to parse PMX vertex data: {e}") from e

    def write(self, f):
        """
        PMX頂点データをファイルハンドルに書き込む。

        Args:
            f (file): バイナリ書き込みモードで開かれたファイルハンドル。
        """
        # Position and normal
        f.write(struct.pack('<fff', *self.position))
        f.write(struct.pack('<fff', *self.normal))
        f.write(struct.pack('<ff', *self.uv))

        # Additional UVs
        for uv in self.additional_uvs:
            f.write(struct.pack('<ffff', *uv))

        # Weight transform type
        f.write(struct.pack('<B', self.weight_transform_type))

        bone_index_format = {1: '<B', 2: '<H', 4: '<I'}[self.header.bone_index_size]

        if self.weight_transform_type == 0:  # BDEF1
            f.write(struct.pack(bone_index_format, self.bone_indices[0]))
        elif self.weight_transform_type == 1:  # BDEF2
            f.write(struct.pack(bone_index_format, self.bone_indices[0]))
            f.write(struct.pack(bone_index_format, self.bone_indices[1]))
            f.write(struct.pack('<f', self.bone_weights[0]))
        elif self.weight_transform_type == 2:  # BDEF4
            for bone_index in self.bone_indices:
                f.write(struct.pack(bone_index_format, bone_index))
            for bone_weight in self.bone_weights:
                f.write(struct.pack('<f', bone_weight))
        elif self.weight_transform_type == 3:  # SDEF
            f.write(struct.pack(bone_index_format, self.bone_indices[0]))
            f.write(struct.pack(bone_index_format, self.bone_indices[1]))
            f.write(struct.pack('<f', self.bone_weights[0]))
            f.write(struct.pack('<fff', *self.sdef_c))
            f.write(struct.pack('<fff', *self.sdef_r0))
            f.write(struct.pack('<fff', *self.sdef_r1))
        elif self.weight_transform_type == 4:  # QDEF (PMX 2.1 only)
            if self.header.version < 2.1:
                raise ValueError("QDEF weight transform type is only supported in PMX 2.1 and later.")
            for bone_index in self.bone_indices:
                f.write(struct.pack(bone_index_format, bone_index))
            for bone_weight in self.bone_weights:
                f.write(struct.pack('<f', bone_weight))
        else:
            raise ValueError(f"Unknown weight transform type: {self.weight_transform_type}")

        # Edge magnification
        f.write(struct.pack('<f', self.edge_magnification))
