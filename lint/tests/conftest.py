# keeps lint/ and core/ on sys.path so the tests import gwt_lint and gwt_core without installing them
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), os.path.join(os.path.dirname(os.path.dirname(HERE)), "core")]
