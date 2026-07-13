"""Logging boundary tests for mmd_parser routing messages.

Internal route "Starting parse as ..." messages must be DEBUG.
Outer start and completion messages remain INFO.
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from mmd_tools.core import mmd_parser


def _message_templates(mock_log):
    # call[0] is args tuple (Py3.7-safe; _Call.args is 3.8+)
    return [call[0][0] for call in mock_log.call_args_list if call[0]]


def _write_temp_file(suffix, content):
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        os.write(fd, content)
    finally:
        os.close(fd)
    return path


class TestMmdParserRouteLogging(unittest.TestCase):
    """Internal route diagnostics must use DEBUG, not INFO."""

    def tearDown(self):
        path = getattr(self, "_temp_path", None)
        if path and os.path.exists(path):
            os.remove(path)

    def _assert_route_debug_not_info(self, mock_logger, route_msg):
        debug_messages = _message_templates(mock_logger.debug)
        info_messages = _message_templates(mock_logger.info)
        self.assertIn(route_msg, debug_messages)
        self.assertNotIn(route_msg, info_messages)

    def _assert_outer_start_info(self, mock_logger, file_path):
        info_messages = _message_templates(mock_logger.info)
        expected = "Starting MMD file parsing: {}".format(file_path)
        self.assertIn(expected, info_messages)

    def test_vpd_route_uses_debug_not_info(self):
        self._temp_path = _write_temp_file(".vpd", b"Vocaloid Pose Data file\n")
        mock_parser = MagicMock()

        with patch.object(mmd_parser, "logger") as mock_logger, patch.object(
            mmd_parser, "VpdData", return_value=mock_parser
        ):
            result = mmd_parser.parse_mmd_file(self._temp_path)

        self.assertIs(result, mock_parser)
        mock_parser.parse_file.assert_called_once_with(self._temp_path)
        self._assert_route_debug_not_info(mock_logger, "Starting parse as VPD file")
        self._assert_outer_start_info(mock_logger, self._temp_path)
        info_messages = _message_templates(mock_logger.info)
        self.assertIn("VPD file parsing completed", info_messages)

    def test_pmd_route_uses_debug_not_info(self):
        self._temp_path = _write_temp_file(".pmd", b"Pmd\x00")
        sentinel = object()

        with patch.object(mmd_parser, "logger") as mock_logger, patch.object(
            mmd_parser, "parse_pmd_file_as_pmx", return_value=sentinel
        ) as mock_pmd:
            result = mmd_parser.parse_mmd_file(self._temp_path)

        self.assertIs(result, sentinel)
        mock_pmd.assert_called_once()
        self._assert_route_debug_not_info(mock_logger, "Starting parse as PMD file")
        self._assert_outer_start_info(mock_logger, self._temp_path)

    def test_pmx_route_uses_debug_not_info(self):
        self._temp_path = _write_temp_file(".pmx", b"PMX ")
        sentinel = object()

        with patch.object(mmd_parser, "logger") as mock_logger, patch.object(
            mmd_parser, "parse_pmx_file", return_value=sentinel
        ) as mock_pmx:
            result = mmd_parser.parse_mmd_file(self._temp_path)

        self.assertIs(result, sentinel)
        mock_pmx.assert_called_once()
        self._assert_route_debug_not_info(mock_logger, "Starting parse as PMX file")
        self._assert_outer_start_info(mock_logger, self._temp_path)

    def test_vmd_route_uses_debug_not_info(self):
        # VMD magic is longer than 4 bytes; first 4 must not look like PMD/PMX.
        self._temp_path = _write_temp_file(".vmd", b"Vocaloid Motion Data 0002")
        mock_parser = MagicMock()

        with patch.object(mmd_parser, "logger") as mock_logger, patch.object(
            mmd_parser, "VmdData", return_value=mock_parser
        ):
            result = mmd_parser.parse_mmd_file(self._temp_path)

        self.assertIs(result, mock_parser)
        mock_parser.parse_file.assert_called_once_with(self._temp_path)
        self._assert_route_debug_not_info(mock_logger, "Starting parse as VMD file")
        self._assert_outer_start_info(mock_logger, self._temp_path)
        info_messages = _message_templates(mock_logger.info)
        self.assertIn("VMD file parsing completed", info_messages)


if __name__ == "__main__":
    unittest.main()
