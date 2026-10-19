"""Pin the API image to the tested environment (docs/deploy.md): writes constraints-prod.txt from this venv.

    .venv/bin/python scripts/freeze_constraints.py
"""
import importlib.metadata as m
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
pins = sorted({(d.metadata["Name"].lower(), d.version) for d in m.distributions() if d.metadata["Name"]})
lines = ["# Exact versions of the tested local environment (python 3.13); the API image installs within these.",
         "# Regenerate after changing requirements: .venv/bin/python scripts/freeze_constraints.py"]
lines += [f"{n}=={v}" for n, v in pins if n not in ("pip", "setuptools", "wheel")]
(ROOT / "constraints-prod.txt").write_text("\n".join(lines) + "\n")
print(f"{len(lines) - 2} pins → constraints-prod.txt")
