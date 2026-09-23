"""Версии каталога сценариев. v1 = data/scenarios.json. Патчи создают новые версии, есть откат и заморозка.
Временные версии (для регрессии патча) живут только в памяти: CATALOG.data[name], без записи в versions/index."""
import json, hashlib, copy, datetime
from .config import CATALOG_DIR
from . import kit

LIST_FIELDS = ("not_this_if", "examples.ru", "examples.kk")

def _hash(obj) -> str:
    return hashlib.sha1(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def _now(): return datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"

def field_get(s: dict, field: str):
    """Значение поля сценария по пути патча: description | not_this_if | examples.ru | examples.kk."""
    if field.startswith("examples."): return s["examples"][field.split(".")[1]]
    return s[field]

def visible_examples(s: dict) -> dict:
    """Примеры, которые роутер реально видит в промпте (зеркало router._system: ru[:3]+ru[4:], kk[:2]+kk[3:]; хвост = добавленные патчами)."""
    ru, kk = s["examples"]["ru"], s["examples"]["kk"]
    return {"ru": ru[:3] + ru[4:], "kk": kk[:2] + kk[3:]}

def _same(a, b) -> bool:
    """Совпадение элемента списка: правило not_this_if сравниваем по condition (регистр/пробелы не важны)."""
    if isinstance(a, dict) and isinstance(b, dict): a, b = a.get("condition", ""), b.get("condition", "")
    elif isinstance(a, dict): a = a.get("condition", "")
    elif isinstance(b, dict): b = b.get("condition", "")
    return " ".join(str(a).lower().split()) == " ".join(str(b).lower().split())

class Catalog:
    def __init__(self):
        CATALOG_DIR.mkdir(parents=True, exist_ok=True)
        self.versions: list[dict] = []
        self.data: dict[str, dict] = {}
        self.temp: set[str] = set()
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
             "created_at": _now(), "current": True, "primary_acc": None}
        self.versions.append(v); self.data[name] = cat
        (CATALOG_DIR / f"{name}.json").write_text(json.dumps(cat, ensure_ascii=False, indent=2))
        self._save()
        return self.public(v)

    def public(self, v: dict) -> dict:
        """Объект CatalogVersion по контракту (frozen только у текущей)."""
        return {**v, "frozen": bool(self.frozen and v.get("current"))}

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
        """Вернуть новый объект каталога с применёнными операциями (без сохранения).
        op: {op: add|append|replace|remove, scenario_id, field, value, old?}. replace для списков заменяет элемент old на value."""
        cat = copy.deepcopy(self.get(base))
        idx = {s["scenario_id"]: s for s in cat["scenarios"]}
        for op in ops:
            s = idx[op["scenario_id"]]; f = op["field"]; kind = op["op"]
            if f == "description":
                s["description"] = op["value"]; continue
            lst = field_get(s, f)
            if kind in ("add", "append"):
                if not any(_same(x, op["value"]) for x in lst): lst.append(op["value"])
            elif kind == "remove":
                lst[:] = [x for x in lst if not _same(x, op["value"])]
            elif kind == "replace":
                old = op.get("old")
                pos = next((i for i, x in enumerate(lst) if old is not None and _same(x, old)), None)
                if pos is None: lst.append(op["value"])
                else: lst[pos] = op["value"]
        return cat

    def add_temp(self, name: str, cat: dict) -> str:
        """Временная версия для регрессии: доступна роутеру (router._system/_ids читают CATALOG.get), не видна в versions."""
        self.data[name] = cat; self.temp.add(name)
        return name

    def drop_temp(self, name: str):
        if name in self.temp:
            self.temp.discard(name); self.data.pop(name, None)

    def commit(self, cat: dict, patch_id: str, note: str) -> dict:
        if self.frozen: raise PermissionError("catalog frozen")
        return self._add(copy.deepcopy(cat), parent=self.version, patch=patch_id, note=note)

    def rollback(self, version: str) -> dict:
        if self.frozen: raise PermissionError("catalog frozen")
        cat = self.get(version); acc = next((v.get("primary_acc") for v in self.versions if v["version"] == version), None)
        v = self._add(copy.deepcopy(cat), parent=self.version, patch=None, note=f"откат к {version}")
        if acc is not None: self.set_accuracy(v["version"], acc); v["primary_acc"] = acc
        return v

    def freeze(self, frozen: bool):
        self.frozen = frozen; self._save()
        return self.public(self.current)

    def set_accuracy(self, version, acc):
        for v in self.versions:
            if v["version"] == version: v["primary_acc"] = acc
        self._save()

CATALOG = Catalog()
