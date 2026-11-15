"""Kör alla tester utan pytest:  python tests/run.py [filter]"""
import importlib
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    failed = passed = 0
    for fname in sorted(os.listdir(HERE)):
        if not (fname.startswith("test_") and fname.endswith(".py")):
            continue
        mod = importlib.import_module(fname[:-3])
        for name in sorted(n for n in dir(mod) if n.startswith("test_")):
            if only and only not in f"{fname}::{name}":
                continue
            t0 = time.time()
            try:
                getattr(mod, name)()
                passed += 1
                print(f"PASS  {fname[:-3]}::{name}  ({time.time() - t0:.1f}s)")
            except Exception:
                failed += 1
                print(f"FAIL  {fname[:-3]}::{name}")
                traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
