"""One module per ``engine.*`` API group (component doc §2.2). Each module
exposes a single ``build(ctx: ApiContext) -> dict[str, Callable]`` entry
point; ``LuaHost._build_engine_table`` merges every group's dict into the
one flat ``engine`` Lua table.
"""
