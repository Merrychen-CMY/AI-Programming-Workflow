"""Check the pinned Doxygen guide and render its view; never certify product C code."""
import argparse
import hashlib
import json
from pathlib import Path

BASE = Path('standards/embedded-c/v1.3/doxygen')
HUAWEI = Path('standards/embedded-c/v1.3/huawei/coverage.json')
UPSTREAM = 'https://github.com/viys/doxygen_stu'
COMMIT = 'd0c0531cf3802f399540387c74641eaa45d033ac'
SOURCE_FILES = {
    'readme.md': ('f9365335b54b8d659c2416558579f3467c9b74f1224985d79e997d9bf872bac4', 3759),
    'LICENSE': ('061671fd79a72a30a3806ee284293f2013c949a51a853a7b07a2868965f90dc6', 1061),
    'Doxyfile': ('b128ee219ec270f6a1b44a1cca8184fc4e831c7ea5937598e757edef9f361fb5', 132410),
}
HUAWEI_SHA256 = '4de7381858f8e47f3c04b6f1950bcebdb41b289bf8fedf67990ff0180ef6efba'
PRECEDENCE = ('Huawei requirements take precedence over this Doxygen reference; '
              'it only supplements comment syntax and organization and must not weaken Huawei requirements.')
PRECEDENCE_SCOPE = ('This decision applies to the Doxygen reference only and does not reopen '
                    'unrelated historical project differences.')
SOURCE_PRECEDENCE = ('本次Doxygen参考与华为规范冲突时，以华为规范为准；Doxygen仅补充且不得削弱华为要求。'
                     '本次决定不重新审查或改变其他历史项目差异。')
# Independently reviewed source sections, strengths and exact child inventory.
REVIEWED = [
    (9, 20, 'MUST', ('MUST', 'MUST', 'MUST')),
    (24, 36, 'MUST', ('MUST', 'MUST', 'MUST')),
    (40, 60, 'MUST', ('MUST', 'MUST', 'MUST', 'MUST')),
    (64, 76, 'MUST', ('MUST', 'SHOULD', 'MUST')),
    (80, 87, 'MUST', ('MUST', 'MUST')),
    (91, 102, 'MUST', ('MUST', 'SHOULD')),
    (106, 113, 'MUST', ('MUST', 'MUST', 'SHOULD')),
    (117, 130, 'MUST', ('MUST', 'MUST', 'MAY', 'MUST')),
    (134, 143, 'MUST', ('MUST', 'SHOULD')),
    (147, 158, 'SHOULD', ('SHOULD', 'MUST')),
    (162, 166, 'MUST', ('SHOULD', 'MUST', 'MAY')),
    (170, 188, 'MAY', ('MAY', 'MAY')),
]
TAGS = ('@file', '@brief', '@details', '@param', '@return', '@retval', '@note',
        '@warning', '@see', '@code', '@endcode', '@mainpage', '@section')


def _object_list(value):
    return isinstance(value, list) and all(isinstance(row, dict) for row in value)


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _product_claims(node, errors):
    if isinstance(node, dict):
        if any(node.get(key) in ('PASS', 'SUCCESS') for key in ('status', 'result', 'adoption')):
            errors.append('Doxygen adoption metadata must not contain a product PASS/SUCCESS result')
        if any(node.get(key) is True for key in ('product_compliance_claimed', 'product_checks_executed')):
            errors.append('Doxygen static reference cannot claim product execution or compliance')
        for value in node.values():
            _product_claims(value, errors)
    elif isinstance(node, list):
        for value in node:
            _product_claims(value, errors)


