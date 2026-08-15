"""Unit and integration tests for Proactive Background Daemon & Event-Driven Automation."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from jarvis.config import Config, DaemonConfig
from jarvis.daemon import (
    Event,
    EventRule,
    EventType,
    ProactiveDaemon,
    get_daemon,
    set_daemon,
)
from jarvis.daemon.watchers import (
    BatteryWatcher,
    FileWatcher,
    ResourceWatcher,
    RoutineWatcher,
    WindowWatcher,
)
from jarvis.tools import registry
from jarvis.tools.schema import ACTIONS_BY_NAME


class EventAndRuleTests(unittest.TestCase):
    def test_01_rule_matching_and_cooldown(self):
        rule = EventRule(
            id="test_bat",
            name="Battery Low Rule",
            trigger_type=EventType.BATTERY_LOW,
            condition={"below": 20},
            action_type="notify",
            action_target="Battery is critically low.",
            cooldown_seconds=60.0,
        )

        now = 1000.0
        # 1. Matching event (battery 15% < 20%)
        ev_low = Event(
            type=EventType.BATTERY_LOW,
            title="Low Battery",
            message="15% remaining",
            payload={"percent": 15, "below": 15},
            timestamp=now,
        )
        self.assertTrue(rule.matches(ev_low, now=now))

        # 2. Fire and mark triggered
        rule.mark_triggered(now=now)
        self.assertEqual(rule.last_triggered, now)

        # 3. Check immediately afterwards: cooldown must block it!
        self.assertFalse(rule.matches(ev_low, now=now + 10.0))

        # 4. Check after cooldown (65 seconds later): should match again!
        self.assertTrue(rule.matches(ev_low, now=now + 65.0))

        # 5. Disabled rule: must not match
        rule.enabled = False
        self.assertFalse(rule.matches(ev_low, now=now + 65.0))


class WatcherTests(unittest.TestCase):
    def test_01_battery_watcher(self):
        watcher = BatteryWatcher(low_threshold=20)

        # Mock psutil.sensors_battery
        mock_bat_unplugged = Mock(percent=15, power_plugged=False)
        mock_bat_plugged = Mock(percent=16, power_plugged=True)

        with patch("psutil.sensors_battery", return_value=mock_bat_unplugged):
            events = watcher.check()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].type, EventType.BATTERY_LOW)
            self.assertEqual(events[0].payload["percent"], 15)

        # Plug in AC power
        with patch("psutil.sensors_battery", return_value=mock_bat_plugged):
            events = watcher.check()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].type, EventType.BATTERY_CHARGING)

    def test_02_resource_watcher(self):
        watcher = ResourceWatcher(cpu_threshold=90, memory_threshold=85)

        # 1st tick: high CPU
        with patch("psutil.cpu_percent", return_value=95.0), \
             patch("psutil.virtual_memory", return_value=Mock(percent=70.0)):
            events_t1 = watcher.check()
            self.assertEqual(len(events_t1), 0)  # requires 2 consecutive ticks for streak

        # 2nd tick: sustained high CPU
        with patch("psutil.cpu_percent", return_value=96.0), \
             patch("psutil.virtual_memory", return_value=Mock(percent=70.0)):
            events_t2 = watcher.check()
            self.assertEqual(len(events_t2), 1)
            self.assertEqual(events_t2[0].type, EventType.HIGH_CPU)
            self.assertEqual(events_t2[0].payload["cpu_percent"], 96.0)

    def test_03_file_watcher(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            watcher = FileWatcher(directories=[tmp_dir])

            # Initial scan
            e0 = watcher.check()
            self.assertEqual(len(e0), 0)

            # Drop a new file
            new_file = Path(tmp_dir) / "test_download.pdf"
            new_file.write_text("dummy content", encoding="utf-8")

            # Next check discovers file
            e1 = watcher.check()
            self.assertEqual(len(e1), 1)
            self.assertEqual(e1[0].type, EventType.FILE_DROPPED)
            self.assertEqual(e1[0].payload["file_name"], "test_download.pdf")

    def test_04_routine_watcher(self):
        watcher = RoutineWatcher(
            morning_time="08:00",
            morning_enabled=True,
            evening_time="20:00",
            evening_enabled=True,
        )

        # Morning timestamp: 2026-08-15 08:30:00 (epoch: 1786762200)
        # We test with datetime mock
        with patch("jarvis.daemon.watchers.datetime") as mock_dt:
            mock_now = Mock()
            mock_now.strftime.side_effect = lambda fmt: "2026-08-15" if fmt == "%Y-%m-%d" else "08:30"
            mock_dt.fromtimestamp.return_value = mock_now
            mock_dt.now.return_value = mock_now

            events = watcher.check()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].type, EventType.MORNING_ROUTINE)

            # Checking again on same day: should not repeat!
            events_again = watcher.check()
            self.assertEqual(len(events_again), 0)


class ProactiveDaemonEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.rules_file = Path(self.tmp_dir.name) / "test_rules.json"
        self.task_calls = []

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_01_daemon_lifecycle_and_rule_management(self):
        cfg = Config(daemon=DaemonConfig(enabled=True, check_interval=0.1))
        daemon = ProactiveDaemon(
            cfg=cfg,
            task_runner=self.task_calls.append,
            rules_path=self.rules_file,
        )

        # Verify initial default rules
        rules = daemon.list_rules()
        self.assertGreaterEqual(len(rules), 3)

        # Add custom rule
        custom = EventRule(
            id="custom_test_rule",
            name="Custom Alert",
            trigger_type=EventType.FILE_DROPPED,
            action_type="notify",
            action_target="File alert!",
        )
        daemon.add_rule(custom)
        self.assertIsNotNone(daemon.get_rule("custom_test_rule"))

        # Persistence check
        self.assertTrue(self.rules_file.exists())
        saved = json.loads(self.rules_file.read_text(encoding="utf-8"))
        rule_ids = [r["id"] for r in saved]
        self.assertIn("custom_test_rule", rule_ids)

        # Disable rule
        daemon.enable_rule("custom_test_rule", False)
        self.assertFalse(daemon.get_rule("custom_test_rule").enabled)

        # Remove rule
        ok = daemon.remove_rule("custom_test_rule")
        self.assertTrue(ok)
        self.assertIsNone(daemon.get_rule("custom_test_rule"))

    def test_02_daemon_event_dispatching(self):
        cfg = Config(daemon=DaemonConfig(enabled=True))
        daemon = ProactiveDaemon(
            cfg=cfg,
            task_runner=self.task_calls.append,
            rules_path=self.rules_file,
        )

        rule = EventRule(
            id="task_rule",
            name="Run Cleanup Task",
            trigger_type=EventType.FILE_DROPPED,
            action_type="task",
            action_target="Organize downloads folder",
            cooldown_seconds=0.0,
        )
        daemon.add_rule(rule)

        ev = Event(
            type=EventType.FILE_DROPPED,
            title="Download",
            message="report.pdf downloaded",
            payload={"file_name": "report.pdf"},
        )

        fired = daemon.process_event(ev)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0].id, "task_rule")


class DaemonToolActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.rules_file = Path(self.tmp_dir.name) / "test_tool_rules.json"
        self.daemon = ProactiveDaemon(rules_path=self.rules_file)
        set_daemon(self.daemon)

    def tearDown(self):
        set_daemon(None)
        self.tmp_dir.cleanup()

    def test_01_daemon_rule_in_registry_and_schema(self):
        self.assertIn("daemon_rule", ACTIONS_BY_NAME)
        cfg = Config()

        # 1. Status
        res_st = registry.execute("daemon_rule", {"action": "status"}, None, cfg)
        self.assertTrue(res_st.ok)
        self.assertIn("Proactive Daemon Status", res_st.message)

        # 2. List
        res_list = registry.execute("daemon_rule", {"action": "list"}, None, cfg)
        self.assertTrue(res_list.ok)
        self.assertIn("Proactive Daemon Rules", res_list.message)

        # 3. Add
        res_add = registry.execute("daemon_rule", {
            "action": "add",
            "name": "Meeting Reminder",
            "trigger": "morning_routine",
            "action_type": "notify",
            "target": "Prepare for morning meeting.",
        }, None, cfg)
        self.assertTrue(res_add.ok)
        self.assertIn("Meeting Reminder", res_add.message)

        # 4. Remove
        res_rm = registry.execute("daemon_rule", {"action": "remove", "rule_id": "default_battery_low"}, None, cfg)
        self.assertTrue(res_rm.ok)
        self.assertIn("Removed proactive rule", res_rm.message)


if __name__ == "__main__":
    unittest.main()
