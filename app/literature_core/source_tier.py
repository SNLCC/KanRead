"""外部来源的结构分层（Phase 7 的 Web 分层）——**只说域名是什么，不评价它好不好**。

要防的两种失败方式：

1. **把搜索结果当权威依据**。用户点开"查看网页依据"看到一串链接时，`arxiv.org` 与一个陌生博客
   排在一起、长得一样。这一层把可核验的**域名事实**标出来，让用户一眼看出"这是一篇预印本"
   还是"这是一个高校机构库"，而不是自己去猜。
2. **假装分层等于质量判定**。"这是个大学网站"与"这个网站上的说法可靠"是两件事；更要紧的是，
   `routing.EXTERNAL_KINDS` 里的"学术参考资料"是**一种检索能力**，而本应用只有通用网页搜索。
   域名规则再准也变不出学术库检索，因此本模块产出的层级**不参与** `source_plan` 的
   `sources` / `needs_external`：它只影响结果的**排序与显示**，一个字都不改文献覆盖状态。

因此这里的词表刻意只放**公开、可核对、归属唯一**的域名（平台自有的域名与其说 A 成立），
并且宁可返回 `GENERAL_WEB`：认不出来就是"一般网页"，不猜。
"""
from urllib.parse import urlsplit

# 层级顺序 = 显示顺序（不是质量顺序）。每一层都对应一个**能核对的域名事实**。
SOURCE_TIERS = ('PREPRINT', 'JOURNAL_DOMAIN', 'ACADEMIC_PUBLISHER', 'INSTITUTION', 'GOVERNMENT',
    'REFERENCE_WORK', 'GENERAL_WEB')

TIER_LABELS = {
    'PREPRINT': '预印本平台',
    'JOURNAL_DOMAIN': '期刊网站',
    'ACADEMIC_PUBLISHER': '学术出版社网站',
    'INSTITUTION': '高校或研究机构网站',
    'GOVERNMENT': '政府或公共机构网站',
    'REFERENCE_WORK': '百科或参考资料站',
    'GENERAL_WEB': '一般网页',
}

# 判断依据只有一条：这个域名**属于谁**。不涉及内容、不涉及该内容是否可信。
_PREPRINT = ('arxiv.org', 'biorxiv.org', 'medrxiv.org', 'ssrn.com', 'osf.io', 'preprints.org',
    'chinaxiv.org', 'techrxiv.org', 'eartharxiv.org', 'psyarxiv.com')
_JOURNAL_DOMAIN = ('nature.com', 'science.org', 'sciencemag.org', 'cell.com', 'thelancet.com',
    'bmj.com', 'jamanetwork.com', 'nejm.org', 'pnas.org', 'plos.org', 'frontiersin.org',
    'mdpi.com', 'springeropen.com', 'tandfonline.com', 'sagepub.com', 'acp.org', 'iopscience.iop.org')
_ACADEMIC_PUBLISHER = ('doi.org', 'link.springer.com', 'springer.com', 'sciencedirect.com', 'elsevier.com',
    'wiley.com', 'onlinelibrary.wiley.com', 'taylorandfrancis.com', 'cambridge.org',
    'academic.oup.com', 'oup.com', 'jstor.org', 'ieeexplore.ieee.org',
    'ieee.org', 'acm.org', 'dl.acm.org', 'aps.org', 'iopscience.org', 'sagepub.co.uk', 'emerald.com',
    'degruyter.com', 'brill.com', 'karger.com', 'thieme-connect.com', 'worldscientific.com')
_REFERENCE_WORK = ('wikipedia.org', 'britannica.com', 'stanford.edu/entries', 'plato.stanford.edu',
    'iep.utm.edu', 'zbmath.org', 'mathworld.wolfram.com')

# 机构域名没有唯一列表（全世界的高校各自一个域名），因此只能按**域名后缀规则**判断。
# 这些后缀是各国教育与政府域名的公开注册规则（`.edu`、`.edu.cn`、`.ac.uk`、`.gov`、`.gov.cn` …），
# 属于事实性规则，不是对某个机构的评价。
_INSTITUTION_SUFFIXES = ('.edu', '.ac.uk', '.ac.jp', '.ac.cn', '.edu.cn', '.edu.hk', '.edu.tw',
    '.edu.au', '.edu.sg', '.ac.kr', '.uni-', '.university')
