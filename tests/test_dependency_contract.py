# Powered by Honyeong
from __future__ import annotations

import importlib.metadata
import unittest


class DependencyContractTests(unittest.TestCase):
    def test_ocr_engine_uses_the_verified_version(self) -> None:
        self.assertEqual(
            "1.2.3",
            importlib.metadata.version("rapidocr-onnxruntime"),
        )

    def test_only_one_opencv_distribution_is_installed(self) -> None:
        installed = {
            distribution.metadata["Name"].lower()
            for distribution in importlib.metadata.distributions()
            if distribution.metadata["Name"]
        }
        opencv_distributions = sorted(
            name
            for name in installed
            if name in {"opencv-python", "opencv-python-headless"}
        )
        self.assertEqual(["opencv-python"], opencv_distributions)


if __name__ == "__main__":
    unittest.main()
