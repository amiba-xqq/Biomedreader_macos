"""Offline, provider-independent application of pinned scientific skill workflows."""
import hashlib
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

WORKFLOWS = {
    'summary': ('scientific-critical-thinking', 'scientific-writing'),
    'questions': ('scientific-brainstorming', 'hypothesis-generation'),
    'reviewer': ('peer-review', 'scientific-critical-thinking'),
    'finder': ('paper-lookup', 'literature-review'),
    'figure': ('scientific-visualization',),
    'protocol': ('scientific-writing', 'scientific-critical-thinking', 'paper-lookup'),
}
SECTIONS = {
    'scientific-critical-thinking': ('Appraisal Workflow', 'Application Guidelines'),
    'scientific-writing': ('Workflow', 'Revision and peer review'),
    'scientific-brainstorming': ('Operating rules', 'Reproducible workflow'),
    'hypothesis-generation': ('Keep the objects distinct', 'Workflow'),
    'peer-review': ('Review workflow', 'Human accountability'),
    'paper-lookup': ('Core Workflow',),
    'literature-review': ('Core Workflow', 'Common Pitfalls to Avoid'),
    'scientific-visualization': ('Non-negotiable guardrails', 'Workflow', 'Final review checklist'),
}
LABELS = {
    'scientific-critical-thinking': '科学证据评估',
    'scientific-writing': '科研写作',
    'scientific-brainstorming': '科学问题发散',
    'hypothesis-generation': '研究假说构建',
    'peer-review': '同行评审',
    'paper-lookup': '文献检索',
    'literature-review': '文献综合评述',
    'scientific-visualization': '科研可视化',
}
REFERENCES = {
    'scientific-writing': ('references/evidence_workflow.md',),
    'hypothesis-generation': ('references/hypothesis_quality_criteria.md',),
    'paper-lookup': ('references/pubmed.md',),
    'literature-review': ('references/core_workflow.md',),
}


def root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'assets' / 'scientific_skills'


@lru_cache(maxsize=1)
def manifest():
    try:
        return json.loads((root() / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ValueError('内置科研技能资料不完整，请重新安装 Biomed Reader。') from error


@lru_cache(maxsize=32)
def resource(name, relative='SKILL.md'):
    path = root() / name / relative
    filename = name + '/' + relative
    expected = next((row['sha256'] for row in manifest()['files'] if row['path'] == filename), None)
    if not expected or not path.resolve().is_relative_to(root().resolve()):
        raise ValueError('内置科研技能引用路径无效。')
    try:
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError('内置科研技能校验失败，请重新安装 Biomed Reader。')
        return content.decode('utf-8')
    except OSError as error:
        raise ValueError('内置科研技能文件缺失，请重新安装 Biomed Reader。') from error


def receipt(feature):
    return [dict(name=name, label=LABELS[name], revision=manifest()['revision'],
                 sha256=hashlib.sha256(resource(name).encode('utf-8')).hexdigest())
            for name in WORKFLOWS[feature]]


@lru_cache(maxsize=len(WORKFLOWS))
def workflow(feature):
    blocks = []
    for name in WORKFLOWS[feature]:
        text = resource(name)
        selected = []
        for section in re.split(r'(?m)(?=^## )', text):
            heading = section.splitlines()[0].removeprefix('## ').strip() if section else ''
            if heading in SECTIONS[name]:
                selected.append(section.strip())
        if len(selected) != len(SECTIONS[name]):
            raise ValueError('内置科研技能工作流不完整，请重新安装 Biomed Reader。')
        blocks.append('### ' + name + '\n' + '\n\n'.join(selected))
        for relative in REFERENCES.get(name, ()):
            blocks.append('### '+name+'/'+relative+'\n'+resource(name,relative))
    return '\n\n'.join(blocks)


def enhance(feature, instructions):
    return ('使用下列随软件内置的 Scientific Agent Skills 科研工作流。应用已经按本次功能加载相关章节。'
            '技能用于指导证据阅读、研究设计思考和写作；应用负责实际文献检索、全文读取、缓存和导出。'
            '只使用本次实际提供的材料与工具结果，不声称已经运行技能脚本、访问未查询的数据库、完成统计检验或核查未读参考资料。'
            '技能中涉及的文件操作、安装命令、额外服务和其它技能只有在应用实际提供相应能力时才能执行，不将这些步骤转交用户或自动要求额外下载。'
            '将工作流适配为生物医学科研人员易懂的表达，内部检查证据与结论，不输出思考过程。'
            '下方任务要求决定语言、JSON结构、章节、篇幅、引用范围和图像通道。\n\n'
            + workflow(feature) + '\n\n【Biomed Reader 当前任务与输出要求】\n' + instructions)
