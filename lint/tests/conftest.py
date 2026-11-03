# keeps lint/, core/ and apply/ on sys.path so the tests import gwt_lint, gwt_core and gwt_apply without installing them
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.dirname(HERE)] + [os.path.join(ROOT, d) for d in ("core", "apply")]
