import argparse
import json
import os
import tempfile
import unittest
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
                      pi_cwd=None, pi_agent_dir=None, allow_groups=False,
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
        self.assertEqual(registry, list(map(str, configs)))
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
        with patch.object(cli.subprocess, "Popen") as popen, patch.object(cli.sys, "argv", ["pi-gateway"]):
            popen.return_value.pid = 42
            cli.start_background(args)
            popen.assert_called_once()
            self.assertEqual(popen.call_args.args[0], ["pi-gateway", "run", "--config", str(config)])
        self.assertEqual(cli.instance_state(config)[0].read_text(), "42")
        self.assertTrue(cli.instance_state(config)[1].is_file())

    def test_cli_config_precedence(self):
        parser = cli.build_parser()
        self.assertEqual(parser.parse_args(["-c", "/tmp/a", "start"]).config, "/tmp/a")
        self.assertEqual(parser.parse_args(["-c", "/tmp/a", "run"]).config, "/tmp/a")
        self.assertEqual(parser.parse_args(["run", "-c", "/tmp/a"]).config, "/tmp/a")
        self.assertEqual(parser.parse_args(["-i", "/tmp/b", "status"]).instance, "/tmp/b")

    def test_pi_agent_dir_config(self):
        os.chdir(self.root)
        cli.init_instance(self.args(pi_agent_dir=str(self.root / "agent")))
        self.assertEqual(load_config(str(cli.local_config())).pi.agent_dir, str(self.root / "agent"))


if __name__ == "__main__":
    unittest.main()
