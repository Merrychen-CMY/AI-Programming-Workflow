"""Check clause enumeration and generated views, not C code or product compliance."""
import argparse
from collections import Counter
import json
from pathlib import Path

BASE = 'standards/embedded-c/v1.3/huawei/'
SOURCE_ID = 'DKBA 2826-2011.5'
SOURCE_SHA256 = '4de7381858f8e47f3c04b6f1950bcebdb41b289bf8fedf67990ff0180ef6efba'
# Independently reviewed counts per chapter, in principle/rule/suggestion order.
COUNTS = [(3,8,4),(2,7,6),(2,6,10),(3,3,5),(0,4,3),(4,5,4),(2,0,4),
          (3,8,3),(0,6,2),(0,1,4),(0,4,1),(1,4,1),(1,10,0),(0,1,1),(0,1,2)]
KINDS = {'P':'原则', 'R':'规则', 'S':'建议', 'G':'治理补充'}
GOVERNANCE = ['0.2-1','0.2-2','0.2-3','0.3-application','0.3-exceptions','0.4-levels']
STRENGTHENED_SUGGESTIONS = {'HW-S-3.7','HW-S-6.3','HW-S-8.3'}


def expected_inventory():
    result = {f'HW-G-{number}': ('治理补充', number, 0) for number in GOVERNANCE}
    for chapter, counts in enumerate(COUNTS, 1):
        for letter, count in zip('PRS', counts):
            for n in range(1, count + 1):
                number = f'{chapter}.{n}'
                result[f'HW-{letter}-{number}'] = (KINDS[letter], number, chapter)
    return result


def validate_coverage(data, inventory, project_rule_ids):
    errors = []
    expected = expected_inventory()
    for obj in (data, inventory):
        if obj.get('source_document_id') != SOURCE_ID or obj.get('source_sha256') != SOURCE_SHA256:
            errors.append('Huawei source identity must match the reviewed PDF')
    for key, value in {'normative':True, 'product_compliance_claimed':False,
                       'product_checks_executed':False, 'scope_resolution_required':True,
                       'not_applicable_requires_reason':True, 'omission_is_not_pass':True}.items():
        if data.get(key) is not value:
            errors.append(f'Huawei coverage boundary changed: {key}')
    if data.get('standard_version') != '1.3' or data.get('coverage_revision') != 1:
        errors.append('Unexpected Huawei coverage/standard revision')
    if data.get('inventory') != BASE + 'source-inventory.json':
        errors.append('Huawei coverage must link its reviewed source inventory')
    if inventory.get('numbered_clause_count') != 139 or inventory.get('governance_supplement_count') != 6:
        errors.append('Huawei inventory must distinguish 139 numbered clauses and 6 governance items')
    rows = data.get('clauses', [])
    entries = inventory.get('entries', [])
    for label, values in [('clauses',rows),('inventory',entries)]:
        ids = [row.get('id') for row in values]
        if len(ids) != len(expected) or set(ids) != set(expected):
            errors.append(f'Huawei {label}: missing, duplicate or unexpected clause IDs')
    source = {row.get('id'):row for row in entries}
    for row in entries:
        identity = row.get('id')
        if identity in expected and tuple(row.get(k) for k in ('source_kind','source_number','chapter')) != expected[identity]:
            errors.append(f'Huawei inventory classification mismatch: {identity}')
        page = row.get('source_page')
        if type(page) is not int or not 5 <= page <= 60:
            errors.append(f'Huawei inventory page invalid: {identity}')
    for row in rows:
        identity = row.get('id')
        if identity not in expected:
            continue
        for field in ('source_kind','source_number','chapter','source_page'):
            if row.get(field) != source.get(identity, {}).get(field):
                errors.append(f'Huawei clause source mismatch: {identity}.{field}')
        for field in ('title','project_requirement','applicability','verification','difference'):
            if not isinstance(row.get(field), str) or not row[field].strip():
                errors.append(f'Huawei clause missing {field}: {identity}')
        level = 'SHOULD' if expected[identity][0] == '建议' and identity not in STRENGTHENED_SUGGESTIONS else 'MUST'
        if row.get('project_level') != level:
            errors.append(f'Huawei clause strength changed: {identity}')
        if row.get('adoption') not in ('ADOPTED','ADAPTED'):
            errors.append(f'Huawei catalog adoption is not a product result: {identity}')
        if row.get('adoption') == 'ADAPTED' and row.get('difference') == '保留原条款意图与强度；按本清单实施。':
            errors.append(f'Huawei adaptation needs a specific explanation: {identity}')
        if identity in STRENGTHENED_SUGGESTIONS and row.get('adoption') != 'ADAPTED':
            errors.append(f'Huawei strengthened suggestion must explain project basis: {identity}')
        mapped = row.get('project_rule_ids', [])
        if not mapped or len(set(mapped)) != len(mapped) or not set(mapped) <= project_rule_ids:
            errors.append(f'Huawei clause has invalid project-rule mapping: {identity}')
        if row.get('finding_intake') != 'L2' or row.get('independent_verification_owner') != 'L4':
            errors.append(f'Huawei findings must retain L4 independence and L2 intake: {identity}')
        owner = 'DOT/L2' if expected[identity][2] == 0 else 'L3'
        if row.get('implementation_owner') != owner:
            errors.append(f'Huawei implementation owner mismatch: {identity}')
        target = BASE + f'{expected[identity][2]:02}.md#{identity.lower()}'
        if row.get('requirement_ref') != target:
            errors.append(f'Huawei clause has invalid normative reference: {identity}')
    chapters = data.get('chapters', [])
    if [r.get('chapter') for r in chapters] != list(range(16)):
        errors.append('Huawei coverage must retain governance and chapters 1-15')
    for chapter in chapters:
        if chapter.get('path') != BASE + f"{chapter.get('chapter'):02}.md" or not chapter.get('title'):
            errors.append('Huawei chapter index path/title invalid')
    return errors


