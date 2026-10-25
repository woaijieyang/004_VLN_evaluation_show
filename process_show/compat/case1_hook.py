"""Backward compatible import for process_show.case1_hook."""
import importlib,sys
sys.modules[__name__]=importlib.import_module("process_show.integrations.case1_hook")
