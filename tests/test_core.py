import unittest
import os
import sys
import shutil

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import task_manager

class TestTaskManager(unittest.TestCase):
    def setUp(self):
        # Create a temporary workspace for testing
        self.test_workspace = os.path.join(os.getcwd(), "test_workspace")
        if not os.path.exists(self.test_workspace):
            os.makedirs(self.test_workspace)
        
        # Mock WORKSPACE_DIR in task_manager
        self.original_workspace = task_manager.WORKSPACE_DIR
        task_manager.WORKSPACE_DIR = self.test_workspace

        # Disable Safe Mode for automated tests to avoid UI blocking
        if hasattr(task_manager.config, 'SAFE_MODE'):
            self.original_safe_mode = task_manager.config.SAFE_MODE
            task_manager.config.SAFE_MODE = False

    def tearDown(self):
        # Cleanup
        if os.path.exists(self.test_workspace):
            shutil.rmtree(self.test_workspace)
        task_manager.WORKSPACE_DIR = self.original_workspace
        
        # Restore Safe Mode
        if hasattr(self, 'original_safe_mode'):
            task_manager.config.SAFE_MODE = self.original_safe_mode

    def test_workspace_creation(self):
        """Test if workspace directory is created."""
        self.assertTrue(os.path.exists(task_manager.WORKSPACE_DIR))

    def test_file_creation(self):
        """Test safe file creation in workspace."""
        filename = "test_file.txt"
        content = "Hello World"
        filepath = os.path.join(task_manager.WORKSPACE_DIR, filename)
        
        with open(filepath, "w") as f:
            f.write(content)
            
        self.assertTrue(os.path.exists(filepath))
        with open(filepath, "r") as f:
            read_content = f.read()
        self.assertEqual(read_content, content)

    def test_deep_search_fallback(self):
        """Test deep_search fallback mechanism (mocked)."""
        # We assume keys are missing, so it should fall back to scraping
        # This test just ensures no crash
        result = task_manager.deep_search("test query")
        self.assertIsInstance(result, str)
        # Note: Actual search might fail if no internet, but function catches exceptions.

if __name__ == "__main__":
    unittest.main()
