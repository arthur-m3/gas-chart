"""Read-only checks for this incident's loaders and editor execution hooks."""
import ast
import os
from pathlib import Path
import re
import shutil
import sys
import warnings

IGNORED = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.next', '.astro-cache', '.pytest_cache', '.mypy_cache', 'logs', 'dist', 'build', '.terraform'}
REVIEWED_CHECKS = {'security/check_source.py', 'security/test_source_check.py', 'scripts/check-source-security.mjs', 'scripts/security.test.mjs'}
FORBIDDEN_FILES = {'temp_auto_push.bat', 'temp_interactive_push.bat', 'branch_structure.json', 'nul'}

def scan(root):
    root = Path(root).resolve()
    findings = []
    count = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in IGNORED and not (Path(directory)/d).is_symlink()]
        for name in files:
            path = Path(directory)/name
            if path.is_symlink() or name.startswith('.env') or name.endswith(('.pem', '.key')) or name == 'airflow_settings.yaml':
                continue
            relative = path.relative_to(root).as_posix()
            raw = path.read_bytes()
            count += 1
            if name in FORBIDDEN_FILES:
                findings.append((relative, 'incident-related spreading/concealment file'))
            # Evaluate signatures as data even in binary-looking asset extensions.
            encoded_loader = b'__0x04b12' in raw and (b'atob(' in raw or b'global.i' in raw)
            historical_loader = b'_$_1e42' in raw and (b'global[' in raw or b'createRequire' in raw)
            padded_loader = re.search(rb'\s{200,}global\s*(?:\[|\.)', raw) and (b'eval' in raw or b'Function' in raw or b'_0x' in raw)
            if relative not in REVIEWED_CHECKS and (encoded_loader or historical_loader or padded_loader):
                findings.append((relative, 'known remote-code loader or hidden padded payload'))
            if relative.startswith(('.vscode/', '.cursor/')) or name.endswith('.code-workspace'):
                if re.search(rb'"runOn"\s*:\s*"folderOpen"|"task\.allowAutomaticTasks"\s*:\s*(?:true|"on")', raw):
                    findings.append((relative, 'automatic editor execution must be reviewed and removed'))
                if re.search(rb'node[^\r\n]*fa-solid-(?:400|600)', raw):
                    findings.append((relative, 'editor command executes a disguised font file'))
            if name == '.gitignore':
                banned = {b'temp_auto_push.bat', b'temp_interactive_push.bat', b'branch_structure.json', b'.gitignore', b'nul'}
                if any(line.strip() in banned for line in raw.splitlines()):
                    findings.append((relative, 'ignore rule conceals incident files or .gitignore changes'))
            if name == 'package.json' and re.search(rb'node\s+\.?/?api\.js\s*&&', raw):
                findings.append((relative, 'npm command executes the known api.js loader'))
            if name.endswith('.py'):
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore', SyntaxWarning)
                        ast.parse(raw, filename=relative)
                except SyntaxError as error:
                    findings.append((relative, 'Python syntax error at line '+str(error.lineno)))
    # Never invoke npm to determine its version or inspect its files.
    executable = shutil.which('npm')
    if executable:
        entry = Path(executable).resolve()
        bootstrap = entry.parent.parent/'lib/cli.js'
        if bootstrap.is_file():
            raw = bootstrap.read_bytes()
            if len(raw) > 64 * 1024 or re.search(rb'A10-\*|__0x04b12|x-payload-b64|_\$_1e42', raw):
                findings.append((str(bootstrap), 'npm installation is suspicious; restore verified tooling'))
    return count, findings

def main():
    root = Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
    count, findings = scan(root)
    if findings:
        print('Source security check failed:')
        for path, reason in findings:
            print('- '+path+': '+reason)
        return 1
    print('Source security check passed; inspected '+str(count)+' files without executing application code.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
