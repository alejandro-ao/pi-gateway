import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pi_gateway import cli
from pi_gateway.config import load_config
from pi_gateway.pi_rpc import PiRpcClient


class ModelSetupTest(unittest.TestCase):
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

    def parse(self, *args):
        return self.parser.parse_args(list(args))

    def test_init_flags_save_model_thinking_and_rpc_startup_uses_them(self):
        args = self.parse(
            "init", "--allowed-user-id", "123", "--model", "huggingface/org/Model-X", "--thinking", "high",
        )
        cli.init_instance(args)
        config = load_config(str(cli.local_config()))
        self.assertEqual(config.pi.default_provider, "huggingface")
        self.assertEqual(config.pi.default_model, "org/Model-X")
        self.assertEqual(config.pi.default_thinking, "high")

        client = PiRpcClient(config.pi)
        with patch.object(asyncio, "create_subprocess_exec", side_effect=RuntimeError("capture")) as spawn:
            with self.assertRaisesRegex(RuntimeError, "capture"):
                asyncio.run(client.start())
        self.assertEqual(
            spawn.call_args.args,
            ("pi", "--mode", "rpc", "--provider", "huggingface", "--model", "org/Model-X", "--thinking", "high"),
        )

    def test_configure_flags_update_only_specified_defaults(self):
        config_path = self.root / "bot.yaml"
        cli.configure_telegram(self.parse("-c", str(config_path), "configure", "telegram", "--model", "anthropic/old"))
        cli.configure_telegram(self.parse("-c", str(config_path), "configure", "telegram", "--thinking", "MAX"))
        pi = load_config(str(config_path)).pi
        self.assertEqual((pi.default_provider, pi.default_model, pi.default_thinking), ("anthropic", "old", "max"))
        cli.configure_telegram(self.parse("-c", str(config_path), "configure", "telegram", "--model", "openai/new"))
        pi = load_config(str(config_path)).pi
        self.assertEqual((pi.default_provider, pi.default_model, pi.default_thinking), ("openai", "new", "max"))

    def test_interactive_prompts_validate_and_preserve_existing_defaults(self):
        args = self.parse("init", "--allowed-user-id", "123", "--bot-token", "env:BOT_TOKEN")
        with patch.object(cli.sys.stdin, "isatty", return_value=True), patch(
            "builtins.input", side_effect=[str(self.root), "bad-model", "anthropic/claude-sonnet-4-5", "invalid", "high", ""]
        ):
            cli.init_instance(args)
        pi = load_config(str(cli.local_config())).pi
        self.assertEqual((pi.default_provider, pi.default_model, pi.default_thinking), ("anthropic", "claude-sonnet-4-5", "high"))

        with patch.object(cli.sys.stdin, "isatty", return_value=True), patch("builtins.input", side_effect=["", "", "", "", "", ""]):
            cli.configure_telegram(self.parse("configure", "telegram"))
        pi = load_config(str(cli.local_config())).pi
        self.assertEqual((pi.default_provider, pi.default_model, pi.default_thinking), ("anthropic", "claude-sonnet-4-5", "high"))

    def test_empty_prompts_keep_pi_defaults(self):
        with patch.object(cli.sys.stdin, "isatty", return_value=True), patch(
            "builtins.input", side_effect=["", "123", str(self.root), "", "", ""]
        ):
            cli.init_instance(self.parse("init"))
        pi = load_config(str(cli.local_config())).pi
        self.assertIsNone(pi.default_provider)
        self.assertIsNone(pi.default_model)
        self.assertIsNone(pi.default_thinking)

    def test_invalid_flags_do_not_create_config(self):
        for flag, value in (("--model", "no-provider"), ("--model", "provider/"), ("--thinking", "ultra")):
            with self.subTest(flag=flag, value=value):
                with self.assertRaises(ValueError):
                    cli.init_instance(self.parse("init", flag, value))
                self.assertFalse(cli.local_config().exists())


if __name__ == "__main__":
    unittest.main()
