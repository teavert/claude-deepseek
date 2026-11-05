"""Inspect candidate source files and history; print locations, never matched secrets."""
import json, re, subprocess, sys, shutil, tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
patterns=[rb'(?i)[A-Z]:[/\\]Users[/\\](?!Public[ /\\])',rb'(?i)(?:sk-|ghp_|github_pat_|ul-)[A-Za-z0-9_-]{20,}',rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----']
forbidden=re.compile(r'(?i)(^|/)(node_modules|venv|backups|jobs|projects|sessions|file-history|shell-snapshots|status|state)(/|$)|\.(dpapi|key|sqlite3|log|jsonl|exe|dll|bak)(\.|$)')
def git(*args):
 return subprocess.check_output(['git','-C',str(root),*args])
try:
 paths=set(git('ls-files','-z','--cached','--others','--exclude-standard').decode().split('\0'))-{''}
except (OSError,subprocess.CalledProcessError):
 sys.exit('Initialize Git before running release checks.')
allowed=set(json.loads((root/'release-files.json').read_text()))
fail=[name+': absent from reviewed release manifest' for name in sorted(paths-allowed)]
fail += [name+': missing release file' for name in sorted(allowed-paths)]
for name in sorted(paths):
 p=root/name
 if forbidden.search(name): fail.append(name+': excluded file type')
 if p.is_file():
  data=p.read_bytes()
  if any(re.search(pattern,data) for pattern in patterns): fail.append(name+': sensitive content pattern')
try:
 commits=git('rev-list','--all').decode().split()
 for commit in commits:
  for record in git('ls-tree','-r',commit).decode().splitlines():
   meta,name=record.split('\t',1)
   if forbidden.search(name): fail.append(commit[:8]+': excluded history path')
   data=git('cat-file','blob',meta.split()[2])
   if any(re.search(pattern,data) for pattern in patterns): fail.append(commit[:8]+': sensitive history content')
except subprocess.CalledProcessError:
 fail.append('History inspection failed')
for item in fail: print(item)
if fail: sys.exit(1)
print(f'Content checks passed: {len(paths)} candidate files; all reachable history inspected.')

if '--gitleaks' in sys.argv:
 with tempfile.TemporaryDirectory(prefix='release-source-') as directory:
  for name in paths:
   source=root/name
   if source.is_file():
    dest=Path(directory)/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
  subprocess.run(['gitleaks','dir','--redact','--no-banner','--config',str(root/'.gitleaks.toml'),directory],check=True)
