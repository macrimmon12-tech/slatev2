-- scripts/examples/damage_trap.lua
--
-- Second minimal example (docs/components/10-lua-scripting-layer.md §2.3):
-- a triggered trap that deals damage to whoever interacts with it,
-- through the exact same engine.deal_damage -> 01's EffectResolver ->
-- damage_dealt pipeline any spell/potion/equipment proc uses. This is the
-- pipeline tests/integration/test_lua_pipeline.py drives end-to-end,
-- asserting the real EffectResolver ran and damage_dealt fired.

engine.subscribe("entity_interacted", function(payload)
  if payload == nil then
    return
  end

  -- The trap (target_id) is the damage source; the entity that
  -- interacted with it (actor_id) is who gets hurt.
  engine.deal_damage(payload.target_id, payload.actor_id, 5, "physical")
end)
