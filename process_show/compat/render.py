"""Backward compatible import for process_show.render."""
import importlib,sys
sys.modules[__name__]=importlib.import_module("process_show.media.render")
