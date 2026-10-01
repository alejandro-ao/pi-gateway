import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pi_gateway import cli


class RemoveGatewayTest(unittest.TestCase):
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
        self.first, self.second = self.root / "first.yaml", self.root / "second.yaml"
        for name, path in (("first", self.first), ("second", self.second)):
            cli.configure_telegram(self.parser.parse_args([
                "-c", str(path), "configure", "telegram", "--name", name,
                "--bot-token", f"fake:{name}", "--allowed-user-id", "123",
            ]))
        db = cli.instance_state(self.first)[0].parent / "pi-gateway.sqlite3"
        db.parent.mkdir(parents=True, exist_ok=True)
        db.write_bytes(b"session mapping")
        self.db = db
        self.log = cli.instance_state(self.first)[1]
        self.log.write_text("gateway log")
        self.pi_session = self.root / "pi-session.jsonl"
        self.pi_session.write_text("Pi history")

    def parse(self, *args):
        return self.parser.parse_args(list(args))

    def names(self):
        registry = cli.expand_path(cli.REGISTRY_PATH)
        return [entry["name"] for entry in json.loads(registry.read_text())["instances"]]

    def test_dry_run_does_not_prompt_stop_or_delete_anything(self):
        pid, _ = cli.instance_state(self.first)
        pid.write_text("1234")
        with patch.object(cli, "is_process_running", return_value=True), patch("builtins.input") as prompt:
            cli.remove_gateway(self.parse("remove", "first", "--dry-run", "--stop"))
        prompt.assert_not_called()
        self.assertEqual(self.names(), ["first", "second"])
        self.assertTrue(self.first.is_file())
        self.assertTrue(pid.is_file())

    def test_cancel_and_running_guard_preserve_everything(self):
        with patch("builtins.input", return_value="no"):
            cli.remove_gateway(self.parse("remove", "first"))
        self.assertTrue(self.first.exists())
        pid, _ = cli.instance_state(self.first)
        pid.write_text("1234")
        with patch.object(cli, "is_process_running", return_value=True), patch("builtins.input") as prompt:
            with self.assertRaisesRegex(SystemExit, "Stop it first"):
                cli.remove_gateway(self.parse("remove", "first", "--yes"))
        prompt.assert_not_called()
        self.assertEqual(self.names(), ["first", "second"])

    def test_removal_by_name_only_deletes_selected_config(self):
        cli.remove_gateway(self.parse("remove", "first", "--yes"))
        self.assertFalse(self.first.exists())
        self.assertEqual(self.names(), ["second"])
        self.assertTrue(self.second.is_file())
        self.assertEqual(self.db.read_bytes(), b"session mapping")
        self.assertEqual(self.log.read_text(), "gateway log")
        self.assertEqual(self.pi_session.read_text(), "Pi history")
        with self.assertRaises(SystemExit):
            cli.resolve_config(self.parse("-i", "first", "status"))

    def test_missing_config_can_be_unregistered_by_name(self):
        self.first.unlink()
        cli.remove_gateway(self.parse("remove", "first", "--yes"))
        self.assertEqual(self.names(), ["second"])
        self.assertTrue(self.db.exists())

    def test_explicit_config_removes_unnamed_instance(self):
        cli.configure_telegram(self.parse(
            "-c", str(self.first), "configure", "telegram", "--name", "first",
            "--allowed-user-id", "123",
        ))
        # An old path-only registry entry has no name; -c still targets it.
        registry = cli.expand_path(cli.REGISTRY_PATH)
        registry.write_text(json.dumps([str(self.first), str(self.second)]))
        cli.remove_gateway(self.parse("remove", "-c", str(self.first), "--yes"))
        self.assertFalse(self.first.exists())
        self.assertTrue(self.second.exists())
        self.assertEqual(self.names(), [None])

    def test_stop_must_succeed_before_config_is_deleted(self):
        pid, _ = cli.instance_state(self.first)
        pid.write_text("1234")
        with patch.object(cli, "is_process_running", return_value=True), patch.object(cli, "stop_background"):
            with self.assertRaisesRegex(SystemExit, "still running"):
                cli.remove_gateway(self.parse("remove", "first", "--stop", "--yes"))
        self.assertTrue(self.first.exists())
        self.assertEqual(self.names(), ["first", "second"])

        def stopped(_args):
            pid.unlink()

        with patch.object(cli, "is_process_running", return_value=True), patch.object(
            cli, "stop_background", side_effect=stopped
        ) as stop:
            cli.remove_gateway(self.parse("remove", "first", "--stop", "--yes"))
            stop.assert_called_once()
        self.assertFalse(self.first.exists())
        self.assertEqual(self.names(), ["second"])
        self.assertTrue(self.db.exists())

    def test_noninteractive_remove_requires_yes(self):
        with patch("builtins.input", side_effect=EOFError), self.assertRaisesRegex(SystemExit, "Confirmation required"):
            cli.remove_gateway(self.parse("remove", "first"))
        self.assertTrue(self.first.exists())
        self.assertEqual(self.names(), ["first", "second"])

    def test_no_implicit_selection_or_ambiguous_target(self):
        with self.assertRaisesRegex(SystemExit, "Specify a gateway"):
            cli.remove_gateway(self.parse("remove", "--yes"))
        with self.assertRaisesRegex(SystemExit, "either a gateway"):
            cli.remove_gateway(self.parse("-c", str(self.first), "remove", "second", "--yes"))
        self.assertEqual(self.names(), ["first", "second"])


if __name__ == "__main__":
    unittest.main()