def validate_reference(data, source, huawei_ids):
    """Validate catalog/provenance structure and reviewed boundaries without network access."""
    errors = []
    if not isinstance(data, dict) or not isinstance(source, dict):
        return ['Doxygen adoption and source must be JSON objects']
    for key, expected in {
        'schema_version': 1, 'adoption_revision': 1, 'classification': 'DOC-001',
        'standard_version': '1.3', 'source_ref': 'source.json', 'upstream_commit': COMMIT,
        'huawei_catalog_parent_count': 145, 'huawei_catalog_subrequirement_count': 50,
        'precedence': PRECEDENCE, 'precedence_scope': PRECEDENCE_SCOPE,
    }.items():
        if data.get(key) != expected:
            errors.append(f'Doxygen adoption baseline/precedence changed: {key}')
    for key, expected in {'normative': True, 'extends_huawei_catalog': False,
                          'product_compliance_claimed': False, 'product_checks_executed': False}.items():
        if data.get(key) is not expected:
            errors.append(f'Doxygen adoption boundary changed: {key}')
    policy = data.get('strength_policy', {})
    if not isinstance(policy, dict) or any(not _nonempty(policy.get(k)) for k in
            ('MUST', 'SHOULD', 'MAY', 'nested_requirements', 'level_origin')):
        errors.append('Doxygen strength policy must retain each level and nested applicability')
    boundary = data.get('verification_boundary', {})
    if not isinstance(boundary, dict):
        errors.append('Doxygen verification boundary must be an object')
    else:
        for key in ('not_run_is_pass', 'template_success_is_product_success', 'all_rules_automatically_checked'):
            if boundary.get(key) is not False:
                errors.append(f'Doxygen must not overclaim product verification: {key}')
        for key in ('syntax_and_entity_coverage', 'semantic_truth'):
            if not _nonempty(boundary.get(key)):
                errors.append(f'Doxygen verification explanation missing: {key}')
    workflow = data.get('workflow', {})
    if not isinstance(workflow, dict) or workflow.get('extra_user_form_required') is not False:
        errors.append('Doxygen adoption must not add a user form')
    elif (workflow.get('implementation_owner') != 'L3'
          or workflow.get('independent_verification_owner') != 'L4'
          or workflow.get('finding_intake') != 'DOT_as_L2'
          or workflow.get('user_review_points') != ['final_report']
          or workflow.get('l1_delegated_review_points') != ['requirements', 'execution_plan']):
        errors.append('Doxygen must retain independent verification, DOT intake and existing approvals')

    if (source.get('schema_version') != 1 or source.get('repository_url') != UPSTREAM
            or source.get('selected_branch') != 'master' or source.get('pinned_commit') != COMMIT):
        errors.append('Doxygen source must identify the reviewed pinned upstream commit')
    if source.get('full_upstream_project_included') is not False:
        errors.append('Doxygen source must not claim to include the complete upstream project')
    records = source.get('read_files')
    if not _object_list(records):
        errors.append('Doxygen source file records must be an object list')
    else:
        if (len(records) != 3 or any(not isinstance(r.get('path'), str) for r in records)
                or {r.get('path') for r in records} != set(SOURCE_FILES)):
            errors.append('Doxygen source requires exactly the three reviewed files')
        for row in records:
            name = row.get('path')
            if not isinstance(name, str) or name not in SOURCE_FILES:
                continue
            digest, size = SOURCE_FILES[name]
            if row.get('sha256') != digest or row.get('bytes') != size:
                errors.append(f'Doxygen pinned source hash/size mismatch: {name}')
            if row.get('url') != f'{UPSTREAM}/blob/{COMMIT}/{name}' or row.get('raw_url') != (
                    f'https://raw.githubusercontent.com/viys/doxygen_stu/{COMMIT}/{name}'):
                errors.append(f'Doxygen source URL is not pinned: {name}')
            if row.get('upstream_file_copied_to_repository') is not False:
                errors.append(f'Doxygen source copy boundary changed: {name}')
            if not _nonempty(row.get('review_scope')) or not _nonempty(row.get('hash_basis')):
                errors.append(f'Doxygen source review/hash provenance missing: {name}')
    license_record = source.get('license', {})
    if not isinstance(license_record, dict):
        errors.append('Doxygen license record must be an object')
    else:
        notice = license_record.get('notice')
        if (license_record.get('spdx_id') != 'MIT'
                or license_record.get('copyright_notice') != 'Copyright (c) 2025 viys'
                or license_record.get('url') != f'{UPSTREAM}/blob/{COMMIT}/LICENSE'
                or license_record.get('notice_preserved_for_attribution') is not True
                or not isinstance(notice, str)
                or hashlib.sha256(notice.encode('utf-8')).hexdigest() != SOURCE_FILES['LICENSE'][0]):
            errors.append('Doxygen MIT license attribution/notice must match the pinned source')
    precedence = source.get('huawei_precedence', {})
    if not isinstance(precedence, dict) or (
            precedence.get('document_id') != 'DKBA 2826-2011.5'
            or precedence.get('source_sha256') != HUAWEI_SHA256
            or precedence.get('decision') != SOURCE_PRECEDENCE):
        errors.append('Doxygen source must preserve Huawei priority without an adaptation exception')
    elif not isinstance(precedence.get('rules'), list) or any(
            not isinstance(ref, str) or ref not in huawei_ids for ref in precedence['rules']):
        errors.append('Doxygen source has invalid Huawei references')
    config = source.get('upstream_configuration_decision', {})
    if not isinstance(config, dict) or config.get('copied_as_product_gate') is not False:
        errors.append('Doxygen upstream example configuration is not a product gate')
    elif not _nonempty(config.get('reason')):
        errors.append('Doxygen upstream configuration decision needs a reason')
    docs = source.get('official_documentation')
    if not _object_list(docs) or not docs:
        errors.append('Doxygen official syntax/technical references must be retained')
    else:
        for doc in docs:
            topics = doc.get('reviewed_topics')
            if (not _nonempty(doc.get('url')) or not _nonempty(doc.get('finding'))
                    or not isinstance(topics, list) or not topics
                    or any(not _nonempty(topic) for topic in topics)):
                errors.append('Doxygen official reference requires URL, topics and finding')
    if (source.get('official_docs_are_live_reference_not_pinned_tool_version') is not True
            or not _nonempty(source.get('product_tool_version_policy'))):
        errors.append('Doxygen source must distinguish live documentation from executed tool versions')

    rows = data.get('rules')
    if not _object_list(rows):
        errors.append('Doxygen rules must be an object list')
        rows = []
    if [r.get('id') for r in rows] != [f'DOX-{n:02}' for n in range(1, 13)]:
        errors.append('Doxygen requires exactly the 12 reviewed rules in source order')
    expected_rows = {f'DOX-{i:02}': (i, *spec) for i, spec in enumerate(REVIEWED, 1)}
    child_total = 0
    for row in rows:
        identity = row.get('id')
        if not isinstance(identity, str) or identity not in expected_rows:
            continue
        index, start, end, level, child_levels = expected_rows[identity]
        if row.get('project_level') != level:
            errors.append(f'Doxygen rule strength changed: {identity}')
        if (row.get('source_section') != (str(index) if index <= 11 else 'unnumbered-mainpage')
                or row.get('source_lines') != {'start': start, 'end': end}
                or row.get('source_url') != f'{UPSTREAM}/blob/{COMMIT}/readme.md#L{start}-L{end}'):
            errors.append(f'Doxygen source location changed: {identity}')
        for key in ('title', 'project_requirement', 'applicability', 'verification', 'conflict_decision'):
            if not _nonempty(row.get(key)):
                errors.append(f'Doxygen rule missing {key}: {identity}')
        refs = row.get('huawei_refs')
        if (not isinstance(refs, list) or not refs or any(not isinstance(ref, str) for ref in refs)
                or len(refs) != len(set(refs)) or not set(refs) <= huawei_ids):
            errors.append(f'Doxygen invalid Huawei references: {identity}')
        children = row.get('requirements')
        if not _object_list(children):
            errors.append(f'Doxygen child requirements must be an object list: {identity}')
            continue
        child_total += len(children)
        expected_children = {f'{identity}.{n:02}': strength for n, strength in enumerate(child_levels, 1)}
        if [c.get('id') for c in children] != list(expected_children):
            errors.append(f'Doxygen missing, duplicate or unexpected child requirements: {identity}')
        for child in children:
            cid = child.get('id')
            if not isinstance(cid, str) or cid not in expected_children:
                continue
            if child.get('project_level') != expected_children[cid]:
                errors.append(f'Doxygen child strength changed: {cid}')
            for key in ('requirement', 'applicability', 'verification'):
                if not _nonempty(child.get(key)):
                    errors.append(f'Doxygen child missing {key}: {cid}')
    if child_total != 33:
        errors.append('Doxygen requires exactly 33 reviewed child requirements')
    tags = data.get('tags')
    if not _object_list(tags) or [t.get('tag') for t in tags] != list(TAGS):
        errors.append('Doxygen complete tag inventory must be retained')
    elif any(not _nonempty(t.get(k)) for t in tags for k in ('meaning', 'selection')):
        errors.append('Doxygen tag meaning/selection must be documented')
    _product_claims(data, errors)
    _product_claims(source, errors)
    return errors


