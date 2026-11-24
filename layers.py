#!/usr/bin/env python3
"""Camadas do diff e recibo do doctor-deploy. WSL2. Não instala nada."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

VERSION = 2
SIZE_LIMIT = 400
CC_LIMIT = 15
SOURCE_EXT = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".go", ".rs",
    ".java", ".kt", ".rb", ".php", ".c", ".cc", ".cpp", ".h", ".hpp",
}
ROOT_LOCKS = {
    "package.json": [
        "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml",
        "yarn.lock", "bun.lock", "bun.lockb",
    ],
    "pyproject.toml": ["uv.lock", "poetry.lock", "pdm.lock", "Pipfile.lock"],
    "go.mod": ["go.sum"],
    "Cargo.toml": ["Cargo.lock"],
    "Gemfile": ["Gemfile.lock"],
}
AUDIT = {
    "package-lock.json": ["npm", "audit", "--audit-level=high"],
    "npm-shrinkwrap.json": ["npm", "audit", "--audit-level=high"],
    "pnpm-lock.yaml": ["pnpm", "audit", "--audit-level=high"],
    "yarn.lock": ["yarn", "audit", "--level", "high"],
    "bun.lock": ["bun", "audit"],
    "bun.lockb": ["bun", "audit"],
}
def git(*args, check=True):
    run = subprocess.run(["git", *args], capture_output=True, text=True)
    if check and run.returncode != 0:
        raise SystemExit(run.stderr.strip() or f"git {' '.join(args)} falhou")
    return run


def root():
    return Path(git("rev-parse", "--show-toplevel").stdout.strip())


def git_dir():
    return Path(git("rev-parse", "--git-dir").stdout.strip())


def receipt_path():
    return git_dir() / "doctor-deploy" / "receipt.json"


def consent_path():
    return Path.home() / ".config" / "doctor-deploy" / "consent.json"


def quality_skip_allowed():
    # Liberação explícita por clone: git config doctor-deploy.allowNoQuality true
    run = git("config", "--bool", "--get", "doctor-deploy.allowNoQuality", check=False)
    return run.stdout.strip() == "true"


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def base_sha():
    head = git("rev-parse", "HEAD").stdout.strip()
    for ref in ("origin/master", "origin/main", "master", "main"):
        if git("rev-parse", "--verify", "--quiet", ref, check=False).returncode != 0:
            continue
        if git("rev-parse", ref).stdout.strip() == head:
            continue
        found = git("merge-base", "HEAD", ref, check=False)
        if found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    return ""


def name_status(base, head="HEAD"):
    diff = git("diff", "--name-status", "-M", base, head, check=False)
    rows = []
    for line in diff.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, paths = parts[0], parts[1:]
        rows.append((status, paths))
    return rows


def changed_paths(rows):
    out = set()
    for status, paths in rows:
        if status.startswith("D"):
            continue
        out.add(paths[-1])
    return out


def is_md(path):
    return path.endswith(".md")


def is_test(path):
    base = os.path.basename(path)
    if base.endswith(".d.ts") or ".test." in base or ".spec." in base:
        return True
    if base.startswith("test_") or base.endswith("_test.py") or base.endswith("_test.go"):
        return True
    return any(part in {"__tests__", "tests", "test", "testdata", "spec"} for part in path.split("/"))


def is_generated(path):
    if any(part in {"generated", "dist", "coverage", ".next", "vendor", "node_modules"} for part in path.split("/")):
        return True
    attr = git("check-attr", "linguist-generated", "--", path, check=False)
    return "set" in attr.stdout


def is_source(path):
    return Path(path).suffix in SOURCE_EXT and not is_test(path) and not is_generated(path)


def blob(rev, path):
    shown = git("show", f"{rev}:{path}", check=False)
    if shown.returncode != 0 or "\0" in shown.stdout:
        return None
    return shown.stdout


def lines_of(text):
    return 0 if text is None else len(text.splitlines())


def norm(path, repo):
    path = path.replace("\\", "/").strip()
    repo_s = str(repo).replace("\\", "/")
    low, repo_low = path.lower(), repo_s.lower()
    idx = low.find(repo_low + "/")
    if idx >= 0:
        path = path[idx + len(repo_s) + 1 :]
    return path.lstrip("./")


def parse_lcov(text, repo):
    cov, cur = {}, None
    for line in text.splitlines():
        if line.startswith("SF:"):
            cur = norm(line[3:], repo)
            cov.setdefault(cur, {})
        elif line.startswith("DA:") and cur:
            num, hits = line[3:].split(",")[:2]
            cov[cur][int(num)] = int(float(hits))
        elif line == "end_of_record":
            cur = None
    return cov


def parse_cobertura(text, repo):
    cov = {}
    try:
        tree = ET.fromstring(text)
    except ET.ParseError:
        return cov
    for node in tree.iter():
        if not node.tag.endswith("class"):
            continue
        filename = norm(node.get("filename") or "", repo)
        for line in node.iter():
            if line.tag.endswith("line") and line.get("number"):
                cov.setdefault(filename, {})[int(line.get("number"))] = int(float(line.get("hits") or 0))
    return cov


def fresh_coverage(repo, started):
    names = [
        "coverage/lcov.info", "lcov.info",
        "coverage/cobertura-coverage.xml", "coverage/cobertura.xml", "cobertura.xml",
    ]
    found = []
    for name in names:
        path = repo / name
        if path.is_file() and path.stat().st_mtime >= started - 2:
            found.append(path)
    if not found:
        return None
    return max(found, key=lambda item: item.stat().st_mtime)


def added_lines(base, path):
    diff = git("diff", "-U0", base, "HEAD", "--", path, check=False)
    nums, current = [], None
    for line in diff.stdout.splitlines():
        if line.startswith("@@"):
            plus = line.split(" ")[2][1:]
            current = int(plus.split(",")[0])
        elif line.startswith("+") and not line.startswith("+++") and current is not None:
            nums.append(current)
            current += 1
        elif line.startswith("\\"):
            continue
        elif current is not None and not line.startswith("-"):
            current += 1
    return nums


def coverage_layer(repo, base, started, warnings):
    if not base:
        warnings.append("diff-cover: sem base, skip")
        return "skip"
    artifact = fresh_coverage(repo, started)
    if artifact is None:
        warnings.append("diff-cover: sem lcov/cobertura fresco, skip")
        return "skip"
    text = artifact.read_text(errors="replace")
    cov = parse_cobertura(text, repo) if artifact.suffix == ".xml" else parse_lcov(text, repo)
    red = False
    for status, paths in name_status(base):
        path = paths[-1]
        if status.startswith("D") or not is_source(path):
            continue
        if path not in cov:
            warnings.append(f"diff-cover: {path} fora do relatório")
            continue
        for num in added_lines(base, path):
            if num in cov[path] and cov[path][num] == 0:
                warnings.append(f"diff-cover: {path}:{num} hits 0")
                red = True
    return "red" if red else "green"


def size_layer(base, warnings):
    if not base:
        warnings.append("tamanho: sem base, skip")
        return "skip"
    red = False
    for status, paths in name_status(base):
        path = paths[-1]
        if status.startswith("D") or not is_source(path):
            continue
        now = lines_of(blob("HEAD", path))
        if status.startswith("A") or blob(base, paths[0]) is None:
            if now > SIZE_LIMIT:
                warnings.append(f"tamanho: {path} novo com {now} linhas")
                red = True
            continue
        old = lines_of(blob(base, paths[0]))
        if old <= SIZE_LIMIT < now:
            warnings.append(f"tamanho: {path} cruzou {SIZE_LIMIT} ({old} → {now})")
            red = True
    return "red" if red else "green"


def lizard_map(path):
    if not shutil.which("lizard"):
        return None
    run = subprocess.run(["lizard", path], capture_output=True, text=True, timeout=60)
    found = {}
    for line in run.stdout.splitlines():
        if "@" not in line:
            continue
        bits = line.split()
        try:
            ccn = int(bits[1])
        except (IndexError, ValueError):
            continue
        token = next((bit for bit in bits if "@" in bit), "")
        name = token.split("@", 1)[0]
        if name:
            found[name] = ccn
    return found


def cc_layer(repo, base, warnings):
    if not shutil.which("lizard"):
        warnings.append("cc: lizard ausente, skip")
        return "skip"
    if not base:
        warnings.append("cc: sem base, skip")
        return "skip"
    red = False
    for status, paths in name_status(base):
        path = paths[-1]
        if status.startswith("D") or not is_source(path):
            continue
        head_map = lizard_map(str(repo / path))
        if head_map is None:
            continue
        old_text = blob(base, paths[0])
        old_map = {}
        if old_text is not None:
            with tempfile.NamedTemporaryFile("w", suffix=Path(path).suffix, delete=False) as tmp:
                tmp.write(old_text)
                tmp_name = tmp.name
            try:
                old_map = lizard_map(tmp_name) or {}
            finally:
                os.unlink(tmp_name)
        for name, ccn in head_map.items():
            old = old_map.get(name)
            if old is None and ccn > CC_LIMIT:
                warnings.append(f"cc: {path} {name} nova com {ccn}")
                red = True
            elif old is not None and old <= CC_LIMIT < ccn:
                warnings.append(f"cc: {path} {name} cruzou {CC_LIMIT} ({old} → {ccn})")
                red = True
    return "red" if red else "green"


def deps_layer(base, warnings):
    if not base:
        warnings.append("deps: sem base, skip")
        return "skip"
    changed = changed_paths(name_status(base))
    red = False
    for manifest, locks in ROOT_LOCKS.items():
        if manifest not in changed:
            continue
        if not any(lock in changed for lock in locks):
            warnings.append(f"deps: {manifest} mudou sem lock")
            red = True
    return "red" if red else "green"


def audit_layer(base, warnings):
    if not base:
        warnings.append("audit: sem base, skip")
        return "skip"
    changed = changed_paths(name_status(base))
    locks = [name for name in changed if name in {lock for locks in ROOT_LOCKS.values() for lock in locks}]
    if not locks:
        return "skip"
    ran = False
    red = False
    seen = set()
    for lock in locks:
        cmd = AUDIT.get(lock)
        if not cmd or tuple(cmd) in seen:
            continue
        seen.add(tuple(cmd))
        exe = shutil.which(cmd[0])
        if not exe:
            warnings.append(f"audit: {cmd[0]} ausente, skip")
            continue
        ran = True
        # No Windows `npm` é um .cmd: `which` resolve, mas CreateProcess com o nome
        # nu falha (WinError 2). Executa o caminho resolvido.
        run = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=60)
        # A saída do `npm audit` cita "network" no texto do próprio advisory; o sinal
        # de rede é o erro em stderr SEM o resumo "N vulnerabilities". Com resumo, o
        # retorno não-zero é achado real e o gate fica vermelho.
        has_findings = re.search(r"\d+\s+vulnerabilit", run.stdout, re.IGNORECASE) is not None
        network_failure = (not has_findings) and re.search(
            r"\b(ENOTFOUND|EAI_AGAIN|ECONNRESET|ECONNREFUSED|ETIMEDOUT)\b", run.stderr, re.IGNORECASE
        ) is not None
        if run.returncode != 0 and network_failure:
            warnings.append(f"audit: {cmd[0]} skip (rede)")
        elif run.returncode != 0:
            warnings.append(f"audit: {cmd[0]} high/critical")
            red = True
    if red:
        return "red"
    return "green" if ran else "skip"


def mutation_layer(warnings):
    consent = load_json(consent_path()) or {}
    if consent.get("mutation") == "no":
        warnings.append("mutação: recusada, skip")
        return "skip"
    cmd = None
    if shutil.which("cargo-mutants") and Path("Cargo.toml").exists():
        cmd = ["cargo-mutants", "--in-diff"]
    elif shutil.which("mutmut") and Path("pyproject.toml").exists():
        cmd = ["mutmut", "run"]
    elif Path("node_modules/.bin/stryker").exists():
        cmd = ["node_modules/.bin/stryker", "run"]
    if cmd is None:
        warnings.append("mutação: sem mutador, skip")
        return "skip"
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        warnings.append(f"mutação: report exit {run.returncode} (nunca vermelho)")
    except subprocess.TimeoutExpired:
        warnings.append("mutação: timeout 180s, skip")
    return "skip"


def cmd_layers(started):
    repo = root()
    os.chdir(repo)
    base = base_sha()
    warnings = []
    layers = {
        "diff_cover": coverage_layer(repo, base, started, warnings),
        "size": size_layer(base, warnings),
        "cc": cc_layer(repo, base, warnings),
        "deps": deps_layer(base, warnings),
        "audit": audit_layer(base, warnings),
        "mutation": mutation_layer(warnings),
    }
    red = [name for name, status in layers.items() if status == "red"]
    skipped = [name for name, status in layers.items() if status == "skip"]
    payload = {"layers": layers, "skipped": skipped, "warnings": warnings, "red": red, "base_sha": base}
    print(json.dumps(payload))
    return 1 if red else 0


def cmd_write(quality_skipped, layers_raw):
    layers = json.loads(layers_raw)
    skipped = list(layers.get("skipped") or [])
    if quality_skipped:
        skipped.insert(0, "quality")
    payload = {
        "skill": "doctor-deploy",
        "version": VERSION,
        "result": "green",
        "tested_sha": git("rev-parse", "HEAD").stdout.strip(),
        "base_sha": layers.get("base_sha") or "",
        "skipped": skipped,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    path = receipt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"[doctor-deploy] recibo {path}", file=sys.stderr)


def cmd_check(sha):
    receipt = load_json(receipt_path())
    if not receipt or receipt.get("version") != VERSION or receipt.get("result") != "green":
        print("[doctor-deploy] recibo ausente ou inválido", file=sys.stderr)
        return 1
    if "quality" in (receipt.get("skipped") or []) and not quality_skip_allowed():
        print("[doctor-deploy] recibo sem quality e repo sem doctor-deploy.allowNoQuality", file=sys.stderr)
        return 1
    tested = receipt.get("tested_sha") or ""
    if git("merge-base", "--is-ancestor", tested, sha, check=False).returncode != 0:
        print("[doctor-deploy] SHA não descende do recibo", file=sys.stderr)
        return 1
    diff = git("diff", "--name-status", "-M", tested, sha, check=False)
    for line in diff.stdout.splitlines():
        paths = line.split("\t")[1:]
        if not paths or any(not is_md(path) for path in paths):
            print("[doctor-deploy] delta fora de *.md, recibo inválido", file=sys.stderr)
            return 1
    print("[doctor-deploy] recibo verde, não repete", file=sys.stderr)
    return 0


def cmd_print():
    receipt = load_json(receipt_path()) or {}
    skipped = receipt.get("skipped") or []
    if skipped:
        print("[doctor-deploy] camadas puladas: " + ", ".join(skipped), file=sys.stderr)
    else:
        print("[doctor-deploy] nenhuma camada pulada", file=sys.stderr)


def cmd_needs():
    consent = load_json(consent_path()) or {}
    if not shutil.which("lizard") and "lizard" not in consent:
        print("NEED_CONSENT lizard")
    if consent.get("mutation") != "no" and not any(shutil.which(name) for name in ("mutmut", "cargo-mutants")) and "mutation" not in consent:
        print("NEED_CONSENT mutation")


def cmd_consent(tool, answer):
    if tool not in {"lizard", "mutation"} or answer not in {"yes", "no"}:
        raise SystemExit("consent inválido")
    path = consent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = load_json(path) or {}
    data[tool] = answer
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"[doctor-deploy] consent {tool}={answer}", file=sys.stderr)


def main():
    argv = sys.argv[1:]
    if not argv:
        raise SystemExit("uso: layers | write-receipt | check | print-skipped | needs-consent | consent")
    cmd = argv[0]
    if cmd == "layers":
        started = 0
        if "--started" in argv:
            started = int(argv[argv.index("--started") + 1])
        raise SystemExit(cmd_layers(started))
    if cmd == "write-receipt":
        skipped = argv[argv.index("--quality-skipped") + 1] == "1"
        raw = argv[argv.index("--layers") + 1]
        cmd_write(skipped, raw)
        return
    if cmd == "check":
        raise SystemExit(cmd_check(argv[1]))
    if cmd == "print-skipped":
        cmd_print()
        return
    if cmd == "needs-consent":
        cmd_needs()
        return
    if cmd == "consent":
        cmd_consent(argv[1], argv[2])
        return
    raise SystemExit(f"comando desconhecido: {cmd}")


if __name__ == "__main__":
    main()
