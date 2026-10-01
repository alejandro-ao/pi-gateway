import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_toolkit.document import Document
from prompt_toolkit.validation import ValidationError

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

    def test_model_flag_skips_catalog_even_in_interactive_mode(self):
        args = self.parse(
            "init", "--bot-token", "fake:token", "--allowed-user-id", "123", "--pi-cwd", str(self.root),
            "--name", "test", "--model", "anthropic/claude-sonnet-4-5", "--thinking", "high",
        )
        with patch.object(cli.sys.stdin, "isatty", return_value=True), patch.object(cli, "list_pi_models") as catalog:
            cli.init_instance(args)
        catalog.assert_not_called()
        self.assertEqual(load_config(str(cli.local_config())).pi.default_model, "claude-sonnet-4-5")

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
        with patch.object(cli.sys.stdin, "isatty", return_value=True), patch.object(cli, "list_pi_models", return_value=None), patch(
            "builtins.input", side_effect=[str(self.root), "bad-model", "anthropic/claude-sonnet-4-5", "invalid", "high", ""]
        ):
            cli.init_instance(args)
        pi = load_config(str(cli.local_config())).pi
        self.assertEqual((pi.default_provider, pi.default_model, pi.default_thinking), ("anthropic", "claude-sonnet-4-5", "high"))

        with patch.object(cli.sys.stdin, "isatty", return_value=True), patch.object(cli, "list_pi_models", return_value=None), patch("builtins.input", side_effect=["", "", "", "", "", ""]):
            cli.configure_telegram(self.parse("configure", "telegram"))
        pi = load_config(str(cli.local_config())).pi
        self.assertEqual((pi.default_provider, pi.default_model, pi.default_thinking), ("anthropic", "claude-sonnet-4-5", "high"))

    def test_empty_prompts_keep_pi_defaults(self):
        with patch.object(cli.sys.stdin, "isatty", return_value=True), patch.object(cli, "list_pi_models", return_value=None), patch(
            "builtins.input", side_effect=["", "123", str(self.root), "", "", ""]
        ):
            cli.init_instance(self.parse("init"))
        pi = load_config(str(cli.local_config())).pi
        self.assertIsNone(pi.default_provider)
        self.assertIsNone(pi.default_model)
        self.assertIsNone(pi.default_thinking)

    def test_pi_catalog_uses_configured_executable_agent_dir_and_preserves_model_ids(self):
        pi = {"command": "/usr/bin/pi", "cwd": str(self.root), "agentDir": str(self.root / "agent")}
        output = ("provider      model                         context  max-out  thinking  images\n"
                  "anthropic     claude-sonnet-4-5             200K     64K      yes       yes\n"
                  "huggingface   org/Long-Model-ID             64K      32K      yes       no\n")
        with patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")) as run:
            self.assertEqual(cli.list_pi_models(pi), ["anthropic/claude-sonnet-4-5", "huggingface/org/Long-Model-ID"])
        self.assertEqual(run.call_args.args[0], ["/usr/bin/pi", "--list-models"])
        self.assertEqual(run.call_args.kwargs["cwd"], str(self.root))
        self.assertEqual(run.call_args.kwargs["env"]["PI_CODING_AGENT_DIR"], str(self.root / "agent"))

    def test_pi_catalog_failure_and_malformed_output_fall_back(self):
        pi = {"command": "pi", "cwd": str(self.root)}
        with patch.object(cli.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(cli.list_pi_models(pi))
        with patch.object(cli.subprocess, "run", side_effect=subprocess.TimeoutExpired("pi", 15)):
            self.assertIsNone(cli.list_pi_models(pi))
        with patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "invalid\n", "")):
            self.assertIsNone(cli.list_pi_models(pi))

    def test_picker_selects_exact_model_and_can_keep_or_reset_existing_default(self):
        models = ["anthropic/claude-sonnet-4-5", "huggingface/org/Long-Model-ID"]
        with patch.object(cli, "list_pi_models", return_value=models), patch("prompt_toolkit.prompt", return_value=models[1]) as prompt:
            self.assertEqual(cli.select_model({"command": "pi", "cwd": str(self.root)}, models[0]), models[1])
            validator = prompt.call_args.kwargs["validator"]
            validator.validate(Document(models[1]))
            with self.assertRaises(ValidationError):
                validator.validate(Document("anthropic/typo"))
        with patch.object(cli, "list_pi_models", return_value=models), patch("prompt_toolkit.prompt", side_effect=["", "default"]):
            self.assertIsNone(cli.select_model({"command": "pi", "cwd": str(self.root)}, models[0]))
            self.assertEqual(cli.select_model({"command": "pi", "cwd": str(self.root)}, models[0]), "")

    def test_interactive_picker_saves_selection_and_resets_explicitly(self):
        config_path = self.root / "bot.yaml"
        args = ("-c", str(config_path), "configure", "telegram", "--allowed-user-id", "123", "--bot-token", "fake:token")
        models = ["huggingface/org/Long-Model-ID"]
        with patch.object(cli.sys.stdin, "isatty", return_value=True), patch.object(cli, "list_pi_models", return_value=models), patch(
            "prompt_toolkit.prompt", side_effect=[models[0], "", "default"]
        ), patch("builtins.input", side_effect=[str(self.root), "", "", str(self.root), "", "", str(self.root), "", ""]):
            cli.configure_telegram(self.parse(*args))
            self.assertEqual(load_config(str(config_path)).pi.default_model, "org/Long-Model-ID")
            cli.configure_telegram(self.parse(*args))
            self.assertEqual(load_config(str(config_path)).pi.default_model, "org/Long-Model-ID")
            cli.configure_telegram(self.parse(*args))
            self.assertIsNone(load_config(str(config_path)).pi.default_model)

    def test_invalid_flags_do_not_create_config(self):
        for flag, value in (("--model", "no-provider"), ("--model", "provider/"), ("--thinking", "ultra")):
            with self.subTest(flag=flag, value=value):
                with self.assertRaises(ValueError):
                    cli.init_instance(self.parse("init", flag, value))
                self.assertFalse(cli.local_config().exists())


if __name__ == "__main__":
    unittest.main()
