"""Backward compatible import for process_show.adapters."""
import importlib,sys
sys.modules[__name__]=importlib.import_module("process_show.capture.adapters")
