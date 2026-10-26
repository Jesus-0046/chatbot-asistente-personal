from __future__ import annotations

import pathlib
import tempfile

# Recrear en un archivo temporal pequeño una firma con Query en default para
# comprobar si ruff B008 la marcaría (un test de ruego aislado, sin tocar el repo).
src = b"from fastapi import Query\nfrom typing import Annotated\n\nasync def demo(limit: int = Query(default=50, le=100)) -> None:\n    pass\n"

p = pathlib.Path(tempfile.gettempdir()) / "b008_probe_query.py"
p.write_bytes(src)

# sin salida; la comprobación real la realiza el llm leyendo este archivo.
print("escrito", p)
