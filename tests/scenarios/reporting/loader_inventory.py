"""Isolated child scenarios extracted from tests/reporting/test_loader_inventory.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "inventory":
    import importlib.machinery
    import importlib.util
    import json
    import pathlib
    import sys
    import types

    import metapathology

    class InventoryLoader:
        pass

    class AssertionRewritingHook:
        pass

    def registered(name, spec_loader, module_loader):
        module = types.ModuleType(name)
        module.__spec__ = importlib.machinery.ModuleSpec(name, spec_loader, origin=name + ".py")
        module.__loader__ = module_loader
        sys.modules[name] = module

    shared = InventoryLoader()
    other = InventoryLoader()
    registered("inventory_shared_a", shared, shared)
    registered("inventory_shared_b", shared, shared)
    registered("inventory_other", other, other)
    registered("inventory_mismatch", shared, other)
    registered("inventory_assertion", AssertionRewritingHook(), None)
    registered(
        "inventory_std_mismatch",
        importlib.machinery.SourceFileLoader("inventory_std_mismatch", "inventory_std_mismatch.py"),
        other,
    )

    class HostileModule(types.ModuleType):
        touched = False

        def __getattribute__(self, name):
            type(self).touched = True
            raise RuntimeError(name)

    hostile = HostileModule("inventory_hostile_subclass")
    namespace = types.ModuleType.__getattribute__(hostile, "__dict__")
    namespace["__spec__"] = importlib.machinery.ModuleSpec("inventory_hostile_subclass", shared)
    namespace["__loader__"] = shared
    sys.modules["inventory_hostile_subclass"] = hostile

    class ModuleLike:
        touched = False

        def __getattribute__(self, name):
            type(self).touched = True
            raise RuntimeError(name)

    module_like = ModuleLike()
    sys.modules["inventory_module_like"] = module_like

    lazy_source = pathlib.Path(sys.argv[1])
    lazy_name = "inventory_lazy"
    eager_loader = importlib.machinery.SourceFileLoader(lazy_name, str(lazy_source))
    lazy_loader = importlib.util.LazyLoader(eager_loader)
    lazy_spec = importlib.util.spec_from_loader(lazy_name, lazy_loader)
    assert lazy_spec is not None
    lazy_module = importlib.util.module_from_spec(lazy_spec)
    sys.modules[lazy_name] = lazy_module
    lazy_loader.exec_module(lazy_module)

    sys.path.insert(0, sys.argv[2])
    import inventory_zip

    metapathology.install(report_at_exit=False)
    sys.modules[42] = None
    document = metapathology.get_report()
    text = metapathology.render_report()
    del sys.modules[42]

    inventory = document["loader_inventory"]
    groups = inventory["groups"]
    records = {module["name"]: (group["loader"], module) for group in groups for module in group["modules"]}
    assert "inventory_shared_a" in records, (sorted(records), inventory)
    shared_loader, shared_a = records["inventory_shared_a"]
    assert shared_loader == records["inventory_shared_b"][0]
    assert shared_loader["object_id"] != records["inventory_other"][0]["object_id"]
    assert shared_a["loader_agreement"] is True
    assert records["inventory_mismatch"][1]["loader_agreement"] is False
    assert records["inventory_mismatch"][1]["loader_source"] == "spec"
    assert records["inventory_assertion"][0]["type_name"] == "AssertionRewritingHook"
    assert records["inventory_zip"][0]["type_name"] == "zipimporter"
    assert records["inventory_hostile_subclass"][1]["inspection"] == "available"
    assert any(group["loader"] and group["loader"]["type_name"] == "BuiltinImporter" for group in groups)
    assert any(group["loader"] and group["loader"]["type_name"] == "FrozenImporter" for group in groups)
    assert any(group["loader"] and group["loader"]["type_name"] == "SourceFileLoader" for group in groups)
    assert inventory["non_string_keys_omitted"] == 1
    assert any(item["name"] == "inventory_module_like" for item in inventory["unavailable"])
    assert "loaders of imported modules" in text
    assert "loader metadata disagreement" in text
    assert "InventoryLoader: 5 module(s)" not in text, text
    assert "inventory_shared_a: origin 'inventory_shared_a.py'" not in text, text
    assert "inventory_std_mismatch: origin 'inventory_std_mismatch.py' [loader metadata disagreement]" in text, text
    assert "    json: origin" not in text, text
    assert ", cached" not in text, text
    assert not HostileModule.touched
    assert not ModuleLike.touched
    assert not lazy_source.with_suffix(".executed").exists()
else:
    raise ValueError(f"unknown scenario: {_scenario}")
