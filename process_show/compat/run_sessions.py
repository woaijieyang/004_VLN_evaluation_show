"""Backward compatible import for process_show.run_sessions."""
import importlib,sys
sys.modules[__name__]=importlib.import_module("process_show.service.run_sessions")
