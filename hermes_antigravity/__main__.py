"""CLI entry point: `python -m hermes_antigravity <command>`."""

from __future__ import annotations

import argparse
import sys

from . import agy, bridge, setup as setup_mod


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hermes-antigravity")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the OpenAI-compatible bridge.")
    serve.add_argument("--host", default=bridge.DEFAULT_HOST)
    serve.add_argument("--port", type=int, default=bridge.DEFAULT_PORT)

    sub.add_parser("setup", help="Install the minimal agy agents.")
    cfg = sub.add_parser("config", help="Declare the provider in Hermes config.yaml.")
    cfg.add_argument("--base-url", default="", help="Override the bridge URL.")
    sub.add_parser("models", help="List models agy currently offers.")
    sub.add_parser("doctor", help="Check that everything is wired up.")

    args = parser.parse_args(argv)

    if args.command == "serve":
        bridge.serve(args.host, args.port)
        return 0

    if args.command == "setup":
        code = setup_mod.install_agents()
        return code or setup_mod.configure_provider()

    if args.command == "config":
        return setup_mod.configure_provider(args.base_url)

    if args.command == "models":
        try:
            for model in agy.list_models():
                print(f"{model['id']}\t{model['display_name']}")
        except agy.AgyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.command == "doctor":
        return setup_mod.doctor()

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
