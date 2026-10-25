"""Model-independent one-way navigation telemetry. No model/simulator imports."""
__version__ = "0.1.0"

def __getattr__(name):
    if name == "Recorder":
        from .sdk import Recorder
        return Recorder
    raise AttributeError(name)

from pathlib import Path as _Path
__path__.append(str(_Path(__file__).parent/"compat"))
