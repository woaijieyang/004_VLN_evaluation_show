"""Backward compatible import for process_show.sync."""
import importlib,sys
sys.modules[__name__]=importlib.import_module("process_show.service.sync")
