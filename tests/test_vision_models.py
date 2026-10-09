import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import vision_models as vm  # noqa: E402


class VisionModelsTest(unittest.TestCase):
    def test_vision_true(self):
        for name in (
            "llava:latest",
            "bakllava",
            "moondream",
            "qwen2-vl:7b",
            "qwen2.5-vl",
            "llama3.2-vision",
            "gemma3:12b",
            "granite-vision",
            "minicpm-v",
        ):
            self.assertTrue(vm.model_supports_vision(name), name)

    def test_vision_false(self):
        for name in ("mistral-nemo", "llama3.2:latest", "codellama", "", None):
            self.assertFalse(vm.model_supports_vision(name), name)

    def test_normalize_strips_data_url(self):
        raw = "iVBORw0KGgo="
        self.assertEqual(
            vm.normalize_image_b64("data:image/png;base64," + raw),
            raw,
        )
        self.assertEqual(vm.normalize_image_b64(raw), raw)
