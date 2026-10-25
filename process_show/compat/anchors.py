"""Backward compatible import for process_show.anchors."""
import importlib,sys
sys.modules[__name__]=importlib.import_module("process_show.playback.anchors")
