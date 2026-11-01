"""Offline documentation and AST claim auditing; source is never executed."""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace

from . import __version__
from .phrases import DIAR, NODIAR


def normalize(text):
    return ' '.join(text.lower().replace("n't", ' not').replace('-', ' ').split())


def prose(text):
    lines = []
    fence = None
    for line in text.splitlines():
        opening = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence:
            if re.match(r'^ {0,3}' + re.escape(fence[0]) + '{' + str(fence[1]) + r',}\s*$', line):
                fence = None
                lines.append('')
        elif opening:
            fence = (opening.group(1)[0], len(opening.group(1)))
            lines.append('')
        else:
            lines.append(line)
    return re.split(r'\n\s*\n', '\n'.join(lines))


def matches(text, phrases):
    found = []
    for phrase in sorted(phrases, key=lambda p: (-len(p), p)):
        pattern = r'\b' + re.escape(phrase) + r'\b'
        if re.search(pattern, text):
            found.append(phrase)
            text = re.sub(pattern, ' ', text)
    return found, text


def claims(text):
    positive, negative = [], []
    for paragraph in prose(text):
        no, remaining = matches(normalize(paragraph), NODIAR)
        yes, _ = matches(remaining, DIAR)
        positive.extend(yes)
        negative.extend(no)
    return positive, negative


def source_evidence(text):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    aliases = {}
    shadows = set()
    diarize_shadow = any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and node.name == 'diarize' for node in ast.walk(tree))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split('.')[0]] = item.name if item.asname else item.name.split('.')[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for item in node.names:
                aliases[item.asname or item.name] = node.module + '.' + item.name
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            shadows.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            shadows.add(node.id)
        elif isinstance(node, ast.arg):
            shadows.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            shadows.add(node.name)
    def resolve(node):
        if isinstance(node, ast.Name):
            return None if node.id in shadows else aliases.get(node.id)
        if isinstance(node, ast.Attribute):
            base = resolve(node.value)
            return base + '.' + node.attr if base else None
        return None
    evidence = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        kind = None
        if isinstance(func, ast.Attribute) and func.attr == 'diarize' and not diarize_shadow:
            kind = 'generic diarize call'
        resolved = resolve(func)
        if resolved:
            module, _, name = resolved.rpartition('.')
            family = module.split('.')[0]
            expected = {'SpeakerDiarization': 'pyannote', 'DiarizeModel': 'whisperx',
                        'assign_word_speakers': 'whisperx', 'NeuralDiarizer': 'nemo',
                        'Diarizer': 'simple_diarizer'}
            if name in expected and family == expected[name] and name not in shadows:
                kind = resolved
            if isinstance(func, ast.Attribute) and func.attr == 'from_pretrained':
                base = resolve(func.value)
                arguments = list(node.args) + [kw.value for kw in node.keywords]
                if base and base.startswith('pyannote.') and base.endswith('.Pipeline') and 'Pipeline' not in shadows:
                    if any(isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                           and re.search('diari[sz]ation', arg.value, re.I) for arg in arguments):
                        kind = 'pyannote Pipeline.from_pretrained'
        if kind:
            evidence.append({'line': node.lineno, 'kind': kind})
    return evidence


# The dispatcher uses the same generic attribute call shape that the audit detects.
detector = SimpleNamespace(diarize=source_evidence)


def audit(root, ignore=()):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise OSError('root must be a readable, non-symlink directory')
    positives, negatives, production, tests = [], [], [], []
    prefixes = [Path(p).as_posix().rstrip('/') for p in ignore]
    def ignored(relative):
        name = relative.as_posix()
        return any(name == p or name.startswith(p + '/') for p in prefixes)
    def walk_error(error):
        raise error
    for directory, dirs, files in os.walk(str(root), followlinks=False, onerror=walk_error):
        base = Path(directory)
        dirs[:] = sorted(d for d in dirs if not (base / d).is_symlink() and not ignored((base / d).relative_to(root)))
        for name in sorted(files):
            path = base / name
            relative = path.relative_to(root)
            if path.is_symlink() or ignored(relative):
                continue
            if (relative == Path('README.md') or ('docs' in relative.parts[:-1] and path.suffix == '.md')):
                yes, no = claims(path.read_text(encoding='utf-8'))
                positives.extend({'path': relative.as_posix(), 'phrase': p} for p in yes)
                negatives.extend({'path': relative.as_posix(), 'phrase': p} for p in no)
            if path.suffix != '.py' or set(relative.parts[:-1]) & {'fixtures', 'docs', 'examples'}:
                continue
            evidence = detector.diarize(path.read_text(encoding='utf-8'))
            target = tests if ('tests' in relative.parts[:-1] or name.startswith('test_') or name.endswith('_test.py')) else production
            target.extend(dict(item, path=relative.as_posix()) for item in evidence)
    findings = []
    if positives and not production:
        findings.append({'rule': 'documented-speaker-diarization-claim-false',
                         'severity': 'MEDIUM' if tests else 'HIGH',
                         'message': 'Evidence exists only in tests.' if tests else 'No production evidence.',
                         'claims': positives})
    if negatives and production:
        findings.append({'rule': 'documented-no-speaker-diarization-claim-false',
                         'severity': 'HIGH', 'message': 'Production evidence contradicts the claim.',
                         'claims': negatives})
    return {'status': 'FAIL' if findings else 'PASS', 'findings': findings,
            'evidence': production, 'test_evidence': tests}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, self.prog + ': error: ' + message + '\n')


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--ignore', action='append', default=[], help='Relative path prefix to exclude (repeatable)')
    parser.add_argument('--fail-on', choices=['LOW', 'MEDIUM', 'HIGH'], default='HIGH')
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args(argv)
    try:
        result = audit(args.root, args.ignore)
    except (OSError, UnicodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result['status'])
        for finding in result['findings']:
            print('{severity} {rule}: {message}'.format(**finding))
        if args.verbose:
            print(json.dumps({'evidence': result['evidence'], 'test_evidence': result['test_evidence']}, indent=2))
    ranks = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2}
    return 2 if any(ranks[f['severity']] >= ranks[args.fail_on] for f in result['findings']) else 0
