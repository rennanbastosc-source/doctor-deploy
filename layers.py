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

VERSION = 3
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
    "go.sum": ["govulncheck", "./..."],
    "Gemfile.lock": ["bundle-audit", "check", "--update"],
    "Cargo.lock": ["cargo-audit", "audit"],
}
def git(*args, check=True):
    # UTF-8 explícito: sem PYTHONUTF8 o Windows decodifica em cp1252 e quebra em acento.
    run = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
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


def go_module(repo):
    try:
        found = re.search(r"^module\s+(\S+)", (repo / "go.mod").read_text(encoding="utf-8"), re.MULTILINE)
    except OSError:
        return None
    return found.group(1) if found else None


def parse_gocover(text, repo):
    """coverprofile do `go test`: "mod/pkg/f.go:12.13,14.3 1 0". Sem branches; linha = maior hits dos blocos."""
    module = go_module(repo) or ""
    cov = {}
    for line in text.splitlines():
        found = re.match(r"(.+\.go):(\d+)\.\d+,(\d+)\.\d+ \d+ (\d+)$", line.strip())
        if not found:
            continue
        name, start, end, hits = found.group(1), int(found.group(2)), int(found.group(3)), int(found.group(4))
        if module and name.startswith(module + "/"):
            name = name[len(module) + 1 :]
        lines = cov.setdefault(norm(name, repo), {})
        for num in range(start, end + 1):
            lines[num] = max(lines.get(num, 0), hits)
    return cov, {}


