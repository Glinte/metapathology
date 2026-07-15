import key_value.aio  # noqa: F401

TEMPLATE = "{which} from bt-repro!"

from .bar import use_template as bar_use_template  # noqa: E402
from .foo import use_template as foo_use_template  # noqa: E402

__all__ = ["TEMPLATE", "bar_use_template", "foo_use_template"]
