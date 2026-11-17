#!/usr/bin/env python3
"""只读校验流程契约的结构与部分状态条件；不执行任何产品命令。"""

import argparse
import json
import math
import sys
from pathlib import Path


SCHEMA_NAMES = (
    "common.schema.json",
    "project-profile.schema.json",
    "dev-task.schema.json",
    "test-plan.schema.json",
    "delivery-report.schema.json",
)
INSTANCE_SCHEMA_NAMES = frozenset(SCHEMA_NAMES[1:])
EXIT_OK = 0
EXIT_INVALID = 1
EXIT_DRAFT_BLOCKED = 2
EXIT_TOOL_ERROR = 3


class ToolError(Exception):
    """工具缺依赖、路径不可读或 Schema 本身不合法。"""


class InvalidDocument(Exception):
    """实例不是唯一键 JSON 对象或不符合受支持的引用规则。"""


class ContractArgumentParser(argparse.ArgumentParser):
    """将命令行用法错误统一为工具错误码，避免与草稿阻塞混淆。"""

    def error(self, message):
        raise ToolError("命令行参数错误：" + message)


def unique_object(pairs):
    """拒绝重复键，避免不同工具选择不同值。"""
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidDocument("JSON 包含重复对象键")
        result[key] = value
    return result


def reject_nonfinite(value):
    """NaN/Infinity 不是合法 JSON 数值。"""
    raise InvalidDocument("JSON 不允许非有限数值常量")


def finite_float(value):
    """拒绝指数溢出产生的无穷值，例如合法 JSON 数字形式 1e309。"""
    parsed = float(value)
    if not math.isfinite(parsed):
        raise InvalidDocument("JSON 数值超出有限浮点表示范围")
    return parsed


def read_json(path):
    """以 UTF-8 读取契约，允许编辑器写入 BOM，不修改文件。"""
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise ToolError("无法读取 UTF-8 文件：" + str(path)) from error
    try:
        return json.loads(content, object_pairs_hook=unique_object,
                          parse_constant=reject_nonfinite,
                          parse_float=finite_float)
    except json.JSONDecodeError as error:
        raise InvalidDocument(
            f"JSON 语法错误，行 {error.lineno}，列 {error.colno}"
        ) from error


def load_validators(schema_directory):
    """离线注册同目录五份 Schema；禁止自动下载缺失引用。"""
    try:
        from jsonschema import Draft202012Validator, FormatChecker
        from referencing import Registry, Resource
        from referencing.exceptions import NoSuchResource
    except ImportError as error:
        raise ToolError(
            "校验依赖缺失，请在隔离环境安装 tools/requirements-validation.txt"
        ) from error

    try:
        directory = schema_directory.resolve(strict=True)
    except OSError as error:
        raise ToolError("Schema 目录不存在或不可访问") from error
    if not directory.is_dir():
        raise ToolError("--schema-dir 必须是目录")

    schemas = {}
    identifiers = set()
    resources = []
    for filename in SCHEMA_NAMES:
        try:
            schema_path = (directory / filename).resolve(strict=True)
        except OSError as error:
            raise ToolError("缺少本地 Schema：" + filename) from error
        if schema_path.parent != directory:
            raise ToolError("Schema 文件解析到指定目录外：" + filename)
        try:
            contract = read_json(schema_path)
            Draft202012Validator.check_schema(contract)
            identifier = contract.get("$id")
            if not isinstance(identifier, str) or not identifier:
                raise ToolError("Schema 必须具有非空 $id：" + filename)
            if identifier in identifiers:
                raise ToolError("Schema $id 重复：" + filename)
            identifiers.add(identifier)
            schemas[filename] = contract
            resources.append((identifier, Resource.from_contents(contract)))
        except ToolError:
            raise
        except Exception as error:
            raise ToolError("本地 Schema 不合法：" + filename) from error

    def offline_only(uri):
        raise NoSuchResource(ref=uri)

    registry = Registry(retrieve=offline_only).with_resources(resources)
    return {
        name: Draft202012Validator(contract, registry=registry,
                                  format_checker=FormatChecker())
        for name, contract in schemas.items() if name in INSTANCE_SCHEMA_NAMES
    }


def instance_path(error):
    """只输出错误位置和规则名，避免将实例字段值复制到日志。"""
    parts = [str(part) for part in error.absolute_path]
    return "/" + "/".join(parts)


def validate_instance(path, validators, require_ready):
    """返回本文件检查结果；草稿阻塞与结构错误独立区分。"""
    document = read_json(path)
    if not isinstance(document, dict):
        raise InvalidDocument("实例根节点必须是 JSON 对象")
    schema_name = document.get("schema_ref")
    if not isinstance(schema_name, str) or schema_name not in INSTANCE_SCHEMA_NAMES:
        raise InvalidDocument(
            "schema_ref 必须是本包四份实例 Schema 的确切文件名；"
            "不允许目录、绝对路径、URI 或目录外引用"
        )

    try:
        errors = list(validators[schema_name].iter_errors(document))
    except Exception as error:
        raise ToolError("Schema 解析或离线引用校验失败：" + str(path)) from error
    if errors:
        print(f"INVALID {path}: {len(errors)} 个结构/状态错误", file=sys.stderr)
        for error in errors[:10]:
            print(f"  {instance_path(error)}: 不符合规则 {error.validator}",
                  file=sys.stderr)
        if len(errors) > 10:
            print("  仅展示前 10 项错误", file=sys.stderr)
        return EXIT_INVALID

    if require_ready and document["status"] == "DRAFT":
        print(f"DRAFT_BLOCKED {path}: 结构合法，仍为 DRAFT", file=sys.stderr)
        return EXIT_DRAFT_BLOCKED

    acceptance = document.get("acceptance_status")
    extra = f" acceptance_status={acceptance}" if acceptance else ""
    print(f"STRUCTURE_OK {path}: status={document['status']}{extra}")
    return EXIT_OK


def main(argv=None):
    """退出码：0结构通过、1文档不合法、2草稿阻塞、3工具/用法错误。"""
    parser = ContractArgumentParser(
        description="离线校验任务契约；只检查结构与部分状态，不执行产品命令。"
    )
    parser.add_argument("instances", nargs="+", type=Path,
                        help="一个或多个 JSON 实例文件")
    parser.add_argument("--schema-dir", type=Path,
                        default=Path(__file__).resolve().parents[1],
                        help="包含本包五份 Schema 的目录，默认脚本上两级目录")
    parser.add_argument("--require-ready", action="store_true",
                        help="拒绝 DRAFT；其余合法状态仍仅作结构检查")
    try:
        args = parser.parse_args(argv)
        validators = load_validators(args.schema_dir)
    except ToolError as error:
        print("TOOL_ERROR: " + str(error), file=sys.stderr)
        return EXIT_TOOL_ERROR

    result_codes = []
    for path in args.instances:
        try:
            code = validate_instance(path, validators, args.require_ready)
        except InvalidDocument as error:
            print(f"INVALID {path}: {error}", file=sys.stderr)
            code = EXIT_INVALID
        except ToolError as error:
            print("TOOL_ERROR: " + str(error), file=sys.stderr)
            code = EXIT_TOOL_ERROR
        result_codes.append(code)

    print("仅结构/部分状态检查；不是执行授权、测试执行记录或产品验收结论。")
    # 工具异常优先，其次结构错误，再其次草稿阻塞；检查其余文件后统一返回。
    for failure in (EXIT_TOOL_ERROR, EXIT_INVALID, EXIT_DRAFT_BLOCKED):
        if failure in result_codes:
            return failure
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