def parse_simplecov(text, repo):
    """coverage/.resultset.json do SimpleCov: {suite: {coverage: {arquivo: {lines, branches}}}}."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}, {}
    cov, br = {}, {}
    for suite in data.values():
        for name, entry in ((suite or {}).get("coverage") or {}).items():
            path = norm(name, repo)
            lines = entry.get("lines", []) if isinstance(entry, dict) else entry
            target = cov.setdefault(path, {})
            for idx, hits in enumerate(lines or []):
                if hits is not None:
                    target[idx + 1] = max(target.get(idx + 1, 0), int(hits))
            if isinstance(entry, dict):
                simplecov_branches(entry.get("branches") or {}, br.setdefault(path, {}))
    return cov, br


def simplecov_branches(branches, target):
    """"[:if, 0, 5, 4, 7, 7]" -> {"[:then, 1, 6, 6, 6, 10]": hits}; o 3º campo é a linha."""
    for arms in branches.values():
        for key, hits in arms.items():
            found = re.match(r"\[:\w+, -?\d+, (\d+)", key)
            if found and not hits:
                num = int(found.group(1))
                target[num] = target.get(num, 0) + 1


COVERAGE_FILES = [
    "coverage/lcov.info", "lcov.info",
    "coverage/cobertura-coverage.xml", "coverage/cobertura.xml", "cobertura.xml",
    "coverage.out", "cover.out", "coverage/coverage.out",  # go test -coverprofile
    "coverage/.resultset.json",  # SimpleCov (Ruby)
]


def parse_coverage(artifact, repo):
    text = artifact.read_text(errors="replace")
    if artifact.suffix == ".xml":
        return parse_cobertura(text, repo)
    if artifact.suffix == ".json":
        return parse_simplecov(text, repo)
    if artifact.suffix == ".out":
        return parse_gocover(text, repo)
    return parse_lcov(text, repo)


def fresh_coverage(repo, started):
    found = []
    for name in COVERAGE_FILES:
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
    cov, br = parse_coverage(artifact, repo)
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
            elif br.get(path, {}).get(num):
                warnings.append(f"diff-cover: {path}:{num} {br[path][num]} branch(es) não exercitada(s)")
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


def cc_layer(repo, base, warnings, rows=None):
    if not shutil.which("lizard"):
        warnings.append("cc: lizard ausente, skip")
        return "skip"
    if not base:
        warnings.append("cc: sem base, skip")
        return "skip"
    red = False
    for status, paths in (name_status(base) if rows is None else rows):
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


MOCK_JS = re.compile(r"""\b(?:vi|jest)\.(?:mock|doMock)\(\s*['"]([^'"]+)['"]""")
MOCK_PY = re.compile(r"""\bpatch\(\s*['"]([^'"]+)['"]""")
DB_MODULE = re.compile(r"(^|[/.])(db|prisma|database|drizzle)([/.]index)?$")
# Banco real injetado pelo mock (padrão do erp-2f: libsql :memory: + migrate) não é mock de banco.
# Rails: rails_helper/TestCase sobem o banco de teste real. Go: txdb, pgtestdb, embedded-postgres.
REAL_DB = re.compile(
    r"migrate\(|:memory:|pglite|testcontainers|rails_helper|ActiveSupport::TestCase|ActionDispatch::IntegrationTest"
    r"|txdb|pgtestdb|embedded.?postgres",
    re.IGNORECASE,
)
# Mock do banco sem seam para injetar banco real: stub de query/persistência do ActiveRecord, sqlmock do Go.
AR_METHODS = (
    r"find|find_by!?|where|create!?|save!?|update!?|update_all|destroy!?|delete_all|all|first|last"
    r"|pluck|exists\?|transaction|insert_all|upsert_all|find_or_create_by!?"
)
MOCK_DB_LINE = re.compile(rf"\breceive(?:_messages)?\(\s*:(?:{AR_METHODS})\b|\bsqlmock\.New")
# Ruby: allow(Pedido) / expect(Pagamentos::Cobrar).to receive -> constante própria mockada.
MOCK_RB = re.compile(r"\b(?:allow|expect)(?:_any_instance_of)?\(\s*([A-Z]\w*(?:::[A-Z]\w*)*)\s*\)\.to\s+receive")


def internal_target(target, repo):
    if target.startswith((".", "@/", "#/", "~/")):
        return True
    # Python: o primeiro pacote do alvo existe no repo.
    top = target.split(".")[0]
    return "/" not in target and ((repo / top).is_dir() or (repo / "src" / top).is_dir())


def mock_finding(found, path, text, repo, graph):
    """(vermelho?, motivo) para um alvo de mock num teste, ou None se ele é externo ou é injeção de banco real."""
    if not (internal_target(found, repo) or graph.resolve(found, path)):
        return None
    if DB_MODULE.search(found):
        return None if REAL_DB.search(text) else (True, f"mocka o banco ({found}); use banco real em memória")
    if re.split(r"[/.]", found.rstrip("/"))[-1] == os.path.basename(path).split(".")[0]:
        return True, f"mocka o próprio módulo testado ({found})"
    return False, f"mocka código próprio ({found}); mock só na fronteira"


def mocks_layer(repo, base, warnings):
    """Só linhas adicionadas: mock antigo em teste editado não acusa."""
    if not base:
        warnings.append("mocks: sem base, skip")
        return "skip"
    red = False
    graph = ImportGraph(repo)
    for status, paths in name_status(base):
        path = paths[-1]
        if status.startswith("D") or not is_test(path) or Path(path).suffix not in SOURCE_EXT:
            continue
        text = blob("HEAD", path) or ""
        lines = text.splitlines()
        for num in added_lines(base, path):
            line = lines[num - 1] if 0 < num <= len(lines) else ""
            for is_red, msg in line_mock_findings(line, path, text, repo, graph):
                red = red or is_red
                warnings.append(f"mocks: {path}:{num} {msg}")
    return "red" if red else "green"


def line_mock_findings(line, path, text, repo, graph):
    """[(vermelho?, motivo)] de uma linha adicionada num teste."""
    if MOCK_DB_LINE.search(line):
        return [(True, "mocka o banco (stub de query/persistência); use o banco de teste real")]
    out = [(False, f"mocka código próprio ({const}); mock só na fronteira") for const in MOCK_RB.findall(line) if graph.resolve_rb(const)]
    for found in (*MOCK_JS.findall(line), *MOCK_PY.findall(line)):
        finding = mock_finding(found, path, text, repo, graph)
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


def load_aliases(repo):
    """[(prefixo, [pastas])] de compilerOptions.paths do tsconfig.json, relativos à raiz do repo."""
    try:
        config = json.loads(strip_jsonc((repo / "tsconfig.json").read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return []
    options = config.get("compilerOptions") or {}
    base = Path(options.get("baseUrl") or ".")
    aliases = []
    for key, targets in (options.get("paths") or {}).items():
        prefix = key[:-1] if key.endswith("*") else key
        dirs = [str(base / (t[:-1] if t.endswith("*") else t)) for t in targets if isinstance(t, str)]
        aliases.append((prefix, dirs))
    # Prefixo mais longo primeiro: "@/lib/" ganha de "@/".
    return sorted(aliases, key=lambda item: -len(item[0]))


class ImportGraph:
    """Imports resolvidos para arquivos do repo, lidos da working tree e guardados em cache."""

    def __init__(self, repo):
        self.repo = repo
        self.aliases = load_aliases(repo)
        self.cache = {}
        self.module = go_module(repo)
        self._rb = None

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
        for prefix, dirs in self.aliases:
            if spec == prefix.rstrip("/") or spec.startswith(prefix):
                for folder in dirs:
                    found = self.existing(Path(folder) / spec[len(prefix):])
                    if found:
                        return found
        return None

    def resolve_py(self, spec, src):
        dots = len(spec) - len(spec.lstrip("."))
        rest = spec[dots:].replace(".", "/")
        roots = [Path(src).parents[dots - 1]] if dots else [Path("."), Path("src")]
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
        found = set()
        for spec in specs:
            if self.module and spec.startswith(self.module + "/"):
                found |= self.go_files(spec[len(self.module) + 1 :])
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


def is_db_module(path):
    return DB_MODULE.search(str(Path(path).with_suffix("")).replace("\\", "/")) is not None


def integration_kind(path, graph):
    """'data' (toca o banco, ou service/action/rota...), 'ui' (componente, hook) ou None."""
    if not is_source(path) or is_db_module(path):
        return None
    parts = set(path.split("/")[:-1])
    base = os.path.basename(path)
    if parts & UI_DIRS:
        return "ui"
    if graph.touches_db(path) or any(is_db_module(item) for item in graph.imports(path)):
        return "data"
    if path.endswith(".rb") and path.startswith("app/") and parts & RB_DATA_DIRS:
        return "data"
    if parts & DATA_DIRS or DATA_FILE.search(base) or Path(base).stem in {"route", "router"}:
        return "data"
    return None


def is_integration(text, kind):
    if kind == "data":
        return REAL_DB.search(text) is not None
    return all(rx.search(text) for rx in UI_INTERACTION)


def test_files(untracked=False):
    files = git("ls-files").stdout.splitlines()
    if untracked:
        files += git("ls-files", "--others", "--exclude-standard").stdout.splitlines()
    return [p for p in files if is_test(p) and Path(p).suffix in SOURCE_EXT and not p.endswith(".d.ts")]


def covered_by_integration(graph, untracked=False):
    """{kind: arquivos alcançados por algum teste de integração daquele tipo}."""
    covered = {"data": set(), "ui": set()}
    for test in test_files(untracked):
        text = graph.text(test)
        kinds = [kind for kind in covered if is_integration(text, kind)]
        if not kinds:
            continue
        mocked = {graph.resolve(spec, test) for spec in MOCK_JS.findall(text)}
        mocked |= {graph.resolve_rb(const) for const in MOCK_RB.findall(text)}
        reached = graph.reach(test, skip={item for item in mocked if item})
        for kind in kinds:
            covered[kind] |= reached
    return covered


def integration_gaps(repo, rows, untracked=False):
    graph = ImportGraph(repo)
    touched = []
    for status, paths in rows:
        path = paths[-1]
        kind = None if status.startswith("D") else integration_kind(path, graph)
        if kind:
            touched.append((path, kind))
    if not touched:
        return []
    covered = covered_by_integration(graph, untracked)
    return [(path, kind) for path, kind in touched if path not in covered[kind]]


GAP_HINT = {
    "data": "teste que importe o módulo e rode contra banco real em memória",
    "ui": "teste que renderize o componente e interaja (render + userEvent/fireEvent)",
}


def integration_layer(repo, base, warnings):
    if not base:
        warnings.append("integração: sem base, skip")
        return "skip"
    gaps = integration_gaps(repo, name_status(base))
    for path, kind in gaps:
        warnings.append(f"integração: {path} sem {GAP_HINT[kind]}")
    return "red" if gaps else "green"


def cmd_integration():
    """Lacunas de integração do diff contra a working tree, antes de escrever os testes."""
    repo = root()
    os.chdir(repo)
    base = base_sha() or git("rev-parse", "HEAD").stdout.strip()
    rows = name_status(base, None)
    rows += [("A", [path]) for path in git("ls-files", "--others", "--exclude-standard").stdout.splitlines()]
    gaps = integration_gaps(repo, rows, untracked=True)
    for path, kind in gaps:
        print(f"[doctor-deploy] integração: falta {GAP_HINT[kind]}: {path}", file=sys.stderr)
    if not gaps:
        print("[doctor-deploy] integração: todo módulo tocado tem teste de integração", file=sys.stderr)
    return 1 if gaps else 0


def cmd_cc():
    """CC das funções do diff contra a working tree, antes de escrever os testes."""
    repo = root()
    os.chdir(repo)
    base = base_sha() or git("rev-parse", "HEAD").stdout.strip()
    rows = name_status(base, None)
    untracked = git("ls-files", "--others", "--exclude-standard").stdout.splitlines()
    rows += [("A", [path]) for path in untracked]
    warnings = []
    status = cc_layer(repo, base, warnings, rows)
    for line in warnings:
        print(f"[doctor-deploy] {line}", file=sys.stderr)
    print(f"[doctor-deploy] cc: {status}", file=sys.stderr)
    return 1 if status == "red" else 0


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
            r"\b(ENOTFOUND|EAI_AGAIN|ECONNRESET|ECONNREFUSED|ETIMEDOUT|no such host|dial tcp|getaddrinfo|SocketError|Could not fetch)\b",
            run.stderr, re.IGNORECASE,
        ) is not None
        if run.returncode != 0 and network_failure:
            warnings.append(f"audit: {cmd[0]} skip (rede)")
        elif run.returncode != 0:
            warnings.append(f"audit: {cmd[0]} high/critical")
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


def mutation_layer(repo, base, warnings):
    consent = load_json(consent_path()) or {}
    if consent.get("mutation") == "no":
        warnings.append("mutação: recusada, skip")
        return "skip"
    exe = stryker_exe()
    if exe is None:
        # shortcut: só Stryker (JS/TS, os dois repos modelo); mutmut/cargo-mutants entram quando houver repo Python/Rust.
        warnings.append("mutação: sem Stryker em node_modules, skip")
        return "skip"
    if not base:
        warnings.append("mutação: sem base, skip")
        return "skip"
    added = {p: set(added_lines(base, p)) for p in changed_paths(name_status(base)) if is_source(p) and Path(p).suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}}
    added = {p: lines for p, lines in added.items() if lines}
    if not added:
        return "green"
    STRYKER_REPORT.unlink(missing_ok=True)
    cmd = [exe, "run", "--mutate", ",".join(sorted(added)), "--reporters", "json"]
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=MUTATION_TIMEOUT)
    except subprocess.TimeoutExpired:
        warnings.append(f"mutação: timeout {MUTATION_TIMEOUT}s, skip")
        return "skip"
    if not STRYKER_REPORT.exists():
        warnings.append(f"mutação: Stryker saiu {run.returncode} sem relatório, skip")
        return "skip"
    survivors = stryker_survivors(repo, added)
    for path, line, mutator in survivors:
        warnings.append(f"mutação: {path}:{line} {mutator} sobreviveu (nenhum teste quebrou)")
    return "red" if survivors else "green"


def cmd_layers(started):
    repo = root()
    os.chdir(repo)
    base = base_sha()
    warnings = []
    layers = {
        "diff_cover": coverage_layer(repo, base, started, warnings),
        "size": size_layer(base, warnings),
        "cc": cc_layer(repo, base, warnings),
        "mocks": mocks_layer(repo, base, warnings),
        "integration": integration_layer(repo, base, warnings),
        "deps": deps_layer(base, warnings),
        "audit": audit_layer(base, warnings),
        "mutation": mutation_layer(repo, base, warnings),
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
    # Só avisos: no portão, cada um destes vira skip, não vermelho.
    repo = root()
    def say(ok, msg_ok, msg_warn):
        print(f"[doctor-deploy] ok: {msg_ok}" if ok else f"[doctor-deploy] aviso: {msg_warn}", file=sys.stderr)
    base = base_sha()
    say(base, f"base {base[:12]}", "sem base diferente do HEAD (origin/master, origin/main, master, main): as camadas do diff vão pular")
    cov = next((name for name in COVERAGE_FILES if (repo / name).is_file()), None)
    say(cov, f"cobertura em {cov} (o portão exige que o quality a regenere)",
        "nenhum artefato de cobertura (lcov, Cobertura XML, coverprofile do Go, .resultset.json do SimpleCov): se o quality não gerar um, o diff-cover vai pular")
    for tool in needs_consent():
        print(f"[doctor-deploy] aviso: {tool} sem consentimento (gate.sh consent {tool} yes|no)", file=sys.stderr)


def needs_consent():
    consent = load_json(consent_path()) or {}
    tools = []
    if not shutil.which("lizard") and "lizard" not in consent:
        tools.append("lizard")
    if stryker_exe() and "mutation" not in consent:
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
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"[doctor-deploy] consent {tool}={answer}", file=sys.stderr)


def main():
    argv = sys.argv[1:]
    if not argv:
        raise SystemExit("uso: layers | cc | integration | write-receipt | check | print-skipped | needs-consent | consent")
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
    raise SystemExit(f"comando desconhecido: {cmd}")


if __name__ == "__main__":
    main()
