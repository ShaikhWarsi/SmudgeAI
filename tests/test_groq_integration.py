import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
# Set provider to groq for testing
config.AI_PROVIDER = "groq"
config.GROQ_API_KEY = "test_key"

import ai_engine

class TestGroqIntegration(unittest.TestCase):
    
    def setUp(self):
        # Reset globals
        ai_engine.groq_client = None
        ai_engine.groq_history = []
        ai_engine.current_groq_model_index = 0
        ai_engine.AI_PROVIDER = "groq" # Ensure it's set in the module
        
    @patch('ai_engine.groq.Groq')
    def test_initialize_groq(self, mock_groq):
        ai_engine.initialize_model([])
        mock_groq.assert_called_once_with(api_key="test_key")
        self.assertIsNotNone(ai_engine.groq_client)
        
    @patch('ai_engine.groq.Groq')
    def test_model_cycling(self, mock_groq):
        # Mock the client instance
        mock_client = MagicMock()
        mock_groq.return_value = mock_client
        ai_engine.groq_client = mock_client
        
        # Setup the mock to raise RateLimitError on first call, then succeed
        import groq
        
        # We need to mock the async behavior. 
        # Since _get_groq_response uses asyncio.to_thread, the side_effect should just happen when called.
        # But wait, asyncio.to_thread runs a sync function in a thread.
        # So the mock needs to be a standard mock, not AsyncMock for the completions.create part.
        
        # First call raises RateLimit, Second call succeeds
        mock_client.chat.completions.create.side_effect = [
            groq.RateLimitError(message="Rate limit", response=MagicMock(), body=None),
            "Success Response"
        ]
        
        # Run the async function
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        response = loop.run_until_complete(ai_engine._get_groq_response([], None))
        
        self.assertEqual(response, "Success Response")
        # Check if model index incremented
        self.assertEqual(ai_engine.current_groq_model_index, 1)
        
    @patch('ai_engine.groq.Groq')
    def test_send_tool_results(self, mock_groq):
        # Setup basic mocks
        mock_client = MagicMock()
        mock_groq.return_value = mock_client
        ai_engine.groq_client = mock_client
        ai_engine.groq_history = [{"role": "user", "content": "hi"}]
        
        mock_client.chat.completions.create.return_value = "Next Response"
        
        tool_outputs = [{"tool_call_id": "123", "content": "result"}]
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        response = loop.run_until_complete(ai_engine.send_groq_tool_results(tool_outputs))
        
        self.assertEqual(response, "Next Response")
        # Check if tool result was added to history
        self.assertEqual(len(ai_engine.groq_history), 2)
        self.assertEqual(ai_engine.groq_history[1]['role'], 'tool')

if __name__ == '__main__':
    unittest.main()
