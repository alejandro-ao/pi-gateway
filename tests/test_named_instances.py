import argparse
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from pi_gateway import cli


class NamedInstancesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        env = patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)
        self.parser = cli.build_parser()
        self.registry_path = cli.expand_path(cli.REGISTRY_PATH)

    def parse(self, *args):
        return self.parser.parse_args(list(args))

    def configure(self, config: Path, name: str | None = None):
        args = ["-c", str(config), "configure", "telegram", "--bot-token", "fake:token", "--allowed-user-id", "123"]
        if name is not None:
            args.append(f"--name={name}")
        cli.configure_telegram(self.parse(*args))

    def entries(self):
        return json.loads(self.registry_path.read_text())["instances"]

    def test_named_configs_sharing_directory_route_start_and_stop_independently(self):
        first, second = self.root / "first.yaml", self.root / "second.yaml"
        self.configure(first, "Research")
        self.configure(second, "coding")
        self.assertEqual(self.entries(), [
            {"name": "research", "config": str(first)},
            {"name": "coding", "config": str(second)},
        ])
        self.assertEqual(cli.resolve_config(self.parse("-i", "research", "status")), first)
        self.assertEqual(cli.resolve_config(self.parse("-i", "coding", "stop")), second)
        self.assertEqual(cli.resolve_config(self.parse("-c", str(first), "-i", "coding", "stop")), first)
        with patch.object(cli.subprocess, "Popen") as popen, patch.object(cli.sys, "argv", ["pi-gateway"]):
            for index, name in enumerate(("research", "coding")):
                popen.return_value.pid = 100 + index
                cli.start_background(self.parse("start", "-i", name))
        with patch.object(cli.os, "kill") as kill:
            cli.stop_background(self.parse("stop", "-i", "coding", "--timeout", "0"))
            self.assertIn((101, cli.signal.SIGTERM), [call.args for call in kill.call_args_list])
            self.assertNotIn(100, [call.args[0] for call in kill.call_args_list])
        output = StringIO()
        with patch.object(cli, "is_process_running", return_value=True), redirect_stdout(output):
            cli.list_instances(self.parse("instances"))
        self.assertIn("research: running (PID 100)", output.getvalue())
        self.assertIn("coding: running (PID 101)", output.getvalue())
        self.assertNotIn("fake:token", output.getvalue())

    def test_rename_preserves_state_and_rejects_duplicate_without_writing(self):
        first, second = self.root / "one.yaml", self.root / "two.yaml"
        self.configure(first, "one")
        self.configure(second, "two")
        before = second.read_text()
        with self.assertRaisesRegex(ValueError, "already assigned"):
            self.configure(second, "ONE")
        self.assertEqual(second.read_text(), before)
        self.assertEqual(self.entries()[1]["name"], "two")
        state = cli.instance_state(first)
        self.configure(first, "new-one")
        self.assertEqual(cli.instance_state(first), state)
        self.assertEqual(cli.resolve_config(self.parse("-i", "new-one", "status")), first)
        with self.assertRaises(SystemExit):
            cli.resolve_config(self.parse("-i", "one", "status"))
        self.configure(first)  # Omitted name preserves the new name.
        self.assertEqual(self.entries()[1]["name"], "new-one")

    def test_unnamed_and_old_path_registry_are_upgraded(self):
        local = cli.local_config(self.root)
        local.parent.mkdir()
        local.write_text("pi:\n  cwd: /tmp\n")
        self.registry_path.parent.mkdir(parents=True)
        self.registry_path.write_text(json.dumps([str(local)]))
        output = StringIO()
        with redirect_stdout(output):
            cli.list_instances(self.parse("instances"))
        self.assertIn("(unnamed): stopped", output.getvalue())
        self.assertEqual(self.entries(), [{"name": None, "config": str(local)}])
        self.assertEqual(cli.resolve_config(self.parse("-i", str(self.root), "status")), local)
        self.configure(local, "named")
        self.assertEqual(self.entries(), [{"name": "named", "config": str(local)}])

    def test_custom_config_and_legacy_are_registered_and_forget_is_safe(self):
        custom = self.root / "custom.yaml"
        self.configure(custom, "custom")
        self.assertEqual(self.entries()[0]["config"], str(custom))
        with patch.object(cli, "is_process_running", return_value=True):
            pid, _ = cli.instance_state(custom)
            pid.parent.mkdir(parents=True)
            pid.write_text("101")
            with self.assertRaisesRegex(SystemExit, "Stop instance"):
                cli.forget_instance(argparse.Namespace(name="custom"))
        with patch.object(cli, "is_process_running", return_value=False):
            cli.forget_instance(argparse.Namespace(name="custom"))
        self.assertEqual(self.entries(), [])
        self.assertTrue(custom.exists())
        self.configure(self.home / ".config/pi-gateway/config.yaml", "legacy")
        self.assertEqual(self.entries()[0]["name"], "legacy")

    def test_interactive_init_prompts_for_name_and_reprompts_on_invalid_input(self):
        with patch.object(cli.sys.stdin, "isatty", return_value=True), patch(
            "builtins.input", side_effect=[str(self.root), "", "", "bad name", "Research"]
        ):
            cli.init_instance(self.parse("init", "--bot-token", "fake:token", "--allowed-user-id", "123"))
        config = cli.local_config()
        self.assertEqual(self.entries(), [{"name": "research", "config": str(config)}])
        self.assertEqual(cli.load_raw_config(config)["instanceName"], "research")
        self.assertEqual(cli.resolve_config(self.parse("-i", "RESEARCH", "status")), config)

    def test_invalid_name_does_not_create_config(self):
        config = self.root / "bad.yaml"
        for name in ("bad name", "-bad", "bad-", "x" * 65):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.configure(config, name)
            self.assertFalse(config.exists())


if __name__ == "__main__":
    unittest.main()
