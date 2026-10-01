import argparse
import json
import os
import shlex
import signal
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from pi_gateway import cli
from pi_gateway.config import load_config


class LocalInstancesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.old_cwd = Path.cwd()
        self.addCleanup(os.chdir, self.old_cwd)

    def args(self, **kwargs):
        values = dict(config=None, instance=None, bot_token=None, allowed_user_id=123,
                      pi_cwd=None, pi_agent_dir=None, model=None, thinking=None, name=None, allow_groups=False,
                      include_user_in_group_session_key=False)
        values.update(kwargs)
        return argparse.Namespace(**values)

    def test_init_two_instances_and_manage_from_elsewhere(self):
        folders = [self.root / "alpha", self.root / "beta"]
        configs = []
        for folder in folders:
            folder.mkdir()
            os.chdir(folder)
            args = self.args()
            cli.init_instance(args)
            config = cli.local_config()
            configs.append(config)
            self.assertEqual(cli.resolve_config(self.args()), config)
            self.assertEqual(load_config(str(config)).pi.cwd, str(folder))
            self.assertEqual(load_config(str(config)).database_path, str(config.parent / "pi-gateway.sqlite3"))
            self.assertEqual(cli.instance_state(config)[0], config.parent / "pi-gateway.pid")
            with self.assertRaises(SystemExit):
                cli.init_instance(self.args())
        registry = json.loads(cli.expand_path(cli.REGISTRY_PATH).read_text())
        self.assertEqual(registry, {"version": 2, "instances": [
            {"name": None, "config": str(config)} for config in configs
        ]})
        os.chdir(self.root)
        self.assertEqual(cli.resolve_config(self.args()), cli.expand_path(cli.DEFAULT_CONFIG_PATH))
        for folder, config in zip(folders, configs):
            self.assertEqual(cli.resolve_config(self.args(instance=str(folder))), config)
        self.assertEqual(cli.resolve_config(self.args(config=str(configs[0]), instance=str(folders[1]))), configs[0])

    def test_legacy_default_and_local_config_precedence(self):
        legacy = cli.expand_path(cli.DEFAULT_CONFIG_PATH)
        legacy.parent.mkdir(parents=True)
        legacy.write_text("logLevel: INFO\n")
        os.chdir(self.root)
        self.assertEqual(cli.resolve_config(self.args()), legacy)
        cli.configure_telegram(self.args())
        self.assertEqual(cli.resolve_config(self.args()), cli.local_config())
        self.assertEqual(legacy.read_text(), "logLevel: INFO\n")

    def test_background_launch_is_per_instance(self):
        os.chdir(self.root)
        cli.init_instance(self.args(bot_token="abc"))
        config = cli.local_config()
        args = self.args()
        output = StringIO()
        with patch.object(cli.subprocess, "Popen") as popen, patch.object(cli.sys, "argv", ["pi-gateway"]), redirect_stdout(output):
            popen.return_value.pid = 42
            cli.start_background(args)
            popen.assert_called_once()
            self.assertEqual(popen.call_args.args[0], ["pi-gateway", "run", "--config", str(config)])
        selected = f"pi-gateway -c {shlex.quote(str(config))}"
        self.assertIn(f"Stop with: {selected} stop", output.getvalue())
        self.assertIn(f"Follow logs with: {selected} logs -f", output.getvalue())
        self.assertEqual(cli.instance_state(config)[0].read_text(), "42")
        self.assertTrue(cli.instance_state(config)[1].is_file())

    def test_start_in_directory_with_spaces_quotes_selected_config(self):
        folder = self.root / "bot workspace"
        folder.mkdir()
        os.chdir(folder)
        cli.init_instance(self.args(bot_token="fake:token"))
        output = StringIO()
        with patch.object(cli.subprocess, "Popen") as popen, patch.object(cli.sys, "argv", ["pi-gateway"]), redirect_stdout(output):
            popen.return_value.pid = 42
            cli.start_background(self.args())
        selector = f"pi-gateway -c {shlex.quote(str(cli.local_config()))}"
        self.assertIn(f"Stop with: {selector} stop", output.getvalue())
        self.assertIn(f"Follow logs with: {selector} logs -f", output.getvalue())

    def test_explicit_configs_in_same_directory_have_separate_state(self):
        os.chdir(self.root)
        configs = [self.root / "a.yaml", self.root / "b.yaml"]
        states = []
        for index, config in enumerate(configs):
            args = self.args(config=str(config), bot_token=f"token-{index}")
            cli.configure_telegram(args)
            states.append(cli.instance_state(config))
            self.assertEqual(load_config(str(config)).database_path, str(states[-1][0].parent / "pi-gateway.sqlite3"))
        self.assertNotEqual(states[0], states[1])
        self.assertNotEqual(load_config(str(configs[0])).database_path, load_config(str(configs[1])).database_path)
        with patch.object(cli.subprocess, "Popen") as popen, patch.object(cli.sys, "argv", ["pi-gateway"]):
            for index, config in enumerate(configs):
                popen.return_value.pid = 100 + index
                cli.start_background(self.args(config=str(config)))
        self.assertEqual(states[0][0].read_text(), "100")
        self.assertEqual(states[1][0].read_text(), "101")
        with patch.object(cli.os, "kill") as kill:
            # A zero timeout avoids waiting for fake processes.
            cli.stop_background(argparse.Namespace(config=str(configs[1]), instance=None, timeout=0))
            kill.assert_any_call(101, signal.SIGTERM)
            self.assertNotIn(100, [call.args[0] for call in kill.call_args_list])

    def test_legacy_and_local_state_paths_remain_unchanged(self):
        legacy = cli.expand_path(cli.DEFAULT_CONFIG_PATH)
        self.assertEqual(cli.instance_state(legacy), (cli.pid_path(), cli.log_path()))
        local = cli.local_config(self.root)
        self.assertEqual(cli.instance_state(local), (local.parent / "pi-gateway.pid", local.parent / "pi-gateway.log"))
        self.assertEqual(cli.default_database_path(str(local)), str(local.parent / "pi-gateway.sqlite3"))

    def test_manual_explicit_config_without_database_path_is_isolated(self):
        configs = [self.root / "a.yaml", self.root / "b.yaml"]
        for config in configs:
            config.write_text("logLevel: INFO\n")
        self.assertNotEqual(load_config(str(configs[0])).database_path, load_config(str(configs[1])).database_path)
        configs[0].write_text("databasePath: ./chosen.sqlite3\n")
        self.assertEqual(load_config(str(configs[0])).database_path, str(self.old_cwd / "chosen.sqlite3"))

    def test_cli_config_precedence(self):
        parser = cli.build_parser()
        self.assertEqual(parser.parse_args(["-c", "/tmp/a", "start"]).config, "/tmp/a")
        self.assertEqual(parser.parse_args(["-c", "/tmp/a", "run"]).config, "/tmp/a")
        self.assertEqual(parser.parse_args(["run", "-c", "/tmp/a"]).config, "/tmp/a")
        self.assertEqual(parser.parse_args(["-i", "/tmp/b", "status"]).instance, "/tmp/b")
        for command in ("run", "start", "stop", "status", "logs", "remove", "config-path"):
            with self.subTest(command=command):
                self.assertEqual(parser.parse_args([command, "-i", "bunny"]).instance, "bunny")
                self.assertEqual(parser.parse_args([command, "-c", "/tmp/a.yaml"]).config, "/tmp/a.yaml")
                self.assertEqual(parser.parse_args(["-i", "bunny", command]).instance, "bunny")
                self.assertEqual(parser.parse_args(["-c", "/tmp/a.yaml", command]).config, "/tmp/a.yaml")
        self.assertEqual(parser.parse_args(["configure", "telegram", "-c", "/tmp/a.yaml"]).config, "/tmp/a.yaml")
        self.assertEqual(parser.parse_args(["-i", "bunny", "configure", "telegram"]).instance, "bunny")
        self.assertEqual(parser.parse_args(["start", "-i", "bunny", "-c", "/tmp/a.yaml"]).config, "/tmp/a.yaml")
        self.assertEqual(parser.parse_args(["-c", "/tmp/a.yaml", "start", "-i", "bunny"]).config, "/tmp/a.yaml")
        self.assertEqual(parser.parse_args(["-i", "old", "start", "-i", "new"]).instance, "new")

    def test_pi_agent_dir_config(self):
        os.chdir(self.root)
        cli.init_instance(self.args(pi_agent_dir=str(self.root / "agent")))
        self.assertEqual(load_config(str(cli.local_config())).pi.agent_dir, str(self.root / "agent"))


if __name__ == "__main__":
    unittest.main()
