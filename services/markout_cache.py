"""Bound and share expensive tick-history scans across RPC and subscriptions."""
from collections import OrderedDict
import threading
import time


class MarkoutReportCache:
    def __init__(self, ttl=30, limit=16):
        self.ttl, self.limit = ttl, limit
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def read(self, key, build):
        # Only markout history scans serialize; quotes and other live streams
        # use their own workers. An initial RPC and subscription share one scan.
        with self.lock:
            now = time.monotonic()
            for old_key, (stamp, _) in list(self.entries.items()):
                if now - stamp >= self.ttl:
                    self.entries.pop(old_key)
            if key in self.entries:
                self.entries.move_to_end(key)
                return self.entries[key][1]
            result = build()
            self.entries[key] = (time.monotonic(), result)
            while len(self.entries) > self.limit:
                self.entries.popitem(last=False)
            return result
