"""Версии каталога сценариев. v1 = data/scenarios.json. Патчи создают новые версии, есть откат и заморозка."""
import json, hashlib, copy, datetime
from .config import CATALOG_DIR
from . import kit

def _hash(obj) -> str:
    return hashlib.sha1(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

class Catalog:
    def __init__(self):
        CATALOG_DIR.mkdir(parents=True, exist_ok=True)
        self.versions: list[dict] = []
        self.data: dict[str, dict] = {}
        self.frozen = False
        self._load_or_init()

    def _load_or_init(self):
        idx = CATALOG_DIR / "index.json"
        if idx.exists():
            meta = json.loads(idx.read_text())
            self.versions, self.frozen = meta["versions"], meta.get("frozen", False)
            for v in self.versions:
                self.data[v["version"]] = json.loads((CATALOG_DIR / f"{v['version']}.json").read_text())
        else:
            self._add(kit.base_catalog(), parent=None, patch=None, note="исходный каталог кита")

    def _save(self):
        (CATALOG_DIR / "index.json").write_text(json.dumps({"versions": self.versions, "frozen": self.frozen}, ensure_ascii=False, indent=2))

    def _add(self, cat: dict, parent, patch, note) -> dict:
        h = _hash(cat["scenarios"])
        name = f"v{len(self.versions) + 1}-{h[:4]}"
        for v in self.versions: v["current"] = False
        v = {"version": name, "parent": parent, "hash": h[:8], "applied_patch": patch, "note": note,
             "created_at": datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat(timespec="seconds") + "Z", "current": True, "primary_acc": None}
        self.versions.append(v); self.data[name] = cat
        (CATALOG_DIR / f"{name}.json").write_text(json.dumps(cat, ensure_ascii=False, indent=2))
        self._save()
        return v

    @property
    def current(self) -> dict:
        return next(v for v in self.versions if v["current"])

    @property
    def version(self) -> str:
        return self.current["version"]

    def get(self, version=None) -> dict:
        return self.data[version or self.version]

    def scenarios(self, version=None) -> dict:
        return {s["scenario_id"]: s for s in self.get(version)["scenarios"]}

    def apply_ops(self, ops: list, base=None) -> dict:
        """Вернуть новый объект каталога с применёнными операциями (без сохранения)."""
        cat = copy.deepcopy(self.get(base))
        idx = {s["scenario_id"]: s for s in cat["scenarios"]}
        for op in ops:
            s = idx[op["scenario_id"]]; f = op["field"]
            if f == "description":
                s["description"] = op["value"]
            elif f == "not_this_if":
                if op["op"] in ("add", "append"): s["not_this_if"].append(op["value"])
                elif op["op"] == "remove": s["not_this_if"] = [x for x in s["not_this_if"] if x != op["value"]]
            elif f.startswith("examples."):
                lang = f.split(".")[1]
                if op["op"] in ("add", "append"): s["examples"][lang].append(op["value"])
                elif op["op"] == "remove": s["examples"][lang] = [x for x in s["examples"][lang] if x != op["value"]]
        return cat

    def commit(self, cat: dict, patch_id: str, note: str) -> dict:
        if self.frozen: raise PermissionError("catalog frozen")
        return self._add(cat, parent=self.version, patch=patch_id, note=note)

    def rollback(self, version: str) -> dict:
        if self.frozen: raise PermissionError("catalog frozen")
        cat = self.get(version)
        return self._add(copy.deepcopy(cat), parent=self.version, patch=None, note=f"откат к {version}")

    def freeze(self, frozen: bool):
        self.frozen = frozen; self._save()
        return {**self.current, "frozen": frozen}

    def set_accuracy(self, version, acc):
        for v in self.versions:
            if v["version"] == version: v["primary_acc"] = acc
        self._save()

CATALOG = Catalog()
