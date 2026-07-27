"""Fake Mongo minimal : uniquement les opérations utilisées par EbiosRepository."""
import copy

import pytest


class FakeCollection:
    def __init__(self):
        self.docs = []

    @staticmethod
    def _match(doc, flt):
        return all(doc.get(k) == v for k, v in flt.items())

    def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))

    def find_one(self, flt, sort=None):
        found = [d for d in self.docs if self._match(d, flt)]
        if sort:
            key, direction = sort[0]
            found.sort(key=lambda d: d.get(key, 0), reverse=direction < 0)
        return copy.deepcopy(found[0]) if found else None

    def find(self, flt=None):
        return [copy.deepcopy(d) for d in self.docs if self._match(d, flt or {})]

    def update_one(self, flt, update):
        for d in self.docs:
            if self._match(d, flt):
                for k, v in update.get("$set", {}).items():
                    d[k] = copy.deepcopy(v)
                for k, v in update.get("$push", {}).items():
                    d.setdefault(k, []).append(copy.deepcopy(v))
                return


class FakeDB(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = FakeCollection()
        return dict.__getitem__(self, name)


@pytest.fixture()
def fake_db():
    return FakeDB()
