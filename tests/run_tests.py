import unittest
import os
import sys

# Add the project root to sys.path to allow importing src modules
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

def run_all_tests():
    loader = unittest.TestLoader()
    # Discover tests in the 'tests' directory
    # start_dir can be 'tests' or the project root if you want to discover all tests
    suite = loader.discover(start_dir='tests', pattern='test_*.py')

    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)

if __name__ == '__main__':
    run_all_tests()
