"""Backward compatible import for process_show.core."""
import importlib,sys
sys.modules[__name__]=importlib.import_module("process_show.storage.core")
