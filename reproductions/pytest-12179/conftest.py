import importlib.util
import sys
import warnings
from importlib.metadata import distribution

six_path = distribution("boto").locate_file("boto/vendored/six.py")
spec = importlib.util.spec_from_file_location("boto.vendored.six", six_path)
assert spec is not None
assert spec.loader is not None
six = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = six
spec.loader.exec_module(six)
warnings.filterwarnings("ignore", category=ImportWarning)
