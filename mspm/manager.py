__all__ = ('PackageManager',)

import asyncio
import sys
from pathlib import Path

import httpx
import tomli_w

# Support built-in tomllib for Python 3.11+ while maintaining backward compatibility
if sys.version_info >= (3, 11):
    import tomllib as tomli
else:
    import tomli

from rich.console import Console
from rich.live import Live
from rich.prompt import Confirm
from rich.table import Table
from .browser import get_cookies_via_browser
from .const import DEFAULT_CONFIG, SOURCE_ICONS, SOURCE_PRIORITY
from .resolvers import ResolverEngine
from .utils import get_real_key

try:
    import cloudscraper

    scraper = cloudscraper.create_scraper(browser='chrome')
    USING_SCRAPER = True
except ImportError:
    USING_SCRAPER = False

console = Console()


class PackageManager:
    def __init__(self, config_path="mspm.toml", auto_confirm=False, allow_untested_global=False, debug=False):
        self.config_path = Path(config_path)
        self.lock_path = self.config_path.with_suffix(".lock")
        self.auto_confirm = auto_confirm
        self.allow_untested_global = allow_untested_global
        self.debug = debug
        self.client = httpx.AsyncClient(
            headers={"User-Agent": "MSPM/2.0"}, follow_redirects=True, timeout=30.0
        )

        # Concurrency and cache controls
        self.browser_lock = asyncio.Lock()
        self.cached_cookies = {}
        self.cached_ua = None

        if not self.config_path.exists():
            with open(self.config_path, "w") as f:
                f.write(DEFAULT_CONFIG)

        with open(self.config_path, "rb") as f:
            self.config = tomli.load(f)

        self.server_conf = self.config["server"]
        self.server_version = self.server_conf["version"]
        self.platform = self.server_conf.get("platform", "PAPER").upper()
        root = Path(self.server_conf.get("root_dir", "."))
        self.plugins_dir = root / self.server_conf.get("plugins_dir", "./plugins")
        self.server_jar = root / self.server_conf.get("jar_name", "server.jar")

        if self.lock_path.exists():
            with open(self.lock_path, "rb") as f:
                self.lock_data = tomli.load(f)
        else:
            self.lock_data = {}

        self.resolver = ResolverEngine(self.client, self.server_version, self.platform, debug=self.debug)

    async def close(self):
        await self.client.aclose()

    def _save_state(self):
        if "dependencies" in self.config:
            self.config["dependencies"] = dict(
                sorted(self.config["dependencies"].items(), key=lambda i: i[0].lower())
            )
        with open(self.config_path, "wb") as f:
            tomli_w.dump(self.config, f)

    def check_compatibility(self, name, supported_versions) -> bool:
        if self.allow_untested_global:
            return True

        real_key = get_real_key(self.config.get("dependencies", {}), name) or name
        plugin_conf = self.config.get("dependencies", {}).get(real_key, {})
        if plugin_conf.get("allow_untested", False):
            return True
        if not supported_versions:
            return True
        if self.server_version in supported_versions:
            return True

        server_major = ".".join(self.server_version.split(".")[:2])
        if any(v.startswith(server_major) for v in supported_versions):
            return True
        if self.auto_confirm:
            return True
        return False

    async def process_plugin(self, name, spec, mode="install"):
        res_dict = {
            "name": name,
            "source": spec.get("source", "?"),
            "status": "resolving",
            "msg": "...",
            "lock_data": None
        }
        if self.debug:
            console.log(f"[dim]Processing {name} ({mode})...[/]")

        locked = self.lock_data.get("dependencies", {}).get(name)
        if mode == "install" and locked:
            installed = self.plugins_dir / locked["filename"]
            ver_match = (spec.get("version") == locked["version"]) if spec.get("version") else True
            if ver_match and installed.exists():
                return {**res_dict, "status": "skip", "msg": "[dim]Up to date[/]"}

        resolved = await self.resolver.resolve(name, spec, self.check_compatibility)
        if not resolved:
            return {**res_dict, "status": "error", "msg": "[red]Not found[/]"}
        if resolved.get("error") == "incompatible":
            return {
                **res_dict,
                "status": "warn_compat",
                "versions": resolved["versions"],
                "msg": "[yellow]Incompatible[/]"
            }

        res_dict["source"] = resolved.get("source")
        if mode == "update" and locked:
            if resolved["version"] == locked["version"] and (self.plugins_dir / resolved["filename"]).exists():
                return {**res_dict, "status": "skip", "msg": "[dim]Latest[/]"}

        res_dict["status"] = "downloading"
        dest = self.plugins_dir / resolved["filename"]
        if locked and locked.get("filename") != resolved["filename"]:
            (self.plugins_dir / locked["filename"]).unlink(missing_ok=True)

        if await self._download_file(resolved["url"], dest, resolved):
            return {
                **res_dict,
                "status": "done",
                "msg": f"[green]{resolved['version']}[/]",
                "lock_data": resolved
            }
        return {**res_dict, "status": "error", "msg": "[red]Download failed[/]"}

    async def _download_file(self, url, dest, info):
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Spigot downloads require cookie management or direct scraping fallbacks
        if info.get("source") == "spigot":
            if self.cached_cookies:
                if await self._try_download_with_cookies(url, dest, self.cached_cookies, self.cached_ua):
                    return True
                if self.debug:
                    console.log(f"[yellow]Cached cookies expired for {info.get('real_name')}.[/]")
                self.cached_cookies = {}

            if USING_SCRAPER:
                try:
                    r = await asyncio.to_thread(scraper.get, url, **{"stream": True, "timeout": 15})
                    if "text/html" in r.headers.get("Content-Type", "") and r.status_code == 200:
                        raise Exception("Verification challenge detected")
                    r.raise_for_status()
                    with open(dest, "wb") as f:
                        for chunk in r.iter_content(8192):
                            f.write(chunk)
                    return True
                except Exception as e:
                    if self.debug:
                        console.log(f"[dim]Cloudscraper download exception: {e}[/]")

            async with self.browser_lock:
                if self.cached_cookies:
                    if await self._try_download_with_cookies(url, dest, self.cached_cookies, self.cached_ua):
                        return True

                if self.debug:
                    console.log(f"[yellow]Launching browser interface for {info.get('real_name')}...[/]")
                target_url = url
                if "spigot_res_id" in info:
                    target_url = f"https://www.spigotmc.org/resources/{info['spigot_res_id']}"

                cookies, ua = await asyncio.to_thread(get_cookies_via_browser, target_url)
                if cookies:
                    self.cached_cookies = cookies
                    self.cached_ua = ua
                    return await self._try_download_with_cookies(url, dest, cookies, ua)
            return False

        return await self._try_download_with_cookies(url, dest, {}, None)

    async def _try_download_with_cookies(self, url, dest, cookies, ua):
        headers = {}
        if ua:
            headers["User-Agent"] = ua
        try:
            async with self.client.stream("GET", url, headers=headers, cookies=cookies, follow_redirects=True) as r:
                r.raise_for_status()
                if "text/html" in r.headers.get("Content-Type", "") and "spigot" in url:
                    return False
                with open(dest, "wb") as f:
                    async for chunk in r.aiter_bytes():
                        f.write(chunk)
            return True
        except Exception as e:
            if self.debug:
                console.log(f"[dim]Cookie download exception ({url}): {e}[/]")
            return False

    async def run_tasks(self, task_mode="install"):
        deps = self.config.get("dependencies", {})
        sorted_keys = sorted(deps.keys(), key=str.casefold)
        results = {}
        incompatible = []

        if self.debug:
            console.rule(f"Task: {task_mode} (DEBUG MODE)")
            tasks = [self.process_plugin(name, deps[name], task_mode) for name in sorted_keys]
            for future in asyncio.as_completed(tasks):
                res = await future
                name = res["name"]
                results[name] = res
                status_icon = "✅" if res["status"] == "done" else "❌" if res["status"] == "error" else "➖"
                if res["status"] == "warn_compat":
                    status_icon = "⚠️"
                    incompatible.append((name, res.get("versions", [])))
                console.print(f"{status_icon} [bold]{name}[/]: {res['msg']} ({res['status']})")
        else:
            table = Table(title=f"Task: {task_mode.capitalize()}")
            table.add_column("S", width=4)
            table.add_column("Plugin", width=30)
            table.add_column("Status")
            rows = {k: {"icon": "?", "msg": "..."} for k in sorted_keys}

            with Live(table, refresh_per_second=10) as live:
                tasks = [self.process_plugin(name, deps[name], task_mode) for name in sorted_keys]
                for future in asyncio.as_completed(tasks):
                    res = await future
                    name = res["name"]
                    results[name] = res
                    rows[name]["icon"] = SOURCE_ICONS.get(res.get("source"), "?")
                    st = res["status"]
                    if st == "done":
                        rows[name]["msg"] = f"✅ {res['msg']}"
                    elif st == "error":
                        rows[name]["msg"] = f"❌ {res['msg']}"
                    elif st == "skip":
                        rows[name]["msg"] = f"➖ {res['msg']}"
                    elif st == "warn_compat":
                        rows[name]["msg"] = "⚠️ Incompatible"
                        incompatible.append((name, res.get("versions", [])))
                    else:
                        rows[name]["msg"] = st

                    table = Table(title=f"Task: {task_mode.capitalize()}")
                    table.add_column("S", width=4)
                    table.add_column("Plugin", width=30)
                    table.add_column("Status")
                    for k in sorted_keys:
                        table.add_row(rows[k]["icon"], k, rows[k]["msg"])
                    live.update(table)

        if "dependencies" not in self.lock_data:
            self.lock_data["dependencies"] = {}
        for name, res in results.items():
            if res.get("lock_data"):
                self.lock_data["dependencies"][name] = res["lock_data"]
        self._save_state()

        if incompatible:
            console.print("\n[bold yellow]Compatibility Warnings:[/]")
            for name, vers in incompatible:
                v_str = ", ".join(vers[:3]) + "..." if vers else "None"
                console.print(f'[dim]{v_str}[/]')
                if Confirm.ask(f"Force install [cyan]{name}[/] anyway?"):
                    real_key = get_real_key(self.config["dependencies"], name) or name
                    self.config["dependencies"][real_key]["allow_untested"] = True
                    self._save_state()
                    console.print(f"[green]Retrying {name}...[/]")
                    await self.process_plugin(name, self.config["dependencies"][real_key], task_mode)

    async def add_plugins(self, names: list, source=None, version=None):
        for name in names:
            if get_real_key(self.config.get("dependencies", {}), name):
                console.print(f"[yellow]Skipping {name}: already exists[/]")
                continue
            final_name = name
            if not source:
                console.print(f"[dim]Searching {name}...[/]")
                results = await self.resolver.search_query(name)
                exact = [r for r in results if r["name"].casefold() == name.casefold()]
                if exact:
                    exact.sort(key=lambda x: SOURCE_PRIORITY.get(x["source"], 99))
                    best = exact[0]
                    console.print(f"[green]Found {best['name']} on {best['source']}[/]")
                    entry = {"source": best["source"], "id": best["id"]}
                    final_name = best["name"]
                else:
                    console.print(f"[red]No exact match found for {name}[/]")
                    continue
            else:
                entry = {"source": source, "id": name}
            if version:
                entry["version"] = version
            if "dependencies" not in self.config:
                self.config["dependencies"] = {}
            self.config["dependencies"][final_name] = entry
        self._save_state()
        await self.run_tasks("install")

    async def remove_plugins(self, names: list):
        for name in names:
            key = get_real_key(self.config.get("dependencies", {}), name)
            if not key:
                console.print(f"[red]Not found: {name}[/]")
                continue
            if key in self.lock_data.get("dependencies", {}):
                fname = self.lock_data["dependencies"][key]["filename"]
                (self.plugins_dir / fname).unlink(missing_ok=True)
                del self.lock_data["dependencies"][key]
            del self.config["dependencies"][key]
            console.print(f"[green]Removed {key}[/]")
        self._save_state()

    async def search(self, query):
        res = await self.resolver.search_query(query)
        t = Table(show_header=True)
        t.add_column("Src")
        t.add_column("Name")
        t.add_column("ID")
        for r in res:
            t.add_row(r["source"], r["name"], r["id"])
        console.print(t)
