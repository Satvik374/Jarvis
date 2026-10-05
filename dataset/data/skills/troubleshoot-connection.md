---
name: troubleshoot-connection
description: Find out why the network is not working before changing anything, then
  fix the one real cause.
when_to_use: internet, wifi, connection, network, online, cannot connect, dns, no
  internet, pages will not load
tools: net_intel, system_status, run_command, http_request, notify
version: '1.0'
created: '2026-09-26T21:39:29'
updated: '2026-09-26T21:39:29'
---

# Fixing a connection

## Steps
1. **Establish what fails and what works.** `net_intel` for interfaces, addresses
   and routes; `system_status` for whether the adapter is up. Is it one site, all
   of the web, or the whole network?
2. **Test DNS and reachability separately:** resolve a name and ping a literal
   address with `run_command` (`nslookup <host>`, `ping 1.1.1.1`). If the literal
   IP works and names do not, it is DNS, not the connection.
3. **Fix the one cause you found, and only that:** renew the address, cycle the
   adapter, flush DNS. Do not stack changes - if three things change at once, you
   learn nothing about which one fixed it.
4. **Re-test the original failure,** not just the layer you touched. A fixed
   adapter is not a fixed connection until the page loads.
5. **Report** the cause in plain words and what you changed; if it is the ISP or
   the router's own problem, say that instead of hunting on the machine.

## Rules
- Do not change network settings, firewall rules, or DNS servers on a hunch;
  make one change with a reason, then test.
- Fixing connectivity must not weaken security (never disable the firewall).
