"""English originals read straight from the game's localization bundle, and the "Polski + EN" line format.

English is the game's base language and has no LocalizationTable: dialogue lines sit in the FELD graph
cards (Chunk-*.asset), interface / item / task texts in DialogueDatabase.asset. The installer uses this
to build the bilingual variant on the player's machine, so the repository never ships any game text.

    en = extract(UnityPy.load(bundle_path))        # game key ("<cardId>/<property>", "<table>/<entry>"…) -> EN
    with_original(pl_text, en.get(key))            # Polish line + the original in small grey type
"""
import collections
import re

EMPH = re.compile(r"\*([^*\n]{1,80})\*")
ORIGINAL = "<size=70%><color=#8a8a8a>{}</color></size>"
DATABASE_ASSET = "Assets/FELDRuntime/Scriptables/Main/DialogueDatabase.asset"


def italics(text: str) -> str:
    """English marks emphasis with *asterisks*; the game renders <i> tags."""
    return EMPH.sub(r"<i>\1</i>", text)


def with_original(pl: str, en: str | None) -> str:
    """Polish line followed by the English original (trailing newlines of the Polish text kept last)."""
    if not en:
        return pl
    body = pl.rstrip("\n")
    return f"{body}\n{ORIGINAL.format(italics(en).rstrip(chr(10)))}{pl[len(body):]}"


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def extract(env, ui: bool = False) -> dict[str, str]:
    """English strings addressable by a localization key.

    Default: the dialogue graph only (card properties such as "<cardId>/dialog_lines") - what the
    "Polski + EN" variant shows under each line. With ui=True also the nested check modifiers and the
    DialogueDatabase (interface tables, items, thoughts, tasks), which the bilingual variant leaves
    Polish-only because those fields are short labels with no room for a second line.
    """
    en: dict[str, str] = {}

    def put(key, val, is_ui=False):
        if (ui or not is_ui) and isinstance(val, str) and val.strip() and key not in en:
            en[key] = val

    # 1. graph cards: "<cardId>/<property>" and nested groups "<cardId>.<group>[i]/<leaf>"
    def cards_of(tree, plain_ui=False):
        refs = {r["rid"]: r for r in tree["references"]["RefIds"]}

        def data(x):
            return (refs.get(x.get("rid"), {}).get("data") or {}) if isinstance(x, dict) else {}

        def walk(group, prefix, cid):
            counters = collections.Counter()
            for k in group.get("m_childProperties", []):
                d = data(k)
                if d.get("m_childProperties") is not None:
                    i = counters[d["m_name"]]
                    counters[d["m_name"]] += 1
                    walk(d, f"{prefix}.{d['m_name']}[{i}]", cid)
                elif isinstance(d.get("m_value"), str):
                    put(f"{cid}{prefix}/{d['m_name']}", d["m_value"], is_ui=True)

        for r in refs.values():
            d = r.get("data") or {}
            cid = d.get("m_cardId")
            if not cid:
                continue
            card_data = d.get("m_cardData") or {}
            for name, ref in zip(card_data.get("m_keys", []), card_data.get("m_values", [])):
                p = data(ref)
                if p.get("m_childProperties") is not None:
                    walk(p, "", cid)
                elif isinstance(p.get("m_value"), str) and name != "character_short_name":
                    put(f"{cid}/{name}", p["m_value"], is_ui=plain_ui)
        return refs

    for path in sorted(p for p in env.container if "/Chunks/Chunk-" in p):
        cards_of(env.container[path].read_typetree())
    if not ui:
        return en

    # 2. DialogueDatabase: white-check cards, UI tables, entities (items, thoughts, tasks…), modifiers
    tree = env.container[DATABASE_ASSET].read_typetree()
    refs = cards_of(tree, plain_ui=True)

    def resolve(x):
        if isinstance(x, dict) and set(x) == {"rid"} and x["rid"] in refs:
            return refs[x["rid"]].get("data") or {}
        return x

    def modifiers(x):
        x = resolve(x)
        if isinstance(x, dict):
            if {"SourceEntityId", "SourcePropertyPath", "Description"} <= set(x):
                put(f"{x['SourceEntityId']}.{x['SourcePropertyPath']}/description", x["Description"])
            for v in x.values():
                modifiers(v)
        elif isinstance(x, list):
            for v in x:
                modifiers(v)

    cands_for = {"fullName": ["character_full_name"],
                 "description": ["description", "fullDescription", "character_description"],
                 "shortDescription": ["shortDescription", "short_description"],
                 "alternativeName": ["task_alternative_name"], "alternativeDescription": ["task_alternative_description"]}
    for r in refs.values():
        cls, d = r["type"]["class"], r.get("data") or {}
        sn = d.get("m_shortName")
        if not sn:
            continue
        if cls == "RtLocalizationTableEntity":
            e = d["m_entries"]
            for k, v in zip(e["m_keys"], e["m_values"]):
                put(f"{sn}/{k}", (v or {}).get("text"))
            continue
        if not cls.endswith("Entity"):
            continue
        for field, val in d.items():
            if isinstance(val, str) and field.startswith("m_"):
                name = field[2:]
                for c in cands_for.get(name, [name, _snake(name)]):
                    put(f"{sn}/{c}", val)   # every candidate key: only the ones present in the table are used
        for i, alt in enumerate(d.get("m_alternativeNames") or []):
            put(f"{sn}/alternativeNames_{i}", alt)
        subs = d.get("m_subTasks") or {}
        for sid, sub in zip(subs.get("m_keys", []), subs.get("m_values", [])):
            sub = resolve(sub)
            put(f"{sn}.subTasks{{{sid}}}/name", sub.get("Name"))
            ups = sub.get("Updates") or {}
            for uid, up in zip(ups.get("m_keys", []), ups.get("m_values", [])):
                put(f"{sn}.subTasks{{{sid}}}.updates{{{uid}}}/entry", resolve(up).get("Entry"))
        modifiers(d)
    return en
