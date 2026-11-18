"""Exécute le protocole de charge sur une ou plusieurs configurations de l'API (Docker).

Pour chaque configuration de configs.json :
  1. recrée le conteneur `api` avec ses limites (API_CPUS, API_WORKERS, API_MEMORY) ;
  2. attend que /health réponde 200 (modèle chargé) ;
  3. échauffe le service quelques secondes (mesure ignorée) ;
  4. pour chaque palier d'utilisateurs : lance Locust en mode sans interface et relève, en parallèle,
     l'usage CPU / mémoire du conteneur avec `docker stats`.
Résultats : loadtests/results/<configuration>/<utilisateurs>u_stats.csv (+ _resources.json) et meta.json.

Usage (depuis la racine du projet, services Docker lancés, API locale arrêtée) :
    uv run python loadtests/run_config.py                 # toutes les configurations
    uv run python loadtests/run_config.py --only B-1cpu-1w
Puis : uv run python loadtests/summarize.py
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
COMPOSE = ["docker", "compose", "--env-file", str(ROOT / ".env"), "-f", str(ROOT / "infra" / "compose.yml")]
HOST = os.environ.get("LOADTEST_HOST", "http://127.0.0.1:8000")


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def recreate_api(config: dict) -> None:
    """Recrée le conteneur api avec les limites de la configuration. Les variables d'environnement du
    processus sont prioritaires sur le fichier .env pour Docker Compose."""
    env = {**os.environ, **{k: v for k, v in config.items() if k.startswith("API_")}}
    subprocess.run([*COMPOSE, "up", "-d", "--force-recreate", "api"], env=env, check=True, cwd=ROOT)


def wait_healthy(timeout: int = 180) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{HOST}/health", timeout=5) as r:
                body = json.loads(r.read())
                if r.status == 200 and body.get("status") == "ok":
                    return body
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError(f"L'API n'est pas devenue saine en {timeout} s : vérifiez `docker compose logs api`.")


def container_id() -> str:
    out = subprocess.run([*COMPOSE, "ps", "-q", "api"], capture_output=True, text=True, check=True, cwd=ROOT)
    return out.stdout.strip()


def parse_mem_mib(text: str) -> float:
    """'245.3MiB / 1GiB' -> 245.3 (MiB utilisés)."""
    used = text.split("/")[0].strip()
    units = {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "kB": 1 / 1024, "MB": 1, "GB": 1024, "B": 1 / 1024 / 1024}
    for unit, factor in units.items():
        if used.endswith(unit):
            return float(used[: -len(unit)]) * factor
    return float("nan")


class ResourceSampler(threading.Thread):
    """Relève CPU (% d'un cœur : 200 % = 2 cœurs) et mémoire du conteneur toutes les ~2 s."""

    def __init__(self, cid: str):
        super().__init__(daemon=True)
        self.cid, self.cpu, self.mem = cid, [], []
        self._stop_event = threading.Event()

    def run(self):
        while not self._stop_event.is_set():
            out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}", self.cid],
                                 capture_output=True, text=True)
            if out.returncode == 0 and out.stdout.strip():
                data = json.loads(out.stdout.strip().splitlines()[0])
                self.cpu.append(float(data["CPUPerc"].rstrip("%")))
                self.mem.append(parse_mem_mib(data["MemUsage"]))

    def stop(self) -> dict:
        self._stop_event.set()
        self.join(timeout=10)
        return {"samples": len(self.cpu),
                "cpu_pct_mean": round(statistics.mean(self.cpu), 1) if self.cpu else None,
                "cpu_pct_max": round(max(self.cpu), 1) if self.cpu else None,
                "mem_mib_max": round(max(self.mem), 1) if self.mem else None}


def run_locust(users: int, seconds: int, prefix: Path | None) -> None:
    cmd = [sys.executable, "-m", "locust", "-f", str(HERE / "locustfile.py"), "--headless", "--host", HOST,
           "-u", str(users), "-r", str(max(1, users // 4)), "--run-time", f"{seconds}s", "--only-summary"]
    if prefix is not None:
        cmd += ["--csv", str(prefix)]
    # Locust renvoie 1 s'il y a eu des échecs : ce n'est pas une erreur du protocole, on les mesure.
    subprocess.run(cmd, cwd=ROOT, stdout=subprocess.DEVNULL)


def run_config(config: dict, protocol: dict, out_dir: Path) -> None:
    name = config["name"]
    target = out_dir / name
    target.mkdir(parents=True, exist_ok=True)
    log(f"=== {name} : recréation du conteneur api")
    recreate_api(config)
    health = wait_healthy()
    log(f"API prête : modèle v{health['model']['version']}, base {health['database']}")
    log(f"Échauffement {protocol['warmup_seconds']} s (non mesuré)")
    run_locust(5, protocol["warmup_seconds"], None)
    cid = container_id()
    for users in protocol["levels"]:
        log(f"{name} : palier {users} utilisateurs pendant {protocol['level_seconds']} s")
        sampler = ResourceSampler(cid)
        sampler.start()
        run_locust(users, protocol["level_seconds"], target / f"{users}u")
        resources = sampler.stop()
        (target / f"{users}u_resources.json").write_text(json.dumps(resources, indent=2), encoding="utf-8")
    meta = {"config": config, "health": health, "levels": protocol["levels"],
            "level_seconds": protocol["level_seconds"], "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "machine": {"os": platform.platform(), "cpu_count": os.cpu_count(), "python": platform.python_version()}}
    (target / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"=== {name} terminé : {target}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--protocol", default=str(HERE / "configs.json"))
    ap.add_argument("--only", help="nom d'une configuration (par défaut : toutes)")
    ap.add_argument("--out", default=str(HERE / "results"))
    args = ap.parse_args(argv)
    protocol = json.loads(Path(args.protocol).read_text(encoding="utf-8"))
    configs = [c for c in protocol["configs"] if not args.only or c["name"] == args.only]
    if not configs:
        ap.error(f"configuration inconnue : {args.only}")
    for config in configs:
        run_config(config, protocol, Path(args.out))
    log("Protocole terminé. Remise de la configuration par défaut de l'API.")
    recreate_api({})
    return 0


if __name__ == "__main__":
    sys.exit(main())
