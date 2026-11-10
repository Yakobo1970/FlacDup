"""Phase 1 + 2 ohne Oberflaeche: python tests/cli_run.py <db> [--var] <ordner> ..."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.db import Database
from engine.pipeline import Comparer, Indexer, Matcher, default_workers


def main():
    args = sys.argv[1:]
    variants = "--var" in args
    args = [a for a in args if a != "--var"]
    db = Database(args[0])
    last = [0.0]

    def status(s):
        if time.time() - last[0] > 5 or "fertig" in s or "nichts" in s:
            last[0] = time.time()
            print(time.strftime("%H:%M:%S"), s, flush=True)

    t0 = time.time()
    if args[1:]:
        Indexer(db, args[1:], default_workers(), status, lambda d, t: None).run()
    t1 = time.time()
    Matcher(db, variants, status, lambda d, t: None).run()
    t2 = time.time()
    Comparer(db, default_workers(), True, status, lambda d, t: None).run()
    print(f"Index {t1 - t0:.0f}s, Abgleich {t2 - t1:.0f}s, Feinvergleich {time.time() - t2:.0f}s", db.counts())


if __name__ == "__main__":
    main()
