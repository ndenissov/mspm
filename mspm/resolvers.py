from __future__ import annotations

__all__ = ('ResolverEngine',)

import asyncio
import re

import httpx
from rich.console import Console

console = Console()


class ResolverEngine:
    def __init__(self, client: httpx.AsyncClient, server_version: str, platform: str, debug=False):
        self.client = client
        self.server_version = server_version
        self.platform = platform.upper()
        self.server_major = ".".join(self.server_version.split(".")[:2])
        self.debug = debug

    async def search_query(self, query: str):
        results = []

        async def search_modrinth():
            try:
                r = await self.client.get(
                    "https://api.modrinth.com/v2/search",
                    params={"query": query, "limit": 5, "facets": '[["project_type:plugin"]]'}
                )
                if r.status_code == 200:
                    for hit in r.json().get("hits", []):
                        results.append({
                            "source": "modrinth",
                            "name": hit["title"],
                            "id": hit["slug"],
                            "desc": hit["description"][:60]
                        })
            except Exception as e:
                if self.debug:
                    console.log(f"[dim]Modrinth search exception: {e}[/]")

        async def search_hangar():
            try:
                r = await self.client.get(
                    "https://hangar.papermc.io/api/v1/projects",
                    params={"q": query, "limit": 5}
                )
                if r.status_code == 200:
                    for hit in r.json().get("result", []):
                        slug = hit.get("namespace", {}).get("slug", hit["name"])
                        results.append({
                            "source": "hangar",
                            "name": hit["name"],
                            "id": slug,
                            "desc": hit["description"][:60]
                        })
            except Exception as e:
                if self.debug:
                    console.log(f"[dim]Hangar search exception: {e}[/]")

        async def search_spigot():
            try:
                r = await self.client.get(
                    f"https://api.spiget.org/v2/search/resources/{query}",
                    params={"size": 5, "field": "name"}
                )
                if r.status_code == 200:
                    for hit in r.json():
                        results.append({
                            "source": "spigot",
                            "name": hit["name"],
                            "id": str(hit["id"]),
                            "desc": hit.get("tag", "")[:60]
                        })
            except Exception as e:
                if self.debug:
                    console.log(f"[dim]Spigot search exception: {e}[/]")

        async def search_bukkit():
            try:
                params = {
                    "gameId": 432,
                    "classId": 5,
                    "searchFilter": query,
                    "sortField": 2,
                    "sortOrder": "desc",
                    "pageSize": 5
                }
                r = await self.client.get("https://api.curse.tools/v1/cf/mods/search", params=params)
                if r.status_code == 200:
                    data = r.json()
                    hits = data.get("data", data)
                    if isinstance(hits, list):
                        for hit in hits:
                            results.append({
                                "source": "bukkit",
                                "name": hit["name"],
                                "id": str(hit["id"]),
                                "desc": hit.get("summary", "")[:60]
                            })
            except Exception as e:
                if self.debug:
                    console.log(f"[dim]Bukkit search exception: {e}[/]")

        await asyncio.gather(search_modrinth(), search_hangar(), search_spigot(), search_bukkit())
        return results

    async def discover_id(self, name, source):
        if source == "modrinth":
            return name.lower()
        if source == "hangar":
            return name
        if source == "spigot":
            try:
                r = await self.client.get(
                    f"https://api.spiget.org/v2/search/resources/{name}",
                    params={"field": "name", "size": 1}
                )
                if r.json():
                    return str(r.json()[0]['id'])
            except Exception as e:
                if self.debug:
                    console.log(f"[dim]Spigot discover_id exception: {e}[/]")
        if source == "bukkit":
            try:
                r = await self.client.get(
                    "https://api.curse.tools/v1/cf/mods/search",
                    params={"gameId": 432, "classId": 5, "searchFilter": name}
                )
                hits = r.json().get("data")
                if hits:
                    return str(hits[0]['id'])
            except Exception as e:
                if self.debug:
                    console.log(f"[dim]Bukkit discover_id exception: {e}[/]")
        return None

    async def resolve(self, name, spec, compat_checker):
        source = spec.get("source")
        if source == "url":
            url = await self._resolve_github(spec["url"])
            return {
                "source": "url",
                "version": "custom",
                "filename": f"{name}.jar",
                "url": url,
                "hash": "",
                "hash_algo": "none"
            }

        res_id = spec.get("id") or spec.get("slug")
        if source == "modrinth" and res_id:
            res_id = res_id.lower()
        if not res_id:
            res_id = await self.discover_id(name, source)
        if not res_id:
            return None

        if self.debug:
            console.log(f"[dim]Resolving {name} on {source} ID={res_id}[/]")

        if source == "modrinth":
            return await self._res_modrinth(name, res_id, spec.get("version"))
        if source == "hangar":
            return await self._res_hangar(name, res_id, spec.get("version"))
        if source == "spigot":
            return await self._res_spigot(name, res_id, spec.get("version"), compat_checker)
        if source == "bukkit":
            return await self._res_bukkit(name, res_id, spec.get("version"), compat_checker)
        return None

    async def _resolve_github(self, url):
        match = re.search(r"github\.com/([^/]+)/([^/]+)", url)
        if not match:
            return url
        owner, repo = match.groups()
        api = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        try:
            r = await self.client.get(api)
            if r.status_code == 200:
                assets = r.json().get("assets", [])
                jars = [a for a in assets if a["name"].endswith(".jar")]
                if jars:
                    return jars[0]["browser_download_url"]
        except Exception as e:
            if self.debug:
                console.log(f"[dim]GitHub resolve exception: {e}[/]")
        return url

    async def _res_modrinth(self, name, slug, req_ver):
        try:
            slug = slug.lower()
            p_r = await self.client.get(f"https://api.modrinth.com/v2/project/{slug}")
            if p_r.status_code == 404:
                if self.debug:
                    console.log(f"[red]Modrinth 404 for {slug}[/]")
                return None

            real_name = p_r.json().get("title", name) if p_r.status_code == 200 else name
            r = await self.client.get(f"https://api.modrinth.com/v2/project/{slug}/version")
            if r.status_code != 200:
                return None

            versions = r.json()
            loaders = {self.platform, "bukkit", "spigot", "paper"}
            found_any = False

            for ver in versions:
                if req_ver and ver["version_number"] != req_ver:
                    continue

                is_loader_ok = bool(set(ver["loaders"]).intersection(loaders))
                is_game_ok = True
                if not req_ver:
                    if self.server_version not in ver["game_versions"]:
                        if not any(gv.startswith(self.server_major) for gv in ver["game_versions"]):
                            is_game_ok = False

                if not is_loader_ok or not is_game_ok:
                    found_any = True
                    if self.debug and not is_loader_ok:
                        console.log(f"[dim]Skip {ver['version_number']}: loader mismatch[/]")
                    if self.debug and not is_game_ok:
                        console.log(f"[dim]Skip {ver['version_number']}: version mismatch[/]")
                    continue

                f = next((x for x in ver.get("files", []) if x.get("primary")), ver.get("files", [{}])[0])
                if not f.get("url"):
                    continue
                return {
                    "source": "modrinth",
                    "version": ver["version_number"],
                    "real_name": real_name,
                    "url": f["url"],
                    "filename": f["filename"],
                    "hash": f["hashes"]["sha1"],
                    "hash_algo": "sha1"
                }
            if found_any:
                return {"error": "incompatible", "versions": [v["version_number"] for v in versions[:5]]}
        except Exception as e:
            if self.debug:
                console.log(f"[dim]Modrinth exception: {e}[/]")
        return None

    async def _res_hangar(self, name, slug, req_ver):
        try:
            r = await self.client.get(
                f"https://hangar.papermc.io/api/v1/projects/{slug}/versions",
                params={"limit": 50}
            )
            if r.status_code != 200:
                if self.debug:
                    console.log(f"[red]Hangar 404/Error: {r.status_code}[/]")
                return None

            versions = r.json().get("result", [])
            found_any = False

            for ver in versions:
                if req_ver and ver["name"] != req_ver:
                    continue
                if self.platform not in ver["downloads"]:
                    continue

                deps = ver.get("platformDependencies", {}).get(self.platform, [])
                if not req_ver and deps and self.server_version not in deps:
                    found_any = True
                    if self.debug:
                        console.log(f"[dim]Skip {ver['name']}: dependency mismatch[/]")
                    continue

                dl = ver["downloads"][self.platform]
                url = dl.get("downloadUrl") or dl.get("externalUrl")
                if not url:
                    continue

                finfo = dl.get("fileInfo")
                filename = finfo.get("name", f"{name}-{ver['name']}.jar") if finfo else f"{name}-{ver['name']}.jar"
                hash_val = finfo.get("sha256Hash", "") if finfo else ""
                return {
                    "source": "hangar",
                    "version": ver["name"],
                    "real_name": name,
                    "url": url,
                    "filename": filename,
                    "hash": hash_val,
                    "hash_algo": "sha256"
                }
            if found_any:
                return {"error": "incompatible", "versions": [v["name"] for v in versions[:5]]}
        except Exception as e:
            if self.debug:
                console.log(f"[dim]Hangar exception: {e}[/]")
            return None
        return None

    async def _res_spigot(self, name, resid, req_ver, compat_check):
        try:
            info = await self.client.get(
                f"https://api.spiget.org/v2/resources/{resid}",
                params={"fields": "testedVersions,name"}
            )
            if info.status_code != 200:
                return None

            info_j = info.json()
            real_name = info_j.get("name", name)
            if not compat_check(name, info_j.get("testedVersions", []), "Spigot"):
                return {"error": "incompatible", "versions": info_j.get("testedVersions", [])}

            r = await self.client.get(
                f"https://api.spiget.org/v2/resources/{resid}/versions",
                params={"size": 10, "sort": "-releaseDate"}
            )
            versions = r.json()
            if not versions:
                return None

            target = versions[0]
            if req_ver:
                target = next((v for v in versions if v["name"] == req_ver), target)
            return {
                "source": "spigot",
                "version": target["name"],
                "real_name": real_name,
                "url": f"https://api.spiget.org/v2/resources/{resid}/versions/{target['id']}/download",
                "filename": f"{name}-{target['name']}.jar",
                "hash": "",
                "hash_algo": "none",
                "spigot_res_id": resid,
                "spigot_ver_id": target['id']
            }
        except Exception as e:
            if self.debug:
                console.log(f"[dim]Spigot exception: {e}[/]")
            return None

    async def _res_bukkit(self, name, pid, req_ver, compat_check):
        try:
            mi = await self.client.get(f"https://api.curse.tools/v1/cf/mods/{pid}")
            real_name = mi.json().get("data", {}).get("name", name) if mi.status_code == 200 else name

            r = await self.client.get(f"https://api.curse.tools/v1/cf/mods/{pid}/files")
            files = r.json().get("data", [])
            if not isinstance(files, list):
                return None

            files.sort(key=lambda x: x['id'], reverse=True)
            for f in files:
                if req_ver and req_ver in f.get("displayName", ""):
                    return self._fmt_bukkit(f, real_name)
                if not req_ver:
                    if self.server_version in f["gameVersions"] or self.server_major in f["gameVersions"]:
                        return self._fmt_bukkit(f, real_name)

            latest = files[0]
            if not compat_check(name, latest["gameVersions"], "Bukkit"):
                return {"error": "incompatible", "versions": latest["gameVersions"]}
            return self._fmt_bukkit(latest, real_name)
        except Exception as e:
            if self.debug:
                console.log(f"[dim]Bukkit exception: {e}[/]")
            return None

    @staticmethod
    def _fmt_bukkit(f, rname):
        h = next((i["value"] for i in f.get("hashes", []) if i["algo"] == 1), "")
        return {
            "source": "bukkit",
            "version": str(f["id"]),
            "real_name": rname,
            "url": f["downloadUrl"],
            "filename": f["fileName"],
            "hash": h,
            "hash_algo": "sha1"
        }
