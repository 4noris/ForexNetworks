"""Small read views for background observers of immutable native generations."""
from dataclasses import dataclass
from types import SimpleNamespace


@dataclass
class MembershipBatch:
    snapshot: object
    marker: tuple
    rows: dict


class NativeMembershipCursor:
    def __init__(self):
        self.marker = None
        self.rows = {}

    def prepare(self, manager, settings):
        with manager.cache_lock:
            cache = manager.cache
            if cache.get("status") != "connected" or cache.get("errors") or not cache.get("last_update"):
                return None
            epoch = cache.get("native_epoch")
            revision = cache.get("native_profile_revision", cache.get("last_update"))
            generation = cache["last_update"]
            rows = cache.get("users_by_login", {})
        books = tuple((key,tuple(sorted(settings.get(key, [])))) for key in ("a_book","b_book","cent","hybrid"))
        marker = (epoch, revision, books)
        if marker == self.marker:
            return None
        force = self.marker is None or self.marker[0] != epoch or self.marker[2] != books
        if force:
            changed = list(rows.values())
        else:
            changed = [row for login,row in rows.items()
                       if login not in self.rows or
                       (row.get("Name"),row.get("Group")) !=
                       self.rows[login]]
        return MembershipBatch(SimpleNamespace(users=changed,last_update=generation),marker,rows)

    def commit(self, batch):
        # Commit after persistence succeeds, so failures are retried.
        # Retain only membership identity, not an obsolete full financial book.
        # Otherwise a reconnect must hold both old and new 170k-account rows.
        if self.marker is None or self.marker[0] != batch.marker[0] or self.marker[2] != batch.marker[2]:
            self.rows = {}
        for row in batch.snapshot.users:
            self.rows[int(row["Login"])] = (row.get("Name"),row.get("Group"))
        for login in self.rows.keys() - batch.rows.keys():
            del self.rows[login]
        self.marker = batch.marker


def online_account_identities(manager, logins):
    with manager.cache_lock:
        index = manager.cache.get("users_by_login", {})
    return {login:{"Name":index[login].get("Name", ""),"Group":index[login].get("Group", "")}
            for login in logins if login in index}