def render_readme(data, source):
    """Deterministic human view of the authoritative adoption/source JSON files."""
    upstream, commit = source['repository_url'], source['pinned_commit']
    lines = [
        '# Doxygen 注释参考的项目采用规范', '',
        '<!-- Generated by scripts/doxygen_reference.py --write; edit adoption.json/source.json or renderer. -->', '',
        f'本补充采用 [viys/doxygen_stu]({upstream}) 的固定提交 `{commit}`，逐项覆盖其 README 的 11 节和可选主页说明。'
        '固定来源、原始文件摘要及 MIT 许可归属见 [source.json](source.json)，机器可读要求见 [adoption.json](adoption.json)。'
        '它属于 **DOC-001 的独立补充**，不增加或改写华为 145 个父条款与 50 个子项。', '',
        '**这份 Doxygen 参考与华为规范冲突时，以华为规范为准；Doxygen 仅作补充，不得削弱华为要求。** '
        '本次决定只处理这份注释参考，不重新审查或改变其他历史项目差异。DOT 在现有任务、执行计划和证据中安排检查；'
        '用户仍只审核需求、执行计划和最终报告，不填写新的逐项表单。', '',
        '## 使用和强度', '',
    ]
    for level in ('MUST', 'SHOULD', 'MAY'):
        lines.append(f"- **{level}**：{data['strength_policy'][level]}")
    lines += ['', data['strength_policy']['nested_requirements'], '', data['strength_policy']['level_origin'], '',
              '先按任务范围识别自有文件与文档实体，再使用对应条款和模板。第三方、工具生成和旧代码沿用既有范围约定；'
              'AI 编写的普通源码不因此成为生成代码豁免。模板中的身份、日期、版本、错误码、单位和版权必须替换为真实信息，'
              '不能原样充当完成证据。', '', '## 逐项采用', '']
    for row in data['rules']:
        lines += [f"### {row['id']} · {row['title']}", '',
                  f"来源：[上游第{row['source_section']}部分]({row['source_url']})；项目级别：**{row['project_level']}**。", '',
                  row['project_requirement'], '', f"适用：{row['applicability']}", '',
                  f"验证：{row['verification']}", '', f"差异与冲突决定：{row['conflict_decision']}", '',
                  f"关联华为条款：{', '.join(row['huawei_refs'])}。", '']
        for child in row['requirements']:
            lines += [f"- **{child['id']} {child['project_level']}**：{child['requirement']}",
                      f"  适用：{child['applicability']} 验证：{child['verification']}"]
        lines.append('')
    lines += ['## 标签速查', '', '| 标签 | 用途 | 何时使用 |', '| --- | --- | --- |']
    for tag in data['tags']:
        lines.append(f"| `{tag['tag']}` | {tag['meaning']} | {tag['selection']} |")
    lines += ['', '文件元数据还使用 `@author`、`@date`、`@version`，信息必须真实。该表不是要求每条注释填写所有标签；'
              '`void` 函数不编造返回值，没有警告事项时不编造 `@warning`。', '', '## 验证与回流', '',
              data['verification_boundary']['syntax_and_entity_coverage'], '',
              data['verification_boundary']['semantic_truth'], '',
              '必须核对实际输入非空、覆盖适用源码与静态实体、工具配置没有抑制必要诊断，并保留候选、命令、版本、配置和原始结果。'
              '模板通过或仓库自身的示例检查通过，不代表产品源码通过。工具缺失、尚未配置或未运行须保留 NOT_RUN/BLOCKED，不能标 PASS。'
              '专用检查器须明确已实现范围，不能宣称自动证明全部语义。', '',
              'L3 实施，L4 独立检查；问题统一提交 DOT/L2 分析调度。实现或注释缺陷由 DOT/L2 派 L3 修复，'
              '需求/验收目标变化回 L1，验证计划问题回 L1-V，设施问题由 L4 处理；最终报告遵守既有候选与审批规则。', '',
              '## 来源与技术核查', '',
              '本目录按项目需求转述采用决定，不复制整个上游项目。上游归属为 '
              f"{source['license']['copyright_notice']}，MIT 许可全文及固定文件哈希保留在 source.json。", '',
              source['upstream_configuration_decision']['reason'], '']
    for doc in source['official_documentation']:
        lines.append(f"- [官方文档：{', '.join(doc['reviewed_topics'])}]({doc['url']})：{doc['finding']}")
    lines += ['', source['product_tool_version_policy'], '', '## 维护检查', '',
              '在仓库根目录运行 `python scripts/doxygen_reference.py` 校验固定来源、条目、强度、关联和本文同步。'
              '修改采用表后运行 `python scripts/doxygen_reference.py --write` 重建本文，再运行检查。'
              '这项静态校验不执行产品 Doxygen，也不证明产品注释合规。', '']
    return '\n'.join(lines)


