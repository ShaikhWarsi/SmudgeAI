import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import sys
import os
import time

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock external dependencies BEFORE importing modules that use them
sys.modules['pyautogui'] = MagicMock()
sys.modules['pygetwindow'] = MagicMock()
sys.modules['pynput'] = MagicMock()
sys.modules['pynput.mouse'] = MagicMock()
sys.modules['pynput.keyboard'] = MagicMock()
sys.modules['edge_tts'] = MagicMock()
sys.modules['pygame'] = MagicMock()
sys.modules['google.generativeai'] = MagicMock()

# Now import the modules to test
# We need to make sure PyQt5 is available or mocked if not installed in test env
try:
    from PyQt5.QtCore import QObject, pyqtSignal, QCoreApplication
    app = QCoreApplication([])
except ImportError:
    # If PyQt5 is not installed, we can't test QObject subclasses easily without mocking QObject too
    # But for this environment, we assume it is.
    sys.modules['PyQt5.QtCore'] = MagicMock()
    QObject = object
    pyqtSignal = MagicMock()

import monitoring
import workflow_learner
import ai_engine

class TestKillerFeatures(unittest.TestCase):

    def setUp(self):
        # Reset mocks
        pass

    @patch('monitoring.pyautogui')
    @patch('monitoring.gw')
    @patch('ai_engine.analyze_image', new_callable=AsyncMock)
    def test_monitoring_logic(self, mock_analyze, mock_gw, mock_pyautogui):
        monitor = monitoring.Monitor()
        
        # 1. Test Idle Detection (Not Idle)
        monitor.last_mouse_pos = (0, 0)
        mock_pyautogui.position.return_value = (10, 10) # Moved
        
        monitor._check_activity()
        self.assertNotEqual(monitor.last_mouse_pos, (0, 0))
        
        # 2. Test Idle Detection (Idle but not long enough)
        monitor.last_mouse_pos = (10, 10)
        mock_pyautogui.position.return_value = (10, 10) # Not moved
        monitor.last_activity_time = time.time() - 10 # 10s idle
        monitor.idle_threshold = 60
        
        monitor._check_activity()
        # Should return early, no analysis
        mock_analyze.assert_not_called()
        
        # 3. Test Idle + Target App
        monitor.last_activity_time = time.time() - 70 # 70s idle
        mock_gw.getActiveWindow.return_value.title = "Visual Studio Code - project"
        
        # Mock analysis result
        mock_analyze.return_value = "I see you're stuck on a SyntaxError."
        
        # We need to mock the async loop or just run _check_activity
        # _check_activity calls asyncio.run inside
        # But since we are mocking ai_engine.analyze_image, it should work if the loop logic is correct.
        # However, _check_activity creates a new loop. If there is a running loop, it might fail.
        # In this test env, there is no running loop.
        
        # Also need to mock os.remove for screenshot cleanup
        with patch('os.remove'), patch('os.path.exists', return_value=True):
             monitor._check_activity()
             
        mock_analyze.assert_called_once()
        # Signal emit check?
        # monitor.alert_signal.emit.assert_called_with(...)
        # Since we can't easily check signal emission without a slot, we trust the logic reached there if analyze was called.

    @patch('workflow_learner.pyautogui')
    @patch('workflow_learner.mouse')
    @patch('workflow_learner.keyboard')
    @patch('ai_engine.generate_workflow_script', new_callable=AsyncMock)
    def test_workflow_learner(self, mock_generate, mock_keyboard, mock_mouse, mock_pyautogui):
        learner = workflow_learner.WorkflowLearner()
        
        # Test Start
        learner.start_learning()
        self.assertTrue(learner.recording)
        
        # Simulate Click
        learner.on_click(100, 200, 'Button.left', True)
        self.assertEqual(len(learner.events), 1)
        self.assertEqual(learner.events[0]['type'], 'click')
        
        # Simulate Key Press
        class MockKey:
            char = 'a'
        learner.on_press(MockKey())
        self.assertEqual(len(learner.events), 2)
        
        # Test Stop & Process
        # We want to verify _process_workflow is called.
        # It runs in a thread.
        # Let's call _process_workflow manually to test generation
        
        learner.recording = True
        mock_generate.return_value = "print('Hello')"
        
        learner._process_workflow()
        
        mock_generate.assert_called_once()

if __name__ == '__main__':
    unittest.main()
