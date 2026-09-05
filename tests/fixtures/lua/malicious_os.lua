-- Attempts to reach a nulled global. Must fail cleanly (component doc §7).
os.execute("echo hacked > /tmp/slatev2_lua_sandbox_escape")
