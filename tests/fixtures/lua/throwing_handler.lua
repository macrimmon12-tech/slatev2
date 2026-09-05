-- Subscribes a handler that throws mid-execution. run_protected must
-- catch this, log it, and let every OTHER subscriber (Lua or Python)
-- still receive the same event (component doc §7).
engine.subscribe("test_broken_handler_event", function(payload)
  error("boom")
end)
