#!/usr/bin/env python3
"""Camadas do diff e recibo do doctor-deploy. WSL2. Não instala nada."""

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

VERSION = 3
CONFIG_FILE = ".doctor-deploy.toml"
FALLBACK_CONFIG_FILE = "doctor-deploy.toml"
LEGACY_CONFIG_FILE = ".doctor-deploy.json"
VALID_LAYERS = {"diff_cover", "size", "cc", "mocks", "integration", "audit", "mutation"}

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - fallback para Python <= 3.10
    tomllib = None


def _scan_toml_unquoted(text):
    """Retorna (pares (idx, ch) fora de aspas, aspa aberta ao final)."""
    out = []
    quote = ""
    escaped = False
    for idx, ch in enumerate(text):
        if escaped:
            escaped = False
        elif quote:
            if quote == '"' and ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
        elif ch in ('"', "'"):
            quote = ch
        else:
            out.append((idx, ch))
    return out, quote


def _strip_toml_comment(line):
    unquoted, _ = _scan_toml_unquoted(line)
    for idx, ch in unquoted:
        if ch == "#":
            return line[:idx]
    return line


def _split_toml_kv(line):
    unquoted, _ = _scan_toml_unquoted(line)
    for idx, ch in unquoted:
        if ch == "=":
            return line[:idx].strip(), line[idx + 1 :].strip()
    raise ValueError(f"linha inválida sem '=': {line!r}")


def _brackets_balanced(expr):
    unquoted, open_quote = _scan_toml_unquoted(expr)
    depth = 0
    for _, ch in unquoted:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth < 0:
                raise ValueError(f"colchete ']' sem abertura: {expr}")
    return depth == 0 and not open_quote


def _parse_toml_basic_str(s):
    inner = s[1:-1]
    out = []
    i = 0
    escapes = {'"': '"', "\\": "\\", "n": "\n", "t": "\t", "r": "\r"}
    while i < len(inner):
        ch = inner[i]
        if ch == "\\":
            i += 1
            if i >= len(inner) or inner[i] not in escapes:
                raise ValueError(f"escape inválido em string TOML: {s}")
            out.append(escapes[inner[i]])
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _split_toml_array_items(inner):
    items, cur = [], []
    depth, quote, escaped = 0, "", False
    for ch in inner:
        cur.append(ch)
        if escaped:
            escaped = False
        elif quote:
            if quote == '"' and ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
        elif ch in ('"', "'"):
            quote = ch
        elif ch in "[]":
            depth += 1 if ch == "[" else -1
        elif ch == "," and depth == 0:
            cur.pop()
            token = "".join(cur).strip()
            if token:
                items.append(token)
            cur = []
    tail = "".join(cur).strip()
    if tail:
        items.append(tail)
    return items


def _parse_toml_value(raw):
    s = raw.strip()
    if not s:
        raise ValueError("valor TOML vazio")
    if s in ("true", "false"):
        return s == "true"
    if re.fullmatch(r"[+-]?\d+", s):
        return int(s)
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        return _parse_toml_basic_str(s)
    if s.startswith("'") and s.endswith("'") and len(s) >= 2:
        if "'" in s[1:-1]:
            raise ValueError(f"string literal inválida: {s}")
        return s[1:-1]
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [_parse_toml_value(tok) for tok in _split_toml_array_items(inner)] if inner else []
    raise ValueError(f"valor TOML não suportado: {s}")


def _fallback_toml_loads(text):
    root_tbl = {}
    cur_tbl = root_tbl
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        clean = _strip_toml_comment(lines[i]).strip()
        i += 1
        if not clean:
            continue
        if clean.startswith("[") and clean.endswith("]") and not clean.startswith("[["):
            parts = [p.strip().strip('"').strip("'") for p in clean[1:-1].split(".")]
            if not parts or any(not p for p in parts):
                raise ValueError(f"seção inválida: {clean}")
            cur_tbl = root_tbl
            for part in parts:
                nxt = cur_tbl.setdefault(part, {})
                if not isinstance(nxt, dict):
                    raise ValueError(f"conflito de seção TOML em {part}")
                cur_tbl = nxt
            continue
        key, val_raw = _split_toml_kv(clean)
        key = key.strip('"').strip("'")
        if not key:
            raise ValueError("chave vazia")
        while not _brackets_balanced(val_raw):
            if i >= len(lines):
                raise ValueError(f"array não fechado em {key}")
            val_raw += " " + _strip_toml_comment(lines[i]).strip()
            i += 1
        cur_tbl[key] = _parse_toml_value(val_raw)
    return root_tbl


def loads_toml(text):
    if tomllib is not None:
        return tomllib.loads(text)
    return _fallback_toml_loads(text)
