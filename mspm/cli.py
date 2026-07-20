from __future__ import annotations

import argparse
import asyncio
import platform

from .manager import PackageManager


async def async_main():
    parser = argparse.ArgumentParser(prog="mspm", description="Minecraft Package Manager")

    # Global flags
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompts")
    parser.add_argument("--allow-untested", action="store_true", help="Allow installing plugins marked as incompatible")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ADD command
    ap = subparsers.add_parser("add")
    ap.add_argument("names", nargs="+", help="Plugin names (space separated)")
    ap.add_argument("--source", "-s", choices=["modrinth", "hangar", "spigot", "bukkit"])
    ap.add_argument("--version", "-v")

    # REMOVE command
    rp = subparsers.add_parser("remove")
    rp.add_argument("names", nargs="+")

    # INSTALL and UPDATE commands
    subparsers.add_parser("install")
    subparsers.add_parser("update")

    # SEARCH command
    sp = subparsers.add_parser("search")
    sp.add_argument("query")

    # UTILS commands
    subparsers.add_parser("freeze")
    subparsers.add_parser("clean")

    args = parser.parse_args()

    pm = PackageManager(
        auto_confirm=args.yes,
        allow_untested_global=args.allow_untested,
        debug=args.debug
    )
    try:
        if args.command == "add":
            await pm.add_plugins(args.names, args.source, args.version)
        elif args.command == "remove":
            await pm.remove_plugins(args.names)
        elif args.command == "install":
            await pm.run_tasks("install")
        elif args.command == "update":
            await pm.run_tasks("update")
        elif args.command == "search":
            await pm.search(args.query)
        elif args.command == "clean":
            if hasattr(pm, 'clean'):
                await pm.clean()
            else:
                print("Clean command is not yet implemented in the package manager.")
    finally:
        await pm.close()


def main():
    if platform.system() == 'Windows':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(async_main())
