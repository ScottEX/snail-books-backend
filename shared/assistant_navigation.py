"""App navigation skill and server-side route catalog for chat."""

from shared.app_search import keyword_variants

# IDs are the iOS AppSearchPage values handled by RootStack.
DESTINATIONS = {
    'profile': '个人中心、个人资料、个人信息、账户资料、profile、my account',
    'account': '账户信息、昵称、账号资料、account info',
    'email': '修改邮箱、更换邮箱、change email',
    'password': '修改密码、改密码、更换密码、change password',
    'security': '安全设置、账户安全、security settings',
    'avatar': '更换头像、换头像、change avatar',
    'language': '语言设置、切换语言、language settings',
    'themeSettings': '主题设置、背景设置、背景图、theme settings',
    'userManagement': '用户管理、使用者管理、审核用户、manage users',
    'invoice': '发票、发票管理、invoice',
    'invoiceInfo': '发票资料、开票资料、billing details',
    'invoiceRecords': '发票记录、开票记录、invoice records',
    'invoiceApply': '申请开票、apply for invoice',
    'expense': '支出记录、支出历史、expense history',
    'expenseEntry': '录入支出、凭证上传、记账表单、add expense',
    'procurement': '采购管理、进货管理、procurement',
    'procurementHistory': '采购记录、进货记录、purchase history',
    'newOrder': '新建采购单、录入采购、new purchase order',
    'products': '商品管理、产品管理、products',
    'procurementCart': '购物车、采购车、cart',
    'statistics': '经营总览、收支统计、统计图表、statistics',
    'revenue': '营收记录、营业额明细、revenue history',
    'revenueEntry': '录入营收、登记营业额、daily revenue',
    'reconciliation': '对账记录、对账历史、reconciliation history',
    'reconciliationEntry': '录入对账、登记对账、reconciliation',
    'fee': '平台手续费、platform fees',
    'feeHistory': '手续费记录、手续费历史、fee history',
    'feeEntry': '录入手续费、add platform fee',
    'partnerStructure': '股东、持股比例、股权结构、shareholders',
    'dividendHistory': '分红记录、分红历史、dividend history',
}

NAVIGATION_SKILL = """
Use find_app_destinations when the user wants to go to an app page, asks where a feature
is, or asks how to find it. For 我要查看个人信息 or 我想看看我的账户资料 use profile.
When asking for actual ledger data, use a query tool. If a useful destination is clear,
also return its ID with a navigation call. Ask one question if navigation is ambiguous.
Use only catalog IDs, never produce a URL or claim the app has already navigated.
The app shows a button; the user chooses whether to open it.
"""


def find_app_destinations(route, *, is_admin):
    if not isinstance(route, str) or route not in DESTINATIONS:
        return None
    return route if is_admin or route != 'userManagement' else None


def match_destination(text, *, is_admin):
    """Local fallback for navigating by an exact or clearly requested feature."""
    variants = keyword_variants(text.casefold().strip('？?。！! '))
    navigation = ('查看', '打开', '在哪', '怎么', '带我去', '我想看', 'open ', 'where ', 'show me ')
    matches = [
        (len(alias), route)
        for route, aliases in DESTINATIONS.items()
        for alias in aliases.split('、')
        if any(variant == alias or (alias in variant and any(word in variant for word in navigation))
               for variant in variants)
    ]
    if not matches:
        return None
    return find_app_destinations(max(matches)[1], is_admin=is_admin)
