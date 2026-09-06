-- A well-formed script that registers itself; used to prove
-- load_scripts_dir skips a broken sibling without preventing this one
-- from loading (component doc §7).
engine.subscribe("test_good_script_loaded_event", function(payload)
  engine.log_message("good_script.lua handled its event", "debug")
end)
