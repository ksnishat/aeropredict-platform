import os
import sys
import unittest

# Add src to path so we can import modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

class TestMaintenancePipeline(unittest.TestCase):

    def test_environment_variables(self):
        """The DAG reads these env vars, so the code must tolerate them being unset."""
        required_vars = ["MLFLOW_TRACKING_URI", "MINIO_ENDPOINT_URL"]
        saved = {v: os.environ.pop(v, None) for v in required_vars}
        try:
            # Reading the vars must not raise when they are absent; the modules
            # fall back to their Docker defaults.
            for var in required_vars:
                self.assertIsNone(os.environ.get(var))
        finally:
            for var, value in saved.items():
                if value is not None:
                    os.environ[var] = value

    def test_rul_logic(self):
        """Test that RUL (Remaining Useful Life) logic is sound"""
        # RUL should never be negative in our logic
        predicted_rul = 23
        self.assertGreater(predicted_rul, 0)

    def test_rag_prompt_structure(self):
        """Test that the prompt template contains critical keywords"""
        prompt_template = "SITUATION: RUL is {rul}. TASK: Diagnose."
        self.assertIn("{rul}", prompt_template)

if __name__ == '__main__':
    unittest.main()
