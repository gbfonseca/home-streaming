#!/usr/bin/env python3
"""Configura a stack de mídia (qBittorrent, Bazarr, Radarr, Sonarr) de forma idempotente.

Uso (dentro do WSL, na raiz do repositório, com os containers rodando):

    python3 scripts/setup.py              # aplica tudo
    python3 scripts/setup.py radarr sonarr  # só os passos escolhidos

Os valores ajustáveis vêm do ambiente ou do arquivo .env na raiz (veja .env.example).
As chaves de API são lidas dos arquivos de configuração de cada app em CONFIG_DIR.
Logins de providers (OpenSubtitles, Legendas.net...) não são tocados: preencha na interface do Bazarr.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- config

def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()


def env(name, default):
    return os.path.expandvars(os.path.expanduser(os.environ.get(name, default)))


def env_list(name, default):
    return [x.strip() for x in env(name, default).split(",") if x.strip()]


CONFIG_DIR = Path(env("CONFIG_DIR", "~/.local/share/home-streaming"))

QBIT_CONTAINER = env("QBIT_CONTAINER", "qbittorrent")
QBIT_BLOCKED = env_list(
    "QBIT_BLOCKED_FILES",
    "*.exe,*.scr,*.bat,*.cmd,*.com,*.pif,*.lnk,*.msi,*.vbs,*.vbe,*.js,*.jse,*.wsf,*.hta,*.ps1,*.jar,*.apk,*.dll,*.reg",
)

# Limite de compartilhamento: ao atingir, o torrent é parado e o Radarr/Sonarr remove torrent + arquivos
# de /downloads (depois de importar). 0 = para assim que termina de baixar; -1 = semeia para sempre.
QBIT_MAX_RATIO = env("QBIT_MAX_RATIO", "0")
QBIT_MAX_SEEDING_MINUTES = env("QBIT_MAX_SEEDING_MINUTES", "-1")

BAZARR_URL =env("BAZARR_URL", "http://localhost:6767")
BAZARR_LANGUAGE = env("BAZARR_LANGUAGE", "pb")  # pb = Português (Brasil)
BAZARR_PROFILE_NAME = env("BAZARR_PROFILE_NAME", "Português (Brasil)")
BAZARR_PROVIDERS = env_list("BAZARR_PROVIDERS", "opensubtitlescom,embeddedsubtitles,yifysubtitles,subf2m")

RADARR_URL = env("RADARR_URL", "http://localhost:7878")
RADARR_MAX_GB = float(env("RADARR_MAX_GB", "15"))
RADARR_CF_NAME = f"Acima de {RADARR_MAX_GB:g} GB"
RADARR_CF_SCORE = -10000

SONARR_URL = env("SONARR_URL", "http://localhost:8989")
SONARR_MAX_MB_PER_MIN = float(env("SONARR_MAX_MB_PER_MIN", "68"))  # 68 MB/min ~ 4 GB por episódio de 1h


# --------------------------------------------------------------------------- helpers

def log(step, msg):
    print(f"[{step}] {msg}")


def http(method, url, api_key, body=None, form=None, header="X-Api-Key"):
    headers = {header: api_key}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form, doseq=True).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {url} -> HTTP {e.code}: {e.read()[:500]!r}") from None


def wait_for(step, url, api_key, header="X-Api-Key", tries=30):
    for _ in range(tries):
        try:
            return http("GET", url, api_key, header=header)
        except (urllib.error.URLError, ConnectionError, RuntimeError):
            time.sleep(2)
    raise RuntimeError(f"{step}: serviço não respondeu em {url}")


def arr_api_key(app):
    cfg = CONFIG_DIR / app / "config.xml"
    m = re.search(r"<ApiKey>([^<]+)</ApiKey>", cfg.read_text(encoding="utf-8"))
    if not m:
        raise RuntimeError(f"ApiKey não encontrada em {cfg}")
    return m.group(1)


# --------------------------------------------------------------------------- qBittorrent

def setup_qbittorrent():
    step = "qbittorrent"
    conf = CONFIG_DIR / "qbittorrent" / "qBittorrent" / "qBittorrent.conf"
    if not conf.exists():
        log(step, f"{conf} não existe; suba o container uma vez antes. Pulando.")
        return

    wanted = {
        "Session\\ExcludedFileNamesEnabled": "true",
        "Session\\ExcludedFileNames": ", ".join(QBIT_BLOCKED),
        # "Stop" (nunca "Remove..."): quem apaga é o Radarr/Sonarr, só depois de importar
        "Session\\GlobalMaxRatio": QBIT_MAX_RATIO,
        "Session\\GlobalMaxSeedingMinutes": QBIT_MAX_SEEDING_MINUTES,
        "Session\\ShareLimitAction": "Stop",
    }

    def render(lines):
        out, in_bt, done = [], False, False
        for line in lines:
            if line.startswith("["):
                in_bt = line.strip() == "[BitTorrent]"
                out.append(line)
                if in_bt:
                    out.extend(f"{k}={v}" for k, v in wanted.items())
                    done = True
                continue
            if in_bt and line.split("=", 1)[0] in wanted:
                continue
            out.append(line)
        if not done:
            if out and out[-1].strip():
                out.append("")
            out.append("[BitTorrent]")
            out.extend(f"{k}={v}" for k, v in wanted.items())
        return out

    current = conf.read_text(encoding="utf-8").splitlines()
    current_vals = dict(l.split("=", 1) for l in current if "=" in l and l.split("=", 1)[0] in wanted)
    if current_vals == wanted:
        log(step, "extensões bloqueadas e limite de compartilhamento já estão configurados.")
        return

    # o qBittorrent reescreve o .conf ao encerrar, então é preciso editar com ele parado
    log(step, f"parando {QBIT_CONTAINER} para editar o .conf...")
    subprocess.run(["docker", "stop", QBIT_CONTAINER], check=True, stdout=subprocess.DEVNULL)
    try:
        lines = conf.read_text(encoding="utf-8").splitlines()
        backup = conf.with_name(conf.name + time.strftime(".bak-%Y%m%d%H%M%S"))
        backup.write_text("\n".join(lines) + "\n", encoding="utf-8")
        conf.write_text("\n".join(render(lines)) + "\n", encoding="utf-8")
    finally:
        subprocess.run(["docker", "start", QBIT_CONTAINER], check=True, stdout=subprocess.DEVNULL)
    log(step, f"bloqueadas {len(QBIT_BLOCKED)} extensões; ratio máx {QBIT_MAX_RATIO}, "
              f"seeding máx {QBIT_MAX_SEEDING_MINUTES} min -> parar (backup em {backup.name}).")


# --------------------------------------------------------------------------- Radarr/Sonarr: limpeza

def ensure_remove_completed(step, api, key):
    """Após importar, remove o torrent do qBittorrent e os arquivos de /downloads."""
    cfg = http("GET", f"{api}/config/downloadclient", key)
    if not cfg.get("enableCompletedDownloadHandling"):
        cfg["enableCompletedDownloadHandling"] = True
        http("PUT", f"{api}/config/downloadclient/{cfg['id']}", key, cfg)
        log(step, "Completed Download Handling ativado.")
    for c in http("GET", f"{api}/downloadclient", key):
        if c.get("removeCompletedDownloads") and c.get("removeFailedDownloads"):
            continue
        c["removeCompletedDownloads"] = True
        c["removeFailedDownloads"] = True
        http("PUT", f"{api}/downloadclient/{c['id']}", key, c)
        log(step, f"cliente '{c['name']}': remover downloads concluídos/falhos ativado.")


# --------------------------------------------------------------------------- Bazarr

def setup_bazarr():
    step = "bazarr"
    cfg = (CONFIG_DIR / "bazarr" / "config" / "config.yaml").read_text(encoding="utf-8")
    m = re.search(r"^auth:\n(?:[ ].*\n)*?\s+apikey:\s*'?([0-9a-f]+)'?", cfg, re.M)
    if not m:
        raise RuntimeError("apikey do Bazarr não encontrada no config.yaml")
    key = m.group(1)
    hdr = "X-API-KEY"

    settings = wait_for(step, f"{BAZARR_URL}/api/system/settings", key, header=hdr)
    general = settings["general"]

    # perfis existentes são preservados: o endpoint substitui a lista inteira
    profiles = http("GET", f"{BAZARR_URL}/api/system/languages/profiles", key, header=hdr) or []
    profile = next((p for p in profiles if p["name"] == BAZARR_PROFILE_NAME), None)
    form = {}
    if profile is None:
        profile_id = max((p["profileId"] for p in profiles), default=0) + 1
        profile = {
            "profileId": profile_id, "name": BAZARR_PROFILE_NAME, "cutoff": None,
            "items": [{"id": 1, "language": BAZARR_LANGUAGE, "forced": "False", "hi": "False",
                       "audio_exclude": "False", "audio_only_include": "False"}],
            "mustContain": [], "mustNotContain": [], "originalFormat": False, "tag": None,
        }
        form["languages-profiles"] = json.dumps(profiles + [profile])
        log(step, f"criando perfil '{BAZARR_PROFILE_NAME}' (id {profile_id}).")
    else:
        log(step, f"perfil '{BAZARR_PROFILE_NAME}' já existe (id {profile['profileId']}).")

    # idiomas habilitados: mantém os atuais e garante o desejado
    langs = http("GET", f"{BAZARR_URL}/api/system/languages?history=false", key, header=hdr)
    enabled = [l["code2"] for l in langs if l["enabled"]]
    if BAZARR_LANGUAGE not in enabled:
        form["languages-enabled"] = enabled + [BAZARR_LANGUAGE]

    # providers: soma aos que já estão ativos (ex.: os que você configurou com login)
    current_providers = general.get("enabled_providers") or []
    providers = current_providers + [p for p in BAZARR_PROVIDERS if p not in current_providers]
    if providers != current_providers:
        form["settings-general-enabled_providers"] = providers
        log(step, f"providers ativos: {', '.join(providers)}")
    else:
        log(step, "providers já estão ativos.")

    pid = str(profile["profileId"])
    for kind in ("serie", "movie"):
        if not general.get(f"{kind}_default_enabled") or str(general.get(f"{kind}_default_profile")) != pid:
            form[f"settings-general-{kind}_default_enabled"] = "true"
            form[f"settings-general-{kind}_default_profile"] = pid

    if form:
        http("POST", f"{BAZARR_URL}/api/system/settings", key, form=form, header=hdr)
        log(step, "configurações aplicadas.")
    else:
        log(step, "nada a alterar.")


# --------------------------------------------------------------------------- Radarr

def setup_radarr():
    step = "radarr"
    key = arr_api_key("radarr")
    api = f"{RADARR_URL}/api/v3"
    wait_for(step, f"{api}/system/status", key)

    spec = {"name": f"Tamanho > {RADARR_MAX_GB:g} GB", "implementation": "SizeSpecification",
            "negate": False, "required": True,
            "fields": [{"name": "min", "value": RADARR_MAX_GB}, {"name": "max", "value": 1000}]}

    formats = http("GET", f"{api}/customformat", key)
    # remove formatos de tamanho criados por este script com outro limite
    for cf in formats:
        if cf["name"].startswith("Acima de ") and cf["name"].endswith(" GB") and cf["name"] != RADARR_CF_NAME:
            http("DELETE", f"{api}/customformat/{cf['id']}", key)
            log(step, f"removido custom format antigo '{cf['name']}'.")
    cf = next((c for c in formats if c["name"] == RADARR_CF_NAME), None)
    if cf is None:
        cf = http("POST", f"{api}/customformat", key,
                  {"name": RADARR_CF_NAME, "includeCustomFormatWhenRenaming": False, "specifications": [spec]})
        log(step, f"criado custom format '{RADARR_CF_NAME}'.")
    else:
        log(step, f"custom format '{RADARR_CF_NAME}' já existe.")

    for p in http("GET", f"{api}/qualityprofile", key):
        item = next((f for f in p["formatItems"] if f["format"] == cf["id"]), None)
        if item and item["score"] == RADARR_CF_SCORE and p.get("minFormatScore", 0) > RADARR_CF_SCORE:
            continue
        p["formatItems"] = [f for f in p["formatItems"] if f["format"] != cf["id"]]
        p["formatItems"].append({"format": cf["id"], "name": RADARR_CF_NAME, "score": RADARR_CF_SCORE})
        if p.get("minFormatScore", 0) <= RADARR_CF_SCORE:
            p["minFormatScore"] = 0
        http("PUT", f"{api}/qualityprofile/{p['id']}", key, p)
        log(step, f"perfil '{p['name']}' agora rejeita releases > {RADARR_MAX_GB:g} GB.")
    ensure_remove_completed(step, api, key)
    log(step, "ok.")


# --------------------------------------------------------------------------- Sonarr

def setup_sonarr():
    step = "sonarr"
    key = arr_api_key("sonarr")
    api = f"{SONARR_URL}/api/v3"
    wait_for(step, f"{api}/system/status", key)

    mx = SONARR_MAX_MB_PER_MIN
    changed = 0
    for d in http("GET", f"{api}/qualitydefinition", key):
        if d["quality"]["name"] == "Unknown":
            continue
        if d.get("maxSize") == mx and (d.get("preferredSize") or 0) < mx:
            continue
        d["maxSize"] = mx
        if d.get("preferredSize") is None or d["preferredSize"] >= mx:
            d["preferredSize"] = mx - 1
        if d["minSize"] >= d["preferredSize"]:
            d["minSize"] = 0
        http("PUT", f"{api}/qualitydefinition/{d['id']}", key, d)
        changed += 1
    ensure_remove_completed(step, api, key)
    log(step, f"limite de {mx:g} MB/min (~{mx * 60 / 1024:.1f} GB por episódio de 1h); "
              f"{changed} qualidade(s) alterada(s).")


# --------------------------------------------------------------------------- main

STEPS = {
    "qbittorrent": setup_qbittorrent,
    "bazarr": setup_bazarr,
    "radarr": setup_radarr,
    "sonarr": setup_sonarr,
}


def main(argv):
    names = argv or list(STEPS)
    unknown = [n for n in names if n not in STEPS]
    if unknown:
        sys.exit(f"passos desconhecidos: {', '.join(unknown)} (disponíveis: {', '.join(STEPS)})")
    failed = []
    for name in names:
        try:
            STEPS[name]()
        except Exception as e:  # segue para os próximos passos
            log(name, f"ERRO: {e}")
            failed.append(name)
    if failed:
        sys.exit(f"falharam: {', '.join(failed)}")


if __name__ == "__main__":
    main(sys.argv[1:])
