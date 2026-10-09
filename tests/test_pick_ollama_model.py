"""Unit tests for music LLM model resolution (no live Ollama required)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))

from server import pick_ollama_model  # noqa: E402


class PickOllamaModelTest(unittest.TestCase):
    def test_prefers_exact_preferred(self):
        available = {"mistral-nemo:latest", "qwen2.5:7b"}
        self.assertEqual(pick_ollama_model("mistral-nemo:latest", available), "mistral-nemo:latest")

    def test_matches_bare_preferred_tag(self):
        available = {"mistral-nemo:latest", "qwen2.5:7b"}
        self.assertEqual(pick_ollama_model("mistral-nemo", available), "mistral-nemo:latest")

    def test_falls_back_when_preferred_missing(self):
        available = {
            "nandhank/qwen3.5-abliterated-truthful:9b",
            "mistral-small3.2:latest",
            "qwen2.5:14b",
            "qwen2.5:7b",
        }
        # Prefer 7b over larger fallbacks when mistral-nemo is absent.
        self.assertEqual(pick_ollama_model("mistral-nemo", available), "qwen2.5:7b")

    def test_falls_back_to_any_when_no_named_match(self):
        available = {"custom-local:latest"}
        self.assertEqual(pick_ollama_model("lyrics", available), "custom-local:latest")

    def test_last_resort_prefers_smaller_model(self):
        available = {"giant-lab:70b", "tiny-lab:7b"}
        self.assertEqual(pick_ollama_model("missing", available), "tiny-lab:7b")

    def test_none_when_empty(self):
        self.assertIsNone(pick_ollama_model("mistral-nemo", set()))


if __name__ == "__main__":
    unittest.main()
