---
name: system-triage
description: Diagnose a slow, hot, full or misbehaving machine and report what is
  actually wrong.
when_to_use: slow, lagging, hot, fan, disk full, memory, cpu, why is it, performance,
  freezing, network
tools: system_status, system_diagnostics, process_intel, net_intel, run_command
version: '1.0'
created: '2026-09-18T14:52:34'
updated: '2026-09-18T14:52:34'
---

# Machine triage

## Steps
1. `system_status` first: CPU, memory, disk, battery, uptime. This is the cheap
   picture and it usually narrows the problem to one resource.
2. `system_diagnostics` for the deeper report when the first answer is unclear.
3. `process_intel` to find who is using the strained resource. Name the top two
   or three processes with their real numbers.
4. `net_intel` when the complaint is about connectivity or slowness that is not
   CPU, memory or disk.
5. Only then propose a fix, and say what it will cost: closing a process loses
   unsaved work, freeing disk is slower than it looks, and a reboot ends whatever
   is running.

## Reporting
- Lead with the bottleneck and its number ("memory is at 94%, and the top three
  processes are eating 6.1 of 7.7 GB").
- Distinguish cause from symptom. High CPU from a fan spinning is a symptom; a
  process in a hot loop is a cause.
- Say plainly when nothing is wrong. "Uptime is 41 days and everything is
  healthy" is a valid finding, and inventing a problem to look useful is not.
- Never kill a process, delete files, or change settings as part of *diagnosis*.
  Diagnose, report, then act only if the user asks.
