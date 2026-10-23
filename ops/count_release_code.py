"""Count physical source lines in a release directory, without reading secrets."""
import argparse
import json
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('root',type=Path)
args=parser.parse_args()
root=args.root.resolve(strict=True)
extensions={'.py','.js','.css','.html','.sh','.ps1'}
development_prefixes=('test_','live_','verify_','prepare_','capture_','build_','setup_existing_','deploy_')
totals={}
files=[]
for area in ('backend','static','ops'):
    directory=root/area
    if not directory.is_dir():
        continue
    for path in sorted(directory.rglob('*')):
        relative=path.relative_to(root)
        if not path.is_file() or path.is_symlink() or path.suffix not in extensions:
            continue
        if '__pycache__' in relative.parts or 'node_modules' in relative.parts:
            continue
        if area=='ops' and path.name.startswith(development_prefixes):
            continue
        if area=='ops' and path.name in ('count_release_code.py','check_mail.py'):
            continue # Counting/one-off mail probes are not shipped service code.
        category='vendor' if area=='static' and 'vendor' in relative.parts else area
        # LF/CRLF source files; count the last unterminated line as one physical line.
        content=path.read_bytes()
        lines=content.count(b'\n')+bool(content and not content.endswith(b'\n'))
        nonblank=sum(bool(line.strip()) for line in content.splitlines())
        value=totals.setdefault(category,{'files':0,'lines':0,'nonblank_lines':0})
        value['files']+=1
        value['lines']+=lines
        value['nonblank_lines']+=nonblank
        files.append({'path':relative.as_posix(),'category':category,'lines':lines})
print(json.dumps({'root':str(root),'definition':'Physical lines including comments and blank lines; excludes tests, QA/build/deploy helpers, caches, dependencies, config, docs, images. Bundled JS/CSS vendor separate.','groups':totals,'own_lines':sum(value['lines'] for name,value in totals.items() if name!='vendor'),'own_files':sum(value['files'] for name,value in totals.items() if name!='vendor'),'files':files},ensure_ascii=False,indent=2))
