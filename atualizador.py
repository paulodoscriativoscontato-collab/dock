"""
Atualizador da Dock (sem dependencias, so biblioteca padrao).

Funciona com os "Releases" do GitHub: quando voce publica uma versao nova (tag v1.0.1, v1.0.2...), o
GitHub Actions gera o instalador Dock-Setup.exe e o anexa ao release. A dock de cada pessoa pergunta ao
GitHub qual e a ultima versao, baixa o instalador, confere o hash SHA-256 e instala em silencio.
"""
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.request

API_PADRAO = "https://api.github.com"
USER_AGENT = "Dock-Atualizador"


def versao_tuple(texto):
    """'v1.2.10' -> (1, 2, 10). Compara certo (1.10 > 1.9), ao contrario de comparar texto."""
    nums = re.findall(r"\d+", str(texto))
    return tuple(int(n) for n in nums[:4]) or (0,)


def _abrir(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _curl(args, timeout):
    """Reserva: o curl.exe do Windows 10/11 usa o armazenamento de certificados do proprio Windows
    (o Python embutido as vezes nao confia na cadeia de certificados do PC: antivirus, rede da empresa...)."""
    flags = 0x08000000 if sys.platform == "win32" else 0        # CREATE_NO_WINDOW
    base = ["curl.exe" if sys.platform == "win32" else "curl", "-sSL", "--ssl-no-revoke",
            "--max-time", str(int(timeout)), "-A", USER_AGENT]
    return subprocess.run(base + args, capture_output=True, creationflags=flags, timeout=timeout + 15)


def _texto(url, timeout):
    try:
        with _abrir(url, timeout) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception as e:
        if not isinstance(getattr(e, "reason", e), ssl.SSLError) and "CERTIFICATE" not in str(e).upper():
            raise
        try:
            p = _curl(["-H", "Accept: application/vnd.github+json", "--fail", url], timeout)
        except Exception:
            raise e
        if p.returncode != 0:
            raise RuntimeError(f"{e} | curl: {p.stderr.decode('utf-8', 'ignore').strip()[:200]}")
        return p.stdout.decode("utf-8", "ignore")


def verificar(repo, versao_atual, api_base=API_PADRAO, timeout=10):
    """Pergunta ao GitHub pela ultima versao publicada.

    Devolve um dict:
      {"status": "atual", "versao": "1.0.0"}
      {"status": "nova", "versao": "1.0.1", "url": ..., "sha_url": ..., "notas": ...}
      {"status": "erro", "msg": "..."}
    """
    if not repo or "/" not in repo:
        return {"status": "erro", "msg": "GITHUB_REPO nao configurado no dock.py"}
    try:
        d = json.loads(_texto(f"{api_base}/repos/{repo}/releases/latest", timeout))
    except Exception as e:
        return {"status": "erro", "msg": f"Nao consegui falar com o GitHub: {e}"}

    tag = str(d.get("tag_name", ""))
    if versao_tuple(tag) <= versao_tuple(versao_atual):
        return {"status": "atual", "versao": tag.lstrip("vV") or str(versao_atual)}

    assets = d.get("assets") or []
    setup = next((a for a in assets if str(a.get("name", "")).lower().endswith("setup.exe")), None)
    if not setup:
        return {"status": "erro", "msg": f"A versao {tag} nao tem o instalador (Dock-Setup.exe) anexado."}
    url = setup.get("browser_download_url", "")
    # so aceita baixar do proprio repositorio no GitHub (protege contra resposta adulterada)
    if api_base == API_PADRAO and not url.lower().startswith(f"https://github.com/{repo}/releases/download/".lower()):
        return {"status": "erro", "msg": "Endereco de download inesperado, atualizacao ignorada."}
    sha = next((a for a in assets if a.get("name") == str(setup.get("name")) + ".sha256"), None)
    return {
        "status": "nova",
        "versao": tag.lstrip("vV"),
        "url": url,
        "sha_url": sha.get("browser_download_url") if sha else None,
        "notas": str(d.get("body") or "")[:600],
    }


def baixar(url, destino, sha_url=None, timeout=60, progresso=None):
    """Baixa o instalador para 'destino' e confere o SHA-256 (se o release trouxe o arquivo .sha256).
    Levanta Exception se o download falhar ou o hash nao bater."""
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    esperado = None
    if sha_url:
        esperado = _texto(sha_url, timeout).split()[0].strip().lower()
    h = hashlib.sha256()
    tmp = destino + ".parte"
    try:
        with _abrir(url, timeout) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            lido = 0
            while True:
                bloco = r.read(1024 * 256)
                if not bloco:
                    break
                f.write(bloco)
                h.update(bloco)
                lido += len(bloco)
                if progresso and total:
                    progresso(lido / total)
    except Exception as e:
        if not isinstance(getattr(e, "reason", e), ssl.SSLError) and "CERTIFICATE" not in str(e).upper():
            raise
        p = _curl(["--fail", "-o", tmp, url], max(timeout, 600))
        if p.returncode != 0:
            raise RuntimeError(f"{e} | curl: {p.stderr.decode('utf-8', 'ignore').strip()[:200]}")
        h = hashlib.sha256()
        with open(tmp, "rb") as f:
            for bloco in iter(lambda: f.read(1024 * 1024), b""):
                h.update(bloco)
    if esperado and h.hexdigest().lower() != esperado:
        os.remove(tmp)
        raise ValueError("O arquivo baixado esta corrompido (hash SHA-256 diferente do esperado).")
    if os.path.exists(destino):
        os.remove(destino)
    os.replace(tmp, destino)
    return destino
