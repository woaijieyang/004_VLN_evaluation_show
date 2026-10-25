"""Backward compatible import for process_show.autostart."""
import importlib,sys
sys.modules[__name__]=importlib.import_module("process_show.capture.autostart")
