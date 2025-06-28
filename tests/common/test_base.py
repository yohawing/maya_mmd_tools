import unittest
import os
import sys
import tempfile
import shutil

# Add the project root to sys.path to allow importing mmd_tools modules
# This assumes tests are run from the project root or a level below
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

class TestBase(unittest.TestCase):
    """
    Base class for all tests in the project.
    Provides common setup/teardown and utility methods.
    """

    @classmethod
    def setUpClass(cls):
        """
        Set up for all tests in the class.
        """
        print(f"\nSetting up test class: {cls.__name__}")

    @classmethod
    def tearDownClass(cls):
        """
        Tear down for all tests in the class.
        """
        print(f"Tearing down test class: {cls.__name__}")

    def setUp(self):
        """
        Set up before each test method.
        """
        print(f"  Running test: {self._testMethodName}")
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        """
        Tear down after each test method.
        """
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    # Example utility method
    def assertFileExists(self, file_path):
        self.assertTrue(os.path.exists(file_path), f"File does not exist: {file_path}")

    def assertFileContent(self, file_path, expected_content):
        with open(file_path, 'r') as f:
            content = f.read()
        self.assertEqual(content, expected_content, f"File content mismatch for {file_path}")
