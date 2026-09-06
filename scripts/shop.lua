-- scripts/shop.lua
--
-- Buy/sell/stock/gold (component doc 11-npc-dialog-shop-content.md §2.4).
--
-- Public entry point (called by npc_interaction.lua via the SLATE shared
-- dispatch table, component doc §2.5):
--   SLATE.Shop.open(actor_id, target_id, shop_id)
--
-- Persistence (doc §2.4): a shop instance's current stock and gold pool
-- are floor-scoped state, keyed by shop_id -- "shop_<id>_stock",
-- "shop_<id>_gold" -- exactly like dialog_walker.lua's per-NPC node
-- tracking, so they persist per floor instance of that shop with zero
-- special-case Python save code.
--
-- Restock bookkeeping (doc §5.2's `restock_interval_floors`: "how many
-- player floor-transitions since last restock of this shop instance"):
-- floor-scoped state alone can't count transitions that happen while the
-- player is on a *different* floor (this shop's floor-state entity is
-- inert then, per 10's set_context -- component doc §2.4). This script's
-- documented choice: a single monotonic floor-transition counter is kept
-- in *campaign*-scoped state (survives across floors on the player
-- entity), and each shop instance remembers, also in campaign-scoped
-- state, the counter's value at its last restock. Stock/gold themselves
-- stay floor-scoped exactly as documented; only this one counter
-- comparison is campaign-scoped. Flagged as this component's own
-- resolution of a genuine spec ambiguity, per CONTRACTS.md §8/component
-- doc §9's "document the choice" precedent.
--
-- Button-subscription timing (doc §2.4: "either is acceptable, document
-- your choice"): this script subscribes to "buy_item"/"sell_item" once,
-- at load time (top-level, like npc_interaction.lua/dialog_walker.lua),
-- rather than re-subscribing per Shop.open -- simpler, and each handler
-- already re-validates shop_id/stock/gold from scratch so there is no
-- per-open state to leak between shops.
--
-- "gold" (doc §2.4/§5.2) is modeled as an ordinary item id ("gold"),
-- moved via the same engine.has_item/remove_item/grant_item wrappers as
-- any other item -- per this component's own documented soft dependency
-- on 04-inventory-items-loot.md (not merged as of this component's
-- implementation; those wrappers are logged-once no-ops until it lands,
-- CONTRACTS.md §8).

SLATE = SLATE or {}
SLATE.Shop = SLATE.Shop or {}

-- -- state keys -------------------------------------------------------

local function stock_key(shop_id)
  return "shop_" .. shop_id .. "_stock"
end

local function gold_key(shop_id)
  return "shop_" .. shop_id .. "_gold"
end

local function last_restock_key(shop_id)
  return "shop_" .. shop_id .. "_last_restock_count"
end

-- -- floor-transition counter (campaign-scoped, see module docstring) -----

engine.subscribe("floor_changed", function(_payload)
  local count = engine.get_campaign_state("slate_floor_transition_count", 0) or 0
  engine.set_campaign_state("slate_floor_transition_count", count + 1)
end)

local function current_floor_transition_count()
  return engine.get_campaign_state("slate_floor_transition_count", 0) or 0
end

-- -- shop content -------------------------------------------------------

local function get_shop_def(shop_id)
  local shop = engine.get_registry_entry("shops", shop_id)
  if shop == nil then
    engine.log_message("shop: unknown shop id " .. tostring(shop_id), "debug")
  end
  return shop
end

local function base_price_of(item_id)
  local item_def = engine.get_registry_entry("items", item_id)
  if item_def == nil or item_def.base_price == nil then
    return 0
  end
  return item_def.base_price
end

local function price_for(shop_def, item_id, formula_key)
  local formulas = shop_def.price_formula or {}
  local formula = formulas[formula_key]
  local base_price = base_price_of(item_id)
  if formula == nil then
    return base_price
  end
  return engine.eval_formula(formula, {base_price = base_price})
end

local function roll_quantity(range)
  if range == nil then
    return 0
  end
  local lo = range[1] or 0
  local hi = range[2] or lo
  if hi < lo then
    hi = lo
  end
  return math.random(lo, hi)
end

-- Generates a fresh stock list from the shop definition's stock_table
-- (doc §5.2). Every entry is restocked with a freshly-rolled quantity;
-- `weight` is stored through (future content/tuning may use it for
-- probabilistic inclusion once a real weighted-loot-table convention
-- lands with 04-inventory-items-loot.md, per this doc's own note that
-- stock_table "reuses the established weighted-table pattern") -- not
-- used to exclude entries here, a deliberately conservative reading of
-- an otherwise-unspecified selection rule (documented in this
-- component's PR).
local function generate_stock(shop_def)
  local stock = {}
  for _, entry in ipairs(shop_def.stock_table or {}) do
    local quantity = roll_quantity(entry.restock_quantity)
    if quantity > 0 then
      table.insert(stock, {item_id = entry.item_id, quantity = quantity})
    end
  end
  return stock
end

local function find_stock_entry(stock, item_id)
  for _, entry in ipairs(stock) do
    if entry.item_id == item_id then
      return entry
    end
  end
  return nil
end

local function ensure_initialized(shop_id, shop_def)
  if engine.get_floor_state(gold_key(shop_id)) == nil then
    engine.set_floor_state(gold_key(shop_id), shop_def.starting_gold or 0)
  end
  if engine.get_floor_state(stock_key(shop_id)) == nil then
    engine.set_floor_state(stock_key(shop_id), generate_stock(shop_def))
  end
end

local function maybe_restock(shop_id, shop_def)
  local interval = shop_def.restock_interval_floors
  if interval == nil or interval <= 0 then
    return false
  end

  local last = engine.get_campaign_state(last_restock_key(shop_id))
  if last == nil then
    -- First time this shop instance is opened -- baseline the counter
    -- without restocking (ensure_initialized already generated its
    -- starting stock).
    engine.set_campaign_state(last_restock_key(shop_id), current_floor_transition_count())
    return false
  end

  local elapsed = current_floor_transition_count() - last
  if elapsed >= interval then
    engine.set_floor_state(stock_key(shop_id), generate_stock(shop_def))
    engine.set_campaign_state(last_restock_key(shop_id), current_floor_transition_count())
    engine.emit("shop_refreshed", {shop_id = shop_id})
    return true
  end
  return false
end

local function shop_display_data(actor_id, target_id, shop_id, shop_def)
  local stock = engine.get_floor_state(stock_key(shop_id)) or {}
  local display = {}
  for _, entry in ipairs(stock) do
    table.insert(display, {
      item_id = entry.item_id,
      quantity = entry.quantity,
      price = price_for(shop_def, entry.item_id, "buy"),
    })
  end
  return {
    entity_id = actor_id,
    target_id = target_id,
    shop_id = shop_id,
    gold = engine.get_floor_state(gold_key(shop_id)),
    stock = display,
  }
end

-- -- public entry point --------------------------------------------------

function SLATE.Shop.open(actor_id, target_id, shop_id)
  local shop_def = get_shop_def(shop_id)
  if shop_def == nil then
    return
  end

  ensure_initialized(shop_id, shop_def)
  maybe_restock(shop_id, shop_def)

  engine.open_shop_panel(shop_display_data(actor_id, target_id, shop_id, shop_def))
  engine.emit("shop_opened", {entity_id = actor_id, shop_id = shop_id})
end

-- -- buy/sell ---------------------------------------------------------

engine.subscribe("buy_item", function(payload)
  if payload == nil then
    return
  end
  local actor_id = payload.entity_id
  local target_id = payload.target_id
  local shop_id = payload.shop_id
  local item_id = payload.item

  local shop_def = get_shop_def(shop_id)
  if shop_def == nil then
    return
  end

  local stock = engine.get_floor_state(stock_key(shop_id)) or {}
  local entry = find_stock_entry(stock, item_id)
  if entry == nil or entry.quantity <= 0 then
    engine.log_message("shop: " .. tostring(item_id) .. " is out of stock", "debug")
    return
  end

  local price = price_for(shop_def, item_id, "buy")
  if not engine.has_item(actor_id, "gold", price) then
    engine.log_message("shop: not enough gold to buy " .. tostring(item_id), "debug")
    return
  end

  engine.remove_item(actor_id, "gold", price)
  engine.grant_item(actor_id, item_id, 1)

  entry.quantity = entry.quantity - 1
  engine.set_floor_state(stock_key(shop_id), stock)

  local shop_gold = engine.get_floor_state(gold_key(shop_id)) or 0
  engine.set_floor_state(gold_key(shop_id), shop_gold + price)

  engine.emit(
    "trade_completed", {entity_id = actor_id, shop_id = shop_id, item = item_id, price = price}
  )
  engine.update_panel("shop", shop_display_data(actor_id, target_id, shop_id, shop_def))
end)

engine.subscribe("sell_item", function(payload)
  if payload == nil then
    return
  end
  local actor_id = payload.entity_id
  local target_id = payload.target_id
  local shop_id = payload.shop_id
  local item_id = payload.item

  local shop_def = get_shop_def(shop_id)
  if shop_def == nil then
    return
  end

  local price = price_for(shop_def, item_id, "sell")
  local shop_gold = engine.get_floor_state(gold_key(shop_id)) or 0
  if shop_gold < price then
    -- "a shop that's out of gold can't buy from the player" (doc §2.4) --
    -- a deliberate, documented content rule, not a bug.
    engine.log_message("shop: " .. tostring(shop_id) .. " doesn't have enough gold to buy that", "debug")
    return
  end

  if not engine.remove_item(actor_id, item_id, 1) then
    engine.log_message("shop: actor does not have " .. tostring(item_id) .. " to sell", "debug")
    return
  end

  engine.grant_item(actor_id, "gold", price)
  engine.set_floor_state(gold_key(shop_id), shop_gold - price)

  local stock = engine.get_floor_state(stock_key(shop_id)) or {}
  local entry = find_stock_entry(stock, item_id)
  if entry ~= nil then
    entry.quantity = entry.quantity + 1
  else
    table.insert(stock, {item_id = item_id, quantity = 1})
  end
  engine.set_floor_state(stock_key(shop_id), stock)

  engine.emit(
    "trade_completed", {entity_id = actor_id, shop_id = shop_id, item = item_id, price = price}
  )
  engine.update_panel("shop", shop_display_data(actor_id, target_id, shop_id, shop_def))
end)

-- -- leave ---------------------------------------------------------------

engine.subscribe("shop_leave_clicked", function(payload)
  if payload == nil then
    return
  end
  engine.destroy_panel("shop")
end)
