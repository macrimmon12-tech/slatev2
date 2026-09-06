-- scripts/npc_interaction.lua
--
-- Dispatch hub for every InteractableComponent-carrying entity (component
-- doc 11-npc-dialog-shop-content.md §2.2). Subscribes to entity_interacted
-- (emitted by 07's movement-collision bump check and interact-key handler,
-- §1.1 -- both now emit it for exactly this case) and branches on
-- InteractableComponent.data.kind -- a convention private to this file
-- and the content JSON it reads; Python never inspects "kind" anywhere
-- (component doc §1/§7/§8).

SLATE = SLATE or {}

engine.subscribe("entity_interacted", function(payload)
  if payload == nil then
    return
  end

  local interactable = engine.get_component(payload.target_id, "InteractableComponent")
  if interactable == nil then
    return  -- bumped/interacted-with entity isn't interactable -- normal case
  end

  local data = interactable.data
  if data == nil then
    return
  end

  local kind = data.kind

  if kind == "npc" then
    local dialog_id = data.dialog_id
    if dialog_id == nil and data.npc_id ~= nil then
      local npc_def = engine.get_registry_entry("npcs", data.npc_id)
      if npc_def ~= nil and npc_def.interactable ~= nil then
        dialog_id = npc_def.interactable.dialog_id
      end
    end
    if dialog_id == nil then
      engine.log_message("npc_interaction: 'npc' interactable with no dialog_id", "debug")
      return
    end
    if SLATE.DialogWalker == nil then
      engine.log_message("npc_interaction: dialog_walker.lua not loaded", "debug")
      return
    end
    SLATE.DialogWalker.open(payload.actor_id, payload.target_id, dialog_id)
  elseif kind == "shop" then
    local shop_id = data.shop_id
    if shop_id == nil then
      engine.log_message("npc_interaction: 'shop' interactable with no shop_id", "debug")
      return
    end
    if SLATE.Shop == nil then
      engine.log_message("npc_interaction: shop.lua not loaded", "debug")
      return
    end
    SLATE.Shop.open(payload.actor_id, payload.target_id, shop_id)
  else
    -- Absence = zero cost (CONTRACTS.md §2 rule 7): an interactable with
    -- data this script doesn't recognize is not an error -- a future
    -- interactable kind (component doc §6) is expected to add its own
    -- subscriber script rather than extend this one's branch list.
    engine.log_message("npc_interaction: unrecognized interactable kind " .. tostring(kind), "debug")
  end
end)
