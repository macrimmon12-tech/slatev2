-- scripts/examples/heal_shrine.lua
--
-- Minimal example proving the Lua pipe works end-to-end
-- (docs/components/10-lua-scripting-layer.md §2.3): a script registers
-- itself at load time via engine.subscribe(), and when the subscribed
-- event fires, calls an engine.* function that produces an observable
-- effect. A player bumping/interacting with a shrine entity restores
-- their HP through the exact same EffectResolver pipeline every potion
-- and spell effect list goes through -- there is no special-cased
-- healing path for scripted content (component doc §1).
--
-- This also doubles as living engine.* API documentation (component doc
-- §2.3's "three birds, one stone" rule) -- read it as a worked example of
-- engine.subscribe + engine.apply_effect + engine.log_message.

engine.subscribe("entity_interacted", function(payload)
  if payload == nil then
    return
  end

  -- restore_hp is clamped to max_hp by 01's EffectResolver, so a large
  -- amount here just means "fully heal" rather than overflowing HP.
  engine.apply_effect({type = "restore_hp", amount = 9999}, payload.actor_id, payload.actor_id)
  engine.log_message("A wave of warmth restores you.", "flavor")
end)
