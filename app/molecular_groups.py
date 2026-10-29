"""At most two primary molecules per paper; one molecule per statistical group."""
import re

INSTRUCTIONS='''资料分类只挑选每篇文章设计研究的一个或两个主要基因／分子，最多2个，按研究核心程度排序；不要枚举背景、下游通路或实验中所有分子。
返回article_molecules数组，每项为{"article_id":1,"molecules":["IRAK1","ATM"]}；molecules的每个元素必须是一个独立基因符号或单一分子名称，不能用组合名称。未能确定时molecules为空，不得编造。
统计分组时每组只对应一个分子；有两个主要分子的文章分别进入两个单分子组，不建立IRAK1/ATM这样的组合组。每篇最多进入2组，组内不重复。分类只整理资料，不限定科学问题的发散范围。'''

def molecular_partition(items,count):
    by_article={i:[] for i in range(1,count+1)}
    for item in items if isinstance(items,list) else []:
        if not isinstance(item,dict):continue
        identifier=item.get('article_id');values=item.get('molecules')
        if type(identifier) is not int or identifier not in by_article or not isinstance(values,list):continue
        for value in values:
            if not isinstance(value,str):continue
            # Separators are split rather than turned into a multi-molecule group.
            # Hyphens remain part of legitimate symbols such as HLA-DRA.
            for name in re.split(r'[,，、;；/&+＋–—↔→]|\s+(?:and|or)\s+|以及|和|与|及',value,flags=re.I):
                name=' '.join(name.split()).strip();existing=by_article[identifier]
                if name and len(name)<=80 and name.casefold() not in {n.casefold() for n in existing} and len(existing)<2:existing.append(name)
    groups={};unknown=[]
    for identifier,names in by_article.items():
        if not names:unknown.append(identifier)
        for name in names:
            row=groups.setdefault(name.casefold(),dict(name=name,article_ids=[]));row['article_ids'].append(identifier)
    result=list(groups.values())
    if unknown:result.append(dict(name='主要基因／分子待确认',article_ids=unknown))
    return result

def classification(data,count,legacy):
    if isinstance(data.get('article_molecules'),list):
        return molecular_partition(data['article_molecules'],count),'primary_molecule_max2'
    return legacy(data.get('categories'),count),'primary_gene_molecule'