_GOVERNMENT_SUFFIXES = ('.gov', '.gov.cn', '.gov.uk', '.gov.au', '.gov.jp', '.mil', '.go.jp', '.gob.es',
    '.gouv.fr', '.europa.eu', '.gov.tw', '.gov.hk')


def host_of(url):
    """取主机名（小写、去掉 `www.`）。取不到时返回空串——不确定就什么都不断言。"""
    try:
        host = urlsplit(str(url or '')).hostname or ''
    except ValueError:
        return ''
    host = host.lower().rstrip('.')
    return host[4:] if host.startswith('www.') else host


def _matches(host, domains):
    """`host` 是否就是这些域名之一或它们的子域（`x.nature.com` 与 `nature.com` 同属一家）。"""
    return any(host == domain or host.endswith('.' + domain) for domain in domains)


def _is_reference(host, path):
    """百科与参考资料站：域名命中，或"某高校域名下的专业百科"这一类路径。"""
    if _matches(host, _REFERENCE_WORK):
        return True
    # `plato.stanford.edu` 这类"机构域名 + 专业词条站"：域名本身说明不了，路径可以。
    return '/entries/' in path and any(host.endswith(suffix) for suffix in _INSTITUTION_SUFFIXES)


def tier_of(url):
    """一个外部来源属于哪一层（**域名事实**，不是质量判断）；认不出来就是 `GENERAL_WEB`。"""
    host = host_of(url)
    if not host:
        return 'GENERAL_WEB'
    path = ''
    try:
        path = urlsplit(str(url)).path.lower()
    except ValueError:
        path = ''
    if _matches(host, _PREPRINT):
        return 'PREPRINT'
    if _matches(host, _JOURNAL_DOMAIN):
        return 'JOURNAL_DOMAIN'
    if _matches(host, _ACADEMIC_PUBLISHER):
        return 'ACADEMIC_PUBLISHER'
    if _is_reference(host, path):
        return 'REFERENCE_WORK'
    if any(host.endswith(suffix) for suffix in _GOVERNMENT_SUFFIXES):
        return 'GOVERNMENT'
    if any(host.endswith(suffix) for suffix in _INSTITUTION_SUFFIXES):
        return 'INSTITUTION'
    return 'GENERAL_WEB'


def label_of(tier):
    return TIER_LABELS.get(tier, TIER_LABELS['GENERAL_WEB'])


def rank_of(tier):
    """层级在显示顺序里的位置；没见过的层排最后（不抛错，也不插队）。"""
    return SOURCE_TIERS.index(tier) if tier in SOURCE_TIERS else len(SOURCE_TIERS)


def annotate(results):
    """给每条结果标上层级，并**按层级稳定排序**（同层保持原有相对顺序）。

    排序与显示是这一层**全部**的作用：它不参与引用编号的取舍、不参与"证据够不够"的判断，
    也不改变 `source_plan` 的建议来源。因此这里用稳定排序，保证同一批结果每次顺序一致。
    """
    rows = []
    for index, item in enumerate(results or []):
        row = dict(item) if isinstance(item, dict) else {'url': str(item)}
        row['tier'] = tier_of(row.get('url'))
        row['tier_label'] = label_of(row['tier'])
        rows.append((rank_of(row['tier']), index, row))
    rows.sort(key=lambda pair: (pair[0], pair[1]))
    return [row for _, _, row in rows]


def listing(results):
    """给报告用的分层清单：`{'counts','groups','note'}`。

    `groups` 只列**确实出现过的**层，`note` 如实说明这套分层的边界——尤其是
    "本应用只有通用网页搜索，没有学术库检索"这件事，否则用户会以为标了"预印本平台"就等于
    已经查过学术资料库了。
    """
    groups = []
    for tier in SOURCE_TIERS:
        rows = [(index, item) for index, item in enumerate(results or []) if (item or {}).get('tier') == tier]
        if not rows:
            continue
        groups.append({'tier': tier, 'label': label_of(tier), 'count': len(rows),
            'hosts': list(dict.fromkeys(host_of((item or {}).get('url')) for _, item in rows if host_of((item or {}).get('url'))))})
    return {'counts': {group['tier']: group['count'] for group in groups}, 'groups': groups,
        'note': '这一层只按域名归属分组（例如域名属于预印本平台或期刊网站），用于排序与显示；'
                '它不代表内容质量，也不等于已经查过学术资料库——本应用只有通用网页搜索。'}
