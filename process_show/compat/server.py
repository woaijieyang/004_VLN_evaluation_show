"""Backward compatible import for process_show.server."""
import importlib,sys
sys.modules[__name__]=importlib.import_module("process_show.service.server")
