import importlib
import sys
from importlib.util import spec_from_file_location
from pathlib import Path

root = Path(__file__).parent
editable_backend = root / "installed-backend" / "my_backend.py"
candidate_backend = root / "candidate-backend"


class _EditableFinder:
    def find_spec(self, fullname: str, path: object = None, target: object = None) -> object:
        if fullname == "my_backend":
            return spec_from_file_location(fullname, editable_backend)
        return None


# This models the .pth-installed finder inherited by pip's in-process build
# environment. The candidate backend-path is first on sys.path but loses.
sys.meta_path.insert(0, _EditableFinder())
sys.path.insert(0, str(candidate_backend))

backend = importlib.import_module("my_backend")
print(f"requested backend-path: {candidate_backend}")
print(f"loaded backend: {backend.__file__}")
print(f"loaded origin marker: {backend.ORIGIN}")
if Path(backend.__file__).parent != candidate_backend:
    print("BackendInvalid: backend was not loaded from backend-path")
