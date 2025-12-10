import sys
import re
import argparse
import hashlib
import requests
import tomli
import tomli_w
import concurrent.futures
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.prompt import Confirm

# Попытка импорта cloudscraper
try:
    import cloudscraper

    scraper = cloudscraper.create_scraper()
    USING_SCRAPER = True
except ImportError:
    USING_SCRAPER = False

console = Console()

# --- CONSTANTS & STYLES ---
SOURCE_ICONS = {
    "modrinth": "[green]M[/]",
    "spigot": "[orange1]S[/]",
    "bukkit": "[red]B[/]",
    "hangar": "[blue]H[/]",
    "url": "[grey70]U[/]",
}


class PluginManager:
    def __init__(self, config_path="mpm.toml", auto_confirm=False, allow_untested_global=False, debug=False):
        self.config_path = Path(config_path)
        self.lock_path = self.config_path.with_suffix(".lock")
        self.auto_confirm = auto_confirm
        self.allow_untested_global = allow_untested_global
        self.debug = debug

        if not self.config_path.exists():
            self.config = {
                "server": {"version": "1.20.4", "platform": "PAPER", "plugins_dir": "./plugins"},
                "dependencies": {}
            }
        else:
            with open(self.config_path, "rb") as f:
                self.config = tomli.load(f)

        self.server_version = self.config["server"]["version"]
        self.platform = self.config["server"].get("platform", "PAPER").lower()
        self.plugins_dir = Path(self.config["server"].get("plugins_dir", "./plugins"))
        self.lock_data = self._load_lock()

        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
        }

    # --- I/O & HELPERS ---

    def _load_lock(self):
        if self.lock_path.exists():
            with open(self.lock_path, "rb") as f: return tomli.load(f)
        return {}

    def _save_lock(self):
        # Sort keys for consistent file output
        sorted_lock = dict(sorted(self.lock_data.items(), key=lambda item: item[0].lower()))
        self.lock_data = sorted_lock  # Update in memory too
        with open(self.lock_path, "wb") as f: tomli_w.dump(self.lock_data, f)

    def _save_config(self):
        # Sort dependencies section alphabetically
        deps = self.config.get("dependencies", {})
        sorted_deps = dict(sorted(deps.items(), key=lambda item: item[0].lower()))
        self.config["dependencies"] = sorted_deps

        with open(self.config_path, "wb") as f: tomli_w.dump(self.config, f)

    def _calculate_hash(self, file_path, algo="sha1"):
        if not file_path.exists(): return None
        if algo not in hashlib.algorithms_available: return None
        h = hashlib.new(algo)
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""): h.update(chunk)
        return h.hexdigest()

    def _log_debug(self, msg):
        if self.debug: console.log(f"[dim italic]DEBUG: {msg}[/]")

    def _get_real_key(self, name):
        """Case-insensitive search for a plugin name in dependencies."""
        target = name.casefold()
        for key in self.config.get("dependencies", {}):
            if key.casefold() == target:
                return key
        return None

    # --- COMPATIBILITY LOGIC ---

    def _check_compatibility(self, name, supported_versions, source_name):
        if self.allow_untested_global: return True

        # We need to find the real key to check per-plugin config
        real_key = self._get_real_key(name) or name
        plugin_conf = self.config["dependencies"].get(real_key, {})
        if plugin_conf.get("allow_untested", False): return True

        if not supported_versions: return True

        if self.server_version in supported_versions: return True

        server_major = ".".join(self.server_version.split(".")[:2])
        if any(v.startswith(server_major) for v in supported_versions): return True

        if self.auto_confirm: return True
        return False

    # --- RESOLVER HELPERS ---

    def find_spigot_id(self, name):
        try:
            url = f"https://api.spiget.org/v2/search/resources/{name}"
            r = requests.get(url, params={"field": "name", "fields": "id,name,testedVersions"}, headers=self.headers)
            hits = r.json()
            if hits:
                name_casefold = name.casefold()
                for hit in hits:
                    if hit['name'].casefold() == name_casefold: return str(hit['id'])
                return str(hits[0]['id'])
        except:
            pass
        return None

    def find_bukkit_id(self, name):
        try:
            url = "https://api.curse.tools/v1/cf/mods/search"
            params = {"gameId": 432, "classId": 5, "searchFilter": name, "sortField": 2, "sortOrder": "desc",
                      "pageSize": 5}
            r = requests.get(url, params=params, headers=self.headers)
            hits = r.json().get('data', r.json())
            if hits: return str(hits[0]['id'])
        except:
            pass
        return None

    def _resolve_github_release(self, url):
        match = re.search(r"github\.com/([^/]+)/([^/]+)/releases/(?:tag/([^/]+)|latest)", url)
        if not match:
            match_root = re.search(r"github\.com/([^/]+)/([^/]+)$", url)
            if match_root:
                owner, repo = match_root.groups()
                api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
            else:
                return url
        else:
            owner, repo, tag = match.groups()
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}" if tag else f"https://api.github.com/repos/{owner}/{repo}/releases/latest"

        try:
            r = requests.get(api_url, headers=self.headers)
            r.raise_for_status()
            data = r.json()
            assets = [a for a in data.get("assets", []) if a["name"].endswith(".jar")]
            if assets: return assets[0]["browser_download_url"]
        except:
            pass
        return url

    # --- MAIN RESOLVER ---

    def resolve_version_data(self, name, spec, force_update=False):
        source = spec.get("source")
        req_version = spec.get("version")

        if source == "url":
            url = self._resolve_github_release(spec["url"])
            return {"source": "url", "version": "custom", "filename": f"{name}.jar", "url": url, "hash": "",
                    "hash_algo": "none"}

        res_id = spec.get("id") or spec.get("slug")
        if not res_id:
            if source == "spigot":
                res_id = self.find_spigot_id(name)
            elif source == "bukkit":
                res_id = self.find_bukkit_id(name)
            elif source in ["modrinth", "hangar"]:
                res_id = name

        if not res_id: return None

        if source == "hangar":
            return self._resolve_hangar(name, res_id, req_version)
        elif source == "modrinth":
            return self._resolve_modrinth(name, res_id, req_version)
        elif source == "bukkit":
            return self._resolve_bukkit(name, res_id, req_version)
        elif source == "spigot":
            return self._resolve_spigot(name, res_id, req_version)
        return None

    # --- SPECIFIC RESOLVERS ---

    def _resolve_spigot(self, name, resource_id, requested_version=None):
        if not str(resource_id).isdigit():
            found = self.find_spigot_id(resource_id)
            if found:
                resource_id = found
            else:
                return None
        try:
            res_info = requests.get(f"https://api.spiget.org/v2/resources/{resource_id}",
                                    params={"fields": "testedVersions,name"}, headers=self.headers).json()

            # Use real name from API if possible for adding
            real_name = res_info.get("name", name)

            if not self._check_compatibility(name, res_info.get("testedVersions", []), "Spigot"):
                return {"error": "incompatible", "versions": res_info.get("testedVersions", [])}

            url = f"https://api.spiget.org/v2/resources/{resource_id}/versions"
            r = requests.get(url, params={"size": 10, "sort": "-releaseDate"}, headers=self.headers)
            versions = r.json()
            if not versions: return None

            target = versions[0]
            if requested_version:
                found = next((v for v in versions if v["name"] == requested_version), None)
                if found: target = found

            return {
                "source": "spigot", "version": target["name"], "real_name": real_name,
                "url": f"https://api.spiget.org/v2/resources/{resource_id}/versions/{target['id']}/download",
                "filename": f"{name}-{target['name']}.jar",
                "hash": "", "hash_algo": "sha1", "spigot_res_id": resource_id, "spigot_ver_id": target['id']
            }
        except:
            return None

    def _resolve_hangar(self, name, slug, requested_version=None):
        platform_upper = self.platform.upper()
        try:
            r = requests.get(f"https://hangar.papermc.io/api/v1/projects/{slug}/versions",
                             params={"limit": 20, "offset": 0}, headers=self.headers)
            if r.status_code != 200: return None
            versions = r.json()["result"]

            for ver in versions:
                if requested_version and ver["name"] != requested_version: continue
                if platform_upper not in ver["downloads"]: continue

                platform_deps = ver.get("platformDependencies", {}).get(platform_upper, [])
                if not requested_version and platform_deps and self.server_version not in platform_deps: continue

                dl = ver["downloads"][platform_upper]
                file_info = dl.get("fileInfo")
                filename = file_info.get("name",
                                         f"{name}-{ver['name']}.jar") if file_info else f"{name}-{ver['name']}.jar"
                file_hash = file_info.get("sha256Hash", "") if file_info else ""
                url = dl.get("downloadUrl") or dl.get("externalUrl")
                if not url: continue

                return {"source": "hangar", "version": ver["name"], "url": url, "filename": filename, "hash": file_hash,
                        "hash_algo": "sha256"}
        except:
            pass
        return None

    def _resolve_modrinth(self, name, slug, requested_version=None):
        try:
            # Check project info for real name
            p_info = requests.get(f"https://api.modrinth.com/v2/project/{slug}", headers=self.headers)
            real_name = name
            if p_info.status_code == 200:
                real_name = p_info.json().get("title", name)

            r = requests.get(f"https://api.modrinth.com/v2/project/{slug}/version", headers=self.headers)
            if r.status_code != 200: return None
            versions = r.json()
            server_major = ".".join(self.server_version.split(".")[:2])
            loaders = {self.platform, "bukkit", "spigot", "paper"}

            for ver in versions:
                if requested_version and ver["version_number"] != requested_version: continue
                if not set(ver["loaders"]).intersection(loaders): continue
                if not requested_version:
                    if self.server_version not in ver["game_versions"]:
                        if not any(gv.startswith(server_major) for gv in ver["game_versions"]): continue

                f = next((x for x in ver["files"] if x.get("primary")), ver["files"][0])
                return {"source": "modrinth", "version": ver["version_number"], "real_name": real_name, "url": f["url"],
                        "filename": f["filename"], "hash": f["hashes"]["sha1"], "hash_algo": "sha1"}
        except:
            return None

    def _resolve_bukkit(self, name, project_id, requested_version=None):
        if not str(project_id).isdigit():
            found = self.find_bukkit_id(project_id)
            if found:
                project_id = found
            else:
                return None
        try:
            # Get Mod Info for real name
            mod_info = requests.get(f"https://api.curse.tools/v1/cf/mods/{project_id}", headers=self.headers)
            real_name = name
            if mod_info.status_code == 200:
                real_name = mod_info.json().get("data", {}).get("name", name)

            r = requests.get(f"https://api.curse.tools/v1/cf/mods/{project_id}/files", headers=self.headers)
            if r.status_code != 200: return None
            files = r.json();
            files = files.get('data', files) if isinstance(files, dict) else files
            files.sort(key=lambda x: x['id'], reverse=True)
            server_major = ".".join(self.server_version.split(".")[:2])

            for f in files:
                ver_name = f.get("displayName", str(f["id"]))
                h = next((i["value"] for i in f.get("hashes", []) if i["algo"] == 1), "")
                is_compat = self.server_version in f["gameVersions"] or server_major in f["gameVersions"]

                if requested_version:
                    if requested_version in ver_name: return {"source": "bukkit", "version": str(f["id"]),
                                                              "real_name": real_name, "url": f["downloadUrl"],
                                                              "filename": f["fileName"], "hash": h, "hash_algo": "sha1"}
                elif is_compat:
                    return {"source": "bukkit", "version": str(f["id"]), "real_name": real_name,
                            "url": f["downloadUrl"], "filename": f["fileName"], "hash": h, "hash_algo": "sha1"}

            latest = files[0]
            if not self._check_compatibility(name, latest["gameVersions"], "Bukkit"):
                return {"error": "incompatible", "versions": latest["gameVersions"]}

            return {"source": "bukkit", "version": str(latest["id"]), "real_name": real_name,
                    "url": latest["downloadUrl"], "filename": latest["fileName"],
                    "hash": next((i["value"] for i in latest.get("hashes", []) if i["algo"] == 1), ""),
                    "hash_algo": "sha1"}
        except:
            return None

    # --- CORE LOGIC (Processors) ---

    def _download_file(self, url, dest_path, resolved_info=None):
        try:
            requester = scraper if USING_SCRAPER else requests
            kwargs = {"headers": self.headers}
            if not USING_SCRAPER: kwargs["stream"] = True

            with requester.get(url, **kwargs) as r:
                if r.status_code == 403 and resolved_info and "spigot_res_id" in resolved_info:
                    rid = resolved_info["spigot_res_id"];
                    vid = resolved_info["spigot_ver_id"]
                    proxy_url = f"https://api.spiget.org/v2/resources/{rid}/versions/{vid}/download/proxy"
                    if "proxy" not in url: return self._download_file(proxy_url, dest_path, resolved_info)
                r.raise_for_status()
                with open(dest_path, "wb") as f:
                    if USING_SCRAPER:
                        f.write(r.content)
                    else:
                        for chunk in r.iter_content(8192): f.write(chunk)
            return True
        except:
            return False

    def process_plugin(self, name, spec, mode="install"):
        """Executed in thread."""
        try:
            res_dict = {
                "name": name,
                "source": spec.get("source", "?"),
                "status": "resolving",
                "msg": "Searching...",
                "lock_data": None
            }

            locked = self.lock_data.get(name)

            # Install skip check
            if mode == "install":
                needs_install = True
                if locked:
                    if spec.get("version") and spec["version"] == locked["version"]:
                        needs_install = False
                    elif not spec.get("version"):
                        needs_install = False

                    if not (self.plugins_dir / locked["filename"]).exists():
                        needs_install = True
                    elif locked.get("hash"):
                        if self._calculate_hash(self.plugins_dir / locked["filename"], locked["hash_algo"]) == locked[
                            "hash"]:
                            needs_install = False

                if not needs_install:
                    return {**res_dict, "status": "skip", "msg": "[dim]Up to date[/]"}

            # Resolve
            force_u = (mode == "update")
            resolved = self.resolve_version_data(name, spec, force_update=force_u)

            if not resolved:
                return {**res_dict, "status": "error", "msg": "[red]Not found[/]"}

            if "error" in resolved and resolved["error"] == "incompatible":
                return {**res_dict, "status": "warn_compat", "versions": resolved["versions"],
                        "msg": "[yellow]Incompatible[/]"}

            res_dict["source"] = resolved.get("source", spec.get("source"))

            # Update skip check
            if mode == "update" and locked:
                if resolved["version"] == locked["version"] and (self.plugins_dir / resolved["filename"]).exists():
                    return {**res_dict, "status": "skip", "msg": "[green]Latest[/]"}

            # Download
            res_dict["status"] = "downloading"
            dest = self.plugins_dir / resolved["filename"]

            if locked and locked.get("filename") != resolved["filename"]:
                old = self.plugins_dir / locked["filename"]
                if old.exists(): old.unlink()

            if self._download_file(resolved["url"], dest, resolved):
                return {**res_dict, "status": "done", "msg": f"[green]{resolved['version']}[/]", "lock_data": resolved}
            else:
                return {**res_dict, "status": "error", "msg": "[red]Download failed[/]"}

        except Exception as e:
            return {**res_dict, "name": name, "status": "error", "msg": f"[red]{str(e)}[/]"}

    # --- COMMANDS ---

    def run_parallel_tasks(self, task_mode="install"):
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        # Sort keys for display
        deps = self.config.get("dependencies", {})
        sorted_keys = sorted(deps.keys(), key=str.casefold)

        if not deps:
            console.print("[yellow]No dependencies found in mpm.toml[/]")
            return

        table = Table(title=f"Plugin {task_mode.capitalize()} ({self.server_version})")
        table.add_column("Source", justify="center", width=4)
        table.add_column("Plugin Name", width=30)
        table.add_column("Status", width=40)

        tasks = {}
        results = {}
        rows_data = {}

        for name in sorted_keys:
            spec = deps[name]
            src_key = spec.get("source", "url")
            icon = SOURCE_ICONS.get(src_key, "[dim]?[/]")
            rows_data[name] = {"icon": icon, "msg": "[dim]Waiting...[/]"}

        incompatible_plugins = []

        with Live(table, refresh_per_second=10) as live:
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                for name in sorted_keys:
                    rows_data[name]["msg"] = "[cyan]Resolving...[/]"
                    future = executor.submit(self.process_plugin, name, deps[name], task_mode)
                    tasks[future] = name

                for future in concurrent.futures.as_completed(tasks):
                    name = tasks[future]
                    try:
                        res = future.result()
                        results[name] = res

                        src = res.get("source", "url")
                        rows_data[name]["icon"] = SOURCE_ICONS.get(src, SOURCE_ICONS["url"])

                        if res["status"] == "downloading":
                            rows_data[name]["msg"] = "[blue]Downloading...[/]"
                        elif res["status"] == "done":
                            rows_data[name]["msg"] = f"✅ {res['msg']}"
                        elif res["status"] == "skip":
                            rows_data[name]["msg"] = f"➖ {res['msg']}"
                        elif res["status"] == "error":
                            rows_data[name]["msg"] = f"❌ {res['msg']}"
                        elif res["status"] == "warn_compat":
                            rows_data[name]["msg"] = "⚠️ [yellow]Incompatible[/]"
                            incompatible_plugins.append((name, res.get("versions", [])))

                    except Exception as e:
                        rows_data[name]["msg"] = f"[red]Exception: {e}[/]"

                    # Rebuild table with sorted keys
                    table = Table(title=f"Plugin {task_mode.capitalize()} ({self.server_version})")
                    table.add_column("Src", justify="center", width=4)
                    table.add_column("Plugin", style="bold white")
                    table.add_column("Status")

                    for pname in sorted_keys:
                        d = rows_data[pname]
                        table.add_row(d["icon"], pname, d["msg"])

                    live.update(table)

        changes = False
        for name, res in results.items():
            if res.get("lock_data"):
                self.lock_data[name] = res["lock_data"]
                changes = True

        if changes: self._save_lock()

        if incompatible_plugins:
            console.print("\n[bold yellow]Some plugins have compatibility warnings:[/]")
            for name, vers in incompatible_plugins:
                console.print(f" • [cyan]{name}[/] supports: {', '.join(vers[:5])}...")
                if Confirm.ask(f"Force install [cyan]{name}[/]?", default=False):
                    self.config["dependencies"][name]["allow_untested"] = True
                    self._save_config()
                    console.print(f"[green]Retrying {name}...[/]")
                    res = self.process_plugin(name, self.config["dependencies"][name], task_mode)
                    if res.get("lock_data"):
                        self.lock_data[name] = res["lock_data"]
                        self._save_lock()
                        console.print(f"[green]Installed {name}[/]")
                    else:
                        console.print(f"[red]Failed to install {name}[/]")

    # --- WRAPPERS ---
    def install(self):
        self.run_parallel_tasks("install")

    def update(self, target_name=None):
        if target_name:
            self.update_single(target_name)
        else:
            self.run_parallel_tasks("update")

    def update_single(self, name):
        real_key = self._get_real_key(name)
        if not real_key:
            return console.print(f"[red]Unknown plugin: {name}[/]")

        # Use real key
        res = self.process_plugin(real_key, self.config["dependencies"][real_key], "update")
        if res.get("lock_data"):
            self.lock_data[real_key] = res["lock_data"]
            self._save_lock()
            console.print(f"[green]Updated {real_key} to {res['msg']}[/]")
        else:
            console.print(f"Status: {res['msg']}")

    def remove(self, name):
        real_key = self._get_real_key(name)
        if not real_key:
            return console.print(f"[red]Not found: {name}[/]")

        if real_key in self.lock_data:
            (self.plugins_dir / self.lock_data[real_key]["filename"]).unlink(missing_ok=True)
            del self.lock_data[real_key]

        if real_key in self.config["dependencies"]:
            del self.config["dependencies"][real_key]
            self._save_config()
            self._save_lock()
            console.print(f"[green]Removed {real_key}[/]")

    def add(self, name, source=None, project_id=None, version=None, url=None):
        # Check if exists insensitive
        real_key = self._get_real_key(name)
        if real_key:
            return console.print(f"[yellow]Exists: {real_key}[/]")

        entry = {}
        final_name = name

        if url:
            entry = {"source": "url", "url": url}
        else:
            if not source:
                console.print("[dim]Auto-detecting source...[/]")
                # We try to get "real name" during detection
                res = None
                if not res: res = self.resolve_version_data(name, {"source": "hangar", "slug": name})
                if not res: res = self.resolve_version_data(name, {"source": "modrinth", "id": name.lower()})
                if not res: res = self.resolve_version_data(name, {"source": "spigot", "id": name})
                if not res: res = self.resolve_version_data(name, {"source": "bukkit", "id": name})

                if res and "source" in res:
                    source = res["source"]
                    # If API gave us a nice capitalization, use it!
                    if "real_name" in res:
                        final_name = res["real_name"]
                        console.print(f"[dim]Detected name: {final_name}[/]")
                else:
                    return console.print(f"[red]Could not find '{name}'. Specify --source[/]")

            entry = {"source": source}
            if project_id:
                if source == "hangar":
                    entry["slug"] = project_id
                else:
                    entry["id"] = project_id
            else:
                if source == "hangar":
                    entry["slug"] = name
                elif source == "modrinth":
                    entry["id"] = name.lower()
                elif source in ["spigot", "bukkit"]:
                    entry["id"] = name

        if version: entry["version"] = version

        # Save using final_name
        self.config["dependencies"][final_name] = entry
        self._save_config()
        console.print(f"[green]Added {final_name} (will install on next 'mpm install')[/]")

    def clean(self):
        if not self.plugins_dir.exists(): return
        locked = {d["filename"] for d in self.lock_data.values()}
        orphans = [f for f in self.plugins_dir.glob("*.jar") if f.name not in locked]
        if orphans:
            console.print(f"[yellow]Found {len(orphans)} orphan files.[/]")
            if Confirm.ask("Delete them?"):
                for f in orphans: f.unlink()
                console.print("[green]Cleaned.[/]")
        else:
            console.print("[green]Clean.[/]")

    def list_plugins(self):
        table = Table(title=f"Installed Plugins", show_header=True)
        table.add_column("Src", width=4)
        table.add_column("Name", style="cyan")
        table.add_column("Version", style="green")

        # Case insensitive sort for list
        sorted_items = sorted(self.lock_data.items(), key=lambda i: i[0].casefold())

        for name, data in sorted_items:
            icon = SOURCE_ICONS.get(data.get("source"), "?")
            table.add_row(icon, name, str(data.get("version")))
        console.print(table)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", "-y", action="store_true")
    parser.add_argument("--allow-untested", action="store_true")
    parser.add_argument("--debug", action="store_true")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init")
    add_p = subparsers.add_parser("add");
    add_p.add_argument("name");
    add_p.add_argument("--source", "-s");
    add_p.add_argument("--id");
    add_p.add_argument("--version", "-v");
    add_p.add_argument("--url")
    rm_p = subparsers.add_parser("remove");
    rm_p.add_argument("name")
    subparsers.add_parser("install")
    up_p = subparsers.add_parser("update");
    up_p.add_argument("name", nargs="?")
    subparsers.add_parser("list");
    subparsers.add_parser("clean")

    args = parser.parse_args()

    if args.command == "init":
        if not Path("mpm.toml").exists():
            with open("mpm.toml", "w") as f: f.write(
                '[server]\nversion="1.20.4"\nplatform="PAPER"\nplugins_dir="./plugins"\n\n[dependencies]\n')
            console.print("[green]Initialized.[/]")
        return

    pm = PluginManager(auto_confirm=args.yes, allow_untested_global=args.allow_untested, debug=args.debug)

    if args.command == "add":
        pm.add(args.name, args.source, args.id, args.version, args.url)
    elif args.command == "remove":
        pm.remove(args.name)
    elif args.command == "install":
        pm.install()
    elif args.command == "update":
        if args.name:
            pm.update_single(args.name)
        else:
            pm.update()
    elif args.command == "list":
        pm.list_plugins()
    elif args.command == "clean":
        pm.clean()


if __name__ == "__main__":
    main()
