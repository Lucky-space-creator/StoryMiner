"""
Jinja2 提示词渲染引擎（M12.2/M12.4/M12.5）

整体思路：
    提供模板语法校验、变量抽取与上下文渲染，供 M12 调试预览，并预留给 M7/M8/M10 复用。

关键点：
    1. validate 用 jinja2.Environment.parse 捕获 TemplateSyntaxError，语法错误即抛 BizError。
    2. extract_variables 用 meta.find_undeclared_variables 列出占位符。
    3. render 缺失变量以空串兜底，保证预览不报错（生产调用亦同）。

实现逻辑：
    基于 jinja2.Environment(BaseLoader)；纯函数无状态。
"""
from jinja2 import Environment, BaseLoader, TemplateSyntaxError, meta
from common.exceptions import BizError


_env = Environment(loader=BaseLoader(), autoescape=False, keep_trailing_newline=True)


def validate(content: str) -> None:
    """校验 Jinja2 语法，错误抛 BizError(400)。"""
    try:
        _env.parse(content)
    except TemplateSyntaxError as e:
        raise BizError(400, f"模板语法错误(行{e.lineno}): {e.message}")


def extract_variables(content: str) -> list[str]:
    """抽取模板声明的变量占位符。"""
    try:
        ast = _env.parse(content)
        return sorted(meta.find_undeclared_variables(ast))
    except TemplateSyntaxError:
        return []


def render(content: str, context: dict) -> str:
    """渲染模板：缺失变量以空串兜底，绝不抛错。"""
    validate(content)
    tmpl = _env.from_string(content)
    safe_ctx = {k: ("" if v is None else v) for k, v in (context or {}).items()}
    return tmpl.render(**safe_ctx)
