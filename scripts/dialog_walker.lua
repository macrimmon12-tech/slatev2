-- scripts/dialog_walker.lua
--
-- Dialog graph state machine (component doc 11-npc-dialog-shop-content.md
-- §2.3). Owns: current-node tracking (floor-scoped, per NPC, per floor —
-- §2.3/§8's zero-special-case persistence guarantee), choice visibility
-- (condition grammar, see below), and the dialog panel's lifecycle.
--
-- Public entry point, called by npc_interaction.lua via the SLATE shared
-- dispatch table (component doc §2.5 -- no `require` under the sandbox):
--   SLATE.DialogWalker.open(actor_id, target_id, dialog_id)
--
-- Event wiring:
--   - Subscribes to "dialog_choice_clicked" (a new event this component's
--     PR adds to CONTRACTS.md §3.2 -- the dialog panel's choice Button
--     `on_click` payload, i.e. the raw "player clicked this choice"
--     request). This is deliberately a *different* event than
--     "dialog_choice_selected": the latter is the documented
--     Lua-emitted *announcement* (CONTRACTS.md's existing row,
--     `{entity_id, dialog_id, node_id}`) this script itself emits once it
--     has actually resolved and applied the choice -- keeping "the UI
--     asked for a transition" and "a transition happened" as two distinct,
--     correctly-attributed events rather than overloading one event with
--     both meanings.
--   - Emits "dialog_opened"/"dialog_choice_selected"/"dialog_closed" per
--     CONTRACTS.md's existing table row.
--
-- Terminal nodes (doc §5.1: "empty list = terminal node (conversation ends
-- when reached, or a [Leave] choice is implicit -- dialog_walker.lua's
-- call, document which)"): this script's choice is "conversation ends
-- when reached" -- a node with an empty `choices` list auto-closes the
-- panel immediately (no synthetic [Leave] button), both when landing there
-- fresh and when a `goto` transitions into one. Simpler and deterministic;
-- documented in this component's PR.
--
-- Condition grammar (doc §5.1's `choices[].condition`, e.g.
-- "not campaign.mira_lore_told"): Lua's own `load`/`loadstring` are nulled
-- by 10's sandbox (component doc §2.1), so a content-authored condition
-- string can't be handed to Lua's own evaluator. `evaluate_condition`
-- below is a small, hand-rolled parser -- optional leading "not", then
-- either a bare truthy reference/literal or a two-sided comparison
-- (==, ~=, >=, <=, >, <) -- deliberately not a general expression
-- language, matching the doc's own "not a new formula language" framing.

SLATE = SLATE or {}
SLATE.DialogWalker = SLATE.DialogWalker or {}

-- -- floor-state keys ---------------------------------------------------

local function floor_node_key(target_id)
  return "dialog_" .. tostring(target_id) .. "_node"
end

-- -- condition grammar ----------------------------------------------------

local function resolve_ref(token, actor_id)
  local ns, key = token:match("^(%a+)%.([%w_]+)$")
  if ns == "campaign" then
    return engine.get_campaign_state(key)
  elseif ns == "floor" then
    return engine.get_floor_state(key)
  elseif ns == "stat" then
    return engine.get_stat(actor_id, key)
  end
  return nil
end

local function literal_value(token)
  if token == "true" then
    return true
  end
  if token == "false" then
    return false
  end
  local quoted = token:match('^"(.*)"$')
  if quoted ~= nil then
    return quoted
  end
  local num = tonumber(token)
  if num ~= nil then
    return num
  end
  return nil
end

local function term_value(token, actor_id)
  if token:match("^%a+%.[%w_]+$") then
    return resolve_ref(token, actor_id)
  end
  return literal_value(token)
end

local function trim(s)
  return s:match("^%s*(.-)%s*$")
end

-- Longer operators first so e.g. ">=" isn't mis-split as "=".
local _COMPARATORS = {"==", "~=", ">=", "<=", ">", "<"}

local function split_by_operator(expr, op)
  local idx = expr:find(op, 1, true)
  if idx == nil then
    return nil, nil
  end
  return trim(expr:sub(1, idx - 1)), trim(expr:sub(idx + #op))
end

local function evaluate_condition(condition, actor_id)
  if condition == nil or condition == "" then
    return true
  end

  local expr = condition
  local negate = false
  local rest = expr:match("^not%s+(.+)$")
  if rest ~= nil then
    negate = true
    expr = rest
  end

  local result = nil
  for _, op in ipairs(_COMPARATORS) do
    local lhs, rhs = split_by_operator(expr, op)
    if lhs ~= nil and lhs ~= "" and rhs ~= nil and rhs ~= "" then
      local left = term_value(lhs, actor_id)
      local right = term_value(rhs, actor_id)
      if op == "==" then
        result = left == right
      elseif op == "~=" then
        result = left ~= right
      elseif left ~= nil and right ~= nil then
        if op == ">=" then
          result = left >= right
        elseif op == "<=" then
          result = left <= right
        elseif op == ">" then
          result = left > right
        elseif op == "<" then
          result = left < right
        end
      else
        result = false
      end
      break
    end
  end

  if result == nil then
    local value = term_value(trim(expr), actor_id)
    result = value == true or (type(value) == "number" and value ~= 0)
  end

  if negate then
    return not result
  end
  return result
end

-- -- dialog content ---------------------------------------------------

local function get_dialog(dialog_id)
  local dialog = engine.get_registry_entry("dialogs", dialog_id)
  if dialog == nil then
    engine.log_message("dialog_walker: unknown dialog id " .. tostring(dialog_id), "debug")
  end
  return dialog
end

local function run_on_enter(node, actor_id)
  local directives = node.on_enter
  if directives == nil then
    return
  end
  for _, directive in ipairs(directives) do
    if directive.campaign_state ~= nil then
      engine.set_campaign_state(directive.campaign_state, directive.value)
    elseif directive.floor_state ~= nil then
      engine.set_floor_state(directive.floor_state, directive.value)
    end
  end
end

local function visible_choices(node, actor_id)
  local result = {}
  local choices = node.choices or {}
  for index, choice in ipairs(choices) do
    if evaluate_condition(choice.condition, actor_id) then
      table.insert(result, {text = choice.text, choice_index = index})
    end
  end
  return result
end

local function node_panel_data(actor_id, target_id, dialog_id, node_id, node)
  return {
    entity_id = actor_id,
    target_id = target_id,
    dialog_id = dialog_id,
    node_id = node_id,
    text = node.text,
    choices = visible_choices(node, actor_id),
  }
end

local function close_dialog(actor_id, target_id, dialog_id, node_id)
  engine.destroy_panel("dialog")
  engine.emit(
    "dialog_closed",
    {entity_id = actor_id, target_id = target_id, dialog_id = dialog_id, node_id = node_id}
  )
end

-- -- public entry point --------------------------------------------------

function SLATE.DialogWalker.open(actor_id, target_id, dialog_id)
  local dialog = get_dialog(dialog_id)
  if dialog == nil then
    return
  end

  local key = floor_node_key(target_id)
  local node_id = engine.get_floor_state(key)
  local fresh = node_id == nil
  if fresh then
    node_id = dialog.start_node
  end

  local node = dialog.nodes[node_id]
  if node == nil then
    engine.log_message(
      "dialog_walker: unknown node " .. tostring(node_id) .. " in dialog " .. tostring(dialog_id),
      "debug"
    )
    return
  end

  if fresh then
    run_on_enter(node, actor_id)
    engine.set_floor_state(key, node_id)
  end

  engine.emit(
    "dialog_opened",
    {entity_id = actor_id, target_id = target_id, dialog_id = dialog_id, node_id = node_id}
  )

  local choices = node.choices or {}
  if #choices == 0 then
    close_dialog(actor_id, target_id, dialog_id, node_id)
    return
  end

  engine.open_dialog_panel(node_panel_data(actor_id, target_id, dialog_id, node_id, node))
end

-- -- choice click handling -----------------------------------------------

engine.subscribe("dialog_choice_clicked", function(payload)
  if payload == nil then
    return
  end

  local actor_id = payload.entity_id
  local target_id = payload.target_id
  local dialog_id = payload.dialog_id

  local dialog = get_dialog(dialog_id)
  if dialog == nil then
    return
  end

  local key = floor_node_key(target_id)
  local current_node_id = engine.get_floor_state(key)
  if current_node_id == nil or current_node_id ~= payload.node_id then
    return  -- stale click against a conversation that already moved on
  end

  local node = dialog.nodes[current_node_id]
  if node == nil then
    return
  end

  local choice = (node.choices or {})[payload.choice_index]
  if choice == nil then
    return
  end

  engine.emit(
    "dialog_choice_selected",
    {entity_id = actor_id, target_id = target_id, dialog_id = dialog_id, node_id = current_node_id}
  )

  if choice.action == "close" then
    close_dialog(actor_id, target_id, dialog_id, current_node_id)
    return
  end

  if choice.action == "open_shop" then
    close_dialog(actor_id, target_id, dialog_id, current_node_id)
    if SLATE.Shop ~= nil then
      SLATE.Shop.open(actor_id, target_id, choice.shop_id)
    else
      engine.log_message("dialog_walker: shop.lua not loaded", "debug")
    end
    return
  end

  local goto_node_id = choice.goto
  if goto_node_id == nil then
    return  -- malformed content -- absence = zero cost, not a crash
  end

  local next_node = dialog.nodes[goto_node_id]
  if next_node == nil then
    engine.log_message(
      "dialog_walker: choice goto references unknown node " .. tostring(goto_node_id), "debug"
    )
    return
  end

  engine.set_floor_state(key, goto_node_id)
  run_on_enter(next_node, actor_id)

  local next_choices = next_node.choices or {}
  if #next_choices == 0 then
    close_dialog(actor_id, target_id, dialog_id, goto_node_id)
    return
  end

  engine.update_panel(
    "dialog", node_panel_data(actor_id, target_id, dialog_id, goto_node_id, next_node)
  )
end)
