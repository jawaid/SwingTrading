"""Pluggable setup detectors. Pick one with `setup.detector` in settings.yaml."""

from swingtrading.setups.base import SetupDetector, SetupResult, make_detector, scan

__all__ = ["SetupDetector", "SetupResult", "make_detector", "scan"]
