"""Backward compatible import for process_show.scope."""
import importlib,sys
sys.modules[__name__]=importlib.import_module("process_show.storage.scope")