def _load_inputs(root):
    loaded, errors = [], []
    for relative in (BASE / 'adoption.json', BASE / 'source.json', HUAWEI):
        try:
            loaded.append(json.loads((root / relative).read_text(encoding='utf-8')))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f'Cannot read Doxygen reference input {relative}: {exc}')
    return loaded, errors


def _validate_inputs(inputs):
    data, source, huawei = inputs
    if not isinstance(huawei, dict) or not _object_list(huawei.get('clauses')):
        return ['Huawei catalog is unavailable for Doxygen reference validation']
    identities = {r['id'] for r in huawei['clauses'] if isinstance(r.get('id'), str)}
    return validate_reference(data, source, identities)


def check_doxygen_reference(root):
    """Return static reference/view errors; suitable for validate_repo.py integration."""
    root = Path(root)
    inputs, errors = _load_inputs(root)
    if errors:
        return errors
    errors.extend(_validate_inputs(inputs))
    if errors:
        return errors
    try:
        actual = (root / BASE / 'README.md').read_text(encoding='utf-8')
        if actual != render_readme(inputs[0], inputs[1]):
            errors.append('Doxygen README is stale; run python scripts/doxygen_reference.py --write')
    except (OSError, UnicodeError) as exc:
        errors.append(f'Cannot read generated Doxygen README: {exc}')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--write', action='store_true', help='regenerate README after valid metadata checks')
    args = parser.parse_args()
    if args.write:
        inputs, errors = _load_inputs(args.root)
        if not errors:
            errors.extend(_validate_inputs(inputs))
        if errors:
            print('\n'.join(errors))
            return 1
        (args.root / BASE / 'README.md').write_text(render_readme(inputs[0], inputs[1]), encoding='utf-8', newline='\n')
    errors = check_doxygen_reference(args.root)
    if errors:
        print('\n'.join(errors))
        return 1
    print('Doxygen reference: 12 rules/33 child requirements and pinned sources verified; no product compliance claimed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