SIZE_LIMIT = 400
CC_LIMIT = 15
SOURCE_EXT = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".go", ".rs",
    ".java", ".kt", ".rb", ".php", ".c", ".cc", ".cpp", ".h", ".hpp",
}
# Lock da raiz -> audit. Lock fora de sincronia com o manifest é portão do CI (instalação travada), reproduzido no quality.
AUDIT = {
    "package-lock.json": ["npm", "audit", "--audit-level=high"],
    "npm-shrinkwrap.json": ["npm", "audit", "--audit-level=high"],
    "pnpm-lock.yaml": ["pnpm", "audit", "--audit-level=high"],
    "yarn.lock": ["yarn", "audit", "--level", "high"],
    "bun.lock": ["bun", "audit"],
    "bun.lockb": ["bun", "audit"],
    "go.sum": ["govulncheck", "./..."],
    "Gemfile.lock": ["bundle-audit", "check", "--update"],
    "Cargo.lock": ["cargo-audit", "audit"],
    "uv.lock": ["pip-audit"],
    "poetry.lock": ["pip-audit"],
    "Pipfile.lock": ["pip-audit"],
}
def git(*args, check=True):
    # UTF-8 explícito: sem PYTHONUTF8 o Windows decodifica em cp1252 e quebra em acento.
    run = subprocess.run(["git", "-c", "core.quotepath=false", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
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
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "doctor-deploy" / "consent.json"


def quality_skip_allowed():
    # Liberação explícita por clone: git config doctor-deploy.allowNoQuality true
    run = git("config", "--bool", "--get", "doctor-deploy.allowNoQuality", check=False)
    return run.stdout.strip() == "true"


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def find_config_path(base):
    primary = base / CONFIG_FILE
    fallback = base / FALLBACK_CONFIG_FILE
    if primary.is_file() and fallback.is_file():
        raise SystemExit(
            f"[doctor-deploy] uso ambíguo: existem {CONFIG_FILE} e {FALLBACK_CONFIG_FILE}; mantenha apenas um"
        )
    if primary.is_file():
        return primary
    if fallback.is_file():
        return fallback
    return None


def _require_str(val, field, label):
    if not isinstance(val, str) or not val.strip():
        raise SystemExit(f"[doctor-deploy] {label}: {field} deve ser string não vazia")
    return val.strip()


def _require_str_list(val, field, label):
    if not isinstance(val, list) or any(not isinstance(x, str) or not x.strip() for x in val):
        raise SystemExit(f"[doctor-deploy] {label}: {field} deve ser lista de strings não vazias")
    return [x.strip() for x in val]


def _require_pos_int(val, field, label):
    if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
        raise SystemExit(f"[doctor-deploy] {label}: {field} deve ser inteiro positivo")
    return val


GATE_KEY_MAP = {
    "base_branch": ("baseBranch", "str"),
    "baseBranch": ("baseBranch", "str"),
    "protected_branches": ("protectedBranches", "str_list"),
    "protectedBranches": ("protectedBranches", "str_list"),
    "quality": ("quality", "str"),
    "coverage_files": ("coverageFiles", "str_list"),
    "coverageFiles": ("coverageFiles", "str_list"),
    "ignore_paths": ("ignorePaths", "str_list"),
    "ignorePaths": ("ignorePaths", "str_list"),
    "disable_layers": ("disableLayers", "layers_list"),
    "disableLayers": ("disableLayers", "layers_list"),
    "db_modules": ("dbModules", "regex_list"),
    "dbModules": ("dbModules", "regex_list"),
    "real_db_patterns": ("realDbPatterns", "regex_list"),
    "realDbPatterns": ("realDbPatterns", "regex_list"),
    "plugin_root": ("pluginRoot", "str"),
    "pluginRoot": ("pluginRoot", "str"),
}

INTEGRATION_KEY_MAP = {
    "data_dirs": "dataDirs",
    "dataDirs": "dataDirs",
    "ui_dirs": "uiDirs",
    "uiDirs": "uiDirs",
    "ignore": "ignore",
}


def _apply_mapped_field(cfg, key, val, spec_map, label, prefix=""):
    if key not in spec_map:
        raise SystemExit(f"[doctor-deploy] {label}: chave desconhecida '{prefix}{key}'")
    dest, kind = spec_map[key]
    if kind == "str":
        cfg[dest] = _require_str(val, prefix + key, label)
    elif kind == "str_list":
        cfg[dest] = _require_str_list(val, prefix + key, label)
    elif kind == "layers_list":
        items = _require_str_list(val, prefix + key, label)
        bad = [item for item in items if item not in VALID_LAYERS]
        if bad:
            raise SystemExit(f"[doctor-deploy] {label}: camada desconhecida em {prefix}{key}: {', '.join(bad)}")
        cfg[dest] = items
    elif kind == "regex_list":
        items = _require_str_list(val, prefix + key, label)
        for pat in items:
            try:
                re.compile(pat)
            except re.error as exc:
                raise SystemExit(f"[doctor-deploy] {label}: regex inválida em {prefix}{key}: {exc}")
        cfg[dest] = items


def _require_table(val, section, label):
    if not isinstance(val, dict):
        raise SystemExit(f"[doctor-deploy] {label}: [{section}] deve ser tabela")
    return val


def _normalize_plugin_section(cfg, val, label):
    for pk, pv in _require_table(val, "plugin", label).items():
        if pk not in {"root", "home"}:
            raise SystemExit(f"[doctor-deploy] {label}: chave desconhecida 'plugin.{pk}'")
        cfg["pluginRoot"] = _require_str(pv, f"plugin.{pk}", label)


def _normalize_limits_section(cfg, val, label):
    limits = {}
    for lk, lv in _require_table(val, "limits", label).items():
        if lk not in {"size", "cc"}:
            raise SystemExit(f"[doctor-deploy] {label}: chave desconhecida 'limits.{lk}'")
        limits[lk] = _require_pos_int(lv, f"limits.{lk}", label)
    cfg["limits"] = limits


def _normalize_integration_section(cfg, val, label):
    integ = {}
    for ik, iv in _require_table(val, "integration", label).items():
        if ik not in INTEGRATION_KEY_MAP:
            raise SystemExit(f"[doctor-deploy] {label}: chave desconhecida 'integration.{ik}'")
        integ[INTEGRATION_KEY_MAP[ik]] = _require_str_list(iv, f"integration.{ik}", label)
    cfg["integration"] = integ


PATTERN_KEY_MAP = {k: v for k, v in GATE_KEY_MAP.items() if k in {"db_modules", "dbModules", "real_db_patterns", "realDbPatterns"}}


def _normalize_repo_config(raw, label):
    if not isinstance(raw, dict):
        raise SystemExit(f"[doctor-deploy] {label} ilegível: esperado objeto TOML")
    project = raw.get("project")
    if project is None:
        raise SystemExit(f"[doctor-deploy] {label}: campo obrigatório 'project' ausente")
    cfg = {"project": _require_str(project, "project", label)}
    if "version" in raw:
        v = _require_pos_int(raw["version"], "version", label)
        if v != 1:
            raise SystemExit(f"[doctor-deploy] {label}: version={v} não suportada (use 1)")
        cfg["version"] = v
    if "workspace" in raw:
        cfg["workspace"] = _require_str(raw["workspace"], "workspace", label)

    handlers = {
        "plugin": _normalize_plugin_section,
        "limits": _normalize_limits_section,
        "integration": _normalize_integration_section,
        "gate": lambda c, v, lb: [_apply_mapped_field(c, k, gv, GATE_KEY_MAP, lb, "gate.") for k, gv in _require_table(v, "gate", lb).items()],
        "patterns": lambda c, v, lb: [_apply_mapped_field(c, k, pv, PATTERN_KEY_MAP, lb, "patterns.") for k, pv in _require_table(v, "patterns", lb).items()],
    }
    for key, val in raw.items():
        if key in {"project", "version", "workspace"}:
            continue
        if key in handlers:
            handlers[key](cfg, val, label)
        elif key in GATE_KEY_MAP:
            _apply_mapped_field(cfg, key, val, GATE_KEY_MAP, label)
        else:
            valid = ", ".join(sorted({"project", "version", "workspace", *handlers}))
            raise SystemExit(f"[doctor-deploy] {label}: chave desconhecida '{key}' (seções válidas: {valid})")
    return cfg


def resolve_plugin_root(raw_root, repo=None):
    if not raw_root:
        return None
    expanded = os.path.expandvars(os.path.expanduser(str(raw_root).strip()))
    if os.name == "nt" and expanded.startswith("/"):
        m_drv = re.match(r"^/([a-zA-Z])/(.*)$", expanded)
        if m_drv:
            expanded = f"{m_drv.group(1).upper()}:/{m_drv.group(2)}"
        else:
            cyg = shutil.which("cygpath")
            if cyg:
                conv = subprocess.run([cyg, "-m", expanded], capture_output=True, text=True, encoding="utf-8", check=False)
                if conv.returncode == 0 and conv.stdout.strip():
                    expanded = conv.stdout.strip()
    p = Path(expanded)
    if not p.is_absolute():
        base = Path(repo) if repo else root()
        p = (base / p).resolve()
    else:
        p = p.resolve()
    needed = ("gate.sh", "layers.py", "pre-push.sh")
    if not all((p / name).is_file() for name in needed):
        raise SystemExit(
            f"[doctor-deploy] plugin.root inválido ({p}): precisa apontar para a raiz do plugin contendo gate.sh, layers.py e pre-push.sh"
        )
    return p


def repo_config(repo=None, required=True):
    """Lê .doctor-deploy.toml (ou doctor-deploy.toml) da raiz do repo. Falha alto se ausente ou inválido."""
    base = Path(repo) if repo else root()
    path = find_config_path(base)
    if path is None:
        if not required:
            return {}
        if (base / LEGACY_CONFIG_FILE).is_file():
            raise SystemExit(
                f"[doctor-deploy] {LEGACY_CONFIG_FILE} foi substituído por {CONFIG_FILE}; migre com `gate.sh init`"
            )
        raise SystemExit(
            f"[doctor-deploy] {CONFIG_FILE} (ou {FALLBACK_CONFIG_FILE}) obrigatório na raiz do repo; rode `gate.sh init` para criar"
        )
    try:
        data = loads_toml(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"[doctor-deploy] {path.name} ilegível: {exc}")
    return _normalize_repo_config(data, path.name)


def cfg_limit(cfg, key, default):
    limits = cfg.get("limits")
    if not isinstance(limits, dict) or key not in limits:
        return default
    return _require_pos_int(limits[key], f"limits.{key}", CONFIG_FILE)


def matches_any(path, patterns):
    for pat in patterns or ():
        if not isinstance(pat, str) or not pat.strip():
            continue
        p = pat.strip().replace("\\", "/")
        prefix = p.rstrip("/")
        if not any(ch in p for ch in "*?[") and (path == prefix or path.startswith(prefix + "/")):
            return True
        if fnmatch.fnmatch(path, p) or PurePosixPath(path).match(p):
            return True
    return False


def is_ignored(path, cfg=None):
    return bool(cfg and matches_any(path, cfg.get("ignorePaths")))


def compile_extra(base_rx, patterns, field, flags=0):
    extras = [p for p in (patterns or ()) if isinstance(p, str) and p]
    if not extras:
        return base_rx
    try:
        return re.compile("|".join([base_rx.pattern, *(f"(?:{p})" for p in extras)]), flags)
    except re.error as exc:
        raise SystemExit(f"[doctor-deploy] {CONFIG_FILE}: regex inválida em {field}: {exc}")


def base_candidates(cfg=None):
    cfg = cfg or {}
    refs = []
    for raw in (os.environ.get("DOCTOR_DEPLOY_BASE") or "", cfg.get("baseBranch") or ""):
        if isinstance(raw, str) and raw.strip():
            b = raw.strip()
            if "/" in b:
                refs.append(b)
            else:
                refs.extend((f"origin/{b}", b))
    sym = git("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", check=False)
    if sym.returncode == 0 and sym.stdout.strip().startswith("refs/remotes/"):
        remote_ref = sym.stdout.strip()[len("refs/remotes/") :]
        refs.append(remote_ref)
        if "/" in remote_ref:
            refs.append(remote_ref.split("/", 1)[1])
    refs.extend(("origin/master", "origin/main", "master", "main"))
    return list(dict.fromkeys(refs))


def base_sha(cfg=None, base_ref=None):
    cfg = repo_config() if cfg is None else cfg
    head = git("rev-parse", "HEAD").stdout.strip()
    if base_ref:
        refs = [base_ref] if "/" in base_ref else [f"origin/{base_ref}", base_ref]
    else:
        refs = base_candidates(cfg)
    for ref in refs:
        if git("rev-parse", "--verify", "--quiet", ref, check=False).returncode != 0:
            continue
        if not base_ref and git("rev-parse", ref).stdout.strip() == head:
            continue
        found = git("merge-base", "HEAD", ref, check=False)
        if found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    return ""


def name_status(base, head="HEAD"):
    # head=None compara a base com a working tree.
    diff = git("diff", "--name-status", "-M", base, *([head] if head else []), check=False)
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


def is_source(path, cfg=None):
    return Path(path).suffix in SOURCE_EXT and not is_test(path) and not is_generated(path) and not is_ignored(path, cfg)


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
    # Só o prefixo "./": lstrip("./") comeria o ponto de .storybook/, .config/...
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


def parse_lcov(text, repo):
    """{arquivo: {linha: hits}} e {arquivo: {linha: branches não tomadas}}."""
    cov, br, cur = {}, {}, None
    for line in text.splitlines():
        if line.startswith("SF:"):
            cur = norm(line[3:], repo)
            cov.setdefault(cur, {})
            br.setdefault(cur, {})
        elif line.startswith("DA:") and cur:
            num, hits = line[3:].split(",")[:2]
            cov[cur][int(num)] = int(float(hits))
        elif line.startswith("BRDA:") and cur:
            bits = line[5:].split(",")
            if len(bits) == 4 and bits[3] in {"-", "0"}:
                num = int(bits[0])
                br[cur][num] = br[cur].get(num, 0) + 1
        elif line == "end_of_record":
            cur = None
    return cov, br


def parse_cobertura(text, repo):
    cov, br = {}, {}
    try:
        tree = ET.fromstring(text)
    except ET.ParseError:
        return cov, br
    for node in tree.iter():
        if not node.tag.endswith("class"):
            continue
        filename = norm(node.get("filename") or "", repo)
        for line in node.iter():
            if line.tag.endswith("line") and line.get("number"):
                num = int(line.get("number"))
                cov.setdefault(filename, {})[num] = int(float(line.get("hits") or 0))
                # condition-coverage="50% (1/2)"
                cond = re.search(r"\((\d+)/(\d+)\)", line.get("condition-coverage") or "")
                if line.get("branch") == "true" and cond and int(cond.group(1)) < int(cond.group(2)):
                    br.setdefault(filename, {})[num] = int(cond.group(2)) - int(cond.group(1))
    return cov, br


def go_module(pkg_dir):
    try:
        found = re.search(r"^module\s+(\S+)", (pkg_dir / "go.mod").read_text(encoding="utf-8"), re.MULTILINE)
    except OSError:
        return None
    return found.group(1) if found else None


def parse_gocover(text, repo, pkg_root=Path(".")):
    """coverprofile do `go test`: "mod/pkg/f.go:12.13,14.3 1 0". Sem branches; linha = maior hits dos blocos."""
    module = go_module(repo / pkg_root) or go_module(repo) or ""
    cov = {}
    for line in text.splitlines():
        found = re.match(r"(.+\.go):(\d+)\.\d+,(\d+)\.\d+ \d+ (\d+)$", line.strip())
        if not found:
            continue
        name, start, end, hits = found.group(1), int(found.group(2)), int(found.group(3)), int(found.group(4))
        if module and name.startswith(module + "/"):
            name = name[len(module) + 1 :]
        rel = norm(name, repo)
        if pkg_root != Path(".") and not (repo / rel).is_file() and (repo / pkg_root / rel).is_file():
            rel = (pkg_root / rel).as_posix()
        lines = cov.setdefault(rel, {})
        for num in range(start, end + 1):
            lines[num] = max(lines.get(num, 0), hits)
    return cov, {}


def parse_simplecov(text, repo):
    """coverage/.resultset.json do SimpleCov: {suite: {coverage: {arquivo: {lines, branches}}}}."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}, {}
    cov, arms = {}, {}
    # Várias execuções no mesmo arquivo (suites, merge do SimpleCov): linha e branch valem o maior hits.
    for suite in data.values():
        for name, entry in ((suite or {}).get("coverage") or {}).items():
            path = norm(name, repo)
            lines = entry.get("lines", []) if isinstance(entry, dict) else entry
            target = cov.setdefault(path, {})
            for idx, hits in enumerate(lines or []):
                if hits is not None:
                    target[idx + 1] = max(target.get(idx + 1, 0), int(hits))
            if isinstance(entry, dict):
                simplecov_arms(entry.get("branches") or {}, arms.setdefault(path, {}))
    return cov, {path: unexercised(found) for path, found in arms.items()}


def unexercised(arms):
    """{(linha, branch): hits} -> {linha: branches com hits 0}."""
    out = {}
    for (line, _), hits in arms.items():
        if not hits:
            out[line] = out.get(line, 0) + 1
    return out


def simplecov_arms(branches, target):
    """"[:if, 0, 5, 4, 7, 7]" -> {"[:then, 1, 6, 6, 6, 10]": hits}; o 3º campo é a linha."""
    for cond, arms in branches.items():
        for key, hits in arms.items():
            found = re.match(r"\[:\w+, -?\d+, (\d+)", key)
            if found:
                arm = (int(found.group(1)), cond + key)
                target[arm] = max(target.get(arm, 0), int(hits or 0))


COVERAGE_FILES = [
    "coverage/lcov.info", "lcov.info",
    "coverage/cobertura-coverage.xml", "coverage/cobertura.xml", "cobertura.xml",
    "coverage.xml",  # coverage.py / pytest-cov --cov-report=xml
    "coverage.out", "cover.out", "coverage/coverage.out",  # go test -coverprofile
    "coverage/.resultset.json",  # SimpleCov (Ruby)
]
WALK_SKIP_DIRS = {".git", "node_modules", "dist", ".next", "vendor", "target", ".venv", "__pycache__"}


def coverage_candidates(cfg=None):
    extra = [p for p in ((cfg or {}).get("coverageFiles") or ()) if isinstance(p, str) and p.strip()]
    return list(dict.fromkeys([*extra, *COVERAGE_FILES]))


def pkg_root_for(rel_artifact):
    rel_s = rel_artifact.as_posix()
    for pat in COVERAGE_FILES:
        if rel_s == pat:
            return Path(".")
        if rel_s.endswith("/" + pat):
            return Path(rel_s[: -len(pat) - 1])
    return rel_artifact.parent if rel_artifact.parent.name == "coverage" and rel_artifact.parent.parent != Path(".") else Path(".")


def find_coverages(repo, cfg=None):
    found = []
    seen = set()
    for name in coverage_candidates(cfg):
        p = repo / name
        if p.is_file():
            res = p.resolve()
            if res not in seen:
                seen.add(res)
                found.append((p, pkg_root_for(Path(name))))
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d not in WALK_SKIP_DIRS]
        cur = Path(dirpath)
        for fname in filenames:
            p = cur / fname
            rel = p.relative_to(repo).as_posix()
            if any(rel == pat or rel.endswith("/" + pat) for pat in COVERAGE_FILES):
                res = p.resolve()
                if res not in seen:
                    seen.add(res)
                    found.append((p, pkg_root_for(p.relative_to(repo))))
    return found


def rebase_cov(cov, br, repo, pkg_root):
    if pkg_root == Path("."):
        return cov, br
    out_cov, out_br = {}, {}
    for path, lines in cov.items():
        new_path = path
        if not (repo / path).is_file() and (repo / pkg_root / path).is_file():
            new_path = (pkg_root / path).as_posix()
        out_cov[new_path] = lines
        if path in br:
            out_br[new_path] = br[path]
    return out_cov, out_br


def parse_coverage(artifact, repo, pkg_root=Path(".")):
    text = artifact.read_text(encoding="utf-8", errors="replace")
    if artifact.suffix == ".xml":
        cov, br = parse_cobertura(text, repo)
    elif artifact.suffix == ".json":
        cov, br = parse_simplecov(text, repo)
    elif artifact.suffix == ".out":
        return parse_gocover(text, repo, pkg_root)
    else:
        cov, br = parse_lcov(text, repo)
    return rebase_cov(cov, br, repo, pkg_root)


def fresh_coverages(repo, started, cfg=None):
    return [(p, pkg) for p, pkg in find_coverages(repo, cfg) if p.stat().st_mtime >= started - 2]


def fresh_coverage(repo, started, cfg=None):
    found = fresh_coverages(repo, started, cfg)
    if not found:
        return None
    return max(found, key=lambda item: item[0].stat().st_mtime)[0]


def merge_coverages(artifacts, repo):
    cov, br = {}, {}
    for artifact, pkg_root in artifacts:
        c, b = parse_coverage(artifact, repo, pkg_root)
        for path, lines in c.items():
            target = cov.setdefault(path, {})
            for num, hits in lines.items():
                target[num] = max(target.get(num, 0), hits)
        for path, bmap in b.items():
            target = br.setdefault(path, {})
            for num, count in bmap.items():
                target[num] = min(target.get(num, count), count) if num in target else count
    return cov, br


def added_lines_map(base, head="HEAD"):
    """{caminho: [linhas adicionadas]} via um único git diff -U0."""
    diff = git("diff", "-U0", "-M", base, head, check=False)
    out, path, current = {}, None, None
    for line in diff.stdout.splitlines():
        if line.startswith("diff --git "):
            path, current = None, None
        elif line.startswith("+++ "):
            target = line[4:].rstrip("\t")
            path = target[2:] if target.startswith("b/") else None
            current = None
        elif line.startswith("--- ") or line.startswith("index "):
            continue
        elif line.startswith("@@ ") and path is not None:
            plus = line.split(" ")[2][1:]
            current = int(plus.split(",")[0])
        elif line.startswith("+") and not line.startswith("+++") and path is not None and current is not None:
            out.setdefault(path, []).append(current)
            current += 1
        elif line.startswith("\\"):
            continue
        elif path is not None and current is not None and not line.startswith("-"):
            current += 1
    return out


def added_lines(base, path):
    return added_lines_map(base).get(path, [])


def coverage_layer(repo, base, started, warnings, cfg=None):
    if not base:
        warnings.append("diff-cover: sem base, skip")
        return "skip"
    artifacts = fresh_coverages(repo, started, cfg)
    if not artifacts:
        warnings.append("diff-cover: sem lcov/cobertura fresco, skip")
        return "skip"
    cov, br = merge_coverages(artifacts, repo)
    added = added_lines_map(base)
    red = False
    for status, paths in name_status(base):
        path = paths[-1]
        if status.startswith("D") or not is_source(path, cfg):
            continue
        if path not in cov:
            warnings.append(f"diff-cover: {path} fora do relatório")
            continue
        for num in added.get(path, ()):
            if num in cov[path] and cov[path][num] == 0:
                warnings.append(f"diff-cover: {path}:{num} hits 0")
                red = True
            elif br.get(path, {}).get(num):
                warnings.append(f"diff-cover: {path}:{num} {br[path][num]} branch(es) não exercitada(s)")
                red = True
    return "red" if red else "green"


def size_layer(base, warnings, cfg=None):
    if not base:
        warnings.append("tamanho: sem base, skip")
        return "skip"
    limit = cfg_limit(cfg or {}, "size", SIZE_LIMIT)
    red = False
    for status, paths in name_status(base):
        path = paths[-1]
        if status.startswith("D") or not is_source(path, cfg):
            continue
        now = lines_of(blob("HEAD", path))
        if status.startswith("A") or blob(base, paths[0]) is None:
            if now > limit:
                warnings.append(f"tamanho: {path} novo com {now} linhas")
                red = True
            continue
        old = lines_of(blob(base, paths[0]))
        if old <= limit < now:
            warnings.append(f"tamanho: {path} cruzou {limit} ({old} → {now})")
            red = True
    return "red" if red else "green"


def lizard_map(path):
    if not shutil.which("lizard"):
        return None
    try:
        run = subprocess.run(["lizard", path], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except subprocess.TimeoutExpired:
        return None
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
            # Homônimos ((anonymous), métodos de classes diferentes): vale o maior, senão o último esconde os outros.
            found[name] = max(found.get(name, 0), ccn)
    return found


def cc_layer(repo, base, warnings, rows=None, cfg=None):
    if not shutil.which("lizard"):
        warnings.append("cc: lizard ausente, skip")
        return "skip"
    if not base:
        warnings.append("cc: sem base, skip")
        return "skip"
    limit = cfg_limit(cfg or {}, "cc", CC_LIMIT)
    red = skipped = False
    for status, paths in (name_status(base) if rows is None else rows):
        path = paths[-1]
        if status.startswith("D") or not is_source(path, cfg):
            continue
        head_map = lizard_map(str(repo / path))
        if head_map is None:
            warnings.append(f"cc: lizard estourou 60s em {path}, skip")
            skipped = True
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
            if old is None and ccn > limit:
                warnings.append(f"cc: {path} {name} nova com {ccn}")
                red = True
            elif old is not None and old <= limit < ccn:
                warnings.append(f"cc: {path} {name} cruzou {limit} ({old} → {ccn})")
                red = True
    return "red" if red else "skip" if skipped else "green"


MOCK_JS = re.compile(r"""\b(?:vi|jest)\.(?:mock|doMock)\(\s*['"]([^'"]+)['"]""")
MOCK_PY = re.compile(r"""\bpatch\(\s*['"]([^'"]+)['"]""")
DB_MODULE = re.compile(r"(^|[/.])(db|prisma|database|drizzle)([/.]index)?$")
# Banco real injetado pelo mock (ex.: libsql :memory: + migrate) não é mock de banco.
# Rails: rails_helper/TestCase sobem o banco de teste real. Go: txdb, pgtestdb, embedded-postgres.
REAL_DB = re.compile(
    r"migrate\(|:memory:|pglite|testcontainers|rails_helper|ActiveSupport::TestCase|ActionDispatch::IntegrationTest"
    r"|txdb|pgtestdb|embedded.?postgres"
    # Python: Django/pytest-django (banco de teste real), pytest-postgresql, migrations do alembic.
    r"|django\.test|django_db|TransactionTestCase|postgresql_proc|command\.upgrade\(",
    re.IGNORECASE,
)
# Mock do banco sem seam para injetar banco real: stub de query/persistência do ActiveRecord, sqlmock do Go.
AR_METHODS = (
    r"find|find_by!?|where|create!?|save!?|update!?|update_all|destroy!?|delete_all|all|first|last"
    r"|pluck|exists\?|transaction|insert_all|upsert_all|find_or_create_by!?"
)
# rspec-mocks receive(:x), mocha stubs/expects(:x), minitest-mock stub/expect(:x).
MOCK_DB_LINE = re.compile(rf"(?:\breceive(?:_messages)?|\.(?:stubs?|expects?))\(\s*:(?:{AR_METHODS})(?![\w?!])|\bsqlmock\.New")
# Ruby: allow(Pedido) / expect(Pagamentos::Cobrar).to receive -> constante própria mockada.
MOCK_RB = re.compile(r"\b(?:allow|expect)(?:_any_instance_of)?\(\s*([A-Z]\w*(?:::[A-Z]\w*)*)\s*\)\.to\s+receive")


def internal_target(target, repo):
    if target.startswith((".", "@/", "#/", "~/")):
        return True
    # Python: o primeiro pacote do alvo existe no repo.
    top = target.split(".")[0]
    return "/" not in target and ((repo / top).is_dir() or (repo / "src" / top).is_dir())


def db_module_rx(cfg=None):
    return compile_extra(DB_MODULE, (cfg or {}).get("dbModules"), "dbModules")


def real_db_rx(cfg=None):
    return compile_extra(REAL_DB, (cfg or {}).get("realDbPatterns"), "realDbPatterns", re.IGNORECASE)


def mock_finding(found, path, text, repo, graph, cfg=None, db_rx=None, real_rx=None):
    """(vermelho?, motivo) para um alvo de mock num teste, ou None se ele é externo ou é injeção de banco real."""
    if not (internal_target(found, repo) or graph.resolve(found, path)):
        return None
    if (db_rx or db_module_rx(cfg)).search(found):
        return None if (real_rx or real_db_rx(cfg)).search(text) else (True, f"mocka o banco ({found}); use banco real em memória")
    if re.split(r"[/.]", found.rstrip("/"))[-1] == os.path.basename(path).split(".")[0]:
        return True, f"mocka o próprio módulo testado ({found})"
    return False, f"mocka código próprio ({found}); mock só na fronteira"


def mocks_layer(repo, base, warnings, cfg=None):
    """Só linhas adicionadas: mock antigo em teste editado não acusa."""
    if not base:
        warnings.append("mocks: sem base, skip")
        return "skip"
    red = False
    graph = ImportGraph(repo)
    db_rx = db_module_rx(cfg)
    real_rx = real_db_rx(cfg)
    added = added_lines_map(base)
    for status, paths in name_status(base):
        path = paths[-1]
        if status.startswith("D") or not is_test(path) or Path(path).suffix not in SOURCE_EXT or is_ignored(path, cfg):
            continue
        text = blob("HEAD", path) or ""
        lines = text.splitlines()
        for num in added.get(path, ()):
            line = lines[num - 1] if 0 < num <= len(lines) else ""
            for is_red, msg in line_mock_findings(line, path, text, repo, graph, cfg, db_rx, real_rx):
                red = red or is_red
                warnings.append(f"mocks: {path}:{num} {msg}")
    return "red" if red else "green"


def line_mock_findings(line, path, text, repo, graph, cfg=None, db_rx=None, real_rx=None):
    """[(vermelho?, motivo)] de uma linha adicionada num teste."""
    if MOCK_DB_LINE.search(line):
        return [(True, "mocka o banco (stub de query/persistência); use o banco de teste real")]
    out = [(False, f"mocka código próprio ({const}); mock só na fronteira") for const in MOCK_RB.findall(line) if graph.resolve_rb(const)]
    for found in (*MOCK_JS.findall(line), *MOCK_PY.findall(line)):
        finding = mock_finding(found, path, text, repo, graph, cfg, db_rx, real_rx)
        if finding:
            out.append(finding)
    return out


DATA_DIRS = {"services", "service", "actions", "api", "routes", "routers", "handlers", "controllers", "repositories"}
UI_DIRS = {"components", "hooks"}
DATA_FILE = re.compile(r"\.(service|action|actions|router|route|controller|handler|repository)\.[a-z]+$")
IMPORT_SPEC = re.compile(r"""(?:\bfrom\s+|\bimport\(\s*|\brequire\(\s*|^\s*import\s+)['"]([^'"]+)['"]""", re.MULTILINE)
PY_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import\b|import\s+([\w.]+))", re.MULTILINE)
UI_INTERACTION = (re.compile(r"\brender\("), re.compile(r"\b(userEvent|fireEvent)\b"))
RESOLVE_EXT = (".ts", ".tsx", ".mts", ".js", ".jsx", ".mjs", ".cjs")
GO_IMPORT_BLOCK = re.compile(r"^import\s*\((.*?)^\)", re.MULTILINE | re.DOTALL)
GO_IMPORT_ONE = re.compile(r'^import\s+(?:[\w.]+\s+)?"([^"]+)"', re.MULTILINE)
GO_DB_LIB = re.compile(
    r'"(database/sql|gorm\.io/[^"]*|github\.com/jackc/pgx[^"]*|github\.com/jmoiron/sqlx|github\.com/uptrace/bun[^"]*|entgo\.io/[^"]*)"'
)
RB_CONST = re.compile(r"\b[A-Z]\w*(?:::[A-Z]\w*)*")
# Rails: o que toca o banco ou é a porta de entrada dele.
RB_DATA_DIRS = {"models", "controllers", "services", "jobs", "graphql", "repositories", "queries", "interactors", "commands"}
GRAPH_DEPTH = 8


def strip_jsonc(text):
    """Tira comentário // e /* */ fora de string. "./src/*" tem /* dentro da string."""
    out, i, quote = [], 0, None
    while i < len(text):
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 1
            elif ch == quote:
                quote = None
        elif ch == '"':
            quote = ch
            out.append(ch)
        elif text.startswith("//", i):
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
            continue
        else:
            out.append(ch)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def load_aliases_file(tsconfig_path, repo):
    """[(prefixo, [pastas])] de compilerOptions.paths do tsconfig.json, relativos à raiz do repo."""
    try:
        config = json.loads(strip_jsonc(tsconfig_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return []
    pkg_dir = tsconfig_path.parent.relative_to(repo)
    options = config.get("compilerOptions") or {}
    base = pkg_dir / (options.get("baseUrl") or ".")
    aliases = []
    for key, targets in (options.get("paths") or {}).items():
        prefix = key[:-1] if key.endswith("*") else key
        dirs = [str(base / (t[:-1] if t.endswith("*") else t)) for t in targets if isinstance(t, str)]
        aliases.append((prefix, dirs))
    # Prefixo mais longo primeiro: "@/lib/" ganha de "@/".
    return sorted(aliases, key=lambda item: -len(item[0]))


def load_aliases(repo):
    return load_aliases_file(repo / "tsconfig.json", repo)


def find_repo_files(repo, filename):
    found = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d not in WALK_SKIP_DIRS]
        if filename in filenames:
            p = Path(dirpath) / filename
            rel_dir = p.parent.relative_to(repo)
            found.append((rel_dir, p))
    return sorted(found, key=lambda item: -len(item[0].parts))


def is_under(path_str, pkg_dir):
    if pkg_dir == Path("."):
        return True
    prefix = pkg_dir.as_posix()
    return path_str == prefix or path_str.startswith(prefix + "/")


class ImportGraph:
    """Imports resolvidos para arquivos do repo, lidos da working tree e guardados em cache."""

    def __init__(self, repo):
        self.repo = repo
        self.tsconfigs = [(pkg_dir, load_aliases_file(p, repo)) for pkg_dir, p in find_repo_files(repo, "tsconfig.json")]
        self.aliases = next((al for pkg_dir, al in self.tsconfigs if pkg_dir == Path(".")), [])
        self.go_mods = [(pkg_dir, go_module(p.parent)) for pkg_dir, p in find_repo_files(repo, "go.mod")]
        self.go_mods = [(d, m) for d, m in self.go_mods if m]
        self.module = next((m for d, m in self.go_mods if d == Path(".")), None)
        self.cache = {}
        self._rb = None

    def aliases_for(self, src):
        for pkg_dir, aliases in self.tsconfigs:
            if is_under(src, pkg_dir):
                return aliases
        return self.aliases

    def go_mod_for(self, path):
        for pkg_dir, mod in self.go_mods:
            if is_under(path, pkg_dir):
                return pkg_dir, mod
        return (Path("."), self.module) if self.module else (Path("."), None)

    def text(self, path):
        try:
            return (self.repo / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def existing(self, base):
        """Caminho relativo do primeiro arquivo que a base resolve, ou None."""
        base = Path(os.path.normpath(base))
        candidates = [base]
        if base.suffix in {".js", ".jsx", ".mjs"}:
            candidates += [base.with_suffix(ext) for ext in (".ts", ".tsx", ".mts")]
        candidates += [Path(f"{base}{ext}") for ext in RESOLVE_EXT]
        candidates += [base / f"index{ext}" for ext in RESOLVE_EXT]
        for item in candidates:
            if (self.repo / item).is_file():
                return item.as_posix()
        return None

    def resolve(self, spec, src):
        if spec.startswith("."):
            return self.existing(Path(src).parent / spec)
        for prefix, dirs in self.aliases_for(src):
            if spec == prefix.rstrip("/") or spec.startswith(prefix):
                for folder in dirs:
                    found = self.existing(Path(folder) / spec[len(prefix):])
                    if found:
                        return found
        return None

    def resolve_py(self, spec, src):
        dots = len(spec) - len(spec.lstrip("."))
        rest = spec[dots:].replace(".", "/")
        if dots:
            parents = Path(src).parents
            roots = [parents[dots - 1]] if dots <= len(parents) else []
        else:
            roots = []
            for parent in Path(src).parents:
                roots.extend((parent, parent / "src"))
        for folder in roots:
            for item in (folder / f"{rest}.py", folder / rest / "__init__.py"):
                if rest and (self.repo / item).is_file():
                    return item.as_posix()
        return None

    def go_files(self, folder):
        """Um import de Go é o pacote inteiro: todos os .go (sem _test) da pasta."""
        return {(Path(folder) / item.name).as_posix() for item in (self.repo / folder).glob("*.go") if not item.name.endswith("_test.go")}

    def imports_go(self, path, text):
        specs = GO_IMPORT_ONE.findall(text)
        for block in GO_IMPORT_BLOCK.findall(text):
            specs += re.findall(r'"([^"]+)"', block)
        pkg_dir, module = self.go_mod_for(path)
        found = set()
        for spec in specs:
            if module and spec.startswith(module + "/"):
                found |= self.go_files(pkg_dir / spec[len(module) + 1 :])
        if path.endswith("_test.go"):
            # O teste compila junto com o pacote da própria pasta.
            found |= self.go_files(Path(path).parent.as_posix())
        return found

    def rb_index(self):
        """(nome completo -> arquivo, último segmento único -> arquivo) pelo layout do Zeitwerk."""
        if self._rb is None:
            full, last = {}, {}
            files = git("ls-files", "--cached", "--others", "--exclude-standard", "--", "*.rb").stdout.splitlines()
            for item in files:
                parts = item[:-3].split("/")
                if parts[0] == "app" and len(parts) >= 3:
                    rest = parts[3:] if parts[2] == "concerns" else parts[2:]
                elif parts[0] == "lib" and len(parts) >= 2:
                    rest = parts[1:]
                else:
                    continue
                name = "::".join("".join(w.capitalize() for w in seg.split("_")) for seg in rest)
                full[name] = item
                last.setdefault(name.split("::")[-1], []).append(item)
            self._rb = (full, {k: v[0] for k, v in last.items() if len(v) == 1})
        return self._rb

    def resolve_rb(self, const):
        full, last = self.rb_index()
        return full.get(const) or last.get(const.split("::")[-1])

    def imports_rb(self, path, text):
        names = set(RB_CONST.findall(text))
        if is_test(path):
            # Request/system spec não cita o controller: pedidos_spec.rb -> PedidosController.
            stem = re.sub(r"_(spec|test)$", "", Path(path).stem)
            camel = "".join(w.capitalize() for w in stem.split("_"))
            names |= {camel, camel + "Controller"}
        return {self.resolve_rb(name) for name in names} - {path}

    def touches_db(self, path):
        return path.endswith(".go") and GO_DB_LIB.search(self.text(path)) is not None

    def imports(self, path):
        if path not in self.cache:
            text = self.text(path)
            if path.endswith(".py"):
                specs = [a or b for a, b in PY_IMPORT.findall(text)]
                found = {self.resolve_py(spec, path) for spec in specs}
            elif path.endswith(".go"):
                found = self.imports_go(path, text)
            elif path.endswith(".rb"):
                found = self.imports_rb(path, text)
            else:
                found = {self.resolve(spec, path) for spec in IMPORT_SPEC.findall(text)}
            self.cache[path] = {item for item in found if item}
        return self.cache[path]

    def reach(self, start, skip=()):
        """Arquivos do repo alcançados a partir de um teste. `skip`: módulos mockados nele,
        cortados em qualquer profundidade (vi.mock troca o módulo no processo inteiro)."""
        seen, frontier = set(), list(self.imports(start))
        for _ in range(GRAPH_DEPTH):
            frontier = [item for item in frontier if item not in seen and item not in skip]
            if not frontier:
                break
            seen.update(frontier)
            frontier = [nxt for item in frontier for nxt in self.imports(item)]
        return seen


def is_db_module(path, cfg=None, db_rx=None):
    return (db_rx or db_module_rx(cfg)).search(str(Path(path).with_suffix("")).replace("\\", "/")) is not None


def integration_kind(path, graph, cfg=None, db_rx=None):
    """'data' (toca o banco, ou service/action/rota...), 'ui' (componente, hook) ou None."""
    cfg = cfg or {}
    db_rx = db_rx or db_module_rx(cfg)
    if not is_source(path, cfg) or is_db_module(path, cfg, db_rx):
        return None
    integ = cfg.get("integration") if isinstance(cfg.get("integration"), dict) else {}
    if matches_any(path, integ.get("ignore")):
        return None
    parts = set(path.split("/")[:-1])
    base = os.path.basename(path)
    ui_dirs = UI_DIRS | {d for d in (integ.get("uiDirs") or ()) if isinstance(d, str)}
    data_dirs = DATA_DIRS | {d for d in (integ.get("dataDirs") or ()) if isinstance(d, str)}
    # UI é JS/TS: components/ do ViewComponent ou hooks/ em Python/Go seguem a classificação normal.
    if parts & ui_dirs and Path(base).suffix in JS_EXT:
        return "ui"
    if graph.touches_db(path) or any(is_db_module(item, cfg, db_rx) for item in graph.imports(path)):
        return "data"
    if path.endswith(".rb") and path.startswith("app/") and parts & RB_DATA_DIRS:
        return "data"
    if parts & data_dirs or DATA_FILE.search(base) or Path(base).stem in {"route", "router"}:
        return "data"
    return None


def is_integration(text, kind, cfg=None, real_rx=None):
    if kind == "data":
        return (real_rx or real_db_rx(cfg)).search(text) is not None
    return all(rx.search(text) for rx in UI_INTERACTION)


def test_files(untracked=False):
    files = git("ls-files").stdout.splitlines()
    if untracked:
        files += git("ls-files", "--others", "--exclude-standard").stdout.splitlines()
    return [p for p in files if is_test(p) and Path(p).suffix in SOURCE_EXT and not p.endswith(".d.ts")]


def covered_by_integration(graph, untracked=False, cfg=None, real_rx=None):
    """{kind: arquivos alcançados por algum teste de integração daquele tipo}."""
    covered = {"data": set(), "ui": set()}
    real_rx = real_rx or real_db_rx(cfg)
    for test in test_files(untracked):
        text = graph.text(test)
        kinds = [kind for kind in covered if is_integration(text, kind, cfg, real_rx)]
        if not kinds:
            continue
        mocked = {graph.resolve(spec, test) for spec in MOCK_JS.findall(text)}
        mocked |= {graph.resolve_rb(const) for const in MOCK_RB.findall(text)}
        reached = graph.reach(test, skip={item for item in mocked if item})
        for kind in kinds:
            covered[kind] |= reached
    return covered


def integration_gaps(repo, rows, untracked=False, cfg=None):
    graph = ImportGraph(repo)
    db_rx = db_module_rx(cfg)
    real_rx = real_db_rx(cfg)
    touched = []
    for status, paths in rows:
        path = paths[-1]
        kind = None if status.startswith("D") else integration_kind(path, graph, cfg, db_rx)
        if kind:
            touched.append((path, kind))
    if not touched:
        return []
    covered = covered_by_integration(graph, untracked, cfg, real_rx)
    return [(path, kind) for path, kind in touched if path not in covered[kind]]


GAP_HINT = {
    "data": "teste que importe o módulo e rode contra banco real em memória",
    "ui": "teste que renderize o componente e interaja (render + userEvent/fireEvent)",
}


def integration_layer(repo, base, warnings, cfg=None):
    if not base:
        warnings.append("integração: sem base, skip")
        return "skip"
    gaps = integration_gaps(repo, name_status(base), cfg=cfg)
    for path, kind in gaps:
        warnings.append(f"integração: {path} sem {GAP_HINT[kind]}")
    return "red" if gaps else "green"


def cmd_integration():
    """Lacunas de integração do diff contra a working tree, antes de escrever os testes."""
    repo = root()
    os.chdir(repo)
    cfg = repo_config(repo)
    base = base_sha(cfg) or git("rev-parse", "HEAD").stdout.strip()
    rows = name_status(base, None)
    rows += [("A", [path]) for path in git("ls-files", "--others", "--exclude-standard").stdout.splitlines()]
    gaps = integration_gaps(repo, rows, untracked=True, cfg=cfg)
    for path, kind in gaps:
        print(f"[doctor-deploy] integração: falta {GAP_HINT[kind]}: {path}", file=sys.stderr)
    if not gaps:
        print("[doctor-deploy] integração: todo módulo tocado tem teste de integração", file=sys.stderr)
    return 1 if gaps else 0


def cmd_cc():
    """CC das funções do diff contra a working tree, antes de escrever os testes."""
    repo = root()
    os.chdir(repo)
    cfg = repo_config(repo)
    base = base_sha(cfg) or git("rev-parse", "HEAD").stdout.strip()
    rows = name_status(base, None)
    untracked = git("ls-files", "--others", "--exclude-standard").stdout.splitlines()
    rows += [("A", [path]) for path in untracked]
    warnings = []
    status = cc_layer(repo, base, warnings, rows, cfg=cfg)
    for line in warnings:
        print(f"[doctor-deploy] {line}", file=sys.stderr)
    print(f"[doctor-deploy] cc: {status}", file=sys.stderr)
    return 1 if status == "red" else 0


AUDIT_TIMEOUT = 120
# Achado real: o resumo "N vulnerabilities" / "N high severity vulnerabilities" (npm & cia., govulncheck,
# cargo audit) ou o "Vulnerabilities found!" do bundle-audit.
AUDIT_FINDINGS = re.compile(r"\d+\s+(?:\w+\s+){0,2}vulnerabilit|Vulnerabilities found", re.IGNORECASE)


def audit_status(run):
    """'green', 'red' ou 'error'. Saída não-zero sem achado (rede, comando inexistente, sem lock) não é vulnerabilidade."""
    if run.returncode == 0:
        return "green"
    return "red" if AUDIT_FINDINGS.search(run.stdout + run.stderr) else "error"


def audit_layer(base, warnings, repo=None, cfg=None):
    if not base:
        warnings.append("audit: sem base, skip")
        return "skip"
    repo = repo or root()
    changed = changed_paths(name_status(base))
    locks = [name for name in changed if os.path.basename(name) in AUDIT and not is_ignored(name, cfg)]
    if not locks:
        return "skip"
    ran = False
    red = False
    seen = set()
    for lock in locks:
        cmd = AUDIT.get(os.path.basename(lock))
        lock_dir = Path(lock).parent
        key = (tuple(cmd or ()), lock_dir.as_posix())
        if not cmd or key in seen:
            continue
        seen.add(key)
        exe = shutil.which(cmd[0])
        if not exe:
            warnings.append(f"audit: {cmd[0]} ausente, skip")
            continue
        # No Windows `npm` é um .cmd: `which` resolve, mas CreateProcess com o nome
        # nu falha (WinError 2). Executa o caminho resolvido.
        try:
            run = subprocess.run([exe, *cmd[1:]], cwd=str(repo / lock_dir), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=AUDIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            warnings.append(f"audit: {cmd[0]} timeout {AUDIT_TIMEOUT}s, skip")
            continue
        status = audit_status(run)
        where = f" ({lock_dir.as_posix()})" if lock_dir != Path(".") else ""
        if status == "error":
            tail = (run.stderr or run.stdout).strip().splitlines()[-1:] or [""]
            warnings.append(f"audit: {cmd[0]}{where} saiu {run.returncode} sem achado ({tail[0][:120]}), skip")
            continue
        ran = True
        if status == "red":
            warnings.append(f"audit: {cmd[0]}{where} achou vulnerabilidade (npm/pnpm/yarn: high ou critical)")
            red = True
    if red:
        return "red"
    return "green" if ran else "skip"


STRYKER_REPORT = Path("reports/mutation/mutation.json")
MUTATION_TIMEOUT = 180


def stryker_exe():
    # No Windows o bin é um shim .cmd; o nome nu falha no CreateProcess.
    bin_dir = Path("node_modules/.bin")
    for name in ("stryker.cmd", "stryker") if os.name == "nt" else ("stryker",):
        if (bin_dir / name).exists():
            return str((bin_dir / name).resolve())
    return None


def stryker_survivors(repo, added):
    """[(arquivo, linha, operador)] de mutantes vivos (Survived/NoCoverage) em linha adicionada."""
    report = load_json(STRYKER_REPORT) or {}
    out = []
    for name, data in (report.get("files") or {}).items():
        path = norm(name, repo)
        for mutant in data.get("mutants") or []:
            line = ((mutant.get("location") or {}).get("start") or {}).get("line")
            if mutant.get("status") in {"Survived", "NoCoverage"} and line in added.get(path, ()):
                out.append((path, line, mutant.get("mutatorName") or "?"))
    return out


JS_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
# mutant: "evil:Pagamentos::Cobrar#call:/abs/app/services/pagamentos/cobrar.rb:7:3f2a1" = mutante vivo.
# O subject tem "::" (Pagamentos::Cobrar#call): ele vai até o ":" antes do caminho absoluto.
MUTANT_EVIL = re.compile(r"^evil:(\S+?):((?:/|[A-Za-z]:[\\/])[^\n]+?\.rb):(\d+):[0-9a-f]+\s*$", re.MULTILINE)


def diff_added(base, exts, cfg=None):
    """{fonte do diff com essas extensões: linhas adicionadas}."""
    all_added = added_lines_map(base)
    added = {p: set(all_added.get(p, ())) for p in changed_paths(name_status(base)) if is_source(p, cfg) and Path(p).suffix in exts}
    return {p: lines for p, lines in added.items() if lines}


def run_mutator(cmd, name, warnings):
    """CompletedProcess, ou None (com aviso) se estourou o tempo."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=MUTATION_TIMEOUT)
    except subprocess.TimeoutExpired:
        warnings.append(f"mutação: {name} timeout {MUTATION_TIMEOUT}s, skip")
        return None


def report_survivors(survivors, warnings):
    for where, what in survivors:
        warnings.append(f"mutação: {where} {what} sobreviveu (nenhum teste quebrou)")
    return "red" if survivors else "green"


def mutate_stryker(repo, base, warnings, cfg=None):
    exe = stryker_exe()
    if exe is None:
        return None
    added = diff_added(base, JS_EXT, cfg)
    if not added:
        return "green"
    STRYKER_REPORT.unlink(missing_ok=True)
    run = run_mutator([exe, "run", "--mutate", ",".join(sorted(added)), "--reporters", "json"], "Stryker", warnings)
    if run is None:
        return "skip"
    if not STRYKER_REPORT.exists():
        warnings.append(f"mutação: Stryker saiu {run.returncode} sem relatório, skip")
        return "skip"
    return report_survivors([(f"{p}:{line}", op) for p, line, op in stryker_survivors(repo, added)], warnings)


def mutate_gremlins(repo, base, warnings, cfg=None):
    exe = shutil.which("gremlins")
    if not exe or not Path("go.mod").exists():
        return None
    added = diff_added(base, {".go"}, cfg)
    if not added:
        return "green"
    mutants = gremlins_report(exe, repo, base, warnings)
    if mutants is None:
        return "skip"
    if os.name == "nt" and mutants and all(m.get("status") == "NOT COVERED" for _, m in mutants):
        # gremlins <= 0.6.0 no Windows: chave da cobertura com "\" e posição com "/"; tudo vira NOT COVERED.
        warnings.append("mutação: gremlins no Windows marcou tudo NOT COVERED (bug do gremlins com caminho \\), skip; o diff-cover cobre as linhas sem teste")
        return "skip"
    return report_survivors(gremlins_live(mutants, added), warnings)


def gremlins_report(exe, repo, base, warnings):
    """[(arquivo, mutante)] do relatório JSON, ou None (com aviso) se não houve relatório."""
    out = git_dir() / "doctor-deploy" / "gremlins.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    # Folga de tempo: com o coeficiente padrão, máquina carregada marca mutante como TIMED OUT (conta como morto).
    run = run_mutator([exe, "unleash", "--diff", base, "--output", str(out), "--timeout-coefficient", "30", "-s"], "gremlins", warnings)
    if run is None:
        return None
    if not out.exists():
        warnings.append(f"mutação: gremlins saiu {run.returncode} sem relatório, skip")
        return None
    return [(norm(f.get("file_name") or "", repo), m) for f in (load_json(out) or {}).get("files") or [] for m in f.get("mutations") or []]


def gremlins_live(mutants, added):
    return [(f"{p}:{m.get('line')}", m.get("type") or "?") for p, m in mutants
            if m.get("status") in {"LIVED", "NOT COVERED"} and m.get("line") in added.get(p, ())]


def mutant_configured():
    lock = Path("Gemfile.lock")
    return lock.exists() and "mutant" in lock.read_text(encoding="utf-8", errors="replace") and any(Path(p).exists() for p in (".mutant.yml", "config/mutant.yml"))


def mutant_evil(out, repo):
    """[(onde, o quê)] dos mutantes vivos; "o quê" é a linha mutada (o "+" do diff que o mutant imprime)."""
    heads = list(MUTANT_EVIL.finditer(out))
    found = []
    for i, head in enumerate(heads):
        if is_test(norm(head.group(2), repo)):
            continue  # matcher largo (Pagamentos*) pega a classe de teste; mutar teste não prova nada
        block = out[head.end() : heads[i + 1].start() if i + 1 < len(heads) else len(out)]
        plus = re.search(r"^\+(?!\+\+)\s*(.+)$", block, re.MULTILINE)
        what = f"mutante `{plus.group(1).strip()[:100]}`" if plus else "mutante"
        found.append((f"{norm(head.group(2), repo)}:{head.group(3)} {head.group(1)}", what))
    return found


def mutate_mutant(repo, base, warnings, cfg=None):
    if not mutant_configured():
        return None
    if os.name == "nt":
        warnings.append("mutação: mutant usa fork(), que o Windows não tem; skip")
        return "skip"
    bundle = shutil.which("bundle")
    if not bundle:
        warnings.append("mutação: mutant configurado, mas sem bundle no PATH; skip")
        return "skip"
    # --since: o próprio mutant só seleciona os métodos tocados desde a base. Granularidade: método.
    run = run_mutator([bundle, "exec", "mutant", "run", "--since", base], "mutant", warnings)
    if run is None:
        return "skip"
    evil = mutant_evil(run.stdout, repo)
    if run.returncode != 0 and not evil:
        tail = (run.stderr or run.stdout).strip().splitlines()[-1:] or [""]
        warnings.append(f"mutação: mutant saiu {run.returncode} sem mutante vivo ({tail[0][:120]}), skip")
        return "skip"
    return report_survivors(evil, warnings)


def mutation_layer(repo, base, warnings, cfg=None):
    consent = load_json(consent_path()) or {}
    if consent.get("mutation") == "no":
        warnings.append("mutação: recusada, skip")
        return "skip"
    if not base:
        warnings.append("mutação: sem base, skip")
        return "skip"
    # O mutador roda a suíte de novo (o mutant com o SimpleCov ligado em cada mutante): a cobertura
    # do quality é restaurada, senão o próximo prepare herda o lixo pelo merge do SimpleCov.
    saved = {p: p.read_bytes() for p, _ in find_coverages(repo, cfg)}
    try:
        for mutate in (mutate_stryker, mutate_gremlins, mutate_mutant):
            result = mutate(repo, base, warnings, cfg)
            if result is not None:
                return result
    finally:
        for path, data in saved.items():
            path.write_bytes(data)
    warnings.append("mutação: sem mutador (Stryker em node_modules, gremlins com go.mod, mutant com .mutant.yml), skip")
    return "skip"


def cmd_layers(started, base_ref=None):
    repo = root()
    os.chdir(repo)
    cfg = repo_config(repo)
    base = base_sha(cfg, base_ref)
    disabled = {item for item in (cfg.get("disableLayers") or ()) if isinstance(item, str)}
    warnings = []
    steps = {
        "diff_cover": lambda: coverage_layer(repo, base, started, warnings, cfg),
        "size": lambda: size_layer(base, warnings, cfg),
        "cc": lambda: cc_layer(repo, base, warnings, cfg=cfg),
        "mocks": lambda: mocks_layer(repo, base, warnings, cfg),
        "integration": lambda: integration_layer(repo, base, warnings, cfg),
        "audit": lambda: audit_layer(base, warnings, repo, cfg),
        "mutation": lambda: mutation_layer(repo, base, warnings, cfg),
    }
    layers = {}
    for name, step in steps.items():
        if name in disabled:
            warnings.append(f"{name}: desativada em {CONFIG_FILE}")
            layers[name] = "skip"
            continue
        # Erro inesperado numa camada é vermelho com nome e motivo, sem derrubar as outras nem o JSON.
        try:
            layers[name] = step()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{name}: erro inesperado ({type(exc).__name__}: {exc})")
            layers[name] = "red"
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
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"[doctor-deploy] recibo {path}", file=sys.stderr)


def cmd_check(sha):
    repo = root()
    cfg = repo_config(repo)
    if cfg.get("pluginRoot"):
        resolve_plugin_root(cfg["pluginRoot"], repo)
    receipt = load_json(receipt_path())
    if not receipt or receipt.get("version") != VERSION or receipt.get("result") != "green":
        print("[doctor-deploy] recibo ausente ou inválido", file=sys.stderr)
        return 1
    if "quality" in (receipt.get("skipped") or []) and not quality_skip_allowed():
        print("[doctor-deploy] recibo sem quality e repo sem doctor-deploy.allowNoQuality", file=sys.stderr)
        return 1
    # Conteúdo, não ancestralidade: squash e rebase do testado passam; código alheio vira delta.
    tested = receipt.get("tested_sha") or ""
    diff = git("diff", "--name-status", "-M", tested, sha, check=False)
    if not tested or diff.returncode != 0:
        print("[doctor-deploy] SHA testado ou a subir não existe aqui", file=sys.stderr)
        return 1
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


def cmd_preflight():
    # Valida .doctor-deploy.toml (obrigatório) e imprime avisos das camadas opcionais.
    repo = root()
    cfg = repo_config(repo)
    if cfg.get("pluginRoot"):
        p_root = resolve_plugin_root(cfg["pluginRoot"], repo)
        print(f"[doctor-deploy] ok: plugin.root -> {p_root.as_posix()}", file=sys.stderr)
    else:
        cfg_file = find_config_path(repo)
        print(f"[doctor-deploy] ok: {cfg_file.name} (project={cfg['project']})", file=sys.stderr)
    def say(ok, msg_ok, msg_warn):
        print(f"[doctor-deploy] ok: {msg_ok}" if ok else f"[doctor-deploy] aviso: {msg_warn}", file=sys.stderr)
    base = base_sha(cfg)
    say(base, f"base {base[:12]}", "sem base diferente do HEAD (origin/HEAD, master, main): as camadas do diff vão pular")
    cov_hits = [p.relative_to(repo).as_posix() for p, _ in find_coverages(repo, cfg)]
    cov = cov_hits[0] if cov_hits else None
    say(cov, f"cobertura em {', '.join(cov_hits)} (o portão exige que o quality a regenere)",
        "nenhum artefato de cobertura (lcov, Cobertura XML, coverprofile do Go, .resultset.json do SimpleCov): se o quality não gerar um, o diff-cover vai pular")
    for tool in needs_consent():
        print(f"[doctor-deploy] aviso: {tool} sem consentimento (gate.sh consent {tool} yes|no)", file=sys.stderr)


def cmd_config_plugin_root():
    repo = root()
    cfg = repo_config(repo)
    if cfg.get("pluginRoot"):
        print(resolve_plugin_root(cfg["pluginRoot"], repo).as_posix())


def cmd_config_quality():
    cfg = repo_config()
    q = cfg.get("quality")
    if isinstance(q, str) and q.strip():
        print(q.strip())


def cmd_config_branches():
    cfg = repo_config()
    branches = cfg.get("protectedBranches")
    if isinstance(branches, list):
        items = [b.strip() for b in branches if isinstance(b, str) and b.strip()]
        if items:
            print(",".join(items))
            return
    base = cfg.get("baseBranch")
    if isinstance(base, str) and base.strip():
        b = base.strip()
        print(b.split("/", 1)[1] if b.startswith("origin/") else b)


def needs_consent():
    consent = load_json(consent_path()) or {}
    tools = []
    if not shutil.which("lizard") and "lizard" not in consent:
        tools.append("lizard")
    has_mutator = stryker_exe() or (shutil.which("gremlins") and Path("go.mod").exists()) or mutant_configured()
    if has_mutator and "mutation" not in consent:
        tools.append("mutation")
    return tools


def cmd_needs():
    for tool in needs_consent():
        print(f"NEED_CONSENT {tool}")


def cmd_consent(tool, answer):
    if tool not in {"lizard", "mutation"} or answer not in {"yes", "no"}:
        raise SystemExit("consent inválido")
    path = consent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = load_json(path) or {}
    data[tool] = answer
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"[doctor-deploy] consent {tool}={answer}", file=sys.stderr)


def extract_ci_commands(path):
    """Extrai comandos shell básicos de workflows/configs de CI sem depender de parser YAML."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    cmds, i = [], 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        m_run = re.match(r"^(?:-\s*)?run:\s*(.*)$", stripped)
        if m_run:
            val = m_run.group(1).strip()
            if val in {"|", "|-", "|+", ">", ">-", ">+"}:
                i += 1
                while i < len(lines):
                    nxt = lines[i]
                    if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                        break
                    if nxt.strip() and not nxt.strip().startswith("#"):
                        cmds.append(nxt.strip())
                    i += 1
                continue
            elif val:
                cmds.append(val.strip("'\""))
        elif re.match(r"^(?:before_script|script|after_script):\s*$", stripped):
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                    break
                m_item = re.match(r"^-\s+(.+)$", nxt.strip())
                if m_item:
                    cmds.append(m_item.group(1).strip("'\""))
                i += 1
            continue
        elif path.name == "Jenkinsfile":
            for m_sh in re.finditer(r"""\bsh\s+['"]([^'"]+)['"]""", stripped):
                cmds.append(m_sh.group(1))
        i += 1
    return list(dict.fromkeys(cmds))


def detect_ci(repo):
    candidates = []
    wf_dir = repo / ".github" / "workflows"
    if wf_dir.is_dir():
        candidates.extend(sorted(wf_dir.glob("*.yml")))
        candidates.extend(sorted(wf_dir.glob("*.yaml")))
    for rel in (".gitlab-ci.yml", ".circleci/config.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml", "Jenkinsfile"):
        p = repo / rel
        if p.is_file():
            candidates.append(p)
    out = []
    for item in candidates:
        out.append({
            "path": item.relative_to(repo).as_posix(),
            "commands": extract_ci_commands(item),
        })
    return out


def detect_stacks(repo):
    stacks = {}
    pkg_path = repo / "package.json"
    if pkg_path.is_file():
        pkg = load_json(pkg_path) or {}
        pm = "npm"
        for lock, candidate in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lock", "bun"), ("bun.lockb", "bun")):
            if (repo / lock).is_file():
                pm = candidate
                break
        scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
        deps = {}
        for k in ("dependencies", "devDependencies"):
            if isinstance(pkg.get(k), dict):
                deps.update(pkg[k])
        tools = [t for t in ("biome", "eslint", "prettier", "typescript", "vitest", "jest") if t in deps or any(t in str(v) for v in scripts.values())]
        if (repo / "biome.json").is_file() and "biome" not in tools:
            tools.append("biome")
        if (repo / "tsconfig.json").is_file() and "typescript" not in tools:
            tools.append("typescript")
        stacks["js"] = {"packageManager": pm, "scripts": list(scripts.keys()), "tools": tools}
    if any((repo / f).is_file() for f in ("pyproject.toml", "requirements.txt", "setup.py", "uv.lock", "poetry.lock", "Pipfile.lock")):
        locks = [f for f in ("uv.lock", "poetry.lock", "Pipfile.lock") if (repo / f).is_file()]
        stacks["python"] = {"locks": locks}
    if (repo / "go.mod").is_file():
        stacks["go"] = {"module": go_module(repo) or "", "hasSum": (repo / "go.sum").is_file()}
    if (repo / "Gemfile").is_file():
        stacks["ruby"] = {"hasLock": (repo / "Gemfile.lock").is_file(), "hasMutant": mutant_configured()}
    if (repo / "Cargo.toml").is_file():
        stacks["rust"] = {"hasLock": (repo / "Cargo.lock").is_file()}
    return stacks


def suggest_quality(repo, stacks):
    if "js" in stacks:
        js = stacks["js"]
        pm = js["packageManager"]
        run = f"{pm} --bun run" if pm == "bun" else f"{pm} run"
        scripts = set(js["scripts"])
        parts = []
        for group in (("check", "lint", "format:check"), ("typecheck", "type-check"), ("build",), ("test:coverage", "test:cov", "coverage", "test")):
            hit = next((name for name in group if name in scripts), None)
            if hit:
                parts.append(f"{run} {hit}")
        if parts:
            return " && ".join(parts)
    if "go" in stacks:
        return 'test -z "$(gofmt -l .)" && go vet ./... && go build ./... && go test -coverprofile=coverage.out ./...'
    if "python" in stacks:
        return "ruff check . && ruff format --check . && pytest --cov --cov-branch --cov-report=xml"
    if "ruby" in stacks:
        runner = "bundle exec rspec" if (repo / "spec").is_dir() else "bin/rails test"
        return f"bundle exec rubocop && {runner}"
    if "rust" in stacks:
        return "cargo fmt --check && cargo clippy -- -D warnings && cargo test"
    return ""


def _toml_fmt(val):
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, list):
        return "[" + ", ".join(_toml_fmt(item) for item in val) + "]"
    return json.dumps(str(val), ensure_ascii=False)


def _derive_project_name(repo):
    remote = git("remote", "get-url", "origin", check=False)
    if remote.returncode == 0 and remote.stdout.strip():
        tail = remote.stdout.strip().rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        if tail.endswith(".git"):
            tail = tail[:-4]
        if tail:
            return tail
    return repo.name


def _read_legacy_json(repo):
    legacy_path = repo / LEGACY_CONFIG_FILE
    if not legacy_path.is_file():
        return {}
    try:
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"[doctor-deploy] {LEGACY_CONFIG_FILE} ilegível para migração: {exc}")
    if not isinstance(legacy, dict):
        raise SystemExit(f"[doctor-deploy] {LEGACY_CONFIG_FILE} ilegível para migração: esperado objeto JSON")
    return legacy


def _toml_pairs(source, mapping):
    out = []
    for camel, snake in mapping:
        val = source.get(camel, source.get(snake))
        if val is not None:
            out.append(f"{snake} = {_toml_fmt(val)}")
    return out


def _optional_toml_section(title, source, mapping):
    if not isinstance(source, dict) or not source:
        return []
    pairs = _toml_pairs(source, mapping)
    return ["", f"[{title}]", *pairs] if pairs else []


def _render_init_toml(proj_name, legacy, plugin_root=""):
    lines = [
        "# Marcador e configuração obrigatória do doctor-deploy (inspirado no .ai-memory.toml).",
        "version = 1",
        f"project = {_toml_fmt(proj_name)}",
    ]
    ws = legacy.get("workspace")
    if isinstance(ws, str) and ws.strip():
        lines.append(f"workspace = {_toml_fmt(ws.strip())}")
    raw_root = plugin_root or legacy.get("pluginRoot") or ""
    p_root = raw_root.strip() if isinstance(raw_root, str) else ""
    lines.extend(["", "[plugin]", f"root = {_toml_fmt(p_root)}"] if p_root else ["", "# [plugin]", '# root = "~/.gemini/config/plugins/doctor-deploy"'])

    gate_pairs = _toml_pairs(legacy, (
        ("baseBranch", "base_branch"), ("protectedBranches", "protected_branches"),
        ("quality", "quality"), ("coverageFiles", "coverage_files"),
        ("ignorePaths", "ignore_paths"), ("disableLayers", "disable_layers"),
    ))
    default_gate = [
        '# base_branch = "main"', '# protected_branches = ["main", "master"]',
        '# quality = "bun run quality"', '# coverage_files = ["coverage/lcov.info"]',
        '# ignore_paths = ["src/legacy/**"]', '# disable_layers = ["mutation"]',
    ]
    lines.extend(["", "[gate]", *(gate_pairs or default_gate)])
    lines.extend(_optional_toml_section("limits", legacy.get("limits"), (("size", "size"), ("cc", "cc"))))
    lines.extend(_optional_toml_section("patterns", legacy, (("dbModules", "db_modules"), ("realDbPatterns", "real_db_patterns"))))
    lines.extend(_optional_toml_section("integration", legacy.get("integration"), (("dataDirs", "data_dirs"), ("uiDirs", "ui_dirs"), ("ignore", "ignore"))))
    return "\n".join(lines) + "\n"


def _parse_init_args(argv):
    force, project, plugin_root = False, "", ""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--force":
            force = True
            i += 1
        elif arg in ("--project", "--plugin-root"):
            if i + 1 >= len(argv) or not argv[i + 1].strip():
                raise SystemExit(f"[doctor-deploy] {arg} exige um valor")
            if arg == "--project":
                project = argv[i + 1].strip()
            else:
                plugin_root = argv[i + 1].strip()
            i += 2
        else:
            raise SystemExit(f"[doctor-deploy] opção desconhecida em init: {arg}")
    return force, project, plugin_root


def cmd_init(force=False, project="", plugin_root=""):
    repo = root()
    os.chdir(repo)
    try:
        existing = find_config_path(repo)
    except SystemExit:
        if not force:
            raise
        existing = None
    if existing and not force:
        raise SystemExit(f"[doctor-deploy] já existe {existing.name}; use --force para sobrescrever")
    legacy = _read_legacy_json(repo)
    if project:
        proj_name = _require_str(project, "--project", "init")
    elif "project" in legacy:
        proj_name = _require_str(legacy["project"], "project", LEGACY_CONFIG_FILE)
    else:
        proj_name = _derive_project_name(repo)
    text = _render_init_toml(proj_name, legacy, plugin_root=plugin_root)
    _normalize_repo_config(loads_toml(text), CONFIG_FILE)
    (repo / CONFIG_FILE).write_text(text, encoding="utf-8")
    msg = f"[doctor-deploy] criado {CONFIG_FILE} (project={proj_name})"
    if (repo / LEGACY_CONFIG_FILE).is_file():
        msg += f" — migrado de {LEGACY_CONFIG_FILE}; pode remover {LEGACY_CONFIG_FILE}"
    print(msg, file=sys.stderr)


def discover_report(repo, current_quality="", quality_error=""):
    branch_run = git("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    branch = branch_run.stdout.strip() if branch_run.returncode == 0 else "HEAD"
    cfg_error = ""
    cfg_path = None
    try:
        cfg_path = find_config_path(repo)
        cfg = repo_config(repo, required=False)
    except SystemExit as exc:
        cfg = {}
        cfg_error = str(exc)
    base = base_sha(cfg)
    hooks_path = git("config", "--get", "core.hooksPath", check=False).stdout.strip()
    hook_dir = git("rev-parse", "--git-path", "hooks", check=False).stdout.strip()
    hook_file = Path(hook_dir) / "pre-push" if hook_dir else Path(".git/hooks/pre-push")
    if not hook_file.is_absolute():
        hook_file = repo / hook_file
    delegates = (repo / "scripts/pre-push.sh").is_file() and "doctor-deploy/pre-push.sh" in (repo / "scripts/pre-push.sh").read_text(encoding="utf-8", errors="replace")
    managers = []
    if (repo / ".husky").is_dir():
        managers.append("husky")
    if (repo / "lefthook.yml").is_file() or (repo / ".lefthook.yml").is_file():
        managers.append("lefthook")
    if (repo / ".pre-commit-config.yaml").is_file():
        managers.append("pre-commit")
    stacks = detect_stacks(repo)
    ci = detect_ci(repo)
    suggested = suggest_quality(repo, stacks)
    cov_files = [p.relative_to(repo).as_posix() for p, _ in find_coverages(repo, cfg)]
    consent_pending = needs_consent()
    legacy_exists = (repo / LEGACY_CONFIG_FILE).is_file()
    gaps = []
    if cfg_error:
        gaps.append(cfg_error)
    elif cfg_path is None:
        if legacy_exists:
            gaps.append(f"{LEGACY_CONFIG_FILE} precisa migrar para {CONFIG_FILE} (rode gate.sh init)")
        else:
            gaps.append(f"sem {CONFIG_FILE} (rode gate.sh init)")
    if quality_error:
        gaps.append(quality_error)
    elif not current_quality and not quality_skip_allowed():
        gaps.append("sem entrypoint de quality")
    if not hook_file.exists():
        gaps.append("sem hook pre-push")
    if not cov_files:
        gaps.append("sem artefato de cobertura")
    if not base:
        gaps.append("sem base diferente do HEAD")
    for tool in consent_pending:
        gaps.append(f"{tool} sem consentimento")
    try:
        hook_rel = hook_file.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        hook_rel = hook_file.as_posix()
    return {
        "git": {
            "root": repo.as_posix(),
            "branch": branch,
            "baseSha": base,
            "coreHooksPath": hooks_path,
            "hookPath": hook_rel,
            "hasPrePushHook": hook_file.exists(),
            "projectHookDelegates": delegates,
            "hooksManagers": managers,
        },
        "config": {
            "exists": cfg_path is not None,
            "path": cfg_path.name if cfg_path else None,
            "required": True,
            "legacyJsonExists": legacy_exists,
            "data": cfg,
            **({"error": cfg_error} if cfg_error else {}),
        },
        "ci": ci,
        "stacks": stacks,
        "quality": {
            "configured": bool(current_quality),
            "entrypoint": current_quality,
            "suggested": suggested,
            **({"error": quality_error} if quality_error else {}),
        },
        "coverage": {
            "artifacts": cov_files,
            "configured": bool(cov_files),
        },
        "needsConsent": consent_pending,
        "gaps": gaps,
    }


def cmd_discover(as_json=False, current_quality="", quality_error=""):
    repo = root()
    os.chdir(repo)
    report = discover_report(repo, current_quality=current_quality, quality_error=quality_error)
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    print(f"[doctor-deploy] repo: {report['git']['root']} (branch {report['git']['branch']})")
    print(f"[doctor-deploy] stacks: {', '.join(report['stacks'].keys()) or 'nenhuma detectada'}")
    print(f"[doctor-deploy] ci: {', '.join(c['path'] for c in report['ci']) or 'nenhum'}")
    q = report["quality"]
    print(f"[doctor-deploy] quality: {q['entrypoint'] or ('ausente — sugestão: ' + q['suggested'] if q['suggested'] else 'ausente')}")
    print(f"[doctor-deploy] cobertura: {', '.join(report['coverage']['artifacts']) or 'nenhum artefato'}")
    if report["gaps"]:
        print("[doctor-deploy] gaps: " + "; ".join(report["gaps"]))
    else:
        print("[doctor-deploy] gaps: nenhum")


def main():
    argv = sys.argv[1:]
    if not argv:
        raise SystemExit("uso: layers | cc | integration | write-receipt | check | print-skipped | needs-consent | consent")
    cmd = argv[0]
    if cmd == "layers":
        started = 0
        base_ref = None
        if "--started" in argv:
            idx_s = argv.index("--started")
            if idx_s + 1 < len(argv):
                started = int(argv[idx_s + 1])
        if "--base" in argv:
            idx_b = argv.index("--base")
            if idx_b + 1 < len(argv):
                base_ref = argv[idx_b + 1]
        raise SystemExit(cmd_layers(started, base_ref=base_ref))
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
    if cmd == "cc":
        raise SystemExit(cmd_cc())
    if cmd == "integration":
        raise SystemExit(cmd_integration())
    if cmd == "preflight":
        cmd_preflight()
        return
    if cmd == "needs-consent":
        cmd_needs()
        return
    if cmd == "consent":
        cmd_consent(argv[1], argv[2])
        return
    if cmd == "config-plugin-root":
        cmd_config_plugin_root()
        return
    if cmd == "config-quality":
        cmd_config_quality()
        return
    if cmd == "config-branches":
        cmd_config_branches()
        return
    if cmd == "init":
        force, proj, proot = _parse_init_args(argv[1:])
        cmd_init(force=force, project=proj, plugin_root=proot)
        return
    if cmd == "discover":
        as_json = "--json" in argv
        idx_q = argv.index("--quality") if "--quality" in argv else -1
        q = argv[idx_q + 1] if 0 <= idx_q and idx_q + 1 < len(argv) else ""
        idx_e = argv.index("--quality-error") if "--quality-error" in argv else -1
        qerr = argv[idx_e + 1] if 0 <= idx_e and idx_e + 1 < len(argv) else ""
        cmd_discover(as_json=as_json, current_quality=q, quality_error=qerr)
        return
    raise SystemExit(f"comando desconhecido: {cmd}")


if __name__ == "__main__":
    main()
