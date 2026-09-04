"""The component-registry lint check (CONTRACTS.md §2 rule 3): every class
carrying the ``@component`` marker must be a value in
``engine.core.save._COMPONENT_REGISTRY``, or it is silently dropped on
save/load. This was a recurring v1 bug class — see component doc §6.
"""

import types
from dataclasses import dataclass

from engine.core import save
from engine.core.ecs import component


def test_find_unregistered_components_flags_an_unregistered_component():
    @component
    @dataclass
    class TotallyUnregisteredComponent:
        value: int = 0

    fake_module = types.ModuleType("fake_module_with_unregistered_component")
    fake_module.TotallyUnregisteredComponent = TotallyUnregisteredComponent

    unregistered = save.find_unregistered_components([fake_module])

    assert TotallyUnregisteredComponent in unregistered


def test_find_unregistered_components_passes_once_registered(monkeypatch):
    @component
    @dataclass
    class NowRegisteredComponent:
        value: int = 0

    fake_module = types.ModuleType("fake_module_with_registered_component")
    fake_module.NowRegisteredComponent = NowRegisteredComponent

    monkeypatch.setitem(
        save._COMPONENT_REGISTRY, "NowRegisteredComponent", NowRegisteredComponent
    )

    unregistered = save.find_unregistered_components([fake_module])

    assert NowRegisteredComponent not in unregistered


def test_find_unregistered_components_ignores_non_component_classes():
    @dataclass
    class PlainDataclassNotAComponent:
        value: int = 0

    fake_module = types.ModuleType("fake_module_with_plain_dataclass")
    fake_module.PlainDataclassNotAComponent = PlainDataclassNotAComponent

    unregistered = save.find_unregistered_components([fake_module])

    assert PlainDataclassNotAComponent not in unregistered


def test_discover_component_modules_skips_nonexistent_package():
    # engine.systems doesn't exist yet at the foundation layer — the
    # discovery walk must skip it silently rather than raising, so this
    # lint check stays usable before any Wave 1 component lands.
    modules = save.discover_component_modules(["engine.core", "engine.does_not_exist"])
    module_names = {m.__name__ for m in modules}
    assert "engine.core" in module_names
    assert not any("does_not_exist" in name for name in module_names)


def test_all_real_components_in_engine_are_registered():
    """The actual lint check CI relies on: walk the real package tree and
    assert nothing carrying the @component marker is missing from
    _COMPONENT_REGISTRY. Vacuously true at the foundation layer (no
    gameplay components exist yet) — stays meaningful as later components
    add real component dataclasses under engine/core and engine/systems.
    """
    modules = save.discover_component_modules()
    unregistered = save.find_unregistered_components(modules)
    assert unregistered == [], (
        "Component(s) missing from _COMPONENT_REGISTRY: "
        f"{[cls.__name__ for cls in unregistered]}"
    )
