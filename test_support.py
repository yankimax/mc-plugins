import importlib.machinery
import importlib.util
import os
import pathlib
import sys
from types import ModuleType


def ensure_sdk_python_dir(test_file: str) -> pathlib.Path:
    test_path = pathlib.Path(test_file).resolve()
    sdk_dir = test_path.parents[2] / 'minachan_app' / 'plugins' / 'sdk_python'
    os.environ.setdefault('MINACHAN_SDK_PYTHON_DIR', str(sdk_dir))
    return sdk_dir


def load_plugin_module(
    test_file: str,
    plugin_dir_name: str,
    module_name: str,
) -> ModuleType:
    test_path = pathlib.Path(test_file).resolve()
    ensure_sdk_python_dir(str(test_path))
    plugin_path = test_path.parent / 'files' / plugin_dir_name / 'plugin.py3'
    loader = importlib.machinery.SourceFileLoader(module_name, str(plugin_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError(
            f'failed to create import spec for {plugin_dir_name} plugin'
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module
