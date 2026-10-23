"""Validate journal field decoding without executing the privileged watchdog."""
import ast,json,re
from pathlib import Path

source=Path(__file__).resolve().parents[1]/'ops'/'hypervisor_monitor.py'
tree=ast.parse(source.read_text(encoding='utf-8'))
function=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='field_text')
namespace={'json':json}
exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),namespace)
decode=namespace['field_text']
assert decode({'MESSAGE':'normal'},'MESSAGE')=='normal'
assert decode({'MESSAGE':None},'MESSAGE')==''
assert decode({'MESSAGE':list('한글 KASAN: benign fixture'.encode())},'MESSAGE')=='한글 KASAN: benign fixture'
assert re.search('KASAN:',decode({'MESSAGE':list(b'KASAN: benign fixture')},'MESSAGE'))
assert decode({'MESSAGE':['one','two']},'MESSAGE')=='["one", "two"]'
assert decode({},'MESSAGE')==''
print('Journal text, binary bytes, Korean, null and multi-value fields passed')
