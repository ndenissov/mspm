# mcpm/const.py

SOURCE_ICONS = {
    "modrinth": "[green]M[/]",
    "spigot": "[orange1]S[/]",
    "bukkit": "[red]B[/]",
    "hangar": "[blue]H[/]",
    "url": "[grey70]U[/]",
    "core": "[magenta]C[/]"
}

DEFAULT_CONFIG = """[server]
version = "1.20.4"
platform = "PAPER"
plugins_dir = "./plugins"
jar_name = "server.jar"

[dependencies]
"""

# Приоритет источников при поиске (меньше = важнее)
SOURCE_PRIORITY = {
    "modrinth": 1,
    "hangar": 2,
    "spigot": 3,
    "bukkit": 4
}