def render_views(data):
    """Markdown is a deterministic projection of the normative JSON catalog."""
    rendered = {}
    clauses = data['clauses']
    totals = Counter(r['source_kind'] for r in clauses)
    lines = ['# 华为条款逐条覆盖总览', '',
             '本表由 [coverage.json](coverage.json) 生成；维护方法和执行边界见 [使用说明](README.md)。', '',
             f"原文编号条款139项：原则{totals['原则']}、规则{totals['规则']}、建议{totals['建议']}；另列治理补充{totals['治理补充']}项，共145项。",
             '', '全部已形成项目要求；ADOPTED表示按原意纳入，ADAPTED表示明确项目适配。二者均不表示产品已验证或PASS。', '',
             '| 主题 | 原则 | 规则 | 建议 | 治理补充 |', '| --- | ---: | ---: | ---: | ---: |']
    for chapter in data['chapters']:
        n = chapter['chapter']
        rows = [r for r in clauses if r['chapter'] == n]
        counts = Counter(r['source_kind'] for r in rows)
        lines.append(f"| [{n} {chapter['title']}]({n:02}.md) | {counts['原则']} | {counts['规则']} | {counts['建议']} | {counts['治理补充']} |")
        body = [f"# {n} {chapter['title']}：逐条项目要求", '',
                '[完整清单](coverage.md) · [使用及证据规则](README.md) · [技术说明适配](technical-notes.md)', '',
                '下列文字为本项目的规范性要求，不是华为原文转载。页码按用户PDF页序；条款强度与项目差异分别列出。', '',
                '实施和独立验证按具体适用条件进行，未检查项不得记PASS；L4发现问题统一交DOT/L2分析和调度。', '']
        for row in rows:
            body.extend([
                f"<a id=\"{row['id'].lower()}\"></a>",
                f"## {row['id']} · {row['title']}", '',
                f"来源：{row['source_kind']} {row['source_number']}，PDF第{row['source_page']}页。项目级别：**{row['project_level']}**。采纳方式：{row['adoption']}。", '',
                f"**项目要求：** {row['project_requirement']}", '',
                f"**适用条件：** {row['applicability']}", '',
                f"**检查与证据：** {row['verification']}", '',
                f"**项目差异：** {row['difference']}", '',
                f"映射：{', '.join(row['project_rule_ids'])}。实施责任：{row['implementation_owner']}；独立验证：{row['independent_verification_owner']}；发现入口：DOT/{row['finding_intake']}。", ''
            ])
        rendered[BASE + f'{n:02}.md'] = '\n'.join(body).rstrip() + '\n'
    lines.extend(['', '## 全部条目索引', '', '| 条款 | 源页 | 项目要求主题 | 强度 | 采纳 | 项目规则 |', '| --- | ---: | --- | --- | --- | --- |'])
    for row in clauses:
        target = f"{row['chapter']:02}.md#{row['id'].lower()}"
        lines.append(f"| [{row['id']}]({target}) | {row['source_page']} | {row['title']} | {row['project_level']} | {row['adoption']} | {', '.join(row['project_rule_ids'])} |")
    rendered[BASE + 'coverage.md'] = '\n'.join(lines) + '\n'
    return rendered


def check_repository_coverage(root, project_rule_ids):
    data = json.loads((root / BASE / 'coverage.json').read_text(encoding='utf-8'))
    inventory = json.loads((root / BASE / 'source-inventory.json').read_text(encoding='utf-8'))
    errors = validate_coverage(data, inventory, project_rule_ids)
    if errors:
        return errors
    for name, expected in render_views(data).items():
        path = root / name
        if not path.is_file() or path.read_text(encoding='utf-8') != expected:
            errors.append(f'Huawei coverage view drift: {name}')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--render', action='store_true', help='Regenerate Markdown, not source metadata or hashes')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / BASE / 'coverage.json').read_text(encoding='utf-8'))
    inventory = json.loads((root / BASE / 'source-inventory.json').read_text(encoding='utf-8'))
    rules = json.loads((root / 'standards/embedded-c/v1.3/source/rules-index.json').read_text(encoding='utf-8'))['rules']
    errors = validate_coverage(data, inventory, {r['id'] for r in rules})
    if not errors and args.render:
        for name, content in render_views(data).items():
            (root / name).write_text(content, encoding='utf-8', newline='\n')
    if not errors:
        errors = check_repository_coverage(root, {r['id'] for r in rules})
    for error in errors:
        print('ERROR:', error)
    if not errors:
        print('Huawei clause catalog: 139 numbered + 6 governance items; product checks NOT_RUN.')
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
