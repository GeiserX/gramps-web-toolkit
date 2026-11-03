# keeps gedcom/, apply/, apply/tests/ and core/ on sys.path so the tests import them without installing
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "apply"), os.path.join(HERE, "..", "apply", "tests"),
                os.path.join(HERE, "..", "core")]
