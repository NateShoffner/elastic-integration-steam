import sys, re, pathlib

def strip(path, varname):
    lines = pathlib.Path(path).read_text(encoding='utf-8').splitlines(keepends=True)
    out, i, removed = [], 0, False
    while i < len(lines):
        m = re.match(r'^(\s*)- name: ' + re.escape(varname) + r'\s*$', lines[i])
        if m:
            indent = len(m.group(1))
            i += 1
            # skip until next list item at same indent, or a line with less indent
            while i < len(lines):
                ln = lines[i]
                if ln.strip() == '':
                    i += 1
                    continue
                cur = len(ln) - len(ln.lstrip())
                if cur <= indent:
                    break
                i += 1
            removed = True
            continue
        out.append(lines[i]); i += 1
    if removed:
        pathlib.Path(path).write_text(''.join(out), encoding='utf-8')
    return removed

for p in sys.argv[2:]:
    print(('removed ' if strip(p, sys.argv[1]) else 'absent  ') + p)
