"""Backward compatible import for process_show.map_jobs."""
import importlib,sys
sys.modules[__name__]=importlib.import_module("process_show.maps.map_jobs")
